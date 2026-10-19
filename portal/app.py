"""Employee portal: My items · Assistant (chatbot) · Access requests · Admin (ACL auditor).

Run:  python app.py   then open http://127.0.0.1:7860
Sign in with a Nextcloud account (employee or admin). The password is only used to check the login.
"""
import contextlib
import io

import gradio as gr
import pandas as pd

import audit
import chatbot
import config as K
import evaluation
from connector import load_index, sync
from guard import Guard
from nextcloud import Nextcloud

NC = Nextcloud(K.NC_URL, K.NC_ADMIN_USER, K.NC_ADMIN_PASS)
S = {"idx": None, "guard": None}
ZONE = {"allow": "✅ allowed", "ask": "❓ needs author approval", "hold": "⛔ held by the guard"}
VIA = {"own_folder": "your own folder", "team_folder": "team folder (through a group)", "share_user": "shared with you",
       "share_group": "shared with your group", "share": "shared", "other": "other", "sender": "you sent it",
       "mail_direct": "sent to you", "mail_list": "sent to a mailing list you are on",
       "mail_forward": "copied to you by a forwarding rule", "mail_hidden": "not addressed to you (Bcc?)"}
AUDIT_COLS = ["rank", "scope", "rank_in_scope", "source", "permission", "accesses", "held", "ask", "employees", "held_share", "suspicion",
              "examples", "recommendation", "status"]


def system():
    if S["idx"] is None:
        S["idx"] = load_index()
    if S["idx"] is None:
        raise gr.Error("No index yet. An administrator must press 'Sync' first.")
    if S["guard"] is None:
        S["guard"] = Guard(S["idx"])
    S["guard"].approvals = set(audit.approvals())
    S["guard"].legit = audit.legit_keys()
    return S["idx"], S["guard"]


def _employee(session):
    if not session or not session.get("user"):
        raise gr.Error("Please sign in first.")
    return session["user"]


def _admin(session):
    if not session or not session.get("admin"):
        raise gr.Error("Administrators only.")
    return session["user"]


# ---------------------------------------------------------------------- sign in
def sign_in(user, pw, session):
    user = (user or "").strip()
    info = NC.check_login(user, pw or "")
    keep = (gr.update(), gr.update(visible=False), "", gr.update(), gr.update(), gr.update(), gr.update(), None, gr.update())
    if not info:
        return (session, "❌ Wrong username or password.") + keep
    try:
        is_admin = "admin" in NC.user_groups(user, auth=(user, pw))
    except Exception:
        is_admin = False
    idx = load_index()
    employee = bool(idx and user in idx["users"])
    if not employee and not is_admin:
        return (session, "This account is not in the portal index yet. Ask an administrator to run Sync.") + keep
    session = {"user": user, "name": info["displayname"], "admin": is_admin}
    groups = ", ".join(idx["users"][user]["groups"]) if employee else "administrator"
    return (session, "", gr.update(visible=False), gr.update(visible=True), f"### {info['displayname']} · `{user}` · {groups}",
            gr.update(visible=employee), gr.update(visible=employee), gr.update(visible=employee), gr.update(visible=is_admin),
            my_items(session) if employee else None, gr.update(selected="items" if employee else "admin"))


def sign_out():
    hide = gr.update(visible=False)
    return {}, gr.update(visible=True), hide, "", [], "", hide, hide, hide, hide


# ---------------------------------------------------------------------- employee
def my_items(session):
    u = _employee(session)
    idx, g = system()
    acc = idx["access"].get(u, {})
    dec = {d["id"]: d for d in g.decide(u, list(acc))}
    rows = []
    for i, a in sorted(acc.items(), key=lambda x: (idx["items"][x[0]]["kind"], x[1]["path"], idx["items"][x[0]]["title"])):
        it = idx["items"][i]
        why = [VIA.get(k, k) for k in a["kinds"]] + [idx["grants"][k]["label"] for k in a["keys"] if k.startswith("share:")]
        rows.append({"Item": it["title"], "Type": "email" if it["kind"] == "email" else "file", "Date": it.get("date", ""),
                     "Assistant may use it": ZONE[dec[i]["zone"]],
                     "Guard reason": dec[i]["reason"] + (f" (score {dec[i]['score']})" if dec[i]["score"] is not None else ""),
                     "Why you can open it": "; ".join(dict.fromkeys(why)), "Author": ", ".join(it.get("authors", [])) or "-",
                     "Location": a["path"]})
    return pd.DataFrame(rows)


