#!/bin/bash
set -e

echo "=== CCServer Production Setup ==="

# Install Caddy
echo "[1/4] Installing Caddy..."
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install -y caddy

# Copy systemd service
echo "[2/4] Setting up systemd service..."
cp /root/ccserver/deploy/ccserver.service /etc/systemd/system/ccserver.service
systemctl daemon-reload
systemctl enable ccserver
systemctl start ccserver

# Copy Caddyfile
echo "[3/4] Setting up Caddy reverse proxy with SSL..."
cp /root/ccserver/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl restart caddy

# Open firewall
echo "[4/4] Configuring firewall..."
ufw allow 80
ufw allow 443
ufw --force enable

echo ""
echo "=== Setup Complete ==="
echo "Service status:"
systemctl status ccserver --no-pager
echo ""
echo "Your API is live at: https://claude.lawexa.com/v1/messages"
echo ""
echo "Useful commands:"
echo "  systemctl status ccserver    - check status"
echo "  systemctl restart ccserver   - restart server"
echo "  journalctl -u ccserver -f    - view logs"
