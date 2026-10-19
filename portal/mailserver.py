"""IMAP access to employee mailboxes (read messages, delete a message)."""
import email
import imaplib
import ssl
from email import policy
from email.utils import getaddresses, parsedate_to_datetime


class MailError(RuntimeError):
    pass


class Mailbox:
    def __init__(self, host, port=993, cafile=None):
        self.host, self.port = host, port
        self.ctx = ssl.create_default_context(cafile=str(cafile)) if cafile else ssl.create_default_context()

    def _open(self, address, password):
        try:
            m = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=self.ctx)
            m.login(address, password)
            return m
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
            raise MailError(f"IMAP login {address}: {e}")

    def check_login(self, address, password):
        try:
            self._open(address, password).logout()
            return True
        except MailError:
            return False

    def fetch(self, address, password, folders=("INBOX", "Sent")):
        """All messages in the given folders: list of dicts (folder, message_id, subject, from, to, cc, date, body)."""
        out = []
        m = self._open(address, password)
        try:
            for folder in folders:
                if m.select(f'"{folder}"', readonly=True)[0] != "OK":
                    continue
                _, data = m.uid("SEARCH", None, "ALL")
                for uid in data[0].split():
                    _, raw = m.uid("FETCH", uid, "(BODY.PEEK[])")
                    msg = email.message_from_bytes(raw[0][1], policy=policy.default)
                    body = msg.get_body(preferencelist=("plain",))
                    try:
                        when = parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else ""
                    except (TypeError, ValueError):
                        when = ""
                    out.append({"folder": folder, "message_id": (msg["Message-ID"] or "").strip(),
                                "subject": str(msg["Subject"] or "(no subject)"),
                                "from": [a.lower() for _, a in getaddresses([str(msg["From"] or "")])],
                                "to": [a.lower() for _, a in getaddresses([str(msg["To"] or "")])],
                                "cc": [a.lower() for _, a in getaddresses([str(msg["Cc"] or "")])],
                                "date": when, "body": body.get_content() if body else ""})
        finally:
            m.logout()
        return out

    def delete(self, address, password, message_id, folder="INBOX"):
        """Delete a message (by Message-ID) from one mailbox folder. Returns how many copies were removed."""
        m = self._open(address, password)
        try:
            if m.select(f'"{folder}"')[0] != "OK":
                return 0
            _, data = m.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
            uids = data[0].split()
            for uid in uids:
                m.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            if uids:
                m.expunge()
            return len(uids)
        finally:
            m.logout()


def parse_aliases(path):
    """postfix-virtual.cf -> (lists, forwards).
    lists:    {'all-staff': ['abello', ...]}
    forwards: {'dreyes': ['nweiss']}   (an address that keeps a copy for itself and copies to others)"""
    lists, forwards = {}, {}
    if not path or not path.exists():
        return lists, forwards
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        src, dst = line.split(None, 1)
        name = src.split("@")[0].lower()
        targets = [t.strip().split("@")[0].lower() for t in dst.split(",") if t.strip()]
        if name in targets:
            forwards[name] = [t for t in targets if t != name]
        else:
            lists[name] = targets
    return lists, forwards
