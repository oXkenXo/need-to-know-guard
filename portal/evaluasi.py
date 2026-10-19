"""Evaluasi auditor di storage uji (butuh MANIFEST = seed/manifest.csv).

Kunci jawaban izin per kasus mengikuti README storage uji:
  K1 share folder 'HR/Gaji 2026' ke grup (Semua)     K2 link publik 'Evaluasi Kinerja Q3'
  K3 file 'Rencana PHK 2026' di Team folder umum     K4 andi masih anggota grup Marketing
  K5 share 'Kampanye Q4' ke dodi
K6 (karyawan baru) dan K7 (mantan karyawan) tidak punya izin yang salah; keduanya muncul di daftar
"akun tanpa histori" (risiko berbasis aturan).

Jalankan:  python evaluasi.py   (setelah sinkron)
"""
import pandas as pd

from sinkron import manifest_pemilik, muat_index


def kasus_izin(idx):
    out = {}
    for k, g in idx["grants"].items():
        d, j = g.get("detail", {}), g["jenis"]
        path = d.get("path") or ""
        if j == "share" and d.get("share_type") == 1 and "Gaji 2026" in path:
            out[k] = "K1"
        elif j == "link" and "Evaluasi Kinerja Q3" in path:
            out[k] = "K2"
        elif j == "lokasi" and "Rencana PHK 2026" in idx["files"].get(d.get("fileid"), {}).get("judul", ""):
            out[k] = "K3"
        elif j == "grup" and d.get("grup") == "Marketing" and d.get("user") == "andi":
            out[k] = "K4"
        elif j == "share" and d.get("share_type") == 0 and d.get("ke") == "dodi" and "Kampanye Q4" in path:
            out[k] = "K5"
    return out


def persen_tidak_perlu(idx):
    """Per izin: dari semua akses yang dibukanya, berapa persen ke orang yang TIDAK perlu tahu (menurut manifest)."""
    man = manifest_pemilik()
    perlu = {fid: set(man[f["path"]]["perlu_tahu"]) for fid, f in idx["files"].items() if f["path"] in man}
    tot, salah = {}, {}
    for u, akses in idx["akses"].items():
        for fid, a in akses.items():
            if fid not in perlu:
                continue
            for k in a["kunci"]:
                tot[k] = tot.get(k, 0) + 1
                salah[k] = salah.get(k, 0) + (u not in perlu[fid])
    for k, g in idx["grants"].items():
        if g["jenis"] == "link":
            for fid in g["detail"].get("files", []):
                for u in idx["users"]:
                    if fid in perlu:
                        tot[k] = tot.get(k, 0) + 1
                        salah[k] = salah.get(k, 0) + (u not in perlu[fid])
    return {k: round(salah[k] / tot[k], 3) for k in tot}


def evaluasi(idx, tabel, k=5):
    kas = kasus_izin(idx)
    ptp = persen_tidak_perlu(idx)
    t = tabel.copy()
    t["kasus"] = t["kunci"].map(kas).fillna("")
    t["persen_tidak_perlu"] = t["kunci"].map(ptp)
    top = t.head(k)
    rows = []
    for key, kode in sorted(kas.items(), key=lambda x: x[1]):
        r = t[t["kunci"] == key]
        rows.append({"kasus": kode, "izin": idx["grants"][key]["label"],
                     "peringkat": int(r["peringkat"].iloc[0]) if len(r) else None,
                     "skor_curiga": float(r["skor_curiga"].iloc[0]) if len(r) else None,
                     f"masuk_top{k}": bool(len(r) and r["peringkat"].iloc[0] <= k)})
    df = pd.DataFrame(rows)
    p_at_k = (top["kasus"] != "").mean() if len(top) else 0.0
    ringkas = (f"**Precision@{k} = {p_at_k:.2f}** (dari {k} izin teratas, {int((top['kasus'] != '').sum())} adalah izin kasus K1–K5) · "
               f"kasus tertangkap di top-{k}: {int(df[f'masuk_top{k}'].sum()) if len(df) else 0} dari {len(df)}")
    return ringkas, df, t


if __name__ == "__main__":
    import audit
    from penilai import Penilai
    idx = muat_index()
    tab = audit.audit_proaktif(idx, Penilai(idx, audit.persetujuan(), audit.izin_sah()))
    teks, df, t = evaluasi(idx, tab)
    print(teks)
    print(df.to_string(index=False))
    print(t[["peringkat", "izin", "skor_curiga", "akses_dinilai", "kasus", "persen_tidak_perlu"]].head(12).to_string(index=False))
