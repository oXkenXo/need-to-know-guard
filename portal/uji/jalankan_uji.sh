#!/bin/bash
# Nextcloud tiruan baru + seed_files.py asli + uji alur portal. Butuh folder uji/storage (salinan repo storage).
cd "$(dirname "$0")"
[ -d storage/seed ] || { echo "Salin dulu repo storage ke portal/uji/storage (lihat README)"; exit 1; }
# password akun di salinan uji = password Nextcloud tiruan (bukan password server sungguhan)
printf 'PW_budi=Uji-Budi-2026!\nPW_sari=Uji-Sari-2026!\nPW_maya=Uji-Maya-2026!\nPW_andi=Uji-Andi-2026!\nPW_dodi=Uji-Dodi-2026!\nPW_rina=Uji-Rina-2026!\nPW_eko=Uji-Eko-2026!!\n' > storage/seed/accounts.env
[ -f fake.pid ] && kill "$(cat fake.pid)" 2>/dev/null; sleep 0.5
python3 fake_nc2.py 8080 > fake.log 2>&1 &
echo $! > fake.pid
sleep 1.5
(cd storage && python3 seed_files.py > /dev/null) || { echo "seed gagal (port 8080 dipakai?)"; kill "$(cat fake.pid)"; exit 1; }
rm -rf data
PORTAL_ENV="$PWD/portal_uji.env" python3 uji_fungsi.py "$@"; status=$?
kill "$(cat fake.pid)" 2>/dev/null; rm -f fake.pid
exit $status
