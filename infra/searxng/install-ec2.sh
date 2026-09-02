#!/usr/bin/env bash
set -euo pipefail

SETTINGS_SOURCE="${1:-/tmp/interact-searxng-settings.yml}"
SERVICE_SOURCE="${2:-/tmp/interact-searxng.service}"
CONFIG_DIR="/opt/interact-searxng/config"
ENV_FILE="/etc/interact-searxng.env"
SERVICE_FILE="/etc/systemd/system/interact-searxng.service"

if [[ ! -f "$SETTINGS_SOURCE" || ! -f "$SERVICE_SOURCE" ]]; then
  echo "SearXNG installation files are missing." >&2
  exit 1
fi

dnf install -y docker openssl
systemctl enable --now docker

install -d -m 0755 "$CONFIG_DIR"
install -m 0644 "$SETTINGS_SOURCE" "$CONFIG_DIR/settings.yml"
install -m 0644 "$SERVICE_SOURCE" "$SERVICE_FILE"

if [[ ! -s "$ENV_FILE" ]]; then
  umask 077
  printf 'SEARXNG_SECRET=%s\n' "$(openssl rand -hex 32)" > "$ENV_FILE"
fi
chmod 0600 "$ENV_FILE"

docker pull ghcr.io/searxng/searxng@sha256:c7cc75852051bf6254afda6ed1b920dd1677d8efe4ab141bf558f02e582f4371
systemctl daemon-reload
systemctl enable interact-searxng.service
systemctl restart interact-searxng.service

healthy=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 8 --get \
    --data-urlencode 'q=Taiwan industrial equipment' \
    --data 'format=json' \
    http://127.0.0.1:8888/search >/tmp/interact-searxng-health.json; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ "$healthy" -ne 1 ]]; then
  journalctl -u interact-searxng.service --no-pager -n 120 >&2 || true
  exit 1
fi

rm -f "$SETTINGS_SOURCE" "$SERVICE_SOURCE" /tmp/interact-searxng-install.sh
echo "SearXNG is healthy on EC2 loopback port 8888."
