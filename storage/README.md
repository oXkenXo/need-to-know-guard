# Deployment & Automation Nextcloud 34 (Docker)

Repositori ini berisi konfigurasi dan skrip otomasi untuk membangun lingkungan Nextcloud 34 berbasis Docker sebagai bahan latihan audit hak akses (storage/ACL):

- Pemasangan modul Team Folders (groupfolders v22.0.6) secara otomatis.
- Pembuatan akun pengguna, grup divisi, dan struktur Team Folders.
- Pengunggahan 23 berkas uji via WebDAV serta penanaman 7 kasus salah-konfigurasi hak akses (K1-K7).

> **Hanya untuk lab lokal.** Kasus K1-K7 sengaja salah. Jangan dipasang di server yang dipakai data sungguhan.

---

## Prasyarat

- Docker dan Docker Compose
- Python 3.9+ dengan library `requests` (`pip install requests`)
- Git

---

## Struktur Repositori

```text
.
├── docker-compose.yml         # Nextcloud 34 + PostgreSQL 16
├── seed_files.py              # Unggah berkas via WebDAV + buat share (OCS)
├── .env.example               # Contoh konfigurasi (salin jadi .env)
├── .gitignore                 # Mengabaikan .env dan seed/accounts.env
├── seed/
│   ├── accounts.env.example   # Contoh password akun (salin jadi accounts.env)
│   ├── manifest.csv           # Manifest 23 berkas uji
│   ├── seed_server.sh         # Grup, akun, dan Team Folders
│   └── docs/                  # Isi dokumen uji
└── README.md
```

---

## Quick Start

### 0. Mulai dari bersih (wajib jika sebelumnya pernah dipasang)

Jika server pernah memasang groupfolders versi lain (misalnya 20.0.0), file lama dan baru bisa tercampur di folder app. Hapus semuanya, termasuk volume:

```bash
docker compose down -v
```

### 1. Buat berkas rahasia dari contoh

```bash
cp .env.example .env
cp seed/accounts.env.example seed/accounts.env
```

Edit kedua berkas itu dan isi **password milik kamu sendiri**. Berkas ini sudah diabaikan oleh `.gitignore`, jangan di-commit, dan jangan menulis password asli di README atau di mana pun di repo.

### 2. Jalankan container

```bash
docker compose up -d
```

Tunggu sekitar satu menit sampai instalasi Nextcloud selesai, lalu buka http://localhost:8080.

### 3. Inisialisasi grup, akun, dan Team Folders

```bash
docker compose exec -u www-data app bash /seed/seed_server.sh
```

### 4. Unggah berkas dan buat share

```bash
python3 seed_files.py
```

Jika berhasil, baris terakhir berbunyi `SELESAI: 23 file, 3 share`.

---

## Verifikasi

```bash
docker compose exec -u www-data app php occ app:list | grep groupfolders
docker compose exec -u www-data app php occ groupfolders:list
```

Yang diharapkan:

- `app:list` menampilkan `groupfolders: 22.0.6` pada daftar aplikasi aktif.
- `groupfolders:list` menampilkan empat folder: **HR, Marketing, Finance, Pengumuman**.

Jika `seed_files.py` berhenti dengan pesan "Team folder ... tidak terlihat", berarti modul groupfolders belum aktif. Ulangi dari langkah 0.

---

## Struktur Hak Akses yang Benar (dasar)

| Team Folder | Grup | Izin |
|---|---|---|
| HR | HR | tulis, bagikan, hapus |
| Marketing | Marketing | tulis, bagikan, hapus |
| Finance | Finance | tulis, bagikan, hapus |
| Pengumuman | Semua | baca |
| Pengumuman | HR | tulis, bagikan, hapus |

| Akun | Grup |
|---|---|
| budi, sari | HR, Semua |
| maya, rina, eko | Marketing, Semua |
| dodi | Finance, Semua |
| andi | Finance, Marketing, Semua |