def ask(message, history, session):
    u = _employee(session)
    if not (message or "").strip():
        return history, "", "", gr.update(), ""
    idx, g = system()
    r = chatbot.answer(idx, g, u, message)
    history = (history or []) + [{"role": "user", "content": message}, {"role": "assistant", "content": r["answer"]}]
    used = "\n".join(f"- ✅ **{d['title']}** ({d['kind']}) · {d['reason']}" for d in r["sources"]) or "- (none)"
    held = "\n".join([f"- ❓ **{d['title']}** ({d['kind']}) · {d['reason']}" for d in r["ask"]] +
                     [f"- ⛔ **{d['title']}** ({d['kind']}) · {d['reason']}" for d in r["held"]]) or "- (none)"
    choices = [(f"{d['title']} ({'needs approval' if d['zone'] == 'ask' else 'held'})", d["id"]) for d in r["ask"] + r["held"]]
    return (history, f"**Used to answer**\n{used}",
            f"**Found but NOT used** (you can open them on the servers, but the guard is not sure you need them)\n{held}",
            gr.update(choices=choices, value=choices[0][1] if choices else None), "")


def request(item, reason, session):
    u = _employee(session)
    if not item:
        return "Pick an item first."
    idx, _ = system()
    return audit.request_access(idx, u, item, reason or "")


def requests_view(session):
    u = _employee(session)
    mine, sent = audit.list_requests(author=u), audit.list_requests(requester=u)
    pending = mine[mine["status"] == "pending"]
    choices = [(f"#{r.id} · {r.requester} → {r.title}", int(r.id)) for r in pending.itertuples()]
    return mine, sent, gr.update(choices=choices, value=choices[0][1] if choices else None)


def decide(rid, approve, session):
    u = _employee(session)
    if not rid:
        return ("Pick a request first.",) + tuple(requests_view(session))
    return (audit.decide_request(int(rid), u, approve),) + tuple(requests_view(session))


# ---------------------------------------------------------------------- admin
def admin_sync(session):
    _admin(session)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            sync()
        except Exception as e:
            print("FAILED:", e)
    S["idx"], S["guard"] = None, None
    return buf.getvalue()


def admin_audit(session):
    _admin(session)
    idx, g = system()
    tab = audit.proactive_audit(idx, g)
    rules = audit.rule_based_risks(idx, g)
    if tab.empty:
        return tab, tab, "Nothing to score yet.", None, gr.update(choices=[]), rules
    text, cases = "", None
    if K.ANSWER_KEY and K.ANSWER_KEY.exists():
        text, cases, _ = evaluation.evaluate(idx, tab, g, rules)
        text = "**Evaluation (lab only, answer key from seed/answer_key):** " + text
    choices = [(f"{r.rank}. [{r.source}] {r.permission}", r.key) for r in tab.itertuples()]
    return tab[AUDIT_COLS], tab, text, cases, gr.update(choices=choices, value=choices[0][1]), rules


def admin_action(key, target, sure, revoke, table, session):
    who = _admin(session)
    if not key:
        return "Pick a permission first.", audit.decision_history()
    if not sure:
        return "Tick **I am sure** first. The change is applied on the real servers.", audit.decision_history()
    idx, _ = system()
    if revoke:
        flagged = ""
        if table is not None and len(table):
            row = table[table["key"] == key]
            flagged = row["flagged_users"].iloc[0] if len(row) else ""
        msg = audit.revoke(idx, key, who, target, flagged)
    else:
        msg = audit.keep(key, who)
    return msg, audit.decision_history()


def admin_log(session):
    _admin(session)
    idx, _ = system()
    return audit.chat_log_summary(idx), audit.decision_history()


def admin_notify(table, session):
    _admin(session)
    if table is None or len(table) == 0:
        return "Run the audit first."
    try:
        return audit.notify_admin(table)
    except Exception as e:
        return f"Could not send the notification: {e}"


