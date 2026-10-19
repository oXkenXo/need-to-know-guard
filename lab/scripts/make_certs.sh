#!/usr/bin/env bash
# Create the lab's own certificate authority and a TLS certificate for the mail server.
# Output (git-ignored): certs/ca.crt, certs/ca.key, certs/mail.crt, certs/mail.key
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs
if [ -f certs/mail.crt ]; then
  echo "certs/ already exists -> skipped (delete the folder to regenerate)"
  exit 0
fi
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
  -keyout certs/ca.key -out certs/ca.crt -subj "/O=Meridian Labs/CN=Meridian Labs Internal CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -sha256 \
  -keyout certs/mail.key -out certs/mail.csr -subj "/O=Meridian Labs/CN=mail.meridian.test" 2>/dev/null
cat > certs/mail.ext <<'EOF'
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
extendedKeyUsage=serverAuth
subjectAltName=DNS:mail.meridian.test,DNS:localhost,IP:127.0.0.1
EOF
openssl x509 -req -in certs/mail.csr -CA certs/ca.crt -CAkey certs/ca.key -CAcreateserial \
  -out certs/mail.crt -days 825 -sha256 -extfile certs/mail.ext 2>/dev/null
rm -f certs/mail.csr certs/mail.ext certs/ca.srl
chmod 644 certs/mail.key   # the mail container must be able to read it (lab only)
echo "certs/ca.crt and certs/mail.crt created"
