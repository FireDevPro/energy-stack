#!/bin/sh
# Generate self-signed CA + server cert for the energy-stack Mosquitto broker.
# Run this ONCE on Pi-lab (or wherever the broker will live) to bootstrap TLS.
# Output: ca.crt, server.crt, server.key in the working directory.
#
# Threat model: LAN-only single-user. Self-signed CA, 10-year expiry. The CA
# private key is generated alongside but is only needed to sign new server
# certs (e.g. on hostname change or expiry); back it up offline if you want
# easy renewal.
#
# Output cert subject:
#   CN: pi-lab.local
#   subjectAltName: DNS:pi-lab.local, DNS:mosquitto, IP:192.168.20.10
#
# After generation:
#   1. Copy ca.crt + server.crt + server.key to /opt/mosquitto-certs/ on Pi-lab.
#   2. Copy ca.crt to wherever clients live (Pi 3B for the publisher, plus the
#      Telegraf container which mounts the same /opt/mosquitto-certs/).
#   3. chmod 644 ca.crt server.crt; chmod 600 server.key.
#   4. chown the broker's runtime user (for eclipse-mosquitto:2: 1883:1883).

set -eu

OUT_DIR="${OUT_DIR:-./certs}"
CA_DAYS="${CA_DAYS:-3650}"     # 10 years
SERVER_DAYS="${SERVER_DAYS:-3650}"
CN="${CN:-pi-lab.local}"
SAN="${SAN:-DNS:pi-lab.local,DNS:mosquitto,IP:192.168.20.10}"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

echo "[gen-certs] writing to $(pwd)"

# 1. CA private key + self-signed CA cert.
if [ -f ca.key ] && [ -f ca.crt ]; then
    echo "[gen-certs] ca.key + ca.crt already exist, skipping CA generation"
else
    openssl genrsa -out ca.key 4096
    openssl req -x509 -new -nodes -key ca.key -sha256 -days "$CA_DAYS" \
        -subj "/CN=energy-stack-mosquitto-ca" \
        -out ca.crt
    echo "[gen-certs] created ca.key + ca.crt"
fi

# 2. Server private key.
openssl genrsa -out server.key 4096

# 3. Server CSR.
cat > server.cnf <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = $CN
[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = $SAN
EOF

openssl req -new -key server.key -out server.csr -config server.cnf

# 4. Sign server cert with the CA.
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days "$SERVER_DAYS" -sha256 \
    -extfile server.cnf -extensions v3_req

# 5. Cleanup intermediates.
rm -f server.csr server.cnf ca.srl

# 6. Permissions.
chmod 644 ca.crt server.crt
chmod 600 ca.key server.key

echo "[gen-certs] done. Files:"
ls -la ca.crt server.crt server.key ca.key

cat <<NOTE

Next steps:
  1. Copy ca.crt + server.crt + server.key to /opt/mosquitto-certs/ on the broker host.
  2. Copy ca.crt to client hosts (Pi 3B publisher and the Telegraf container's mount).
  3. Verify: openssl x509 -in server.crt -noout -text | grep -A1 'Subject Alternative Name'
  4. Back up ca.key offline (or accept that cert renewal will require regenerating both).

NOTE
