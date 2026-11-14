#!/usr/bin/env bash
# One-shot setup of the Moth cloud on a fresh Vultr Ubuntu 22.04/24.04 VM. Run as root:
#     bash deploy.sh                      # times spoken in America/New_York
#     bash deploy.sh America/Chicago      # or any other IANA timezone
# Expects moth_cloud.py next to this script. Safe to re-run (keeps existing tokens and data).
set -euo pipefail

TZ_NAME="${1:-America/New_York}"
HERE="$(cd "$(dirname "$0")" && pwd)"
IP="$(hostname -I | awk '{print $1}')"
HOST="${IP//./-}.sslip.io"          # sslip.io: a free hostname that resolves to this IP, so we can get real HTTPS

echo "== installing python3 + caddy"
apt-get update -qq
apt-get install -y -qq python3 tzdata >/dev/null   # tzdata: real local times in spoken answers
HAVE_CADDY=1
apt-get install -y -qq caddy >/dev/null 2>&1 || HAVE_CADDY=0

echo "== installing the service"
id moth >/dev/null 2>&1 || useradd --system --home /var/lib/moth --shell /usr/sbin/nologin moth
install -d -o moth -g moth -m 750 /var/lib/moth
install -d -m 755 /opt/moth
install -m 644 "$HERE/moth_cloud.py" /opt/moth/moth_cloud.py

if [ ! -f /etc/moth.env ]; then
  umask 077
  cat > /etc/moth.env <<EOF
MOTH_INGEST_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
MOTH_QUERY_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
MOTH_TZ=$TZ_NAME
MOTH_RETENTION_DAYS=30
EOF
fi

BIND=127.0.0.1
[ "$HAVE_CADDY" = 1 ] || BIND=0.0.0.0     # no Caddy -> expose the API directly over plain HTTP (testing only)

cat > /etc/systemd/system/moth-cloud.service <<EOF
[Unit]
Description=Moth cloud occupancy API
After=network-online.target

[Service]
User=moth
EnvironmentFile=/etc/moth.env
ExecStart=/usr/bin/python3 /opt/moth/moth_cloud.py --host $BIND --port 8000 --db /var/lib/moth/moth.db
Restart=always
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/moth

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now moth-cloud >/dev/null
systemctl restart moth-cloud

if [ "$HAVE_CADDY" = 1 ]; then
  echo "== HTTPS via Caddy for $HOST"
  cat > /etc/caddy/Caddyfile <<EOF
$HOST {
    reverse_proxy 127.0.0.1:8000
}
EOF
  systemctl restart caddy
fi

if command -v ufw >/dev/null && ufw status | grep -q active; then
  if [ "$HAVE_CADDY" = 1 ]; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  else ufw allow 8000/tcp >/dev/null; fi
fi

# shellcheck disable=SC1091
. /etc/moth.env
if [ "$HAVE_CADDY" = 1 ]; then URL="https://$HOST"; else URL="http://$IP:8000"; fi
sleep 3
echo
echo "================ MOTH CLOUD READY ================"
echo " URL           : $URL      (first HTTPS request may take ~20 s while the certificate is issued)"
echo " health check  : curl $URL/health"
echo " INGEST token  : $MOTH_INGEST_TOKEN    -> laptop config/cloud.json"
echo " QUERY token   : $MOTH_QUERY_TOKEN    -> Chert agent tool config"
echo " timezone      : $MOTH_TZ"
echo "================================================="
