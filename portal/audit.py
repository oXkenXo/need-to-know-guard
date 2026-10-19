"""The link between the AI and the administrators: logging, permission audit, recommendations,
Revoke / Keep actions, and access requests.

Principle: the AI only RECORDS and RECOMMENDS. Permissions change only when an administrator presses a
button (and ticks the confirmation). Every decision is stored in data/audit.db.
"""
import datetime as dt
import json
import shutil
import sqlite3
from collections import Counter, defaultdict

import pandas as pd

import config as K
from mailserver import MailError, Mailbox
from nextcloud import Nextcloud, NextcloudError

DB = K.DATA_DIR / "audit.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_log (id INTEGER PRIMARY KEY, time TEXT, user TEXT, item TEXT, title TEXT,
    zone TEXT, score REAL, keys TEXT);
CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, action TEXT, by TEXT, time TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, time TEXT, requester TEXT, item TEXT, title TEXT,
    author TEXT, reason TEXT, status TEXT, decided_by TEXT, decided_at TEXT);
"""
SOURCE = {"share": "Files", "link": "Files", "location": "Files", "teamfolder": "Files",
          "group": "Directory", "list": "Mail", "email": "Mail", "forward": "Mail"}


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def nextcloud():
    return Nextcloud(K.NC_URL, K.NC_ADMIN_USER, K.NC_ADMIN_PASS)


def wilson_lower(bad, n, z=1.96):
    """Lower bound of the 95% interval of the share of held accesses: little evidence never ranks first."""
    if n == 0:
        return 0.0
    p, d = bad / n, 1 + z * z / n
    return max(0.0, (p + z * z / (2 * n) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d)


# ---------------------------------------------------------------------- chatbot log (passive audit)
def log_chat(idx, user, decisions):
    """Every guard decision made while the chatbot answers, with the permission that caused the access."""
    rows = [(_now(), user, d["id"], idx["items"][d["id"]]["title"], d["zone"], d["score"],
             json.dumps(idx["access"].get(user, {}).get(d["id"], {}).get("keys", [])))
            for d in decisions if d["score"] is not None]           # author / approved / new hire: not evidence
    with db() as con:
        con.executemany("INSERT INTO chat_log (time,user,item,title,zone,score,keys) VALUES (?,?,?,?,?,?,?)", rows)


def chat_log_summary(idx):
    with db() as con:
        df = pd.read_sql("SELECT * FROM chat_log", con)
    cols = ["permission", "source", "requests", "held", "ask", "employees", "suspicion"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    st, who = defaultdict(Counter), defaultdict(set)
    for _, r in df.iterrows():
        for k in json.loads(r["keys"]):
            st[k][r["zone"]] += 1
            who[k].add(r["user"])
    rows = [{"key": k, "permission": idx["grants"].get(k, {}).get("label", k),
             "source": SOURCE.get(idx["grants"].get(k, {}).get("kind"), "?"), "requests": sum(c.values()),
             "held": c["hold"], "ask": c["ask"], "employees": len(who[k]),
             "suspicion": round(wilson_lower(c["hold"] + 0.5 * c["ask"], sum(c.values())), 3)}
            for k, c in st.items() if idx["grants"].get(k, {}).get("kind") != "teamfolder"]
    return pd.DataFrame(rows).sort_values(["suspicion", "employees"], ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------- proactive audit
def recommendation(idx, key):
    g = idx["grants"].get(key, {})
    d, kind = g.get("detail", {}), g.get("kind")
    path = (d.get("path") or "").strip("/")
    if kind == "share":
        return (f"Remove the share of '{path}' with group {d.get('to')}; share only with the people who need it"
                if d.get("share_type") == 1 else f"Remove the share of '{path}' with {d.get('to')}, or add an expiry date")
    if kind == "link":
        return f"Delete the public link to '{path}', or protect it with a password and an expiry date"
    if kind == "group":
        return f"Remove {d.get('user')} from group {d.get('group')} (check whether they still work there)"
    if kind == "location":
        return f"Move '{idx['items'].get(d.get('item'), {}).get('title')}' out of /{d.get('folder')} to a restricted folder"
    if kind == "teamfolder":
        return f"Review group {d.get('group')} on team folder /{d.get('folder')} (php occ groupfolders:group)"
    if kind == "email":
        return f"Purge '{d.get('subject')}' from the mailboxes of the people who do not need it"
    if kind == "list":
        return f"Remove {d.get('user')} from the {d.get('list')}@ mailing list"
    if kind == "forward":
        return f"Remove the forwarding rule that copies {d.get('src')}@ mail to {d.get('user')}"
    return "Review manually"


def proactive_audit(idx, guard):
    """Score EVERY access each permission opens (not only what someone asked the chatbot), then rank."""
    st, who, examples, flagged = defaultdict(Counter), defaultdict(set), defaultdict(list), defaultdict(set)
    not_scored = Counter()

    def add(key, u, d):
        st[key][d["zone"]] += 1
        who[key].add(u)
        if d["zone"] != "allow":
            flagged[key].add(u)
            examples[key].append(f"{u}: {idx['items'][d['id']]['title']}")

    for u, acc in idx["access"].items():
        ids = [i for i in acc if u not in idx["items"][i].get("authors", [])]
        if guard.new_hire(u):                    # no history -> cannot be judged; never blame the permission
            for i in ids:
                for k in acc[i]["keys"]:
                    not_scored[k] += 1
            continue
        for d in guard.decide(u, ids):
            if d["score"] is None:               # approved / confirmed by admin
                continue
            for k in acc[d["id"]]["keys"]:
                add(k, u, d)
    for k, g in idx["grants"].items():           # public links are open to every employee
        if g["kind"] != "link":
            continue
        for u in idx["users"]:
            if guard.new_hire(u):
                continue
            ids = [i for i in g["detail"].get("items", []) if u not in idx["items"][i].get("authors", [])]
            for d in guard.decide(u, ids):
                add(k, u, d)
    dec = decisions()
    rows = []
    for k, c in st.items():
        n, kind = sum(c.values()), idx["grants"].get(k, {}).get("kind", "?")
        if kind == "teamfolder":                 # already represented per file (location) and per member (group)
            continue
        rows.append({"key": k, "source": SOURCE.get(kind, "?"), "kind": kind, "scope": scope(idx, k),
                     "permission": idx["grants"].get(k, {}).get("label", k),
                     "accesses": n, "held": c["hold"], "ask": c["ask"], "allowed": c["allow"], "employees": len(who[k]),
                     "held_share": round((c["hold"] + 0.5 * c["ask"]) / max(n, 1), 3),
                     "suspicion": round(wilson_lower(c["hold"] + 0.5 * c["ask"], n), 3),
                     "not_scored": not_scored.get(k, 0), "examples": "; ".join(examples[k][:3]),
                     "flagged_users": ",".join(sorted(flagged[k])), "recommendation": recommendation(idx, k),
                     "status": dec.get(k, {}).get("action", "")})
    have = {r["key"] for r in rows}
    for k, v in dec.items():                     # decided permissions stay visible
        if k not in have and k in idx["grants"]:
            g = idx["grants"][k]
            rows.append({"key": k, "source": SOURCE.get(g["kind"], "?"), "kind": g["kind"], "scope": scope(idx, k), "permission": g["label"],
                         "accesses": 0, "held": 0, "ask": 0, "allowed": 0, "employees": 0, "held_share": 0.0,
                         "suspicion": 0.0, "not_scored": not_scored.get(k, 0), "examples": "", "flagged_users": "",
                         "recommendation": recommendation(idx, k), "status": v["action"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["suspicion", "employees", "accesses"], ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    df.insert(1, "rank_in_scope", df.groupby("scope").cumcount() + 1)
    return df


def reach(idx, key):
    """How many employees a permission reaches."""
    g = idx["grants"].get(key, {})
    if g.get("kind") == "link":
        return len(idx["users"])
    return sum(1 for acc in idx["access"].values() if any(key in a["keys"] for a in acc.values()))


def scope(idx, key):
    """broad = the permission reaches >= BROAD_GROUP_SHARE of all staff (announcement channels, all-staff
    shares, public links). Need-to-know is a weak signal there: real announcements match nobody's work."""
    return "broad" if reach(idx, key) >= K.BROAD_GROUP_SHARE * len(idx["users"]) else "narrow"


