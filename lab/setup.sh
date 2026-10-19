#!/usr/bin/env bash
# Meridian Labs — build the whole enterprise test environment step by step.
#
#   ./setup.sh all          run every step in order (first time)
#   ./setup.sh <step>       run one step: check certs config up ldap nextcloud files emails verify
#   ./setup.sh reset        delete containers AND all data (asks first)
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mOK\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
dc()   { docker compose "$@"; }
occ()  { docker compose exec -T -u www-data nextcloud php occ "$@"; }

load_env() {
  [ -f .env ] || die ".env missing. Run: cp .env.example .env   (then change the passwords)"
  set -a; . ./.env; set +a
  BASE_DN=$($PY scripts/lab_info.py base-dn)
}

step_check() {
  say "Checking prerequisites"
  command -v docker >/dev/null || die "docker not found"
  docker compose version >/dev/null 2>&1 || die "docker compose plugin not found"
  command -v openssl >/dev/null || die "openssl not found"
  $PY -c "import requests, yaml" 2>/dev/null || die "Python packages missing. Run: pip install -r requirements.txt"
  [ -f .env ] || die ".env missing. Run: cp .env.example .env"
  grep -q "change-me" .env && die ".env still contains 'change-me' passwords"
  [ -f seed/accounts.env ] || die "seed/accounts.env missing. Run: $PY scripts/make_passwords.py"
  grep -q "=change-me" seed/accounts.env && die "seed/accounts.env still contains 'change-me' passwords"
  for p in 389 587 993 8080 8081 8082; do
    if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then
      docker ps --format '{{.Ports}}' | grep -q "127.0.0.1:$p->" || die "port $p is already used by another program"
    fi
  done
  ok "docker, openssl, python packages, .env, seed/accounts.env, free ports"
}

step_certs()  { say "Certificates (lab CA + mail server)"; bash scripts/make_certs.sh; }

step_config() {
  say "Directory and mail configuration from company.yaml"
  $PY scripts/gen_directory.py
}

step_up() {
  load_env
  say "Starting containers"
  [ -f certs/mail.crt ] || die "run ./setup.sh certs first"
  [ -f config/mail/postfix-accounts.cf ] || die "run ./setup.sh config first"
  dc up -d
  printf '    waiting for Nextcloud to finish installing '
  for i in $(seq 1 120); do
    if occ status 2>/dev/null | grep -q "installed: true"; then echo; ok "Nextcloud installed"; break; fi
    printf '.'; sleep 5
    [ "$i" = 120 ] && die "Nextcloud did not finish installing in 10 minutes (docker compose logs nextcloud)"
  done
  printf '    waiting for the directory '
  for i in $(seq 1 60); do
    if dc exec -T ldap ldapsearch -x -H ldap://localhost -b "$BASE_DN" -D "cn=admin,$BASE_DN" -w "$LDAP_ADMIN_PASSWORD" -s base >/dev/null 2>&1; then
      echo; ok "LDAP answers"; break; fi
    printf '.'; sleep 3
    [ "$i" = 60 ] && die "LDAP did not start (docker compose logs ldap)"
  done
  printf '    waiting for the mail server '
  for i in $(seq 1 60); do
    if (exec 3<>/dev/tcp/127.0.0.1/993) 2>/dev/null && (exec 3<>/dev/tcp/127.0.0.1/587) 2>/dev/null; then
      echo; ok "IMAPS 993 and submission 587 open"; break; fi
    printf '.'; sleep 5
    [ "$i" = 60 ] && die "mail server did not open its ports (docker compose logs mail)"
  done
}

step_ldap() {
  load_env
  say "Filling the directory (people + groups)"
  if dc exec -T ldap ldapsearch -x -H ldap://localhost -D "cn=admin,$BASE_DN" -w "$LDAP_ADMIN_PASSWORD" \
       -b "ou=people,$BASE_DN" "(uid=dreyes)" uid 2>/dev/null | grep -q "^uid: dreyes"; then
    ok "directory already filled -> skipped"
  else
    dc exec -T ldap ldapadd -x -H ldap://localhost -D "cn=admin,$BASE_DN" -w "$LDAP_ADMIN_PASSWORD" -f /seed/directory.ldif >/dev/null
    ok "directory.ldif imported"
  fi
  n=$(dc exec -T ldap ldapsearch -x -H ldap://localhost -D "cn=readonly,$BASE_DN" -w "$LDAP_READONLY_PASSWORD" \
        -b "ou=people,$BASE_DN" "(objectClass=inetOrgPerson)" uid | grep -c "^uid:" || true)
  g=$(dc exec -T ldap ldapsearch -x -H ldap://localhost -D "cn=readonly,$BASE_DN" -w "$LDAP_READONLY_PASSWORD" \
        -b "ou=groups,$BASE_DN" "(objectClass=groupOfNames)" cn | grep -c "^cn:" || true)
  ok "readonly account sees $n people and $g groups (expected 19 and 8)"
}

