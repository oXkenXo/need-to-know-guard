# Need-to-Know Guard · Storage Uji & Portal

Bagian sistem dari riset **Need-to-Know Guard for Enterprise RAG** (MBC Lab, Telkom University).

| Folder | Isi | Mulai dari |
|---|---|---|
| [`storage/`](storage/) | Storage server uji: Nextcloud 34 + PostgreSQL 16 (Docker), 7 akun, 4 Team folders, 23 dokumen, 7 kasus ACL salah (K1–K7) | `storage/README.md` |
| [`portal/`](portal/) | Portal karyawan: *Dokumen saya*, chatbot RAG dengan guard 3 zona, permintaan akses ke pemilik, dan panel admin (auditor ACL: Cabut / Abaikan) | `portal/README.md` |

## Urutan menjalankan

1. **Storage:** ikuti `storage/README.md` (langkah 0–4), sampai keluar `SELESAI: 23 file, 3 share`.
2. **Portal:**
   ```bash
   cd portal
   pip install -r requirements.txt
   cp portal.env.example portal.env     # isi NC_ADMIN_PASS = ADMIN_PASSWORD di storage/.env
   python sinkron.py
   python app.py                        # http://127.0.0.1:7860
   ```

`portal.env.example` sudah menunjuk ke `../storage/seed/accounts.env` dan `../storage/seed/manifest.csv`, jadi kedua folder harus tetap berdampingan.

Uji tanpa Docker (Nextcloud tiruan): lihat bagian "Uji tanpa Docker" di `portal/README.md`.

## Keamanan

- `.env`, `storage/seed/accounts.env`, dan `portal/portal.env` berisi password dan **tidak** di-commit (lihat `.gitignore`). Yang ada di repo hanya versi `.example`.
- Kasus K1–K7 **sengaja salah**. Pakai hanya di lab lokal.
- Notebook eksperimen (EnronQA, guard DeBERTa) tidak ada di repo ini.
