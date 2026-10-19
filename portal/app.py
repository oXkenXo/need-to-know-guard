"""Portal karyawan: Dokumen saya · Chatbot · Permintaan akses · Panel admin (auditor ACL).

Jalankan:  python app.py   lalu buka http://127.0.0.1:7860
Login memakai akun Nextcloud (karyawan atau admin). Password hanya dipakai untuk mengecek login ke Nextcloud.
"""
import io
import contextlib

import gradio as gr
import pandas as pd

import audit
import chatbot
import evaluasi
import konfigurasi as K
from nextcloud import Nextcloud
from penilai import Penilai
from sinkron import muat_index, sinkron

NC = Nextcloud(K.NC_URL, K.NC_ADMIN_USER, K.NC_ADMIN_PASS)
S = {"idx": None, "penilai": None}
ZONA = {"lolos": "✅ boleh dipakai", "tanya": "❓ perlu izin pemilik", "tahan": "⛔ ditahan guard"}
JALUR = {"pemilik": "folder pribadimu", "team_folder": "Team folder (lewat grup)", "share_user": "dibagikan ke kamu",
         "share_grup": "dibagikan ke grupmu", "share": "dibagikan", "lain": "lainnya"}


def sistem():
    if S["idx"] is None:
        S["idx"] = muat_index()
    if S["idx"] is None:
        raise gr.Error("Index belum ada. Admin perlu menekan 'Sinkron dari Nextcloud' dulu.")
    if S["penilai"] is None:
        S["penilai"] = Penilai(S["idx"], audit.persetujuan(), audit.izin_sah())
    S["penilai"].persetujuan = set(audit.persetujuan())
    S["penilai"].sah = audit.izin_sah()
    return S["idx"], S["penilai"]


def _karyawan(sesi):
    if not sesi or not sesi.get("user"):
        raise gr.Error("Silakan login dulu.")
    return sesi["user"]


def _admin(sesi):
    if not sesi or not sesi.get("admin"):
        raise gr.Error("Hanya admin.")
    return sesi["user"]


# ---------------------------------------------------------------------- login
def login(u, pw, sesi):
    u = (u or "").strip()
    info = NC.cek_login(u, pw or "")
    if not info:
        return (sesi, "❌ Username atau password salah.", gr.update(), gr.update(visible=False), "", gr.update(),
                gr.update(), gr.update(), gr.update(), None, gr.update())
    try:
        is_admin = "admin" in NC.grup_user(u, auth=(u, pw))
    except Exception:
        is_admin = False
    idx = muat_index()
    karyawan = bool(idx and u in idx["users"])
    if not karyawan and not is_admin:
        return (sesi, "Akun ini belum ada di index portal. Minta admin menjalankan Sinkron.", gr.update(),
                gr.update(visible=False), "", gr.update(), gr.update(), gr.update(), gr.update(), None, gr.update())
    sesi = {"user": u, "nama": info["displayname"], "admin": is_admin}
    grup = ", ".join(idx["users"][u]["grup"]) if karyawan else "admin"
    head = f"### Halo, {info['displayname']} · `{u}` · {grup}"
    return (sesi, "", gr.update(visible=False), gr.update(visible=True), head,
            gr.update(visible=karyawan), gr.update(visible=karyawan), gr.update(visible=karyawan),
            gr.update(visible=is_admin), dokumen_saya(sesi) if karyawan else None,
            gr.update(selected="dok" if karyawan else "admin"))


def logout():
    return ({}, gr.update(visible=True), gr.update(visible=False), "", [], "", gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False))


# ---------------------------------------------------------------------- karyawan
def dokumen_saya(sesi):
    u = _karyawan(sesi)
    idx, pen = sistem()
    akses = idx["akses"].get(u, {})
    nilai = {r["fid"]: r for r in pen.nilai(u, list(akses))}
    rows = []
    for fid, a in sorted(akses.items(), key=lambda x: x[1]["path"]):
        f = idx["files"][fid]
        lewat = [JALUR.get(j, j) for j in a["jenis"]]
        for k in a["kunci"]:
            if k.startswith("share:"):
                lewat.append(idx["grants"].get(k, {}).get("label", k))
        rows.append({"Dokumen": f["judul"], "Untuk chatbot": ZONA[nilai[fid]["zona"]],
                     "Alasan guard": nilai[fid]["alasan"] + (f" (skor {nilai[fid]['skor']})" if nilai[fid]["skor"] is not None else ""),
                     "Kenapa kamu bisa membukanya": "; ".join(dict.fromkeys(lewat)),
                     "Penulis": ", ".join(f["penulis"]) or "-", "Lokasi": a["path"]})
    return pd.DataFrame(rows)