---

## Kasus ACL yang Ditanam

| Kode | Kasus | Cara ditanam | Dokumen terdampak |
|---|---|---|---|
| K1 | Folder rahasia HR dibagikan ke grup **Semua** | `seed_files.py`: budi membagikan `HR/Gaji 2026` ke grup Semua (hanya baca) | Gaji Manajer 2026 |
| K2 | Dokumen rahasia punya **link publik** | `seed_files.py`: sari membuat link publik untuk `Kerjaan Sari/Evaluasi Kinerja Q3.txt` | Evaluasi Kinerja Q3 |
| K3 | Dokumen rahasia **salah tempat** di folder pengumuman | Berkas diunggah ke `Pengumuman/`, yang terbaca grup Semua | Rencana PHK 2026 |
| K4 | **Mutasi** karyawan: akses lama tidak dicabut | `seed_server.sh`: andi pindah ke Finance tetapi masih anggota grup Marketing | Seluruh folder Marketing |
| K5 | Akses **proyek selesai** tidak dicabut | `seed_files.py`: maya membagikan `Marketing/Kampanye Q4` ke dodi, padahal tugas dodi selesai 30 September | Anggaran Iklan Q4, Rencana Iklan Q1 2027 |
| K6 | **Karyawan baru**, belum punya histori kerja | `seed_server.sh`: akun rina dibuat di grup Marketing tanpa file pribadi | seluruh dokumen Marketing (sistem belum punya histori untuk menilai) |
| K7 | Karyawan **resign**, akun masih aktif | `seed_server.sh`: akun eko tetap aktif di grup Marketing dan Semua | Strategi Kampanye 2027, folder Marketing, Pengumuman |

Kunci jawaban (siapa yang sebenarnya perlu tahu setiap berkas) ada di kolom `perlu_tahu` pada `seed/manifest.csv`.

---

## Cek Hasil per Akun

Login di http://localhost:8080 dengan tiap akun (password dari `seed/accounts.env`).

| Akun | Seharusnya melihat | Kelebihan akses (kasus) |
|---|---|---|
| budi | HR, Pengumuman, Kerjaan Budi | tidak ada |
| sari | HR, Pengumuman, Kerjaan Sari | tidak ada |
| maya | Marketing, Pengumuman, Kerjaan Maya | `Gaji 2026` di "Dibagikan dengan Anda" (K1) |
| andi | Finance, Pengumuman, Kerjaan Andi | **Marketing** (K4), `Gaji 2026` (K1) |
| dodi | Finance, Pengumuman, Kerjaan Dodi | **Kampanye Q4** termasuk Rencana Iklan Q1 2027 (K5), `Gaji 2026` (K1) |
| rina | Marketing, Pengumuman (tanpa folder pribadi: karyawan baru, K6) | `Gaji 2026` (K1) |
| eko | tidak ada (sudah resign) | login masih berhasil, melihat Marketing dan Pengumuman (K7), `Gaji 2026` (K1) |

Semua akun juga bisa membaca `Pengumuman/Rencana PHK 2026.txt` (K3). Untuk K2, jalankan `seed_files.py` lalu buka URL link publik yang dicetak di terminal pada jendela incognito tanpa login.

Bukti utama bahwa Team Folders dan ACL berjalan: login sebagai **andi** harus menampilkan folder `/Finance` **dan** `/Marketing`.

---

## Pemecahan Masalah

| Gejala | Penyebab | Solusi |
|---|---|---|
| `seed_files.py` berhenti: "Team folder ... tidak terlihat" | groupfolders belum aktif | `docker compose down -v`, ulangi dari langkah 1 |
| `occ app:list` tidak menampilkan groupfolders | pemasangan gagal (jaringan container) | jalankan ulang `seed_server.sh`, lihat pesan error curl |
| `seed/accounts.env belum ada` | langkah 1 terlewat | `cp seed/accounts.env.example seed/accounts.env` |