step_nextcloud() {
  load_env
  say "Nextcloud: connect to the directory (LDAP)"
  occ app:enable user_ldap >/dev/null
  cfg=$(occ ldap:show-config 2>/dev/null | grep -oE "\bs[0-9]{2}\b" | head -1 || true)
  if [ -z "$cfg" ]; then
    cfg=$(occ ldap:create-empty-config | grep -oE "s[0-9]{2}" | head -1)
  fi
  set_cfg() { occ ldap:set-config "$cfg" "$1" "$2" >/dev/null; }
  set_cfg ldapHost ldap
  set_cfg ldapPort 389
  set_cfg ldapAgentName "cn=readonly,$BASE_DN"
  set_cfg ldapAgentPassword "$LDAP_READONLY_PASSWORD"
  set_cfg ldapBase "$BASE_DN"
  set_cfg ldapBaseUsers "ou=people,$BASE_DN"
  set_cfg ldapBaseGroups "ou=groups,$BASE_DN"
  set_cfg ldapUserFilterObjectclass inetOrgPerson
  set_cfg ldapUserFilter "(objectClass=inetOrgPerson)"
  set_cfg ldapLoginFilter "(&(objectClass=inetOrgPerson)(uid=%uid))"
  set_cfg ldapLoginFilterUsername 1
  set_cfg ldapGroupFilterObjectclass groupOfNames
  set_cfg ldapGroupFilter "(objectClass=groupOfNames)"
  set_cfg ldapGroupMemberAssocAttr member
  set_cfg ldapUserDisplayName displayName
  set_cfg ldapGroupDisplayName cn
  set_cfg ldapEmailAttribute mail
  set_cfg ldapExpertUsernameAttr uid          # Nextcloud username = directory uid (e.g. skim)
  set_cfg ldapConfigurationActive 1
  occ ldap:test-config "$cfg" | grep -qi "valid" || die "LDAP configuration test failed: occ ldap:test-config $cfg"
  ok "LDAP config $cfg is valid"
  users=$(occ user:list --output=json | $PY -c "import json,sys; print(len(json.load(sys.stdin)))")
  ok "Nextcloud sees $users accounts (19 employees + admin = 20)"

  say "Nextcloud: Team folders (groupfolders)"
  if ! occ app:enable groupfolders >/dev/null 2>&1; then
    echo "    installing groupfolders v22.0.6 (for Nextcloud 34)"
    dc exec -T nextcloud bash -c "rm -rf /tmp/gf.tar.gz && curl -fsSL -o /tmp/gf.tar.gz https://github.com/nextcloud-releases/groupfolders/releases/download/v22.0.6/groupfolders-v22.0.6.tar.gz && tar -xzf /tmp/gf.tar.gz -C /var/www/html/apps/ && chown -R www-data:www-data /var/www/html/apps/groupfolders"
    occ app:enable groupfolders >/dev/null
  fi
  ok "groupfolders $(occ app:list --output=json | $PY -c "import json,sys; d=json.load(sys.stdin); print(d['enabled'].get('groupfolders','?'))")"
  existing=$(occ groupfolders:list --output=json)
  while IFS=$'\t' read -r folder group perm; do
    id=$(echo "$existing" | $PY -c "import json,sys; d=json.load(sys.stdin); d=d.values() if isinstance(d,dict) else d; print(next((str(f.get('id')) for f in d if f.get('mount_point')=='$folder'), ''))")
    if [ -z "$id" ]; then
      id=$(occ groupfolders:create "$folder" | tr -dc '0-9')
      existing=$(occ groupfolders:list --output=json)
    fi
    if [ "$perm" = write ]; then occ groupfolders:group "$id" "$group" read write share delete >/dev/null
    else occ groupfolders:group "$id" "$group" read >/dev/null; fi
    ok "Team folder /$folder ($id): $group = $perm"
  done < <($PY scripts/lab_info.py team-folders)
}

step_files()  { say "Uploading documents and creating shares (as each employee)"; NC_URL=http://localhost:8080 $PY scripts/seed_nextcloud.py; }
step_emails() { say "Sending emails (SMTP submission as each employee)"; SMTP_HOST=localhost $PY scripts/send_emails.py; }

step_verify() {
  load_env
  say "Verification"
  $PY scripts/verify_lab.py
}

step_reset() {
  read -r -p "This deletes ALL lab containers and data (directory, files, mail). Type 'yes': " a
  [ "$a" = yes ] || exit 0
  dc down -v
  rm -rf build/sent_emails.txt build/public_links.txt
  ok "removed. Run ./setup.sh all to start again"
}

case "${1:-}" in
  all) step_check; step_certs; step_config; step_up; step_ldap; step_nextcloud; step_files; step_emails; step_verify
       say "Done. Nextcloud http://localhost:8080 · Webmail http://localhost:8081 · Directory http://localhost:8082" ;;
  check|certs|config|up|ldap|nextcloud|files|emails|verify|reset) "step_$1" ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
