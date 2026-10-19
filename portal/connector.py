"""Connector: read Nextcloud and the mail server as each employee -> index of items, effective ACL,
access paths (the permission that causes each access) and work history.

The effective ACL is never guessed: an item is in user X's ACL if it shows up in X's WebDAV listing or
X's mailbox. For every access the PATH is recorded, so the auditor can point at the permission to fix:

  files  teamfolder:<F>:<G>   team folder F is open to group G        (structure; changed by an admin)
         group:<G>:<X>        X is a member of group G                (action: remove X from G)
         location:<id>        the file sits in an open team folder    (action: move the file)
         share:<id>           a share to a user or group              (action: delete the share)
         link:<id>            a public link                           (action: delete the link)
  mail   send:<message-id>    an email was delivered to these people  (action: purge it from mailboxes)
         list:<L>:<X>         X receives the mailing list L           (action: remove X from L)
         forward:<A>:<X>      a forwarding rule copies A's mail to X  (action: remove the rule)

Groups/lists that contain >= BROAD_GROUP_SHARE of all staff (e.g. all-staff) are not audited per member.

Run:  python connector.py
"""
import datetime as dt
import json
from collections import Counter, defaultdict
from email.utils import parsedate_to_datetime

import config as K
from mailserver import MailError, Mailbox, parse_aliases
from nextcloud import Nextcloud, NextcloudError, file_text

INDEX = K.DATA_DIR / "index.json"


def _name(path):
    return path.rstrip("/").rsplit("/", 1)[-1]


def _date(s):
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).date().isoformat()          # WebDAV getlastmodified
    except (TypeError, ValueError):
        return str(s)[:10]