def tanya(pesan, riwayat, sesi):
    u = _karyawan(sesi)
    if not (pesan or "").strip():
        return riwayat, "", "", gr.update(), ""
    idx, pen = sistem()
    r = chatbot.jawab(idx, pen, u, pesan)
    riwayat = (riwayat or []) + [{"role": "user", "content": pesan}, {"role": "assistant", "content": r["jawaban"]}]
    src = "\n".join(f"- ✅ **{d['judul']}** · `{d['path']}` · {d['alasan']}" for d in r["sumber"]) or "- (tidak ada)"
    held = "\n".join([f"- ❓ **{d['judul']}** · {d['alasan']}" for d in r["tanya"]] +
                     [f"- ⛔ **{d['judul']}** · {d['alasan']}" for d in r["ditahan"]]) or "- (tidak ada)"
    pilihan = [(f"{d['judul']} ({'perlu izin' if d['zona'] == 'tanya' else 'ditahan'})", d["fid"]) for d in r["tanya"] + r["ditahan"]]
    return (riwayat, f"**Dokumen yang dipakai untuk menjawab**\n{src}",
            f"**Dokumen yang ditemukan tapi TIDAK dipakai** (kamu punya akses di Nextcloud, tapi guard menilai belum tentu perlu)\n{held}",
            gr.update(choices=pilihan, value=pilihan[0][1] if pilihan else None), "")


def minta(fid, alasan, sesi):
    u = _karyawan(sesi)
    if not fid:
        return "Pilih dokumen dulu."
    idx, _ = sistem()
    return audit.minta_akses(idx, u, fid, alasan or "")


def daftar_req(sesi):
    u = _karyawan(sesi)
    untuk = audit.daftar_permintaan(pemilik=u)
    dari = audit.daftar_permintaan(pemohon=u)
    pending = untuk[untuk["status"] == "menunggu"]
    pil = [(f"#{r.id} · {r.pemohon} → {r.judul}", int(r.id)) for r in pending.itertuples()]
    return untuk, dari, gr.update(choices=pil, value=pil[0][1] if pil else None)


def putus(pid, setuju, sesi):
    u = _karyawan(sesi)
    if not pid:
        return "Pilih permintaan dulu.", *daftar_req(sesi)
    return audit.putuskan(int(pid), u, setuju), *daftar_req(sesi)


# ---------------------------------------------------------------------- admin
def admin_sinkron(sesi):
    _admin(sesi)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            sinkron()
        except Exception as e:
            print("GAGAL:", e)
    S["idx"], S["penilai"] = None, None
    return buf.getvalue()


KOLOM = ["peringkat", "izin", "jenis", "akses_dinilai", "ditahan", "tanya", "karyawan", "persen_ditahan", "skor_curiga",
         "contoh", "rekomendasi", "status"]


def admin_audit(sesi):
    _admin(sesi)
    idx, pen = sistem()
    tab = audit.audit_proaktif(idx, pen)
    if tab.empty:
        return tab, None, "Belum ada akses yang bisa dinilai.", None, gr.update(choices=[]), audit.risiko_aturan(idx, pen)
    eval_md, kasus = "", None
    if K.MANIFEST and K.MANIFEST.exists():
        eval_md, kasus, _ = evaluasi.evaluasi(idx, tab)
        eval_md = "**Evaluasi (khusus storage uji, kunci jawaban dari manifest):** " + eval_md
    pil = [(f"{r.peringkat}. {r.izin}", r.kunci) for r in tab.itertuples()]
    return (tab[KOLOM], tab, eval_md, kasus, gr.update(choices=pil, value=pil[0][1]), audit.risiko_aturan(idx, pen))


