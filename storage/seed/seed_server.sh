#!/bin/bash
set -e
cd /var/www/html
source /seed/accounts.env

# 1. Pemasangan Groupfolders v22.0.6 (Resmi untuk Nextcloud 34)
if ! php occ app:enable groupfolders >/dev/null 2>&1; then
  echo "Memasang modul groupfolders v22.0.6..."
  rm -f /tmp/gf.tar.gz
  curl -L -o /tmp/gf.tar.gz https://github.com/nextcloud-releases/groupfolders/releases/download/v22.0.6/groupfolders-v22.0.6.tar.gz
  tar -xzf /tmp/gf.tar.gz -C /var/www/html/apps/
  chown -R www-data:www-data /var/www/html/apps/groupfolders
  php occ app:enable groupfolders
fi

# 2. Buat Grup
for g in HR Marketing Finance Semua; do
  php occ group:add "$g" || echo "grup $g sudah ada, dilewati"
done

# 3. Buat Akun Karyawan
add() {
  local u="$1" name="$2"; shift 2
  local pw_var="PW_$u"
  if php occ user:info "$u" > /dev/null 2>&1; then
    echo "akun $u sudah ada, dilewati"
  else
    OC_PASS="${!pw_var}" php occ user:add --password-from-env --display-name="$name" "$u"
    for g in "$@" Semua; do php occ group:adduser "$g" "$u" || true; done
  fi
}

add budi "Budi Santoso" HR
add sari "Sari Wulandari" HR
add maya "Maya Putri" Marketing
add andi "Andi Pratama" Finance Marketing   # K4: pindah dari Marketing, grup Marketing lupa dicabut
add dodi "Dodi Hidayat" Finance
add rina "Rina Lestari" Marketing           # K6: karyawan baru, tanpa histori kerja
add eko "Eko Saputra" Marketing             # K7: sudah resign, akun masih aktif

# 4. Buat Team Folders
if php occ groupfolders:list | grep -q "Pengumuman"; then
  echo "Team folders sudah ada, dilewati."
else
  HR=$(php occ groupfolders:create HR)
  MKT=$(php occ groupfolders:create Marketing)
  FIN=$(php occ groupfolders:create Finance)
  PENG=$(php occ groupfolders:create Pengumuman)

  php occ groupfolders:group "$HR" HR write share delete
  php occ groupfolders:group "$MKT" Marketing write share delete
  php occ groupfolders:group "$FIN" Finance write share delete
  php occ groupfolders:group "$PENG" Semua read
  php occ groupfolders:group "$PENG" HR write share delete
fi

php occ groupfolders:list