# ---------------------------------------------------------------------- layout
with gr.Blocks(title="Meridian Labs · Document Assistant") as demo:
    session = gr.State({})
    audit_table = gr.State(None)
    gr.Markdown("# Document Assistant\nAnswers from your company files and email, with two layers: "
                "**server permissions**, then the **Need-to-Know Guard**.")
    with gr.Group(visible=True) as box_login:
        with gr.Row():
            in_user = gr.Textbox(label="Username")
            in_pw = gr.Textbox(label="Password", type="password")
        b_login = gr.Button("Sign in", variant="primary")
        msg_login = gr.Markdown()
    with gr.Column(visible=False) as box_main:
        with gr.Row():
            header = gr.Markdown()
            b_logout = gr.Button("Sign out", size="sm", scale=0)
        with gr.Tabs() as tabs:
            with gr.Tab("My items", visible=False, id="items") as t_items:
                gr.Markdown("Every file and email **you can open on the servers**, why you can open it, and whether the "
                            "assistant may use it for you.")
                b_items = gr.Button("Refresh", size="sm")
                tbl_items = gr.Dataframe(wrap=True, interactive=False)
            with gr.Tab("Assistant", visible=False, id="chat") as t_chat:
                chat = gr.Chatbot(height=380, label="Document assistant")
                with gr.Row():
                    in_q = gr.Textbox(placeholder="Ask about company documents or email…", show_label=False, scale=5)
                    b_q = gr.Button("Send", variant="primary", scale=1)
                md_used = gr.Markdown()
                md_held = gr.Markdown()
                with gr.Accordion("Request access from the author", open=False):
                    dd_req = gr.Dropdown(label="Item", choices=[])
                    in_reason = gr.Textbox(label="Reason (e.g. 'I am working on this project')")
                    b_req = gr.Button("Send request")
                    md_req = gr.Markdown()
            with gr.Tab("Access requests", visible=False, id="requests") as t_req:
                b_req_ref = gr.Button("Refresh", size="sm")
                gr.Markdown("**Requests for items you wrote**")
                tbl_mine = gr.Dataframe(pd.DataFrame(columns=["id", "time", "requester", "title", "reason", "status"]), interactive=False)
                with gr.Row():
                    dd_dec = gr.Dropdown(label="Pending request", choices=[])
                    b_yes = gr.Button("Approve", variant="primary")
                    b_no = gr.Button("Reject", variant="stop")
                md_dec = gr.Markdown()
                gr.Markdown("**Requests you sent**")
                tbl_sent = gr.Dataframe(pd.DataFrame(columns=["id", "time", "title", "author", "status"]), interactive=False)
            with gr.Tab("Admin", visible=False, id="admin") as t_admin:
                gr.Markdown("The AI only **records and recommends**. Permissions change only when an administrator presses a button.")
                with gr.Row():
                    b_sync = gr.Button("1 · Sync from the servers")
                    b_aud = gr.Button("2 · Run the audit", variant="primary")
                    b_notify = gr.Button("Send the top 5 to my Nextcloud notifications", size="sm")
                log_sync = gr.Textbox(label="Sync log", lines=6)
                md_notify = gr.Markdown()
                gr.Markdown("### Suspicious permissions (proactive audit: every access scored by the guard)\n"
                            "`suspicion` = lower bound of the 95% interval of the share of held accesses "
                            "(permissions with little evidence do not jump to the top).")
                tbl_aud = gr.Dataframe(wrap=True, interactive=False)
                md_eval = gr.Markdown()
                tbl_cases = gr.Dataframe(interactive=False)
                with gr.Row():
                    dd_key = gr.Dropdown(label="Permission", choices=[], scale=3)
                    in_target = gr.Textbox(label="Target: folder for 'move file' (e.g. /Executive) · usernames for 'purge email' (e.g. glin)", scale=2)
                with gr.Row():
                    cb_sure = gr.Checkbox(label="I am sure (the change is applied on the real servers)")
                    b_revoke = gr.Button("Revoke / fix", variant="stop")
                    b_keep = gr.Button("Keep (this permission is intended)")
                md_action = gr.Markdown()
                gr.Markdown("### Rule-based risks (no AI needed)")
                tbl_rules = gr.Dataframe(wrap=True, interactive=False)
                gr.Markdown("### From the assistant log (passive audit)")
                b_log = gr.Button("Refresh log", size="sm")
                tbl_log = gr.Dataframe(pd.DataFrame(columns=["permission", "source", "requests", "held", "ask", "employees", "suspicion"]),
                                       wrap=True, interactive=False)
                gr.Markdown("### Administrator decisions")
                tbl_dec = gr.Dataframe(pd.DataFrame(columns=["time", "by", "action", "key", "result"]), wrap=True, interactive=False)

    out_login = [session, msg_login, box_login, box_main, header, t_items, t_chat, t_req, t_admin, tbl_items, tabs]
    b_login.click(sign_in, [in_user, in_pw, session], out_login)
    in_pw.submit(sign_in, [in_user, in_pw, session], out_login)
    b_logout.click(sign_out, None, [session, box_login, box_main, header, chat, in_pw, t_items, t_chat, t_req, t_admin])
    b_items.click(my_items, session, tbl_items)
    b_q.click(ask, [in_q, chat, session], [chat, md_used, md_held, dd_req, in_q])
    in_q.submit(ask, [in_q, chat, session], [chat, md_used, md_held, dd_req, in_q])
    b_req.click(request, [dd_req, in_reason, session], md_req)
    b_req_ref.click(requests_view, session, [tbl_mine, tbl_sent, dd_dec])
    b_yes.click(lambda r, s: decide(r, True, s), [dd_dec, session], [md_dec, tbl_mine, tbl_sent, dd_dec])
    b_no.click(lambda r, s: decide(r, False, s), [dd_dec, session], [md_dec, tbl_mine, tbl_sent, dd_dec])
    b_sync.click(admin_sync, session, log_sync)
    b_aud.click(admin_audit, session, [tbl_aud, audit_table, md_eval, tbl_cases, dd_key, tbl_rules])
    b_revoke.click(lambda k, t, y, tab, s: admin_action(k, t, y, True, tab, s), [dd_key, in_target, cb_sure, audit_table, session], [md_action, tbl_dec])
    b_keep.click(lambda k, t, y, tab, s: admin_action(k, t, y, False, tab, s), [dd_key, in_target, cb_sure, audit_table, session], [md_action, tbl_dec])
    b_log.click(admin_log, session, [tbl_log, tbl_dec])
    b_notify.click(admin_notify, [audit_table, session], md_notify)

if __name__ == "__main__":
    demo.launch(server_name=K.HOST, server_port=K.PORT)
