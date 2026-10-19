"""Uji alur lengkap portal terhadap Nextcloud tiruan (yang sudah di-seed oleh seed_files.py asli)."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app  # noqa: E402
import audit  # noqa: E402
import konfigurasi as K  # noqa: E402

PW = K.akun_uji()
ok = lambda c, m: print(("OK   " if c else "GAGAL") + " " + m) or (c or sys.exit(1))

# login
ok(app.NC.cek_login("maya", PW["maya"]) is not None, "login maya")
ok(app.NC.cek_login("maya", "salah") is None, "password salah ditolak")
out = app.login("admin", "Admin-Uji-2026!", {})
ok(out[0].get("admin") is True, "admin terdeteksi dari grup admin")
ADMIN = out[0]
print(app.admin_sinkron(ADMIN).strip().splitlines()[-1])

sesi = {u: app.login(u, PW[u], {})[0] for u in PW}
ok(all(s.get("user") for s in sesi.values()), "semua karyawan bisa login")

# dokumen saya
d = app.dokumen_saya(sesi["maya"])
print(d[["Dokumen", "Kenapa kamu bisa membukanya", "Untuk chatbot"]].to_string(index=False))
ok("Gaji Manajer 2026.txt" in set(d["Dokumen"]), "maya bisa membuka Gaji (K1: share ke grup Semua)")

# chatbot
for u, q in [("maya", "berapa gaji manajer tahun 2026?"), ("rina", "apa strategi kampanye 2027?"),
             ("dodi", "berapa anggaran iklan Q4?"), ("budi", "kapan libur akhir tahun?")]:
    r = app.tanya(q, [], sesi[u])
    print(f"\n[{u}] {q}\n  jawab: {r[0][-1]['content'][:160]!r}\n  {r[1][:200]!r}\n  {r[2][:260]!r}")
r = app.tanya("berapa gaji manajer tahun 2026?", [], sesi["maya"])
ok("Gaji" not in r[1] or "Gaji" in r[2], "gaji tidak dipakai untuk menjawab maya")

# rina (karyawan baru) minta akses -> maya (penulis) setujui -> chatbot memakai dokumen itu
r = app.tanya("apa strategi kampanye 2027?", [], sesi["rina"])
fid = r[3]["value"]
print(app.minta(fid, "saya tim Marketing baru", sesi["rina"]))
untuk, dari, dd = app.daftar_req(sesi["maya"])
ok(len(untuk) >= 1, "maya melihat permintaan rina")
print(app.putus(dd["value"], True, sesi["maya"])[0])
r = app.tanya("apa strategi kampanye 2027?", [], sesi["rina"])
ok("disetujui pemilik" in r[1], "setelah disetujui, dokumen dipakai untuk rina")
ok("Hanya penulis" in audit.putuskan(1, "dodi", True) or "sudah" in audit.putuskan(1, "dodi", True), "bukan penulis tidak bisa memutuskan")

# admin: audit
tab, full, eval_md, kasus, dd, aturan = app.admin_audit(ADMIN)
print("\n" + tab[["peringkat", "izin", "akses_dinilai", "persen_ditahan", "skor_curiga"]].head(10).to_string(index=False))
print(eval_md)
print(kasus.to_string(index=False))
print(aturan.to_string(index=False)[:800])
log, kep = app.admin_log(ADMIN)
print("\nlog chat:\n", log.head(6).to_string(index=False))

# admin: aksi
kk = {v: k for k, v in __import__("evaluasi").kasus_izin(app.S["idx"]).items()}
print(kk)
print(app.admin_aksi(kk["K1"], "", False, True, ADMIN)[0])
for kode, tujuan in [("K1", ""), ("K4", ""), ("K3", "/HR"), ("K2", "")]:
    print(kode, app.admin_aksi(kk[kode], tujuan, True, True, ADMIN)[0])
libur = full[full["izin"].str.contains("Libur")]["kunci"].iloc[0]
print(app.admin_aksi(libur, "", True, False, ADMIN)[0])
try:
    app.admin_audit(sesi["maya"])
    ok(False, "karyawan tidak boleh memanggil fungsi admin")
except Exception as e:
    ok("Hanya admin" in str(e), "karyawan tidak boleh memanggil fungsi admin")

# sinkron ulang -> izin benar-benar berubah di Nextcloud
print(app.admin_sinkron(ADMIN).strip().splitlines()[-1])
idx = app.sistem()[0]
judul_maya = {idx["files"][f]["judul"] for f in idx["akses"]["maya"]}
ok("Gaji Manajer 2026.txt" not in judul_maya, "K1 dicabut: maya tidak lagi melihat Gaji")
ok("Marketing" not in idx["users"]["andi"]["grup"], "K4: andi keluar dari grup Marketing")
ok("Rencana PHK 2026.txt" not in {idx["files"][f]["judul"] for f in idx["akses"]["dodi"]}, "K3: PHK dipindah ke /HR")
ok(not any(g["jenis"] == "link" for g in idx["grants"].values()), "K2: link publik dihapus")
tab2 = app.admin_audit(ADMIN)[0]
ok((tab2["izin"].str.contains("Libur") & (tab2["status"] == "abaikan")).any(), "keputusan 'abaikan' tetap tercatat")
print(audit.riwayat_keputusan().to_string(index=False))
print("\nSEMUA UJI LULUS")