def rule_based_risks(idx, guard):
    """Things that need no AI: public links, accounts without history, accounts with no recent activity."""
    rows = []
    for k, g in idx["grants"].items():
        if g["kind"] == "link":
            d = g["detail"]
            rows.append({"type": "public link", "object": (d.get("path") or "").strip("/"), "key": k,
                         "note": "anyone with the link can open it" + ("" if d.get("expires") else "; no expiry date")})
    seen = idx.get("last_seen", {})
    latest = max(seen.values(), default="")
    for u, info in idx["users"].items():
        if guard.new_hire(u):
            rows.append({"type": "account without history", "object": f"{info['name']} ({u})", "key": "",
                         "note": f"groups: {', '.join(info['groups'])}. New hire -> fine (documents need owner approval). "
                                 "Former employee -> disable the account (HR/IAM, outside the guard)."})
        elif latest and seen.get(u) and (dt.date.fromisoformat(latest) - dt.date.fromisoformat(seen[u])).days >= K.INACTIVE_DAYS:
            days = (dt.date.fromisoformat(latest) - dt.date.fromisoformat(seen[u])).days
            rows.append({"type": "inactive account", "object": f"{info['name']} ({u})", "key": "",
                         "note": f"last activity {seen[u]} ({days} days before the most recent activity in the company). "
                                 "Check with HR whether this person still works here."})
    return pd.DataFrame(rows, columns=["type", "object", "key", "note"])


