"""Penghubung AI <-> admin: log temuan, audit izin, rekomendasi, aksi Cabut/Abaikan, dan permintaan akses.

Prinsip: AI hanya MENCATAT dan MENYARANKAN. Perubahan izin di Nextcloud hanya terjadi kalau admin
menekan tombol (dan mencentang konfirmasi). Semua keputusan tercatat di data/audit.db.
"""
import datetime as dt
import json
import sqlite3
from collections import Counter, defaultdict

import pandas as pd

import konfigurasi as K
from nextcloud import Nextcloud, NextcloudError

DB = K.DATA_DIR / "audit.db"
SKEMA = """
CREATE TABLE IF NOT EXISTS log_chat (id INTEGER PRIMARY KEY, waktu TEXT, user TEXT, fileid TEXT, judul TEXT,
    zona TEXT, skor REAL, kunci TEXT);
CREATE TABLE IF NOT EXISTS keputusan (kunci TEXT PRIMARY KEY, aksi TEXT, oleh TEXT, waktu TEXT, hasil TEXT);
CREATE TABLE IF NOT EXISTS permintaan (id INTEGER PRIMARY KEY, waktu TEXT, pemohon TEXT, fileid TEXT, judul TEXT,
    pemilik TEXT, alasan TEXT, status TEXT, diputus_oleh TEXT, waktu_putus TEXT);
"""


