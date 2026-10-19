"""Shared helpers: load company.yaml + content, expand groups / lists / need-to-know."""
import datetime as dt
import glob
import os
import pathlib

import yaml

LAB = pathlib.Path(__file__).resolve().parent.parent
SEED = LAB / "seed"


def load_company():
    return yaml.safe_load((LAB / "company.yaml").read_text(encoding="utf-8"))


C = load_company()
DOMAIN = C["company"]["domain"]
BASE_DN = C["company"]["base_dn"]
PEOPLE = {p["uid"]: p for p in C["people"]}
TEAM_ROOTS = set(C["team_folders"])


def _date(x):
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


def directory_groups():
    """Groups exactly as they exist in the DIRECTORY (LDAP), including planted mistakes."""
    g = {d: [] for d in C["departments"]}
    g["all-staff"] = []
    for name, members in (C.get("groups") or {}).items():
        g[name] = list(members)
    for p in C["people"]:
        own = p.get("groups", [p["dept"]])
        for x in own:
            g.setdefault(x, [])
            if p["uid"] not in g[x]:
                g[x].append(p["uid"])
        g["all-staff"].append(p["uid"])
    return {k: sorted(v) for k, v in g.items()}


def actual_groups(on):
    """Directory membership as it really was on a date (mistakes included). Used to deliver historical mail."""
    on = _date(on)
    g = {d: [] for d in C["departments"]}
    g["all-staff"] = []
    for name, members in (C.get("groups") or {}).items():
        g[name] = [m for m in members if _date(PEOPLE[m]["joined"]) <= on]
    for p in C["people"]:
        if _date(p["joined"]) > on:
            continue
        own = p.get("groups_before", [p["dept"]]) if p.get("changed") and on < _date(p["changed"]) else p.get("groups", [p["dept"]])
        for x in own:
            g.setdefault(x, []).append(p["uid"])
        g["all-staff"].append(p["uid"])        # accounts stay in all-staff until disabled (C07: never)
    return {k: sorted(set(v)) for k, v in g.items()}


def policy_groups(on=None):
    """Groups as HR POLICY says they should be on a given date (truth used for need-to-know)."""
    on = _date(on or C["company"]["today"])
    g = {d: [] for d in C["departments"]}
    g["all-staff"] = []
    for name, members in (C.get("groups") or {}).items():
        g[name] = [m for m in members if is_employed(m, on)]
    for p in C["people"]:
        uid = p["uid"]
        if p.get("changed") and on < _date(p["changed"]):
            own = p.get("groups_before", [p["dept"]])
        else:
            own = p.get("should_be", [p["dept"]])
        if not is_employed(uid, on):
            continue
        for x in own:
            g.setdefault(x, []).append(uid)
        g["all-staff"].append(uid)
    return {k: sorted(set(v)) for k, v in g.items()}


def is_employed(uid, on):
    p = PEOPLE[uid]
    if p.get("status") == "resigned" and p.get("changed") and _date(on) >= _date(p["changed"]):
        return False
    return _date(p["joined"]) <= _date(on)


def expand(tokens, on=None):
    """need-to-know tokens -> set of uids. Tokens: uid, '@group', '@all'."""
    pg = policy_groups(on)
    out = set()
    for t in tokens or []:
        t = str(t)
        if t == "@all":
            out |= set(pg["all-staff"])
        elif t.startswith("@"):
            out |= set(pg[t[1:]])
        else:
            out.add(t)
    return out


def list_members(name, groups=None):
    groups = groups or directory_groups()
    spec = C["mail_lists"][name]
    return sorted(groups[spec["group"]]) if "group" in spec else sorted(spec["members"])


def addr(token):
    token = str(token)
    if token.startswith("list:"):
        return f"{token[5:]}@{DOMAIN}"
    return f"{token}@{DOMAIN}"


def load_docs():
    docs = []
    for f in sorted(glob.glob(str(SEED / "content" / "docs_*.yaml"))):
        docs += yaml.safe_load(open(f, encoding="utf-8"))
    return docs


def load_emails():
    mails = []
    for f in sorted(glob.glob(str(SEED / "content" / "emails_*.yaml"))):
        mails += yaml.safe_load(open(f, encoding="utf-8"))
    mails.sort(key=lambda m: str(m["date"]))
    return mails


def email_need(m):
    """Default need-to-know of an email: sender + INTENDED recipients (lists expanded by HR policy on that date)."""
    if m.get("need"):
        return expand(m["need"], m["date"])
    pg = policy_groups(m["date"])
    out = {m["from"]}
    for t in (m.get("to") or []) + (m.get("cc") or []):
        t = str(t)
        if t.startswith("list:"):
            spec = C["mail_lists"][t[5:]]
            out |= set(pg[spec["group"]]) if "group" in spec else set(spec["members"])
        else:
            out.add(t)
    return out


def envelope_recipients(m):
    """SMTP RCPT TO list: lists expanded with the directory membership of that day (mistakes included)."""
    ag = actual_groups(m["date"])
    out = set()
    for t in (m.get("to") or []) + (m.get("cc") or []):
        t = str(t)
        out |= set(list_members(t[5:], ag)) if t.startswith("list:") else {t}
    return out


def email_delivery(m):
    """Mailboxes that ACTUALLY receive the email (envelope + server-side forwarding rules)."""
    out = envelope_recipients(m)
    for f in C.get("mail_forwards") or []:
        if f["from"] in out and str(m["date"])[:10] >= str(f["since"]):
            out.add(f["to"])
    return out


def message_id(m):
    return f"<{m['id']}.{str(m['date'])[:10].replace('-', '')}@{DOMAIN}>"


def read_accounts(path=None):
    path = pathlib.Path(path or os.environ.get("ACCOUNTS_FILE", SEED / "accounts.env"))
    if not path.exists():
        raise SystemExit(f"{path} not found. Run: cp seed/accounts.env.example seed/accounts.env  (and set passwords)")
    pw = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PW_") and "=" in line:
            k, v = line.split("=", 1)
            pw[k[3:]] = v
    missing = sorted(set(PEOPLE) - set(pw))
    if missing:
        raise SystemExit(f"accounts.env has no password for: {', '.join(missing)}")
    return pw
