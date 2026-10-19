"""End-to-end test of the portal against a running lab (or the fake Nextcloud + a real IMAP/LDAP server).

    PORTAL_ENV=/path/to/test.env python tests/test_flow.py

Checks: sign-in, my items, assistant + logging, access request -> approval, audit, and every admin action
(delete share, delete link, move file, purge email, edit mailing list, remove forwarding rule, LDAP group).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app  # noqa: E402
import audit  # noqa: E402
import config as K  # noqa: E402
import evaluation  # noqa: E402
from mailserver import Mailbox  # noqa: E402

PW = K.accounts()
fails = []


def check(cond, msg):
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails.append(msg)


ADMIN = app.sign_in(K.NC_ADMIN_USER, K.NC_ADMIN_PASS, {})[0]
check(ADMIN.get("admin") is True, "admin recognised from the admin group")
print(app.admin_sync(ADMIN).strip().splitlines()[-1])
s = {u: app.sign_in(u, PW[u], {})[0] for u in PW}
check(all(x.get("user") for x in s.values()), "every employee can sign in")
check(app.sign_in("skim", "wrong", {})[1].startswith("❌"), "wrong password rejected")

items = app.my_items(s["glin"])
check(len(items) > 0 and "email" in set(items["Type"]), "My items lists files and emails")
check("Payroll adjustments October - please check" in set(items["Item"]), "glin can open the misdirected payroll email (C09)")

r = app.ask("What is the opening offer for Project Falcon?", [], s["gli"])
print("  gli asks about Falcon ->", r[1][:160].replace("\n", " | "))
check("Falcon - Acquisition Memo" not in r[1], "Falcon memo not used for gli (outside the deal team)")

r = app.ask("What is on the social media calendar for November?", [], s["abello"])
choices = r[3]["choices"]
check(bool(choices), "new hire gets items in the 'ask' zone")
if choices:
    print(app.request(choices[0][1], "I am designing the November posts", s["abello"]))
    author = app.S["idx"]["items"][choices[0][1]]["authors"][0]
    mine, sent, dd = app.requests_view(s[author])
    check(len(mine) >= 1, f"{author} sees the request")
    print(app.decide(dd["value"], True, s[author])[0])
    r = app.ask("What is on the social media calendar for November?", [], s["abello"])
    check("approved by the author" in r[1], "after approval the item is used for abello")

tab, full, text, cases, dd, rules = app.admin_audit(ADMIN)
print(text.replace("**", ""))
print(cases.to_string(index=False))
keys = evaluation.case_keys(app.S["idx"])
try:
    app.admin_audit(s["skim"])
    check(False, "employees cannot run the audit")
except Exception as e:
    check("Administrators only" in str(e), "employees cannot run the audit")

msg = app.admin_action(keys["C01"][0], "", False, True, full, ADMIN)[0]
check("I am sure" in msg, "confirmation tick required")
for case, target in [("C01", ""), ("C02", ""), ("C03", "/Executive"), ("C09", "glin"), ("C10", ""), ("C04", ""), ("C11", "")]:
    for k in keys[case]:
        print(f"  {case} {k[:60]} -> {app.admin_action(k, target, True, True, full, ADMIN)[0]}")
libur = full[full["permission"].str.contains("Holiday schedule 2026-2027")]["key"].iloc[0]
print("  keep ->", app.admin_action(libur, "", True, False, full, ADMIN)[0])

# effects on the servers
mb = Mailbox(K.IMAP_HOST, K.IMAP_PORT, K.IMAP_CAFILE)
glin_ids = {m["message_id"] for m in mb.fetch(f"glin@{K.MAIL_DOMAIN}", PW["glin"], folders=("INBOX",))}
check(not any("m100." in i for i in glin_ids), "C09: payroll email purged from glin's mailbox")
c10_left = [u for u in PW if any("m020." in m["message_id"] for m in mb.fetch(f"{u}@{K.MAIL_DOMAIN}", PW[u], folders=("INBOX",)))]
print("  C10 restructuring email still in:", c10_left)
aliases = K.MAIL_ALIASES.read_text()
check("nweiss" not in [l for l in aliases.splitlines() if l.startswith("dreyes@")][0] if any(l.startswith("dreyes@") for l in aliases.splitlines()) else True,
      "C11: forwarding rule dreyes -> nweiss removed from the alias file")
check("aturner" not in [l for l in aliases.splitlines() if l.startswith("marketing@")][0], "C04: aturner removed from marketing@")
if K.LDAP_URL:
    import ldap3
    with ldap3.Connection(ldap3.Server(K.LDAP_URL), K.LDAP_ADMIN_DN, K.LDAP_ADMIN_PASSWORD, auto_bind=True) as con:
        con.search(f"cn=marketing,ou=groups,{K.LDAP_BASE}", "(objectClass=groupOfNames)", attributes=["member"])
        members = [str(m) for m in con.entries[0].member]
    check(not any("uid=aturner," in m for m in members), "C04: aturner removed from the LDAP marketing group")
print(app.admin_sync(ADMIN).strip().splitlines()[-1])
idx = app.S["idx"] or app.system()[0]
titles = {idx["items"][i]["title"] for i in idx["access"]["bcarter"]}
check("Salary Bands 2026.xlsx" not in titles, "C01: bcarter no longer sees HR/Compensation 2026")
check(not any(g["kind"] == "link" and "Ben Carter" in (g["detail"].get("path") or "") for g in idx["grants"].values()), "C02: public link deleted")
check("Restructuring Plan 2027 - DRAFT.docx" not in {idx["items"][i]["title"] for i in idx["access"]["enovak"]}, "C03: draft moved out of Announcements")
tab2 = app.admin_audit(ADMIN)[1]
check(((tab2["key"] == libur) & (tab2["status"] == "keep")).any(), "'keep' decision is remembered")
print(audit.decision_history().to_string(index=False))
print("\nALL TESTS PASSED" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