def sync(log=print):
    acc = K.accounts()
    if not acc:
        raise RuntimeError("ACCOUNTS_FILE is not set or empty (lab: ../lab/seed/accounts.env)")
    nc = Nextcloud(K.NC_URL, K.NC_ADMIN_USER, K.NC_ADMIN_PASS)

    # 1. employees and their groups
    users = {}
    for u, pw in sorted(acc.items()):
        info = nc.check_login(u, pw)
        if not info:
            log(f"  ! login {u} failed -> skipped")
            continue
        try:
            groups = nc.user_groups(u)
        except NextcloudError:
            groups = nc.user_groups(u, auth=(u, pw))
        users[u] = {"name": info["displayname"], "groups": sorted(groups)}
    if not users:
        raise RuntimeError("no employee account could log in")
    size = Counter(g for x in users.values() for g in x["groups"])
    broad = sorted(g for g, k in size.items() if k >= max(2, K.BROAD_GROUP_SHARE * len(users)))
    try:
        team = nc.team_folders()
    except NextcloudError as e:
        log(f"  ! team folders not readable ({e}); team-folder paths are guessed from group names")
        team = {}
    log(f"Employees: {len(users)} · broad groups (not audited per member): {broad} · team folders: {sorted(team)}")

    items, access, grants = {}, {u: {} for u in users}, {}
    paths = defaultdict(dict)
    shares, authors_act = {}, defaultdict(set)
    last_seen = defaultdict(str)

    def grant(key, kind, label, **detail):
        grants.setdefault(key, {"kind": kind, "label": label, "detail": detail})
        return key

    def add_access(u, iid, path, keys, kinds):
        rec = access[u].setdefault(iid, {"path": path, "keys": [], "kinds": []})
        rec["keys"] = sorted(set(rec["keys"]) | set(keys))
        rec["kinds"] = sorted(set(rec["kinds"]) | set(kinds))

    # 2. files
    for u in users:
        pw = acc[u]
        for s in nc.shares_created(u, pw):
            shares[str(s["id"])] = s
        received = {}
        for s in nc.shares_received(u, pw):
            shares.setdefault(str(s["id"]), s)
            received[(s.get("file_target") or "").strip("/")] = s
        for a in nc.activity(u, pw):
            if a.get("type") in ("file_created", "file_changed") and a.get("user") == u and a.get("object_id"):
                authors_act[f"file:{a['object_id']}"].add(u)
        entries = nc.walk(u, pw)
        for e in entries:
            if e["fileid"]:
                paths[u][e["path"]] = f"file:{e['fileid']}"
        for e in entries:
            if e["is_dir"] or not e["fileid"]:
                continue
            iid, top, title = f"file:{e['fileid']}", e["path"].strip("/").split("/")[0], _name(e["path"])
            keys, kinds = [], []
            if e["mount"] == "group":
                gs = sorted(set(users[u]["groups"]) & set(team.get(top, {}))) or [g for g in users[u]["groups"] if g.lower() == top.lower()]
                for g in gs:
                    kinds.append("team_folder")
                    keys.append(grant(f"teamfolder:{top}:{g}", "teamfolder", f"Team folder /{top} is open to group {g}", folder=top, group=g))
                    if g not in broad:
                        keys.append(grant(f"group:{g}:{u}", "group", f"{users[u]['name']} ({u}) is a member of group {g}", group=g, user=u))
                keys.append(grant(f"location:{iid}", "location", f"File '{title}' sits in team folder /{top}", item=iid, folder=top))
            elif e["mount"] == "shared":
                s = received.get(top)
                if s is None:
                    kinds.append("share")
                    keys.append(grant(f"share:?{u}:{top}", "share", f"Share '{top}' for {u} (details unreadable)"))
                else:
                    st, to = int(s.get("share_type", -1)), s.get("share_with") or ""
                    kinds.append("share_group" if st == 1 else "share_user")
                    target = f"group {to}" if st == 1 else (s.get("share_with_displayname") or to)
                    keys.append(grant(f"share:{s['id']}", "share", f"{s.get('uid_owner')} shared '{(s.get('path') or '').strip('/')}' with {target}",
                                      id=str(s["id"]), owner=s.get("uid_owner"), path=s.get("path"), share_type=st, to=to))
                    if st == 1 and to not in broad:
                        keys.append(grant(f"group:{to}:{u}", "group", f"{users[u]['name']} ({u}) is a member of group {to}", group=to, user=u))
            else:
                kinds.append("own_folder" if e["owner"] == u else "other")
            add_access(u, iid, e["path"], keys, kinds)
            it = items.setdefault(iid, {"kind": "file", "title": title, "path": None, "team_folder": None, "home_owner": None,
                                        "date": _date(e["modified"]), "size": e["size"], "_seen": (u, e["path"])})
            if e["mount"] in ("", None) and e["owner"] == u:
                it["path"], it["home_owner"] = e["path"], u
            elif e["mount"] == "group" and not it["home_owner"]:
                it["path"], it["team_folder"] = e["path"], top
        log(f"  {u:<8} files: {len(access[u])}")

    for sid, s in shares.items():                                  # public links: open to everybody
        if int(s.get("share_type", -1)) != 3:
            continue
        own, p = s.get("uid_owner"), (s.get("path") or "").rstrip("/")
        cover = sorted({iid for path, iid in paths.get(own, {}).items() if path == p or path.startswith(p + "/")} & set(items))
        grant(f"link:{sid}", "link", f"{own} created a public link to '{p.strip('/')}'", id=sid, owner=own, path=p,
              items=cover, url=s.get("url"), expires=s.get("expiration"))

    for iid, it in items.items():
        u, p = it.pop("_seen")
        it["path"] = it["path"] or p
        try:
            it["text"] = file_text(it["title"], nc.download(u, acc[u], p))
        except NextcloudError as e:
            it["text"] = ""
            log(f"  ! download failed {p}: {e}")
        au = authors_act.get(iid) or ({it["home_owner"]} if it["home_owner"] else set())
        it["authors"] = sorted(au)
        for a in au:
            last_seen[a] = max(last_seen[a], it["date"])

    # 3. mail
    n_mail = 0
    if K.IMAP_HOST:
        mb = Mailbox(K.IMAP_HOST, K.IMAP_PORT, K.IMAP_CAFILE)
        lists, forwards = parse_aliases(K.MAIL_ALIASES)
        all_users = set(users)
        broad_lists = {l for l, m in lists.items() if len(set(m) & all_users) >= K.BROAD_GROUP_SHARE * len(users)}
        local = lambda a: a.split("@")[0] if a.endswith("@" + K.MAIL_DOMAIN) else None  # noqa: E731
        for u in users:
            try:
                msgs = mb.fetch(f"{u}@{K.MAIL_DOMAIN}", acc[u])
            except MailError as e:
                log(f"  ! {e}")
                continue
            for m in msgs:
                mid = m["message_id"] or f"<no-id-{u}-{m['subject']}>"
                iid = f"mail:{mid.strip('<>')}"
                sender = local(m["from"][0]) if m["from"] else None
                rcpt = [x for x in (local(a) for a in m["to"] + m["cc"]) if x]
                it = items.setdefault(iid, {"kind": "email", "title": m["subject"], "path": "Mail", "date": m["date"][:10],
                                            "message_id": mid, "sender": sender, "recipients": rcpt,
                                            "text": f"Subject: {m['subject']}\n\n{m['body']}", "authors": [sender] if sender else []})
                if sender:
                    last_seen[sender] = max(last_seen[sender], it["date"])
                if m["folder"] == "Sent" or sender == u:
                    add_access(u, iid, "Mail/Sent", [], ["sender"])
                    continue
                key = grant(f"send:{mid}", "email", f"Email '{m['subject']}' from {sender} ({it['date']})",
                            message_id=mid, sender=sender, subject=m["subject"], item=iid)
                if u in rcpt:
                    add_access(u, iid, "Mail/INBOX", [key], ["mail_direct"])
                    continue
                via = [l for l in rcpt if l in lists and u in lists[l]]
                if via:
                    keys = [key] + [grant(f"list:{l}:{u}", "list", f"{users[u]['name']} ({u}) receives the {l}@ list", list=l, user=u)
                                    for l in via if l not in broad_lists]
                    add_access(u, iid, "Mail/INBOX", keys, ["mail_list"])
                    continue
                fw = [a for a, tos in forwards.items() if u in tos and (a in rcpt or any(a in lists.get(l, []) for l in rcpt))]
                if fw:
                    keys = [key] + [grant(f"forward:{a}:{u}", "forward", f"Forwarding rule: mail for {a}@ is copied to {u}", src=a, user=u) for a in fw]
                    add_access(u, iid, "Mail/INBOX", keys, ["mail_forward"])
                else:
                    add_access(u, iid, "Mail/INBOX", [key], ["mail_hidden"])
            n_mail += len(msgs)
            log(f"  {u:<8} mail copies: {len(msgs)}")
        for g in grants.values():                                   # who received each email
            if g["kind"] == "email":
                g["detail"]["recipients"] = sorted(u for u in users if g["detail"]["item"] in access[u]
                                                   and "sender" not in access[u][g["detail"]["item"]]["kinds"])

    history = {u: sorted(i for i, it in items.items() if u in it.get("authors", [])) for u in users}
    idx = {"time": dt.datetime.now().isoformat(timespec="seconds"), "users": users, "broad_groups": broad,
           "team_folders": team, "items": items, "access": access, "grants": grants, "history": history,
           "last_seen": dict(last_seen)}
    INDEX.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"Done: {sum(1 for i in items.values() if i['kind'] == 'file')} files, "
        f"{sum(1 for i in items.values() if i['kind'] == 'email')} emails ({n_mail} mailbox copies), "
        f"{sum(len(a) for a in access.values())} accesses, {len(grants)} permissions -> {INDEX}")
    return idx


def load_index():
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else None


if __name__ == "__main__":
    sync()
