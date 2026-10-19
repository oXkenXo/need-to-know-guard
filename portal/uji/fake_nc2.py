"""Nextcloud TIRUAN untuk menguji portal tanpa Docker.
Meniru: WebDAV (PROPFIND Depth 1, PUT, GET, MKCOL, MOVE), OCS user/grup, share (user/grup/link),
Team folders (groupfolders), Activity, admin notifications, dan endpoint /v1/chat/completions (tiruan Ollama).
Kondisi awal = hasil seed/seed_server.sh (grup, akun, Team folders)."""
import base64
import datetime as dt
import itertools
import json
import threading
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from xml.sax.saxutils import escape

PW = {"budi": "Uji-Budi-2026!", "sari": "Uji-Sari-2026!", "maya": "Uji-Maya-2026!", "andi": "Uji-Andi-2026!",
      "dodi": "Uji-Dodi-2026!", "rina": "Uji-Rina-2026!", "eko": "Uji-Eko-2026!!", "admin": "Admin-Uji-2026!"}
NAMA = {"budi": "Budi Santoso", "sari": "Sari Wulandari", "maya": "Maya Putri", "andi": "Andi Pratama",
        "dodi": "Dodi Hidayat", "rina": "Rina Lestari", "eko": "Eko Saputra", "admin": "admin"}
GROUPS = {"admin": {"admin"}, "HR": {"budi", "sari"}, "Marketing": {"maya", "andi", "rina", "eko"},
          "Finance": {"andi", "dodi"}, "Semua": {"budi", "sari", "maya", "andi", "dodi", "rina", "eko"}}
GF = {"HR": {"HR": 31}, "Marketing": {"Marketing": 31}, "Finance": {"Finance": 31}, "Pengumuman": {"Semua": 1, "HR": 31}}

LOCK = threading.RLock()
_ids = itertools.count(100)
NODES = {}          # (storage, relpath) -> dict(fileid, dir, data, mtime)
SHARES = []         # dict
ACT = []            # activity
NOTIF = []
for u in PW:
    NODES[(f"home:{u}", "")] = {"fileid": str(next(_ids)), "dir": True, "data": b"", "mtime": 0}
for g in GF:
    NODES[(f"gf:{g}", "")] = {"fileid": str(next(_ids)), "dir": True, "data": b"", "mtime": 0}


def groups_of(u):
    return {g for g, m in GROUPS.items() if u in m}


def shares_for(u):
    out = []
    for s in SHARES:
        if s["share_type"] == 0 and s["share_with"] == u:
            out.append(s)
        elif s["share_type"] == 1 and u in GROUPS.get(s["share_with"], set()) and u != s["uid_owner"]:
            out.append(s)
    return out


def resolve(u, path):
    """path di tampilan user -> (storage, rel, mount, share)"""
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts:
        return f"home:{u}", "", "", None
    top, rest = parts[0], "/".join(parts[1:])
    if top in GF and groups_of(u) & set(GF[top]):
        return f"gf:{top}", rest, "group", None
    for s in shares_for(u):
        if s["file_target"].strip("/") == top:
            rel = s["_rel"] + ("/" + rest if rest else "")
            return s["_storage"], rel.strip("/"), "shared", s
    return f"home:{u}", "/".join(parts), "", None


def children(storage, rel):
    pre = rel + "/" if rel else ""
    return sorted({k[1] for k in NODES if k[0] == storage and k[1].startswith(pre) and k[1] != rel
                   and "/" not in k[1][len(pre):]})


def view_children(u, path):
    st, rel, mount, sh = resolve(u, path)
    out = []
    for c in children(st, rel):
        out.append(("/" + (path.strip("/") + "/" if path.strip("/") else "") + c.rsplit("/", 1)[-1], st, c, mount, sh))
    if not path.strip("/"):
        for g in GF:
            if groups_of(u) & set(GF[g]):
                out.append(("/" + g, f"gf:{g}", "", "group", None))
        for s in shares_for(u):
            out.append((s["file_target"], s["_storage"], s["_rel"], "shared", s))
    return out


def owner_of(u, storage, mount, sh):
    if mount == "shared":
        return sh["uid_owner"]
    if storage.startswith("home:"):
        return storage[5:]
    return u          # Team folder: Nextcloud tidak punya pemilik tetap -> tiruan memakai user yang mengakses


