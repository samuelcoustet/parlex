#!/bin/bash
# install.sh — Déploiement de Parlex sur RPi Zero + AIOC
# Usage: sudo bash install.sh
set -e

INSTALL_DIR="/opt/parlex"
CONFIG_DIR="/etc/parlex"
DATA_DIR="/var/lib/parlex"
RUN_DIR="/run/parlex"
LOG_FILE="/var/log/parlex.log"

# Variables AIOC — remplies par detect_aioc()
AIOC_SERIAL=""
AIOC_CARD_NUM=""
AIOC_CARD_NAME=""

# ─────────────────────────────────────────────────────────────
# Détection AIOC (VID=0483 / PID=a30c)
# ─────────────────────────────────────────────────────────────
detect_aioc() {
    echo ""
    echo "--- Détection AIOC ---"

    # 1. Port série (/dev/ttyACM*)
    for dev in /dev/ttyACM*; do
        [ -e "$dev" ] || continue
        info=$(udevadm info --query=property --name="$dev" 2>/dev/null) || continue
        vid=$(echo "$info" | grep '^ID_VENDOR_ID='  | cut -d= -f2 | tr -d '[:space:]')
        pid=$(echo "$info" | grep '^ID_MODEL_ID='   | cut -d= -f2 | tr -d '[:space:]')
        if [ "$vid" = "0483" ] && [ "$pid" = "a30c" ]; then
            AIOC_SERIAL="$dev"
            break
        fi
    done

    # 2. Carte ALSA
    alsa_line=$(aplay -l 2>/dev/null | grep -i "AIOC" | head -n1)
    if [ -n "$alsa_line" ]; then
        # Extrait le numéro de carte : "card N:"
        AIOC_CARD_NUM=$(echo "$alsa_line" | grep -oP 'card \K[0-9]+')
        AIOC_CARD_NAME="AIOC"
    fi

    # 3. Rapport
    if [ -n "$AIOC_SERIAL" ] && [ -n "$AIOC_CARD_NUM" ]; then
        echo "  [OK] Port série  : $AIOC_SERIAL"
        echo "  [OK] Carte ALSA  : card $AIOC_CARD_NUM ($AIOC_CARD_NAME)"
        export AIOC_SERIAL AIOC_CARD_NUM AIOC_CARD_NAME
        return 0
    fi

    # Détection partielle — avertissement
    echo ""
    echo "  [AVERTISSEMENT] AIOC non détecté complètement :"
    [ -z "$AIOC_SERIAL"   ] && echo "    - Aucun port série /dev/ttyACM* avec VID=0483/PID=a30c trouvé"
    [ -z "$AIOC_CARD_NUM" ] && echo "    - Aucune carte ALSA 'AIOC' trouvée (aplay -l)"
    echo ""
    echo "  Vérifiez que l'AIOC est branché et reconnu (lsusb | grep 0483)."
    echo ""
    read -r -p "  Continuer quand même l'installation ? [o/N] " answer
    case "$answer" in
        [oOyY]) echo "  Installation poursuivie sans AIOC détecté." ; return 1 ;;
        *)      echo "  Installation annulée."; exit 1 ;;
    esac
}

# ─────────────────────────────────────────────────────────────
# Écriture de /etc/asound.conf (dsnoop sur la carte AIOC)
# ─────────────────────────────────────────────────────────────
write_asound_conf() {
    local card="$1"
    echo "--- Écriture /etc/asound.conf (dsnoop card $card) ---"
    cat > /etc/asound.conf << EOF
# /etc/asound.conf — généré par install.sh Parlex
# Carte AIOC détectée : card $card

pcm.aioc_capture {
    type dsnoop
    ipc_key 5678
    slave {
        pcm "hw:$card,0"
        channels 1
        rate 48000
        period_size 1024
        buffer_size 4096
    }
}

pcm.aioc_playback {
    type dmix
    ipc_key 1234
    slave {
        pcm "hw:$card,0"
        channels 1
        rate 48000
        period_size 1024
        buffer_size 4096
    }
}

# Périphérique par défaut → AIOC
pcm.!default {
    type asym
    playback.pcm "aioc_playback"
    capture.pcm  "aioc_capture"
}
ctl.!default {
    type hw
    card $card
}
EOF
    echo "  [OK] /etc/asound.conf écrit."
}

