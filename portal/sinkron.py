"""Konektor: baca Nextcloud sebagai tiap karyawan -> index (file, ACL efektif, jalur akses, histori kerja).

ACL efektif TIDAK ditebak: sebuah file masuk ACL user X kalau file itu muncul di WebDAV milik X.
Untuk setiap akses dicatat JALURNYA (izin penyebab) supaya auditor bisa menunjuk izin mana yang salah:

  pemilik            file ada di folder pribadi X
  teamfolder:<F>:<G> Team folder F dibuka untuk grup G          (struktur, diubah manual oleh admin)
  grup:<G>:<X>       X anggota grup G                          (aksi: keluarkan X dari G)
  lokasi:<fileid>    file berada di Team folder yang terbuka    (aksi: pindahkan file)
  share:<id>         share ke user / grup                       (aksi: hapus share)
  link:<id>          link publik                                (aksi: hapus link)

Grup yang berisi >= 80% karyawan (GRUP_LUAS_MIN, mis. "Semua") dianggap grup luas: keanggotaannya tidak diaudit.

Jalankan:  python sinkron.py
"""
import csv
import datetime as dt
import json
import pathlib
from collections import Counter, defaultdict

import konfigurasi as K
from nextcloud import Nextcloud, NextcloudError, ke_teks

INDEX = K.DATA_DIR / "index.json"


def _nama_file(path):
    return path.rstrip("/").rsplit("/", 1)[-1]


def manifest_pemilik():
    """Kunci jawaban storage uji: path -> {'pemilik', 'perlu_tahu', 'kasus'} (path diawali '/')."""
    if not K.MANIFEST or not pathlib.Path(K.MANIFEST).exists():
        return {}
    with open(K.MANIFEST, encoding="utf-8") as f:
        return {"/" + r["path"].strip().strip("/"): {"pemilik": r["pemilik"].strip(), "perlu_tahu": [x.strip() for x in r["perlu_tahu"].split(";") if x.strip()],
                                             "kasus": [x.strip() for x in (r.get("kasus") or "").split(";") if x.strip()]}
                for r in csv.DictReader(f)}