# ---------------------------------------------------------------------- admin actions
def decisions():
    with db() as con:
        cur = con.execute("SELECT key, action, by, time, result FROM decisions")
        return {r[0]: {"action": r[1], "by": r[2], "time": r[3], "result": r[4]} for r in cur.fetchall()}


def decision_history():
    with db() as con:
        return pd.read_sql("SELECT time, by, action, key, result FROM decisions ORDER BY time DESC", con)


def _record(key, action, by, result):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)", (key, action, by, _now(), result))


def keep(key, by):
    _record(key, "keep", by, "confirmed as intended")
    return f"Permission `{key}` marked as **intended**. Nothing was changed; the guard will no longer hold access through it."


def _edit_aliases(src, remove_user):
    """Remove one recipient from a line of postfix-virtual.cf (the mail server reloads it automatically)."""
    path = K.MAIL_ALIASES
    if not path or not path.exists():
        raise RuntimeError("MAIL_ALIASES is not set; edit the mail server's alias file by hand")
    shutil.copy(path, path.with_suffix(".cf.bak"))
    out, changed = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and not line.startswith("#") and parts[0].split("@")[0].lower() == src:
            rcpt = [r for r in parts[1].split(",") if r.split("@")[0].lower() != remove_user]
            changed = len(rcpt) != len(parts[1].split(","))
            if rcpt == [parts[0]]:               # only the mailbox itself is left -> rule no longer needed
                continue
            line = f"{parts[0]} {','.join(rcpt)}"
        out.append(line)
    if not changed:
        raise RuntimeError(f"{remove_user} not found in the {src}@ line")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _ldap_remove_member(group, user):
    import ldap3
    srv = ldap3.Server(K.LDAP_URL)
    with ldap3.Connection(srv, K.LDAP_ADMIN_DN, K.LDAP_ADMIN_PASSWORD, auto_bind=True) as con:
        con.modify(f"cn={group},ou=groups,{K.LDAP_BASE}",
                   {"member": [(ldap3.MODIFY_DELETE, [f"uid={user},ou=people,{K.LDAP_BASE}"])]})
        if con.result["result"] != 0:
            raise RuntimeError(f"LDAP: {con.result['description']} {con.result.get('message', '')}")