# ─────────────────────────────────────────────────────────────
# Injection des valeurs AIOC dans config.yaml
# ─────────────────────────────────────────────────────────────
patch_config_aioc() {
    local cfg="$CONFIG_DIR/config.yaml"
    local serial="$1"
    local card_name="$2"
    [ -f "$cfg" ] || return

    echo "--- Mise à jour config.yaml avec les périphériques AIOC ---"

    # serial_port
    if [ -n "$serial" ]; then
        if grep -q 'serial_port:' "$cfg"; then
            sed -i "s|^serial_port:.*|serial_port: \"$serial\"|" "$cfg"
        else
            echo "serial_port: \"$serial\"" >> "$cfg"
        fi
        echo "  serial_port → $serial"
    fi

    # alsa_capture
    local capture="plughw:$card_name,0"
    if grep -q 'alsa_capture:' "$cfg"; then
        sed -i "s|^alsa_capture:.*|alsa_capture: \"$capture\"|" "$cfg"
    else
        echo "alsa_capture: \"$capture\"" >> "$cfg"
    fi
    echo "  alsa_capture → $capture"

    # alsa_playback
    local playback="plughw:$card_name,0"
    if grep -q 'alsa_playback:' "$cfg"; then
        sed -i "s|^alsa_playback:.*|alsa_playback: \"$playback\"|" "$cfg"
    else
        echo "alsa_playback: \"$playback\"" >> "$cfg"
    fi
    echo "  alsa_playback → $playback"

    echo "  [OK] config.yaml mis à jour."
}

# ═════════════════════════════════════════════════════════════
# DÉBUT INSTALLATION
# ═════════════════════════════════════════════════════════════
echo "=== Installation Parlex — relais simplex ==="

# ─────────────────────────────────────────────────────────────
# Dépendances système
# ─────────────────────────────────────────────────────────────
echo "--- Installation des paquets ---"
apt-get update -q
apt-get install -y python3 python3-venv python3-pip \
                   libasound2-dev alsa-utils sox \
                   python3-serial 2>/dev/null || true

# ─────────────────────────────────────────────────────────────
# Détection AIOC (après les paquets, aplay disponible)
# ─────────────────────────────────────────────────────────────
AIOC_FOUND=0
detect_aioc && AIOC_FOUND=1 || true

# ─────────────────────────────────────────────────────────────
# Arborescence
# ─────────────────────────────────────────────────────────────
echo "--- Création de l'arborescence ---"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" \
         "$DATA_DIR/announce" "$DATA_DIR/voicemail" \
         "$RUN_DIR"

# ─────────────────────────────────────────────────────────────
# Copie des sources
# ─────────────────────────────────────────────────────────────
echo "--- Copie des sources ---"
cp -r "$(dirname "$0")/../parlex" "$INSTALL_DIR/"

# ─────────────────────────────────────────────────────────────
# Environnement virtuel
# ─────────────────────────────────────────────────────────────
echo "--- Création du virtualenv ---"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$(dirname "$0")/../requirements.txt"

# ─────────────────────────────────────────────────────────────
# Lien CLI global
# ─────────────────────────────────────────────────────────────
echo "--- Lien /usr/local/bin/parlex ---"
cat > /usr/local/bin/parlex << 'EOF'
#!/bin/bash
exec /opt/parlex/venv/bin/python -m parlex "$@"
EOF
chmod +x /usr/local/bin/parlex

# ─────────────────────────────────────────────────────────────
# Config par défaut (ne pas écraser si existante)
# Les variables AIOC sont exportées pour que Python les lise
# ─────────────────────────────────────────────────────────────
echo "--- Génération de la config ---"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    "$INSTALL_DIR/venv/bin/python" -c "
from parlex.config import RepeaterConfig
from pathlib import Path
import os

cfg = RepeaterConfig()

# Injection des périphériques AIOC si détectés
serial = os.environ.get('AIOC_SERIAL', '')
card   = os.environ.get('AIOC_CARD_NAME', '')

if serial:
    cfg.serial_port = serial
if card:
    cfg.alsa_capture  = f'plughw:{card},0'
    cfg.alsa_playback = f'plughw:{card},0'

