"""Send the lab emails through the mail server, each one logged in as its sender (SMTP submission).

    python scripts/send_emails.py      (SMTP_HOST=localhost, SMTP_PORT=587; TLS checked against certs/ca.crt)

Mailing lists are expanded with the directory membership of the day the email was written, so a person
who joined later does not receive older mail. Server-side forwarding rules apply as on a real server.
Like a mail client, a copy of every email is saved in the sender's "Sent" folder (IMAP APPEND).
Already-sent emails are recorded in build/sent_emails.txt and skipped on the next run.
"""
import datetime as dt
import imaplib
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import format_datetime, formataddr

import common as c

HOST = os.environ.get("SMTP_HOST", "localhost")
PORT = int(os.environ.get("SMTP_PORT", "587"))
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
CA = os.environ.get("SMTP_CAFILE", str(c.LAB / "certs" / "ca.crt"))
TZ = dt.timezone(dt.timedelta(hours=7))
LIST_NAMES = {"all-staff": "All Staff", "exec": "Executive Team", "managers": "Managers", "hr": "HR",
              "finance": "Finance", "marketing": "Marketing", "it": "IT", "ops": "Operations",
              "ap": "Accounts Payable", "support": "IT Support"}


def header_addr(token):
    token = str(token)
    if token.startswith("list:"):
        return formataddr((LIST_NAMES.get(token[5:], token[5:]), c.addr(token)))
    return formataddr((c.PEOPLE[token]["name"], c.addr(token)))


def build(m, by_id):
    msg = EmailMessage()
    msg["From"] = header_addr(m["from"])
    msg["To"] = ", ".join(header_addr(t) for t in m.get("to") or [])
    if m.get("cc"):
        msg["Cc"] = ", ".join(header_addr(t) for t in m["cc"])
    msg["Subject"] = m["subject"]
    when = dt.datetime.strptime(str(m["date"]), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    msg["Date"] = format_datetime(when)
    msg["Message-ID"] = c.message_id(m)
    chain, p = [], m.get("reply_to")
    while p:
        chain.insert(0, c.message_id(by_id[p]))
        p = by_id[p].get("reply_to")
    if chain:
        msg["In-Reply-To"] = chain[-1]
        msg["References"] = " ".join(chain)
    msg.set_content(m["body"])
    return msg


def save_sent(m, msg, pw, ctx):
    """Store the sender's copy in 'Sent', as webmail and desktop clients do."""
    with imaplib.IMAP4_SSL(HOST, IMAP_PORT, ssl_context=ctx) as im:
        im.login(c.addr(m["from"]), pw[m["from"]])
        if im.select("Sent")[0] != "OK":
            im.create("Sent")
            im.subscribe("Sent")
        when = dt.datetime.strptime(str(m["date"]), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        im.append("Sent", "(\\Seen)", imaplib.Time2Internaldate(when), msg.as_bytes())


def main():
    pw = c.read_accounts()
    mails = c.load_emails()
    by_id = {m["id"]: m for m in mails}
    log = c.LAB / "build" / "sent_emails.txt"
    log.parent.mkdir(exist_ok=True)
    done = set(log.read_text().split()) if log.exists() else set()
    ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
    sent = 0
    for m in mails:
        if m["id"] in done:
            continue
        rcpt = sorted(c.addr(u) for u in c.envelope_recipients(m))
        msg = build(m, by_id)
        with smtplib.SMTP(HOST, PORT, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(c.addr(m["from"]), pw[m["from"]])
            s.send_message(msg, from_addr=c.addr(m["from"]), to_addrs=rcpt)
        save_sent(m, msg, pw, ctx)
        with open(log, "a") as f:
            f.write(m["id"] + "\n")
        sent += 1
        print(f"sent {m['id']} {m['date']}  {m['from']:<8} -> {len(rcpt):>2} mailbox(es)  {m['subject'][:50]}")
        time.sleep(0.2)
    print(f"DONE: {sent} sent now, {len(done)} sent before, {len(mails)} total")


if __name__ == "__main__":
    main()