def revoke(idx, key, by, target=None, flagged_users=""):
    """Apply the fix on the real servers. Returns a message (also stored)."""
    g = idx["grants"].get(key)
    if not g:
        return "Permission not found (run Sync again)."
    d, kind, acc, nc = g["detail"], g["kind"], K.accounts(), nextcloud()
    try:
        if kind in ("share", "link"):
            owner = d.get("owner")
            if owner in acc:
                nc.delete_share(owner, acc[owner], d["id"])
                result = f"share/link {d['id']} of {owner} deleted"
            else:
                nc.notify(owner, "Please remove a share", f"An administrator asks you to remove the share of '{d.get('path')}'.")
                result = f"no password for {owner} -> {owner} was notified to remove it"
        elif kind == "group":
            if K.LDAP_URL:
                _ldap_remove_member(d["group"], d["user"])
                result = f"{d['user']} removed from directory group {d['group']}"
            else:
                nc.remove_from_group(d["user"], d["group"])
                result = f"{d['user']} removed from Nextcloud group {d['group']}"
        elif kind == "location":
            if not target:
                return "Fill in the **target folder** (e.g. `/Executive`) to move the file."
            it = idx["items"][d["item"]]
            mover = next((u for u in it.get("authors", []) if u in acc), None)
            if not mover:
                return "The author has no password in ACCOUNTS_FILE; move the file in Nextcloud by hand."
            dst = "/" + target.strip("/") + "/" + it["title"]
            nc.move(mover, acc[mover], idx["access"][mover][d["item"]]["path"], dst)
            result = f"'{it['title']}' moved to {dst} (as {mover})"
        elif kind == "email":
            # purge from the usernames typed by the admin, otherwise from the mailboxes the guard flagged
            users = [u.strip() for u in (target or flagged_users).split(",") if u.strip()]
            if not users:
                return ("The guard flagged no mailbox for this email. Type the usernames to purge it from in the "
                        "target field (e.g. `glin`).")
            unknown = [u for u in users if u not in acc]
            if unknown:
                return f"No password for {', '.join(unknown)} in ACCOUNTS_FILE; purge with doveadm on the mail server."
            mb = Mailbox(K.IMAP_HOST, K.IMAP_PORT, K.IMAP_CAFILE)
            n = sum(mb.delete(f"{u}@{K.MAIL_DOMAIN}", acc[u], d["message_id"]) for u in users if u in acc)
            result = f"'{d.get('subject')}' purged from {n} mailbox(es): {', '.join(users)}"
        elif kind == "list":
            _edit_aliases(d["list"], d["user"])
            result = f"{d['user']} removed from the {d['list']}@ list (alias file updated)"
        elif kind == "forward":
            _edit_aliases(d["src"], d["user"])
            result = f"forwarding of {d['src']}@ to {d['user']} removed (alias file updated)"
        elif kind == "teamfolder":
            return ("Team folder permissions are changed by the server admin: "
                    f"`php occ groupfolders:list`, then `php occ groupfolders:group <id> {d.get('group')} ...`")
        else:
            return "This kind of permission cannot be revoked automatically."
    except (NextcloudError, MailError, RuntimeError, OSError) as e:
        _record(key, "failed", by, str(e))
        return f"Failed: {e}"
    _record(key, "revoke", by, result)
    if K.NOTIFY:
        try:
            nc.notify(K.NC_ADMIN_USER, "Permission revoked in the portal", f"{key}: {result}")
        except NextcloudError:
            pass
    return f"Done: {result}. Press **Sync** so the portal reads the new permissions."


def notify_admin(table, n=5):
    top = table[(table["suspicion"] > 0) & (table["status"] == "")].head(n)
    if top.empty:
        return "Nothing suspicious to send."
    text = "\n".join(f"{r.rank}. {r.permission} (suspicion {r.suspicion})" for r in top.itertuples())
    nextcloud().notify(K.NC_ADMIN_USER, f"Guard: {len(top)} permissions to review", text)
    return f"Notification sent to {K.NC_ADMIN_USER}."


# ---------------------------------------------------------------------- access requests (ask zone)
def request_access(idx, requester, item, reason=""):
    it = idx["items"][item]
    author = it.get("authors", [None])[0] if it.get("authors") else None
    if not author:
        return "This item has no recorded author; contact an administrator."
    with db() as con:
        if con.execute("SELECT 1 FROM requests WHERE requester=? AND item=? AND status='pending'", (requester, item)).fetchone():
            return "A request for this item is already pending."
        con.execute("INSERT INTO requests (time,requester,item,title,author,reason,status) VALUES (?,?,?,?,?,?,?)",
                    (_now(), requester, item, it["title"], author, reason, "pending"))
    if K.NOTIFY:
        try:
            nextcloud().notify(author, f"{requester} asks for access to '{it['title']}'", reason or "(no reason given)")
        except NextcloudError:
            pass
    return f"Request sent to **{author}** (the author)."


def list_requests(author=None, requester=None):
    q, p = "SELECT id, time, requester, title, author, reason, status FROM requests", []
    if author:
        q, p = q + " WHERE author=?", [author]
    elif requester:
        q, p = q + " WHERE requester=?", [requester]
    with db() as con:
        return pd.read_sql(q + " ORDER BY id DESC", con, params=p)


def decide_request(rid, by, approve):
    with db() as con:
        r = con.execute("SELECT author, status FROM requests WHERE id=?", (rid,)).fetchone()
        if not r:
            return "Request not found."
        if r[0] != by:
            return "Only the author can decide."
        if r[1] != "pending":
            return f"Request already {r[1]}."
        con.execute("UPDATE requests SET status=?, decided_by=?, decided_at=? WHERE id=?",
                    ("approved" if approve else "rejected", by, _now(), rid))
    return "Approved: the chatbot may now use this item for the requester." if approve else "Rejected."


def approvals():
    with db() as con:
        return [tuple(r) for r in con.execute("SELECT requester, item FROM requests WHERE status='approved'")]


def legit_keys():
    return {k for k, v in decisions().items() if v["action"] == "keep"}
