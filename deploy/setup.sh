#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
if [[ ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]]; then
  echo "Give your domain, lowercase, like: sudo bash deploy/setup.sh leadsage.duckdns.org" >&2
  exit 1
fi
APP=/opt/leadsage/app
VENV=/opt/leadsage/venv
SITE=/var/www/leadsage-site
HERE="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$HERE" != "$APP" ]]; then
  echo "Clone the repo to $APP first (see deploy/README.md). This copy is at $HERE." >&2
  exit 1
fi
if [[ "$(id -u)" != 0 ]]; then
  echo "Run it with sudo." >&2
  exit 1
fi

echo "1/6 Packages: Python and Caddy"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip curl gpg debian-keyring debian-archive-keyring apt-transport-https
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || {
  echo "Python 3.12 or newer is needed (Ubuntu 24.04 has it)." >&2
  exit 1
}

echo "2/6 A user with no login for the app"
id -u leadsage >/dev/null 2>&1 || useradd --system --home-dir /opt/leadsage --shell /usr/sbin/nologin leadsage
mkdir -p "$APP/data"
chown -R leadsage:leadsage /opt/leadsage

echo "3/6 Python packages"
sudo -u leadsage python3 -m venv "$VENV"
sudo -u leadsage "$VENV/bin/pip" install -q --upgrade pip
sudo -u leadsage "$VENV/bin/pip" install -q -r "$APP/requirements.txt"

echo "4/6 Website copy, with Open the app pointing at app.$DOMAIN"
mkdir -p "$SITE"
cp -r "$APP/site/." "$SITE/"
sed -i "s#http://127.0.0.1:8000/#https://app.$DOMAIN/#g" "$SITE/index.html"
chown -R caddy:caddy "$SITE" 2>/dev/null || true

echo "5/6 Caddy (HTTPS) and the app service"
sed "s/DOMAIN/$DOMAIN/g" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
cp "$APP/deploy/leadsage.service" /etc/systemd/system/leadsage.service

if [[ "${SKIP_SERVICES:-}" == 1 ]]; then
  echo "SKIP_SERVICES=1: not starting services or changing the firewall (test run)."
  exit 0
fi

echo "6/6 Firewall: web ports 80 and 443 only"
for port in 80 443; do
  iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save

if [[ ! -f "$APP/.env" ]]; then
  echo "Now write $APP/.env (see deploy/README.md, step 5), then run:"
  echo "  sudo systemctl enable --now leadsage && sudo systemctl reload caddy"
  exit 0
fi
chown leadsage:leadsage "$APP/.env"
chmod 600 "$APP/.env"
systemctl daemon-reload
systemctl enable --now leadsage
systemctl reload caddy
echo "Done. Website: https://$DOMAIN  App: https://app.$DOMAIN"