def admin_aksi(kunci, tujuan, yakin, cabut, sesi):
    admin = _admin(sesi)
    if not kunci:
        return "Pilih izin dulu.", audit.riwayat_keputusan()
    if not yakin:
        return "Centang **Saya yakin** dulu. Perubahan ini langsung berlaku di Nextcloud.", audit.riwayat_keputusan()
    idx, _ = sistem()
    pesan = audit.cabut(idx, kunci, admin, tujuan) if cabut else audit.abaikan(kunci, admin)
    return pesan, audit.riwayat_keputusan()


def admin_log(sesi):
    _admin(sesi)
    idx, _ = sistem()
    return audit.ringkas_log_chat(idx), audit.riwayat_keputusan()


def admin_notif(tab, sesi):
    _admin(sesi)
    if tab is None or len(tab) == 0:
        return "Jalankan audit dulu."
    try:
        return audit.kirim_ringkasan_ke_admin(tab)
    except Exception as e:
        return f"Gagal mengirim notifikasi: {e}"


# ---------------------------------------------------------------------- tampilan
with gr.Blocks(title="Portal Dokumen · Need-to-Know Guard") as demo:
    sesi = gr.State({})
    tab_audit = gr.State(None)
    gr.Markdown("# Portal Dokumen Karyawan\nChatbot dokumen dengan dua lapis: **izin Nextcloud** lalu **Need-to-Know Guard**.")
    with gr.Group(visible=True) as box_login:
        with gr.Row():
            in_user = gr.Textbox(label="Username Nextcloud")
            in_pw = gr.Textbox(label="Password", type="password")
        b_login = gr.Button("Masuk", variant="primary")
        msg_login = gr.Markdown()
    with gr.Column(visible=False) as box_main:
        with gr.Row():
            judul = gr.Markdown()
            b_logout = gr.Button("Keluar", size="sm", scale=0)
        with gr.Tabs() as tabs:
            with gr.Tab("Dokumen saya", visible=False, id="dok") as t_dok:
                gr.Markdown("Semua file yang **bisa kamu buka di Nextcloud**, alasan kenapa kamu bisa membukanya, dan "
                            "apakah chatbot boleh memakainya untukmu.")
                b_dok = gr.Button("Muat ulang", size="sm")
                tbl_dok = gr.Dataframe(wrap=True, interactive=False)
            with gr.Tab("Chatbot", visible=False, id="chat") as t_chat:
                chat = gr.Chatbot(height=380, label="Asisten dokumen")
                with gr.Row():
                    in_q = gr.Textbox(placeholder="Tanya soal dokumen kantor…", show_label=False, scale=5)
                    b_q = gr.Button("Kirim", variant="primary", scale=1)
                md_src = gr.Markdown()
                md_held = gr.Markdown()
                with gr.Accordion("Minta akses ke pemilik dokumen", open=False):
                    dd_req = gr.Dropdown(label="Dokumen", choices=[])
                    in_alasan = gr.Textbox(label="Alasan (mis. 'saya ikut proyek ini')")
                    b_req = gr.Button("Kirim permintaan")
                    md_req = gr.Markdown()
            with gr.Tab("Permintaan akses", visible=False, id="req") as t_req:
                b_req_ref = gr.Button("Muat ulang", size="sm")
                gr.Markdown("**Permintaan untuk dokumen yang kamu tulis**")
                tbl_untuk = gr.Dataframe(pd.DataFrame(columns=["id", "waktu", "pemohon", "judul", "alasan", "status"]), interactive=False)
                with gr.Row():
                    dd_putus = gr.Dropdown(label="Permintaan yang menunggu", choices=[])
                    b_setuju = gr.Button("Setujui", variant="primary")
                    b_tolak = gr.Button("Tolak", variant="stop")
                md_putus = gr.Markdown()
                gr.Markdown("**Permintaan yang kamu kirim**")
                tbl_dari = gr.Dataframe(pd.DataFrame(columns=["id", "waktu", "judul", "pemilik", "status"]), interactive=False)
            with gr.Tab("Panel admin", visible=False, id="admin") as t_admin:
                gr.Markdown("AI hanya **mencatat dan menyarankan**. Izin di Nextcloud baru berubah kalau admin menekan tombol.")
                with gr.Row():
                    b_sync = gr.Button("1 · Sinkron dari Nextcloud")
                    b_aud = gr.Button("2 · Audit sekarang", variant="primary")
                    b_notif = gr.Button("Kirim 5 teratas ke lonceng Nextcloud admin", size="sm")
                log_sync = gr.Textbox(label="Log sinkron", lines=6)
                md_notif = gr.Markdown()
                gr.Markdown("### Izin yang mencurigakan (audit proaktif: semua akses dinilai guard)\n"
                            "`skor_curiga` = batas bawah selang 95% dari persen akses yang ditahan (izin dengan bukti sedikit "
                            "tidak langsung naik ke atas).")
                tbl_aud = gr.Dataframe(wrap=True, interactive=False)
                md_eval = gr.Markdown()
                tbl_kasus = gr.Dataframe(interactive=False)
                with gr.Row():
                    dd_izin = gr.Dropdown(label="Pilih izin", choices=[], scale=3)
                    in_tujuan = gr.Textbox(label="Folder tujuan (khusus 'pindahkan file', mis. /HR)", scale=2)
                with gr.Row():
                    cb_yakin = gr.Checkbox(label="Saya yakin (perubahan langsung berlaku di Nextcloud)")
                    b_cabut = gr.Button("Cabut / perbaiki", variant="stop")
                    b_abaikan = gr.Button("Abaikan (izin ini sah)")
                md_aksi = gr.Markdown()
                gr.Markdown("### Risiko berbasis aturan (tidak butuh AI)")
                tbl_aturan = gr.Dataframe(wrap=True, interactive=False)
                gr.Markdown("### Dari log chatbot (audit pasif)")
                b_log = gr.Button("Muat ulang log", size="sm")
                tbl_log = gr.Dataframe(pd.DataFrame(columns=["izin", "akses_diminta", "ditahan", "tanya", "karyawan", "skor_curiga"]), wrap=True, interactive=False)
                gr.Markdown("### Riwayat keputusan admin")
                tbl_kep = gr.Dataframe(pd.DataFrame(columns=["waktu", "oleh", "aksi", "kunci", "hasil"]), wrap=True, interactive=False)

    out_login = [sesi, msg_login, box_login, box_main, judul, t_dok, t_chat, t_req, t_admin, tbl_dok, tabs]
    b_login.click(login, [in_user, in_pw, sesi], out_login)
    in_pw.submit(login, [in_user, in_pw, sesi], out_login)
    b_logout.click(logout, None, [sesi, box_login, box_main, judul, chat, in_pw, t_dok, t_chat, t_req, t_admin])
    b_dok.click(dokumen_saya, sesi, tbl_dok)
    b_q.click(tanya, [in_q, chat, sesi], [chat, md_src, md_held, dd_req, in_q])
    in_q.submit(tanya, [in_q, chat, sesi], [chat, md_src, md_held, dd_req, in_q])
    b_req.click(minta, [dd_req, in_alasan, sesi], md_req)
    b_req_ref.click(daftar_req, sesi, [tbl_untuk, tbl_dari, dd_putus])
    b_setuju.click(lambda p, s: putus(p, True, s), [dd_putus, sesi], [md_putus, tbl_untuk, tbl_dari, dd_putus])
    b_tolak.click(lambda p, s: putus(p, False, s), [dd_putus, sesi], [md_putus, tbl_untuk, tbl_dari, dd_putus])
    b_sync.click(admin_sinkron, sesi, log_sync)
    b_aud.click(admin_audit, sesi, [tbl_aud, tab_audit, md_eval, tbl_kasus, dd_izin, tbl_aturan])
    b_cabut.click(lambda k, t, y, s: admin_aksi(k, t, y, True, s), [dd_izin, in_tujuan, cb_yakin, sesi], [md_aksi, tbl_kep])
    b_abaikan.click(lambda k, t, y, s: admin_aksi(k, t, y, False, s), [dd_izin, in_tujuan, cb_yakin, sesi], [md_aksi, tbl_kep])
    b_log.click(admin_log, sesi, [tbl_log, tbl_kep])
    b_notif.click(admin_notif, [tab_audit, sesi], md_notif)

if __name__ == "__main__":
    demo.launch(server_name=K.HOST, server_port=K.PORT)
