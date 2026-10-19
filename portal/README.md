# Portal Karyawan · Need-to-Know Guard

Portal web untuk storage server uji (Nextcloud). Karyawan bisa melihat **dokumen apa saja yang bisa ia buka** dan bertanya ke **chatbot**. Admin punya **panel auditor** yang menunjuk izin mana yang salah.

```
Karyawan ──login──▶ Portal (Gradio) ──WebDAV/OCS──▶ Nextcloud
                       │
   pertanyaan ──▶ ① ACL Nextcloud (file yang memang bisa dibuka)
                  ② Guard 3 zona: lolos / tanya pemilik / tahan
                  ③ LLM menjawab HANYA dari dokumen yang lolos
                       │
                       └─ setiap "ACL boleh, guard ragu/tahan" dicatat ─▶ Panel admin
                                                                          │
                       Admin klik Cabut / Abaikan ◀─ izin diurutkan ◀─────┘
                       (hapus share, keluarkan dari grup, pindahkan file)
```

**Prinsip:** AI hanya mencatat dan menyarankan. Izin di Nextcloud berubah **hanya** kalau admin menekan tombol dan mencentang "Saya yakin". Semua keputusan tercatat.

## Isi folder

| File | Fungsi |
|---|---|
| `sinkron.py` | Konektor: membaca Nextcloud sebagai tiap karyawan → file, ACL efektif, **jalur akses** (izin penyebab), histori kerja (dari app Activity) |
| `penilai.py` | Guard + pencarian dokumen. Keputusan 3 zona |
| `chatbot.py` | RAG: ACL → guard → LLM |
| `audit.py` | Log temuan, audit proaktif, rekomendasi, aksi Cabut/Abaikan, permintaan akses ke pemilik |
| `evaluasi.py` | Precision@5 terhadap kasus K1–K5 (khusus storage uji) |
| `app.py` | Tampilan web |
| `nextcloud.py` | Klien WebDAV / OCS / Team folders / Activity / Notifications |
| `uji/` | Nextcloud **tiruan** + uji otomatis (tanpa Docker) |

## Menjalankan

Prasyarat: storage server uji sudah jalan (Langkah 1–4 di repo storage), Python 3.10+.

```bash
cd portal
pip install -r requirements.txt
cp portal.env.example portal.env      # isi NC_ADMIN_PASS = ADMIN_PASSWORD di .env storage
python sinkron.py                     # baca Nextcloud -> data/index.json
python app.py                         # buka http://127.0.0.1:7860
```

Login dengan akun Nextcloud:
- **Karyawan** (mis. `maya` / password di `seed/accounts.env`) → tab *Dokumen saya*, *Chatbot*, *Permintaan akses*.
- **admin** (password = `ADMIN_PASSWORD`) → tab *Panel admin*.

Password hanya dipakai untuk mengecek login ke Nextcloud, tidak disimpan.

## Cara AI terhubung ke admin

1. **Catat.** Setiap dokumen yang lolos ACL tapi dinilai guard *tanya* atau *tahan* dicatat bersama **jalur aksesnya**: share, link publik, keanggotaan grup, atau lokasi file di Team folder.
2. **Audit proaktif** (tombol *Audit sekarang*). Guard menilai **semua** akses yang dibuka tiap izin, bukan hanya yang kebetulan ditanyakan. Izin diurutkan dengan `skor_curiga` = batas bawah selang 95% dari persen akses yang ditahan. Izin dengan bukti sedikit tidak langsung naik ke atas.
3. **Kirim ke admin.** Hasilnya muncul di panel admin. Tombol *Kirim 5 teratas* juga mengirimnya ke lonceng notifikasi Nextcloud admin.
4. **Admin memutuskan.**

   | Jenis izin | Tombol *Cabut / perbaiki* menjalankan |
   |---|---|
   | share ke user/grup, link publik | `DELETE /ocs/v2.php/apps/files_sharing/api/v1/shares/{id}` sebagai pemilik share |
   | keanggotaan grup | `DELETE /ocs/v2.php/cloud/users/{user}/groups` (akun admin) |
   | file salah taruh | WebDAV `MOVE` ke folder tujuan yang diisi admin |

   *Abaikan* menandai izin itu **sah**: tidak dihapus, dan guard tidak lagi menahan akses lewat izin itu (umpan balik dari manusia).