cfg.save(Path('$CONFIG_DIR/config.yaml'))
print('Config créée : $CONFIG_DIR/config.yaml')
"
else
    echo "  Config existante conservée : $CONFIG_DIR/config.yaml"
    # Mise à jour uniquement des clés matériel si AIOC détecté
    if [ "$AIOC_FOUND" = "1" ]; then
        patch_config_aioc "$AIOC_SERIAL" "$AIOC_CARD_NAME"
    fi
fi

# ─────────────────────────────────────────────────────────────
# /etc/asound.conf si AIOC détecté
# ─────────────────────────────────────────────────────────────
if [ "$AIOC_FOUND" = "1" ] && [ -n "$AIOC_CARD_NUM" ]; then
    write_asound_conf "$AIOC_CARD_NUM"
fi

# ─────────────────────────────────────────────────────────────
# Groupes utilisateur (dialout = série PTT, audio = ALSA)
# ─────────────────────────────────────────────────────────────
echo "--- Vérification des groupes ---"
TARGET_USER="${SUDO_USER:-root}"
for grp in audio dialout; do
    if id -nG "$TARGET_USER" | grep -qw "$grp"; then
        echo "  [OK] $TARGET_USER est déjà dans le groupe $grp"
    else
        usermod -aG "$grp" "$TARGET_USER" && \
            echo "  [OK] $TARGET_USER ajouté au groupe $grp" || \
            echo "  [WARN] Impossible d'ajouter $TARGET_USER au groupe $grp"
    fi
done

# ─────────────────────────────────────────────────────────────
# Répertoire run (tmpfs, recréé au boot)
# ─────────────────────────────────────────────────────────────
echo "d /run/parlex 0755 root root -" > /etc/tmpfiles.d/parlex.conf

# ─────────────────────────────────────────────────────────────
# Service systemd
# ─────────────────────────────────────────────────────────────
echo "--- Installation du service systemd ---"
cp "$(dirname "$0")/simplex-repeater.service" /etc/systemd/system/parlex.service
systemctl daemon-reload
systemctl enable parlex.service

touch "$LOG_FILE"
chmod 644 "$LOG_FILE"

# ─────────────────────────────────────────────────────────────
# Résumé final
# ─────────────────────────────────────────────────────────────
echo ""
echo "=== Installation terminée ==="
echo ""
if [ "$AIOC_FOUND" = "1" ]; then
    echo "  AIOC détecté :"
    echo "    Port série   : $AIOC_SERIAL"
    echo "    Carte ALSA   : card $AIOC_CARD_NUM ($AIOC_CARD_NAME)"
    echo "    asound.conf  : /etc/asound.conf"
else
    echo "  [WARN] AIOC non détecté — configurez manuellement serial_port,"
    echo "         alsa_capture et alsa_playback dans $CONFIG_DIR/config.yaml"
fi
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

# ─────────────────────────────────────────────────────────────
# Proposition Tailscale (accès distant sécurisé)
# ─────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────────"
echo " Tailscale — accès distant sécurisé (VPN mesh)"
echo "─────────────────────────────────────────────────────────"
echo " Tailscale permet d'accéder au relais depuis n'importe"
echo " où via un réseau VPN privé, sans ouvrir de port."
echo " Utile pour : SSH, parlex remote, monitoring web."
echo ""
read -r -p " Installer Tailscale maintenant ? [o/N] " answer
case "$answer" in
    [oOyY])
        echo ""
        echo "--- Installation de Tailscale ---"
        curl -fsSL https://tailscale.com/install.sh | sh
        systemctl enable --now tailscaled
        echo ""
        echo "  [OK] Tailscale installé et activé."
        echo ""
        echo "  Connectez ce Pi à votre réseau Tailscale :"
        echo "    tailscale up"
        echo ""
        echo "  Puis depuis n'importe quelle machine du réseau :"
        echo "    ssh pi@<nom-du-pi>.tail.net"
        echo "    parlex remote --url http://<nom-du-pi>.tail.net:8080"
        echo ""
        ;;
    *)
        echo ""
        echo "  Tailscale non installé."
        echo "  Pour l'installer plus tard : curl -fsSL https://tailscale.com/install.sh | sh"
        echo ""
        ;;
esac