def wilson_bawah(bad, n, z=1.96):
    """Batas bawah selang 95% dari persen akses yang ditahan. Izin dengan bukti sedikit tidak langsung naik ke atas."""
    if n == 0:
        return 0.0
    p, d = bad / n, 1 + z * z / n
    return max(0.0, (p + z * z / (2 * n) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d)


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def db():
    con = sqlite3.connect(DB)
    con.executescript(SKEMA)
    return con


def nc():
    return Nextcloud(K.NC_URL, K.NC_ADMIN_USER, K.NC_ADMIN_PASS)


# ---------------------------------------------------------------------- log dari chatbot (audit pasif)
def catat_chat(idx, user, hasil_nilai):
    """Setiap dokumen yang dinilai saat chatbot menjawab dicatat (lolos / tanya / tahan) + izin penyebab aksesnya."""
    rows = []
    for r in hasil_nilai:
        if r["skor"] is None:          # bukan penilaian guard (penulis, sudah disetujui, karyawan baru) -> bukan bukti
            continue
        a = idx["akses"].get(user, {}).get(r["fid"], {})
        rows.append((_now(), user, r["fid"], idx["files"][r["fid"]]["judul"], r["zona"], r["skor"],
                     json.dumps(a.get("kunci", []))))
    with db() as con:
        con.executemany("INSERT INTO log_chat (waktu,user,fileid,judul,zona,skor,kunci) VALUES (?,?,?,?,?,?,?)", rows)


def ringkas_log_chat(idx):
    with db() as con:
        df = pd.read_sql("SELECT * FROM log_chat", con)
    if df.empty:
        return pd.DataFrame(columns=["izin", "akses_diminta", "ditahan", "tanya", "karyawan", "skor_curiga"])
    st = defaultdict(Counter)
    who = defaultdict(set)
    for _, r in df.iterrows():
        if r["user"] in idx["files"].get(r["fileid"], {}).get("penulis", []):
            continue
        for k in json.loads(r["kunci"]):
            st[k][r["zona"]] += 1
            who[k].add(r["user"])
    rows = [{"kunci": k, "izin": idx["grants"].get(k, {}).get("label", k), "akses_diminta": sum(c.values()),
             "ditahan": c["tahan"], "tanya": c["tanya"], "karyawan": len(who[k]),
             "skor_curiga": round(wilson_bawah(c["tahan"] + 0.5 * c["tanya"], sum(c.values())), 3)}
            for k, c in st.items() if idx["grants"].get(k, {}).get("jenis") != "teamfolder"]
    return pd.DataFrame(rows).sort_values(["skor_curiga", "karyawan"], ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------- audit proaktif
def rekomendasi(idx, kunci):
    g = idx["grants"].get(kunci, {})
    d, j = g.get("detail", {}), g.get("jenis")
    if j == "share":
        return (f"Hapus share '{(d.get('path') or '').strip('/')}' ke grup {d.get('ke')}; bagikan hanya ke yang perlu"
                if d.get("share_type") == 1 else
                f"Cabut share '{(d.get('path') or '').strip('/')}' ke {d.get('ke')}, atau beri tanggal kedaluwarsa")
    if j == "link":
        return f"Hapus link publik '{(d.get('path') or '').strip('/')}', atau beri password + kedaluwarsa"
    if j == "grup":
        return f"Keluarkan {d.get('user')} dari grup {d.get('grup')} (cek apakah masih bertugas di sana)"
    if j == "lokasi":
        return f"Pindahkan '{idx['files'].get(d.get('fileid'), {}).get('judul')}' dari /{d.get('folder')} ke folder yang lebih tertutup"
    if j == "teamfolder":
        return f"Tinjau izin grup {d.get('grup')} di Team folder /{d.get('folder')} (php occ groupfolders:group)"
    return "Periksa manual"


def audit_proaktif(idx, penilai):
    """Nilai SEMUA akses yang dibuka tiap izin (bukan hanya yang ditanyakan ke chatbot), lalu urutkan izin."""
    st, who, contoh = defaultdict(Counter), defaultdict(set), defaultdict(list)
    tidak_dinilai = Counter()

    def tambah(kunci, u, r):
        st[kunci][r["zona"]] += 1
        who[kunci].add(u)
        if r["zona"] != "lolos":
            contoh[kunci].append(f"{u}: {idx['files'][r['fid']]['judul']}")

    for u, akses in idx["akses"].items():
        fids = [f for f in akses if u not in idx["files"][f]["penulis"]]
        if penilai.karyawan_baru(u):                      # tanpa histori -> tidak bisa dinilai, jangan menyalahkan izinnya
            for f in fids:
                for k in akses[f]["kunci"]:
                    tidak_dinilai[k] += 1
            continue
        for r in penilai.nilai(u, fids):
            if r["skor"] is None:          # disetujui pemilik / izin sah -> bukan penilaian guard
                continue
            for k in akses[r["fid"]]["kunci"]:
                tambah(k, u, r)
    for k, g in idx["grants"].items():                    # link publik: terbuka untuk semua karyawan
        if g["jenis"] != "link":
            continue
        for u in idx["users"]:
            if penilai.karyawan_baru(u):
                continue
            fids = [f for f in g["detail"].get("files", []) if u not in idx["files"][f]["penulis"]]
            for r in penilai.nilai(u, fids):
                tambah(k, u, r)
    kep = keputusan()
    rows = []
    for k, c in st.items():
        n, jenis = sum(c.values()), idx["grants"].get(k, {}).get("jenis", "?")
        if jenis == "teamfolder":          # struktur Team folder sudah terwakili oleh izin per file (lokasi) & per anggota (grup)
            continue
        rows.append({"kunci": k, "jenis": jenis,
                     "izin": idx["grants"].get(k, {}).get("label", k), "akses_dinilai": n,
                     "ditahan": c["tahan"], "tanya": c["tanya"], "lolos": c["lolos"], "karyawan": len(who[k]),
                     "persen_ditahan": round((c["tahan"] + 0.5 * c["tanya"]) / max(n, 1), 3),
                     "skor_curiga": round(wilson_bawah(c["tahan"] + 0.5 * c["tanya"], n), 3),
                     "tidak_dinilai": tidak_dinilai.get(k, 0),
                     "contoh": "; ".join(contoh[k][:3]), "rekomendasi": rekomendasi(idx, k),
                     "status": kep.get(k, {}).get("aksi", "")})
    ada = {r["kunci"] for r in rows}
    for k, v in kep.items():                  # izin yang sudah diputuskan admin tetap tampil (tanpa bukti baru)
        if k not in ada and k in idx["grants"]:
            rows.append({"kunci": k, "jenis": idx["grants"][k]["jenis"], "izin": idx["grants"][k]["label"],
                         "akses_dinilai": 0, "ditahan": 0, "tanya": 0, "lolos": 0, "karyawan": 0, "persen_ditahan": 0.0,
                         "skor_curiga": 0.0, "tidak_dinilai": tidak_dinilai.get(k, 0), "contoh": "",
                         "rekomendasi": rekomendasi(idx, k), "status": v["aksi"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["skor_curiga", "karyawan", "akses_dinilai"], ascending=False).reset_index(drop=True)
    df.insert(0, "peringkat", range(1, len(df) + 1))
    return df


def risiko_aturan(idx, penilai):
    """Hal yang tidak butuh AI: link publik, akun tanpa histori kerja (karyawan baru ATAU mantan karyawan)."""
    rows = []
    for k, g in idx["grants"].items():
        if g["jenis"] == "link":
            d = g["detail"]
            rows.append({"jenis": "link publik", "objek": d.get("path", "").strip("/"), "kunci": k,
                         "keterangan": "bisa dibuka siapa saja yang tahu link-nya" +
                                       ("" if d.get("kedaluwarsa") else ", tanpa tanggal kedaluwarsa")})
    for u, info in idx["users"].items():
        if penilai.karyawan_baru(u):
            rows.append({"jenis": "akun tanpa histori", "objek": f"{info['nama']} ({u})", "kunci": "",
                         "keterangan": f"grup {', '.join(info['grup'])}; aktivitas terakhir "
                                       f"{idx['aktivitas_terakhir'].get(u) or 'tidak ada'}. Karyawan baru -> wajar "
                                       "(dokumen lewat persetujuan pemilik). Mantan karyawan -> nonaktifkan akun (urusan HR/IAM, "
                                       "di luar cakupan guard)."})
    return pd.DataFrame(rows, columns=["jenis", "objek", "kunci", "keterangan"])


# ---------------------------------------------------------------------- aksi admin
def keputusan():
    with db() as con:
        cur = con.execute("SELECT kunci, aksi, oleh, waktu, hasil FROM keputusan")
        return {r[0]: {"aksi": r[1], "oleh": r[2], "waktu": r[3], "hasil": r[4]} for r in cur.fetchall()}


def riwayat_keputusan():
    with db() as con:
        return pd.read_sql("SELECT waktu, oleh, aksi, kunci, hasil FROM keputusan ORDER BY waktu DESC", con)


def _catat(kunci, aksi, oleh, hasil):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO keputusan VALUES (?,?,?,?,?)", (kunci, aksi, oleh, _now(), hasil))


def abaikan(kunci, oleh):
    _catat(kunci, "abaikan", oleh, "ditandai sengaja / sah")
    return f"Izin `{kunci}` ditandai **sah**. Tidak dihapus dari Nextcloud."


def cabut(idx, kunci, oleh, tujuan=None):
    """Jalankan perbaikan di Nextcloud. Kembalikan pesan hasil (juga dicatat)."""
    g = idx["grants"].get(kunci)
    if not g:
        return "Izin tidak ditemukan (jalankan sinkron ulang)."
    d, j, akun, client = g["detail"], g["jenis"], K.akun_uji(), nc()
    try:
        if j in ("share", "link"):
            pemilik = d.get("pemilik")
            if pemilik in akun:
                client.hapus_share(pemilik, akun[pemilik], d["id"])
                hasil = f"share/link {d['id']} milik {pemilik} dihapus"
            else:
                client.notifikasi(pemilik, "Mohon cabut share", f"Admin meminta share '{d.get('path')}' dicabut.")
                hasil = f"password {pemilik} tidak ada -> {pemilik} diberi notifikasi untuk mencabut sendiri"
        elif j == "grup":
            client.keluarkan_dari_grup(d["user"], d["grup"])
            hasil = f"{d['user']} dikeluarkan dari grup {d['grup']}"
        elif j == "lokasi":
            if not tujuan:
                return "Isi dulu **folder tujuan** (mis. `/HR`) untuk memindahkan file."
            f = idx["files"][d["fileid"]]
            pelaku = next((u for u in f["penulis"] if u in akun), None)
            if not pelaku:
                return "Penulis file tidak punya password di AKUN_FILE; pindahkan manual lewat Nextcloud."
            dst = "/" + tujuan.strip("/") + "/" + f["judul"]
            client.pindah(pelaku, akun[pelaku], idx["akses"][pelaku][d["fileid"]]["path"], dst)
            hasil = f"'{f['judul']}' dipindah ke {dst} (oleh akun {pelaku})"
        elif j == "teamfolder":
            return ("Izin Team folder diubah manual oleh admin server: "
                    f"`php occ groupfolders:list` lalu `php occ groupfolders:group <id> {d.get('grup')} ...`")
        else:
            return "Jenis izin ini tidak bisa dicabut otomatis."
    except NextcloudError as e:
        _catat(kunci, "gagal", oleh, str(e))
        return f"Gagal: {e}"
    _catat(kunci, "cabut", oleh, hasil)
    if K.KIRIM_NOTIFIKASI:
        try:
            client.notifikasi(K.NC_ADMIN_USER, "Izin dicabut lewat portal", f"{kunci}: {hasil}")
        except NextcloudError:
            pass
    return f"Berhasil: {hasil}. Jalankan **Sinkron** supaya portal membaca izin terbaru."


def kirim_ringkasan_ke_admin(tabel, n=5):
    """Notifikasi lonceng Nextcloud ke admin: n izin paling mencurigakan."""
    top = tabel[(tabel["skor_curiga"] > 0) & (tabel["status"] == "")].head(n)
    if top.empty:
        return "Tidak ada izin mencurigakan yang perlu dikirim."
    isi = "\n".join(f"{r.peringkat}. {r.izin} (skor {r.skor_curiga})" for r in top.itertuples())
    nc().notifikasi(K.NC_ADMIN_USER, f"Guard: {len(top)} izin perlu ditinjau", isi)
    return f"Notifikasi terkirim ke {K.NC_ADMIN_USER}."


# ---------------------------------------------------------------------- permintaan akses (zona tanya)
def minta_akses(idx, pemohon, fid, alasan=""):
    f = idx["files"][fid]
    pemilik = f["penulis"][0] if f["penulis"] else None
    if not pemilik:
        return "Dokumen ini tidak punya penulis yang tercatat; hubungi admin."
    with db() as con:
        ada = con.execute("SELECT status FROM permintaan WHERE pemohon=? AND fileid=? AND status='menunggu'",
                          (pemohon, fid)).fetchone()
        if ada:
            return "Permintaan untuk dokumen ini masih menunggu."
        con.execute("INSERT INTO permintaan (waktu,pemohon,fileid,judul,pemilik,alasan,status) VALUES (?,?,?,?,?,?,?)",
                    (_now(), pemohon, fid, f["judul"], pemilik, alasan, "menunggu"))
    if K.KIRIM_NOTIFIKASI:
        try:
            nc().notifikasi(pemilik, f"{pemohon} minta akses ke '{f['judul']}'", alasan or "(tanpa alasan)")
        except NextcloudError:
            pass
    return f"Permintaan dikirim ke **{pemilik}** (penulis dokumen)."


def daftar_permintaan(pemilik=None, pemohon=None):
    q, p = "SELECT id, waktu, pemohon, judul, pemilik, alasan, status FROM permintaan", []
    if pemilik:
        q, p = q + " WHERE pemilik=?", [pemilik]
    elif pemohon:
        q, p = q + " WHERE pemohon=?", [pemohon]
    with db() as con:
        return pd.read_sql(q + " ORDER BY id DESC", con, params=p)


def putuskan(pid, oleh, setuju):
    with db() as con:
        r = con.execute("SELECT pemilik, status FROM permintaan WHERE id=?", (pid,)).fetchone()
        if not r:
            return "Permintaan tidak ditemukan."
        if r[0] != oleh:
            return "Hanya penulis dokumen yang bisa memutuskan."
        if r[1] != "menunggu":
            return f"Permintaan sudah {r[1]}."
        con.execute("UPDATE permintaan SET status=?, diputus_oleh=?, waktu_putus=? WHERE id=?",
                    ("disetujui" if setuju else "ditolak", oleh, _now(), pid))
    return "Disetujui: dokumen bisa dipakai chatbot untuk pemohon." if setuju else "Ditolak."


def izin_sah():
    """Izin yang ditandai 'abaikan' (sah) oleh admin -> dipakai guard sebagai pengecualian."""
    return {k for k, v in keputusan().items() if v["aksi"] == "abaikan"}


def persetujuan():
    with db() as con:
        return [tuple(r) for r in con.execute("SELECT pemohon, fileid FROM permintaan WHERE status='disetujui'")]
