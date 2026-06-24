#!/bin/bash
# build_deb.sh — Construit le paquet Debian parlex_*.deb
# Usage: bash package/build_deb.sh
# Nécessite: dpkg-deb (paquet dpkg, présent par défaut sur Debian/Ubuntu/RPi OS)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Lecture de la version depuis pyproject.toml ────────────────────────────────
VERSION=$(grep '^version' "$ROOT_DIR/pyproject.toml" | head -1 | \
          sed 's/.*= *"\(.*\)"/\1/')
PKGNAME="parlex_${VERSION}_all"
OUT_DIR="$ROOT_DIR/dist"

echo "=== Build du paquet Debian ==="
echo "    Version  : $VERSION"
echo "    Paquet   : $PKGNAME.deb"
echo "    Sortie   : $OUT_DIR/"
echo ""

# ── Dossier de staging ─────────────────────────────────────────────────────────
STAGE="$OUT_DIR/$PKGNAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# ── DEBIAN/ ───────────────────────────────────────────────────────────────────
cp -r "$SCRIPT_DIR/DEBIAN" "$STAGE/"
# Mettre à jour la version dans control
sed -i "s/^Version:.*/Version: $VERSION/" "$STAGE/DEBIAN/control"
chmod 755 "$STAGE/DEBIAN/postinst" \
           "$STAGE/DEBIAN/prerm"   \
           "$STAGE/DEBIAN/postrm"

# ── Sources Python → /opt/parlex/ ─────────────────────────────────────────────
mkdir -p "$STAGE/opt/parlex"
cp -r "$ROOT_DIR/parlex"          "$STAGE/opt/parlex/"
cp    "$ROOT_DIR/requirements.txt" "$STAGE/opt/parlex/" 2>/dev/null || true

# ── Config template → /etc/parlex/ ────────────────────────────────────────────
mkdir -p "$STAGE/etc/parlex"
# On place un config.yaml minimal ; postinst le génère proprement si absent.
# Ce fichier sert de référence conffiles (dpkg le préserve à l'upgrade).
cat > "$STAGE/etc/parlex/config.yaml" << 'EOF'
# /etc/parlex/config.yaml — Configuration Parlex
# Généré par l'installateur. Modifiez puis relancez : systemctl restart parlex
# Voir toutes les options : parlex config list
serial_port: /dev/ttyACM0
alsa_capture: plughw:AIOC,0
alsa_playback: plughw:AIOC,0
repeater_on: true
EOF

# ── Service systemd → /etc/systemd/system/ ────────────────────────────────────
mkdir -p "$STAGE/etc/systemd/system"
cp "$ROOT_DIR/install/simplex-repeater.service" \
   "$STAGE/etc/systemd/system/parlex.service"

# ── tmpfiles.d → /etc/tmpfiles.d/ ─────────────────────────────────────────────
mkdir -p "$STAGE/etc/tmpfiles.d"
echo "d /run/parlex 0755 root root -" > "$STAGE/etc/tmpfiles.d/parlex.conf"

# ── Wrapper CLI → /usr/local/bin/parlex ───────────────────────────────────────
mkdir -p "$STAGE/usr/local/bin"
cat > "$STAGE/usr/local/bin/parlex" << 'EOF'
#!/bin/bash
exec /opt/parlex/venv/bin/python -m parlex "$@"
EOF
chmod 755 "$STAGE/usr/local/bin/parlex"

# ── Calcul de la taille installée ─────────────────────────────────────────────
INSTALLED_SIZE=$(du -sk "$STAGE" | cut -f1)
sed -i "s/^Installed-Size:.*/Installed-Size: $INSTALLED_SIZE/" \
    "$STAGE/DEBIAN/control" 2>/dev/null || \
    echo "Installed-Size: $INSTALLED_SIZE" >> "$STAGE/DEBIAN/control"

# ── Construction du .deb ───────────────────────────────────────────────────────
mkdir -p "$OUT_DIR"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT_DIR/$PKGNAME.deb"

echo ""
echo "=== Paquet prêt ==="
echo "    $OUT_DIR/$PKGNAME.deb"
echo ""
echo "Transférer sur le Pi :"
echo "  scp $OUT_DIR/$PKGNAME.deb pi@<ip>:~/"
echo ""
echo "Installer sur le Pi :"
echo "  sudo dpkg -i $PKGNAME.deb"
echo "  sudo systemctl start parlex"
echo ""
echo "Désinstaller :"
echo "  sudo apt remove parlex          # conserve /etc/parlex"
echo "  sudo apt purge parlex           # supprime tout"
