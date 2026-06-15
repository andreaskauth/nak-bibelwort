#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# NAK Bibelwort-Drucker – Installationsskript
# Für Raspberry Pi OS Lite (64-bit) auf dem Raspberry Pi Zero 2W
#
# Aufruf:  bash setup.sh
# ──────────────────────────────────────────────────────────────────────
set -e

echo "═══════════════════════════════════════════════════"
echo " NAK Bibelwort-Drucker – Installation"
echo "═══════════════════════════════════════════════════"

# ── 1. Systempakete ──────────────────────────────────────────────────
echo "→ Installiere Systempakete..."
sudo apt update
sudo apt install -y python3-pip git fonts-dejavu-core hostapd dnsmasq

# ── 2. Python-Abhängigkeiten ─────────────────────────────────────────
echo "→ Installiere Python-Pakete..."
sudo pip install flask requests beautifulsoup4 pillow python-escpos pyusb \
                 gpiozero RPi.GPIO spidev --break-system-packages

# ── 3. Waveshare E-Paper Treiber ─────────────────────────────────────
echo "→ Installiere Waveshare-Display-Treiber..."
if [ ! -d "$HOME/e-Paper" ]; then
    git clone https://github.com/waveshareteam/e-Paper.git "$HOME/e-Paper"
fi
cd "$HOME/e-Paper/RaspberryPi_JetsonNano/python"
sudo python3 setup.py install
cd "$HOME"

# ── 4. SPI aktivieren (für E-Paper Display) ──────────────────────────
echo "→ Aktiviere SPI-Schnittstelle..."
sudo raspi-config nonint do_spi 0

# ── 5. App installieren ──────────────────────────────────────────────
echo "→ Kopiere app.py..."
INSTALL_DIR="/home/$(whoami)"
cp app.py "$INSTALL_DIR/app.py"
mkdir -p "$INSTALL_DIR/data"

# ── 6. systemd-Dienst einrichten ─────────────────────────────────────
echo "→ Richte Autostart-Dienst ein..."
sudo tee /etc/systemd/system/bibelwort.service > /dev/null <<EOF
[Unit]
Description=NAK Bibelwort-Drucker
After=network.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $INSTALL_DIR/app.py
WorkingDirectory=$INSTALL_DIR
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bibelwort
sudo systemctl start bibelwort

# ── 7. hostapd vorbereiten (Access Point) ────────────────────────────
echo "→ Bereite WLAN-Access-Point vor..."
sudo systemctl unmask hostapd 2>/dev/null || true

echo ""
echo "═══════════════════════════════════════════════════"
echo " Installation abgeschlossen!"
echo "═══════════════════════════════════════════════════"
echo ""
echo " Die Weboberfläche ist erreichbar unter:"
echo "   http://$(hostname).local   oder   http://$(hostname -I | awk '{print $1}')"
echo ""
echo " Noch zu tun:"
echo "   • hostapd.conf und dnsmasq.conf einrichten (siehe README)"
echo "   • Online-Feed-URL in der Weboberfläche eintragen"
echo "   • Drucker und Display anschließen"
echo ""
