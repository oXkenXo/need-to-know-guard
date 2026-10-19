"""Upload the documents to Nextcloud as their authors and create the shares from company.yaml.

    python scripts/seed_nextcloud.py           (NC_URL defaults to http://localhost:8080)

Safe to run again: existing folders/files are overwritten, existing shares are not duplicated.
"""
import datetime as dt
import os
import posixpath
from urllib.parse import quote

import requests

import common as c

BASE = os.environ.get("NC_URL", "http://localhost:8080").rstrip("/")
PW = c.read_accounts()
GROUPS = c.directory_groups()
OCS = f"{BASE}/ocs/v2.php/apps/files_sharing/api/v1/shares"
HDR = {"OCS-APIRequest": "true", "Accept": "application/json"}
PERM = {"read": 1, "write": 15}


def dav(user, path):
    return f"{BASE}/remote.php/dav/files/{quote(user)}/{quote(path.strip('/'))}"


def req(method, url, user, **kw):
    r = requests.request(method, url, auth=(user, PW[user]), timeout=60, **kw)
    return r


def writers(team):
    return [u for g, perm in c.C["team_folders"][team].items() if perm == "write" for u in GROUPS.get(g, [])]


def can_write(user, path):
    top = path.split("/")[0]
    return top not in c.TEAM_ROOTS or user in writers(top)


def check_team_folder(user, team):
    r = req("PROPFIND", dav(user, team), user, headers={"Depth": "0"})
    if r.status_code != 207:
        raise SystemExit(f"Team folder '{team}' is not visible for {user} (HTTP {r.status_code}). "
                         "Run ./setup.sh nextcloud first and check: php occ groupfolders:list")


def mkdirs(user, folder, skip_root):
    parts = [p for p in folder.split("/") if p]
    for i in range(1, len(parts) + 1):
        if i == 1 and skip_root:
            continue
        r = req("MKCOL", dav(user, "/".join(parts[:i])), user)
        if r.status_code not in (201, 405):
            raise SystemExit(f"MKCOL {'/'.join(parts[:i])} as {user}: HTTP {r.status_code}")


def upload(user, remote, local, date):
    mtime = int(dt.datetime.combine(dt.date.fromisoformat(str(date)), dt.time(9, 30)).timestamp())
    r = req("PUT", dav(user, remote), user, data=local.read_bytes(), headers={"X-OC-MTime": str(mtime)})
    if r.status_code not in (201, 204):
        raise SystemExit(f"PUT {remote} as {user}: HTTP {r.status_code}")


def existing_shares(user):
    r = req("GET", OCS, user, params={"format": "json"}, headers=HDR)
    r.raise_for_status()
    return r.json()["ocs"]["data"] or []


def share(s):
    t = {"user": 0, "group": 1, "link": 3}[s["type"]]
    have = existing_shares(s["owner"])
    for e in have:
        if (e.get("path") or "").strip("/") == s["path"] and int(e["share_type"]) == t and (t == 3 or e.get("share_with") == s.get("to")):
            return e, False
    data = {"path": "/" + s["path"], "shareType": t, "permissions": PERM[s.get("permissions", "read")]}
    if t != 3:
        data["shareWith"] = s["to"]
    if s.get("expire"):
        data["expireDate"] = str(s["expire"])
    r = req("POST", OCS, s["owner"], params={"format": "json"}, headers=HDR, data=data)
    if r.status_code >= 400:
        raise SystemExit(f"share {s['path']} by {s['owner']}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["ocs"]["data"], True


def via_share(owner, path):
    """Path in the owner's view when access comes from a user share with write permission (e.g. CFO -> Falcon)."""
    for s in c.C["shares"]:
        if s["type"] == "user" and s.get("to") == owner and s.get("permissions") == "write" and \
                (path == s["path"] or path.startswith(s["path"] + "/")):
            return posixpath.basename(s["path"]) + path[len(s["path"]):]
    return None


def main():
    docs = c.load_docs()
    for team in c.TEAM_ROOTS:
        check_team_folder(writers(team)[0], team)
    # 1. folders
    for d in docs:
        folder = posixpath.dirname(d["path"])
        if not folder:
            continue
        top = d["path"].split("/")[0]
        if top in c.TEAM_ROOTS:
            user = d["owner"] if d["owner"] in writers(top) else writers(top)[0]
            mkdirs(user, folder, skip_root=True)
        else:
            mkdirs(d["owner"], folder, skip_root=False)
    # 2. files the author can write directly
    later = []
    for d in docs:
        local = c.SEED / "files" / (d["path"] if d["path"].split("/")[0] in c.TEAM_ROOTS else f"_home/{d['owner']}/{d['path']}")
        if can_write(d["owner"], d["path"]):
            upload(d["owner"], d["path"], local, d["date"])
            print(f"upload  {d['owner']:<8} {d['path']}")
        else:
            later.append((d, local))
    # 3. shares
    links = []
    for s in c.C["shares"]:
        data, new = share(s)
        label = f"{s.get('case') or 'legit':<5} {s['owner']:<8} {s['path']} -> {s.get('to') or 'public link'}"
        print(("share   " if new else "exists  ") + label)
        if data.get("url"):
            links.append(f"{s.get('case') or 'legit'}\t{s['path']}\t{data['url']}")
    # 4. files that the author can only reach through a share
    for d, local in later:
        p = via_share(d["owner"], d["path"])
        if not p:
            raise SystemExit(f"{d['owner']} cannot write {d['path']} (no team-folder access and no write share)")
        upload(d["owner"], p, local, d["date"])
        print(f"upload  {d['owner']:<8} {d['path']}  (via share /{p})")
    (c.LAB / "build").mkdir(exist_ok=True)
    (c.LAB / "build" / "public_links.txt").write_text("\n".join(links) + "\n", encoding="utf-8")
    print(f"DONE: {len(docs)} files, {len(c.C['shares'])} shares. Public links: build/public_links.txt")


if __name__ == "__main__":
    main()