def prop_xml(u, vpath, storage, rel, mount, sh):
    n = NODES[(storage, rel)]
    stypes = sorted({s["share_type"] for s in SHARES if s["_storage"] == storage and s["_rel"] == rel
                     and s["uid_owner"] == u})
    href = f"/remote.php/dav/files/{quote(u)}/{quote(vpath.strip('/'))}" + ("/" if n["dir"] else "")
    rt = "<d:collection/>" if n["dir"] else ""
    st = "".join(f"<oc:share-type>{t}</oc:share-type>" for t in stypes)
    return (f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>"
            f"<d:getlastmodified>{formatdate(n['mtime'] or 1760000000, usegmt=True)}</d:getlastmodified>"
            + ("" if n["dir"] else f"<d:getcontentlength>{len(n['data'])}</d:getcontentlength><d:getcontenttype>text/plain</d:getcontenttype>")
            + f"<d:resourcetype>{rt}</d:resourcetype><oc:fileid>{n['fileid']}</oc:fileid>"
            f"<oc:owner-id>{owner_of(u, storage, mount, sh)}</oc:owner-id><oc:permissions>RGDNVW</oc:permissions>"
            f"<oc:share-types>{st}</oc:share-types><nc:mount-type>{mount}</nc:mount-type>"
            f"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def auth(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return None
        u, _, p = base64.b64decode(h[6:]).decode().partition(":")
        return u if PW.get(u) == p else None

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def send(self, code, data=b"", ctype="application/json"):
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def ocs(self, data=None, code=200, msg="OK"):
        self.send(code, {"ocs": {"meta": {"status": "ok" if code == 200 else "failure", "statuscode": code, "message": msg},
                                 "data": data if data is not None else []}})

    def davpath(self):
        p = unquote(urlparse(self.path).path)
        user, _, rest = p.split("/remote.php/dav/files/", 1)[1].partition("/")
        return user, rest.rstrip("/")

    # ------------------------------------------------------------------ WebDAV
    def dav(self, method):
        u = self.auth()
        if not u:
            return self.send(401)
        user, path = self.davpath()
        if user != u:
            return self.send(403)
        with LOCK:
            st, rel, mount, sh = resolve(u, path)
            if method == "PROPFIND":
                if (st, rel) not in NODES:
                    return self.send(404)
                self.body()
                parts = [prop_xml(u, "/" + path, st, rel, mount, sh)]
                if NODES[(st, rel)]["dir"]:
                    parts += [prop_xml(u, vp, s2, r2, m2, sh2) for vp, s2, r2, m2, sh2 in view_children(u, path)]
                xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
                       'xmlns:nc="http://nextcloud.org/ns">' + "".join(parts) + "</d:multistatus>")
                return self.send(207, xml, "application/xml; charset=utf-8")
            if method == "MKCOL":
                if (st, rel) in NODES:
                    return self.send(405)
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                if (st, parent) not in NODES:
                    return self.send(409)
                NODES[(st, rel)] = {"fileid": str(next(_ids)), "dir": True, "data": b"", "mtime": 1760000000}
                return self.send(201)
            if method == "PUT":
                data = self.body()
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                if (st, parent) not in NODES:
                    return self.send(409)
                new = (st, rel) not in NODES
                n = NODES.setdefault((st, rel), {"fileid": str(next(_ids)), "dir": False})
                n.update(data=data, mtime=1760000000)
                ACT.append({"activity_id": len(ACT) + 1, "app": "files", "type": "file_created" if new else "file_changed",
                            "user": u, "affecteduser": u, "object_type": "files", "object_id": int(n["fileid"]),
                            "object_name": "/" + path, "datetime": dt.datetime(2026, 10, 1, 9, len(ACT) % 60).isoformat() + "+00:00"})
                return self.send(201)
            if method == "GET":
                n = NODES.get((st, rel))
                if not n or n["dir"]:
                    return self.send(404)
                return self.send(200, n["data"], "text/plain")
            if method == "MOVE":
                dst = unquote(urlparse(self.headers["Destination"]).path).split(f"/remote.php/dav/files/{u}/", 1)[1]
                st2, rel2, _, _ = resolve(u, dst)
                if (st, rel) not in NODES:
                    return self.send(404)
                for k in [k for k in NODES if k[0] == st and (k[1] == rel or k[1].startswith(rel + "/"))]:
                    NODES[(st2, rel2 + k[1][len(rel):])] = NODES.pop(k)
                return self.send(201)
        return self.send(405)

    def do_PROPFIND(self): self.dav("PROPFIND")
    def do_MKCOL(self): self.dav("MKCOL")
    def do_PUT(self): self.dav("PUT")
    def do_MOVE(self): self.dav("MOVE")

    def do_GET(self):
        if "/remote.php/dav/files/" in self.path:
            return self.dav("GET")
        return self.api("GET")

    def do_POST(self):
        return self.api("POST")

    def do_DELETE(self):
        return self.api("DELETE")

    # ------------------------------------------------------------------ OCS / API
    def api(self, method):
        url = urlparse(self.path)
        p, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        if p == "/v1/chat/completions" and method == "POST":
            req = json.loads(self.body() or b"{}")
            last = req["messages"][-1]["content"]
            return self.send(200, {"choices": [{"message": {"content": f"[jawaban tiruan LLM] konteks {len(last)} karakter"}}]})
        u = self.auth()
        if not u:
            return self.send(401, {"ocs": {"meta": {"statuscode": 401}}})
        form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()} if method in ("POST", "DELETE") else {}
        admin = u in GROUPS["admin"]
        with LOCK:
            if p == "/ocs/v2.php/cloud/user":
                return self.ocs({"id": u, "displayname": NAMA[u]})
            if p.startswith("/ocs/v2.php/cloud/users/") and p.endswith("/groups"):
                target = unquote(p.split("/")[-2])
                if method == "GET":
                    if not (admin or target == u):
                        return self.ocs(None, 403, "forbidden")
                    return self.ocs({"groups": sorted(groups_of(target))})
                if method == "DELETE":
                    if not admin:
                        return self.ocs(None, 403, "forbidden")
                    GROUPS[form["groupid"]].discard(target)
                    return self.ocs()
            if p.startswith("/ocs/v2.php/cloud/groups/") and p.endswith("/users"):
                return self.ocs({"users": sorted(GROUPS.get(unquote(p.split("/")[-2]), []))}) if admin else self.ocs(None, 403)
            if p in ("/index.php/apps/groupfolders/folders", "/apps/groupfolders/folders"):
                if not admin:
                    return self.ocs(None, 403)
                return self.ocs({str(i + 1): {"id": i + 1, "mount_point": g, "groups": GF[g], "quota": -1, "size": 0}
                                 for i, g in enumerate(GF)})
            if p.startswith("/ocs/v2.php/apps/files_sharing/api/v1/shares"):
                return self.share_api(u, method, p, q, form)
            if p == "/ocs/v2.php/apps/activity/api/v2/activity/all":
                return self.ocs([a for a in ACT if a["affecteduser"] == u][::-1])
            if p.startswith("/ocs/v2.php/apps/notifications/api/v2/admin_notifications/"):
                if not admin:
                    return self.ocs(None, 403)
                NOTIF.append({"ke": p.rsplit("/", 1)[-1], **form})
                return self.ocs()
        return self.ocs(None, 404, "not found")

    def share_api(self, u, method, p, q, form):
        if method == "POST":
            st, rel, mount, sh = resolve(u, form["path"])
            if (st, rel) not in NODES:
                return self.ocs(None, 404, "Wrong path")
            sid = len(SHARES) + 1
            t = int(form["shareType"])
            name = form["path"].rstrip("/").rsplit("/", 1)[-1]
            s = {"id": sid, "share_type": t, "uid_owner": u, "displayname_owner": NAMA[u], "path": "/" + form["path"].strip("/"),
                 "item_type": "folder" if NODES[(st, rel)]["dir"] else "file", "file_source": int(NODES[(st, rel)]["fileid"]),
                 "share_with": form.get("shareWith"), "share_with_displayname": NAMA.get(form.get("shareWith"), form.get("shareWith")),
                 "permissions": int(form.get("permissions", 1)), "file_target": "/" + name, "expiration": None,
                 "token": f"tok{sid}" if t == 3 else None, "url": f"http://localhost:8080/s/tok{sid}" if t == 3 else None,
                 "_storage": st, "_rel": rel}
            SHARES.append(s)
            return self.ocs(self.pub(s))
        if method == "GET":
            if q.get("shared_with_me") == "true":
                return self.ocs([self.pub(s) for s in shares_for(u)])
            return self.ocs([self.pub(s) for s in SHARES if s["uid_owner"] == u])
        if method == "DELETE":
            sid = int(p.rsplit("/", 1)[-1])
            s = next((s for s in SHARES if s["id"] == sid), None)
            if not s:
                return self.ocs(None, 404, "Wrong share ID")
            if s["uid_owner"] != u:
                return self.ocs(None, 403, "not allowed")
            SHARES.remove(s)
            return self.ocs()

    @staticmethod
    def pub(s):
        return {k: v for k, v in s.items() if not k.startswith("_")}


def start(port=8080):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import sys
    import time
    start(int(sys.argv[1]) if len(sys.argv) > 1 else 8080)
    print("Nextcloud tiruan berjalan", flush=True)
    while True:
        time.sleep(3600)
