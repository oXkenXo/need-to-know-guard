"""Check that the running lab matches the answer key: files each employee can open, and mail in each mailbox.

    python scripts/verify_lab.py     (NC_URL, IMAP_HOST, IMAP_PORT can be overridden)
"""
import csv
import imaplib
import os
import ssl
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

import requests

import common as c

NC = os.environ.get("NC_URL", "http://localhost:8080").rstrip("/")
IMAP_HOST = os.environ.get("IMAP_HOST", "localhost")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
CA = os.environ.get("IMAP_CAFILE", str(c.LAB / "certs" / "ca.crt"))
PW = c.read_accounts()
NS = {"d": "DAV:"}


def nc_files(user):
    out, todo = set(), [""]
    while todo:
        p = todo.pop()
        r = requests.request("PROPFIND", f"{NC}/remote.php/dav/files/{quote(user)}/{quote(p)}",
                             auth=(user, PW[user]), headers={"Depth": "1"}, timeout=60)
        if r.status_code != 207:
            raise SystemExit(f"PROPFIND as {user}: HTTP {r.status_code}")
        for resp in ET.fromstring(r.content).findall("d:response", NS):
            href = unquote(resp.findtext("d:href", namespaces=NS)).split(f"/remote.php/dav/files/{user}", 1)[-1].strip("/")
            if href == p.strip("/"):
                continue
            if resp.find(".//d:collection", NS) is not None:
                todo.append(href)
            else:
                out.add(href.rsplit("/", 1)[-1])
    return out


def mailbox_ids(user):
    ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx) as m:
        m.login(c.addr(user), PW[user])
        m.select("INBOX", readonly=True)
        _, data = m.search(None, "ALL")
        ids = set()
        for num in data[0].split():
            _, h = m.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            v = h[0][1].decode(errors="replace").split(":", 1)[-1].strip()
            ids.add(v)
        return ids


def main():
    files = list(csv.DictReader(open(c.SEED / "answer_key" / "files.csv", encoding="utf-8")))
    mails = list(csv.DictReader(open(c.SEED / "answer_key" / "mail.csv", encoding="utf-8")))
    print(f"{'user':<9}{'files':>7}{'mail':>7}{'mail expected':>15}   status")
    problems = 0
    for u in c.PEOPLE:
        try:
            nf = len(nc_files(u))
        except Exception as e:
            nf, problems = f"ERR {e}"[:20], problems + 1
        exp = {r["message_id"] for r in mails if u in r["delivered_to"].split(";")}
        try:
            got = mailbox_ids(u)
            missing = exp - got
            status = "ok" if not missing else f"{len(missing)} missing"
            problems += bool(missing)
        except Exception as e:
            got, status, problems = set(), f"IMAP error: {e}", problems + 1
        print(f"{u:<9}{nf:>7}{len(got):>7}{len(exp):>15}   {status}")
    print("\nAll checks passed." if not problems else f"\n{problems} problem(s) - see above.")
    print(f"Answer key: {len(files)} documents, {len(mails)} emails (seed/answer_key/).")


if __name__ == "__main__":
    main()