5. **Karyawan juga ikut.** Dokumen di zona *tanya* bisa diminta ke penulisnya (tab *Chatbot* → *Minta akses*). Penulis menyetujui atau menolak di tab *Permintaan akses*.

Hal yang tidak butuh AI ditampilkan terpisah di *Risiko berbasis aturan*: link publik, dan akun tanpa histori kerja (karyawan baru **atau** mantan karyawan; menonaktifkan akun adalah urusan HR/IAM, di luar cakupan guard).

## Pilihan guard (`PENILAI`)

| Pilihan | Cara kerja | Catatan |
|---|---|---|
| `tfidf` (bawaan) | kemiripan kata dokumen ↔ histori kerja (ala Carvalho) | offline, langsung jalan |
| `embedding` | kemiripan makna, model multibahasa | butuh `sentence-transformers` (unduh model ±470 MB sekali) |
| `deberta` | guard v5 dari notebook (`GUARD_DIR` = folder `v5_seed42/guard`) | **dilatih dengan email berbahasa Inggris**. Untuk dokumen Indonesia hasilnya tidak bisa dipercaya |

Histori kerja = file yang dibuat/diubah karyawan itu (app **Activity** Nextcloud). Karyawan tanpa histori (mis. `rina`) tidak bisa dinilai: semua dokumennya lewat persetujuan pemilik (`MODE_KARYAWAN_BARU=tanya`).

## LLM untuk chatbot

Bawaan `LLM=tanpa`: chatbot menampilkan kutipan paling relevan. Untuk jawaban sungguhan di laptop (RTX 4060 8 GB):

```bash
# pasang Ollama dari https://ollama.com lalu:
ollama pull qwen2.5:7b
```

Lalu di `portal.env`: `LLM=ollama`. LLM hanya menerima dokumen yang **lolos**. Kebocoran sudah dicegah sebelum LLM.

## Uji tanpa Docker (Nextcloud tiruan)

```bash
cp -r storage portal/uji/storage     # dari root repo ini (butuh seed/ dan seed_files.py)
cd portal/uji
./jalankan_uji.sh                    # server tiruan di port 8080 + seed_files.py asli + 16 pengecekan
```

Server tiruan meniru WebDAV, OCS (user, grup, share), Team folders, Activity, dan Notifications. Ini **bukan** Nextcloud asli. Uji terakhir tetap harus di server Docker.

## Hasil uji di Nextcloud tiruan (23 dokumen, `PENILAI=tfidf`)

| Kasus | Izin | Peringkat | Masuk 5 teratas? |
|---|---|---|---|
| K1 | share `HR/Gaji 2026` ke grup Semua | 4 | ya |
| K2 | link publik `Evaluasi Kinerja Q3` | 5 | ya (juga di daftar aturan) |
| K3 | `Rencana PHK 2026` di /Pengumuman | 2 | ya |
| K4 | andi masih di grup Marketing | 10 | tidak |
| K5 | share `Kampanye Q4` ke dodi | 9 | tidak |

**Precision@5 = 0,60.** K4 dan K5 adalah kasus **akses basi**. Histori lama andi (Marketing) dan catatan proyek dodi memang mirip dengan dokumennya, jadi guard meloloskan. Ini **sama** dengan temuan skenario C di paper; mitigasinya profil berbasis waktu. Peringkat 1 (`Libur Akhir Tahun` di /Pengumuman) adalah **alarm palsu**: pengumuman tidak mirip kerjaan siapa pun. Admin menekan *Abaikan*.

Angka ini **hanya ilustrasi**: 23 dokumen, dan ambang bawaan `tfidf` (0,06 / 0,12) dipilih setelah melihat skor di dokumen yang sama. Angka utama paper tetap dari notebook.

Semua aksi (cabut K1, K2, K4; pindah K3 ke /HR; abaikan Libur) sudah diuji berubah di server tiruan setelah sinkron ulang.

## Keamanan

- `portal.env` dan `seed/accounts.env` berisi password. Jangan di-commit (sudah ada `.gitignore`).
- Portal hanya mendengarkan `127.0.0.1`. Jangan dibuka ke internet.
- `AKUN_FILE` (password semua karyawan) hanya wajar untuk **server uji**. Di perusahaan nyata konektor memakai *app password* / OAuth per karyawan.
- Fungsi admin mengecek sesi di server, bukan hanya menyembunyikan tombol.
