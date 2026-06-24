#!/bin/bash
# install.sh — Déploiement de Parlex sur RPi Zero + AIOC
# Usage: sudo bash install.sh
set -e

INSTALL_DIR="/opt/parlex"
CONFIG_DIR="/etc/parlex"
DATA_DIR="/var/lib/parlex"
RUN_DIR="/run/parlex"
LOG_FILE="/var/log/parlex.log"

echo "=== Installation Parlex — relais simplex ==="

# Dépendances système
apt-get update -q
apt-get install -y python3 python3-venv python3-pip \
                   libasound2-dev alsa-utils sox \
                   python3-serial 2>/dev/null || true

# Arborescence
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" \
         "$DATA_DIR/announce" "$DATA_DIR/voicemail" \
         "$RUN_DIR"

# Copie des sources
cp -r "$(dirname "$0")/../parlex" "$INSTALL_DIR/"

# Environnement virtuel
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$(dirname "$0")/../requirements.txt"

# Lien CLI global
ln -sf "$INSTALL_DIR/venv/bin/python -m parlex" /usr/local/bin/parlex 2>/dev/null || \
cat > /usr/local/bin/parlex << 'EOF'
#!/bin/bash
exec /opt/parlex/venv/bin/python -m parlex "$@"
EOF
chmod +x /usr/local/bin/parlex

# Config par défaut (ne pas écraser si existante)
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    "$INSTALL_DIR/venv/bin/python" -c "
from parlex.config import RepeaterConfig
from pathlib import Path
RepeaterConfig().save(Path('$CONFIG_DIR/config.yaml'))
print('Config créée : $CONFIG_DIR/config.yaml')
"
fi

# Permissions ALSA + série
usermod -aG audio root 2>/dev/null || true
usermod -aG dialout root 2>/dev/null || true

# Répertoire run (tmpfs, recréé au boot)
echo "d /run/parlex 0755 root root -" > /etc/tmpfiles.d/parlex.conf

# Service systemd
cp "$(dirname "$0")/simplex-repeater.service" /etc/systemd/system/parlex.service
systemctl daemon-reload
systemctl enable parlex.service

touch "$LOG_FILE"
chmod 644 "$LOG_FILE"

echo ""
echo "=== Installation terminée ==="
echo ""
echo "Commandes :"
echo "  systemctl start parlex          # démarrer le daemon"
echo "  systemctl status parlex         # statut"
echo "  journalctl -u parlex -f         # logs en direct"
echo ""
echo "  parlex run --tui               # avec interface Textual"
echo "  parlex run --curses            # avec interface curses"
echo "  parlex status                  # état + configuration"
echo "  parlex stats                   # statistiques QSO session"
echo "  parlex config list             # tous les paramètres"
echo "  parlex config set vox_threshold 0.015"
echo "  parlex voicemail list"
echo "  parlex announce list"
echo ""
echo "Config  : $CONFIG_DIR/config.yaml"
echo "Données : $DATA_DIR"
echo "Logs    : $LOG_FILE"