def sinkron(log=print):
    akun = K.akun_uji()
    if not akun:
        raise RuntimeError("AKUN_FILE belum diatur / kosong. Isi AKUN_FILE di portal.env dengan path seed/accounts.env")
    nc = Nextcloud(K.NC_URL, K.NC_ADMIN_USER, K.NC_ADMIN_PASS)

    # 1. karyawan dan grupnya
    users = {}
    for u, pw in sorted(akun.items()):
        info = nc.cek_login(u, pw)
        if not info:
            log(f"  ! login {u} gagal -> dilewati (cek password di accounts.env)")
            continue
        try:
            grup = nc.grup_user(u)
        except NextcloudError:
            grup = nc.grup_user(u, auth=(u, pw))
        users[u] = {"nama": info["displayname"], "grup": sorted(grup)}
    if not users:
        raise RuntimeError("tidak ada akun karyawan yang bisa login")
    n_anggota = Counter(g for x in users.values() for g in x["grup"])
    grup_luas = sorted(g for g, k in n_anggota.items() if k >= max(2, K.GRUP_LUAS_MIN * len(users)))
    try:
        team = nc.team_folders()
    except NextcloudError as e:
        log(f"  ! daftar Team folders tidak terbaca ({e}); jalur Team folder ditebak dari grup")
        team = {}
    log(f"Karyawan: {len(users)} · grup luas (tidak diaudit): {grup_luas} · Team folders: {sorted(team)}")

    files, akses, grants = {}, {u: {} for u in users}, {}
    pathmap = defaultdict(dict)              # user -> path -> fileid
    shares = {}
    penulis = defaultdict(dict)              # fileid -> {user: waktu}
    aktivitas_terakhir = {}

    def grant(kunci, jenis, label, **detail):
        g = grants.setdefault(kunci, {"jenis": jenis, "label": label, "detail": detail})
        return kunci

    for u in users:
        pw = akun[u]
        for s in nc.share_milik(u, pw):
            shares[str(s["id"])] = s
        diterima = {}
        for s in nc.share_untuk(u, pw):
            shares.setdefault(str(s["id"]), s)
            diterima[(s.get("file_target") or "").strip("/")] = s
        for a in nc.aktivitas(u, pw):
            if a.get("user") == u:
                aktivitas_terakhir[u] = max(aktivitas_terakhir.get(u, ""), a.get("datetime", ""))
            if a.get("type") in ("file_created", "file_changed") and a.get("user") == u and a.get("object_id"):
                penulis[str(a["object_id"])].setdefault(u, a.get("datetime", ""))
        ents = nc.jelajah(u, pw)
        for e in ents:
            if e["fileid"]:
                pathmap[u][e["path"]] = str(e["fileid"])
        for e in ents:
            if e["is_dir"] or not e["fileid"]:
                continue
            fid, top = str(e["fileid"]), e["path"].strip("/").split("/")[0]
            judul = _nama_file(e["path"])
            kunci, jenis = [], []
            if e["mount"] == "group":
                gs = sorted(set(users[u]["grup"]) & set(team.get(top, {}))) or [g for g in users[u]["grup"] if g == top]
                for g in gs:
                    jenis.append("team_folder")
                    kunci.append(grant(f"teamfolder:{top}:{g}", "teamfolder", f"Team folder /{top} dibuka untuk grup {g}",
                                       folder=top, grup=g))
                    if g not in grup_luas:
                        kunci.append(grant(f"grup:{g}:{u}", "grup", f"{users[u]['nama']} ({u}) anggota grup {g}",
                                           grup=g, user=u))
                kunci.append(grant(f"lokasi:{fid}", "lokasi", f"File '{judul}' berada di Team folder /{top}",
                                   fileid=fid, folder=top))
            elif e["mount"] == "shared":
                s = diterima.get(top)
                if s is None:
                    jenis.append("share")
                    kunci.append(grant(f"share:?{u}:{top}", "share", f"Share '{top}' untuk {u} (detail tidak terbaca)"))
                else:
                    st = int(s.get("share_type", -1))
                    ke = s.get("share_with") or ""
                    jenis.append("share_grup" if st == 1 else "share_user")
                    target = f"grup {ke}" if st == 1 else (s.get("share_with_displayname") or ke)
                    kunci.append(grant(f"share:{s['id']}", "share",
                                       f"{s.get('uid_owner')} membagikan '{s.get('path', '').strip('/')}' ke {target}",
                                       id=str(s["id"]), pemilik=s.get("uid_owner"), path=s.get("path"),
                                       share_type=st, ke=ke))
                    if st == 1 and ke not in grup_luas:
                        kunci.append(grant(f"grup:{ke}:{u}", "grup", f"{users[u]['nama']} ({u}) anggota grup {ke}",
                                           grup=ke, user=u))
            else:
                jenis.append("pemilik" if e["owner"] == u else "lain")
            rec = akses[u].setdefault(fid, {"path": e["path"], "kunci": [], "jenis": []})
            rec["kunci"] = sorted(set(rec["kunci"]) | set(kunci))
            rec["jenis"] = sorted(set(rec["jenis"]) | set(jenis))
            f = files.setdefault(fid, {"judul": judul, "path": None, "team_folder": None, "pemilik_home": None,
                                       "diubah": e["diubah"], "ukuran": e["ukuran"], "_lihat": (u, e["path"])})
            if e["mount"] in ("", None) and e["owner"] == u:
                f["path"], f["pemilik_home"] = e["path"], u
            elif e["mount"] == "group" and not f["pemilik_home"]:
                f["path"], f["team_folder"] = e["path"], top
        log(f"  {u:<6} melihat {len(akses[u])} file")

    # 2. link publik: terbuka untuk SEMUA orang (diaudit terhadap semua karyawan)
    for sid, s in shares.items():
        if int(s.get("share_type", -1)) != 3:
            continue
        own, p = s.get("uid_owner"), (s.get("path") or "").rstrip("/")
        cover = sorted({fid for path, fid in pathmap.get(own, {}).items() if path == p or path.startswith(p + "/")} & set(files))
        grant(f"link:{sid}", "link", f"{own} membuat link publik untuk '{p.strip('/')}'", id=sid, pemilik=own,
              path=p, files=cover, url=s.get("url"), kedaluwarsa=s.get("expiration"))

    # 3. teks, path kanonik, penulis (dari Activity; kalau tidak ada: pemilik folder pribadi; storage uji: manifest)
    man = manifest_pemilik()
    for fid, f in files.items():
        u, p = f.pop("_lihat")
        f["path"] = f["path"] or p
        try:
            f["teks"] = ke_teks(f["judul"], nc.unduh(u, akun[u], p))
        except NextcloudError as e:
            f["teks"] = ""
            log(f"  ! gagal unduh {p}: {e}")
        pen = set(penulis.get(fid, {}))
        sumber = "activity"
        if not pen and f["pemilik_home"]:
            pen, sumber = {f["pemilik_home"]}, "folder pribadi"
        if not pen and f["path"] in man:
            pen, sumber = {man[f["path"]]["pemilik"]}, "manifest (uji)"
        f["penulis"], f["sumber_penulis"] = sorted(pen), sumber
    histori = {u: sorted(fid for fid, f in files.items() if u in f["penulis"]) for u in users}

    idx = {"waktu": dt.datetime.now().isoformat(timespec="seconds"), "nc_url": K.NC_URL, "users": users,
           "grup_luas": grup_luas, "team_folders": team, "files": files, "akses": akses, "grants": grants,
           "histori": histori, "aktivitas_terakhir": aktivitas_terakhir}
    INDEX.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"Selesai: {len(files)} file · {sum(len(a) for a in akses.values())} akses · {len(grants)} izin -> {INDEX}")
    return idx


def muat_index():
    if not INDEX.exists():
        return None
    return json.loads(INDEX.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sinkron()
