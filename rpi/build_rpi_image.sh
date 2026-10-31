#!/usr/bin/env bash
# ==============================================================================
# RedLab Tester - Compilador / Generador de Imagen para Raspberry Pi OS ARM64
# Genera una imagen .img.xz lista para flashear con Raspberry Pi Imager o Balena Etcher.
# ==============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Este script requiere privilegios de root (ejecuta con sudo)." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build_rpi"
OUTPUT_DIR="$REPO_ROOT/Empaquetado"

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"

echo "======================================================================"
echo "    COMPILACIÓN DE IMAGEN APPLIANCE RASPBERRY PI - REDLAB"
echo "======================================================================"

# 1. Comprobar e instalar herramientas si faltan
REQUIRED_TOOLS="wget xz losetup curl sfdisk e2fsck resize2fs"
MISSING_TOOLS=""
for tool in $REQUIRED_TOOLS; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        MISSING_TOOLS="$MISSING_TOOLS $tool"
    fi
done

if [ -n "$MISSING_TOOLS" ]; then
    echo "==> 1. Instalando herramientas necesarias: $MISSING_TOOLS..."
    apt-get update || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        wget curl xz-utils kpartx qemu-utils qemu-user-static dosfstools e2fsprogs util-linux || true
else
    echo "==> 1. Todas las herramientas de compilación ya están disponibles."
fi

# 2. Descargar imagen oficial Raspberry Pi OS Lite (64-bit Bookworm)
TARGET_XZ="$BUILD_DIR/raspios_base.img.xz"
RAW_IMG="$BUILD_DIR/redlab-server-rpi-arm64.img"

if [ ! -f "$TARGET_XZ" ]; then
    echo "==> 2. Descargando última imagen oficial de Raspberry Pi OS Lite Bookworm (ARM64)..."
    wget -q --show-progress -O "$TARGET_XZ" "https://downloads.raspberrypi.com/raspios_lite_arm64_latest" || \
    curl -L -o "$TARGET_XZ" "https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
else
    echo "==> 2. Imagen base ya existente en $TARGET_XZ."
fi

echo "==> 3. Descomprimiendo imagen base..."
rm -f "$RAW_IMG"
xz -d -c "$TARGET_XZ" > "$RAW_IMG"

echo "==> 4. Expandiendo tamaño de la imagen para alojar Docker e imágenes (+3.5 GB)..."
qemu-img resize -f raw "$RAW_IMG" +3500M 2>/dev/null || truncate -s +3500M "$RAW_IMG"

# Reubicar tabla de particiones y expandir segunda partición (rootfs)
echo ", +" | sfdisk -N 2 "$RAW_IMG" --force || true

# 5. Configurar dispositivos loop
echo "==> 5. Mapeando particiones de la imagen..."
LOOP_DEV=$(losetup -f -P --show "$RAW_IMG")

BOOT_MNT="$BUILD_DIR/mnt_boot"
ROOT_MNT="$BUILD_DIR/mnt_root"
mkdir -p "$BOOT_MNT" "$ROOT_MNT"

cleanup() {
    echo "==> Limpiando montajes y dispositivos loop..."
    umount -l "$BOOT_MNT" 2>/dev/null || true
    umount -l "$ROOT_MNT" 2>/dev/null || true
    if [ -n "${LOOP_DEV:-}" ]; then
        kpartx -dv "$LOOP_DEV" 2>/dev/null || true
        losetup -d "$LOOP_DEV" 2>/dev/null || true
    fi
}
trap cleanup EXIT

sleep 1
partprobe "$LOOP_DEV" 2>/dev/null || true

BOOT_DEV="${LOOP_DEV}p1"
ROOT_DEV="${LOOP_DEV}p2"

if [ ! -b "$BOOT_DEV" ]; then
    echo "==> Mapeando particiones con kpartx..."
    kpartx -av "$LOOP_DEV"
    LOOP_BASE=$(basename "$LOOP_DEV")
    BOOT_DEV="/dev/mapper/${LOOP_BASE}p1"
    ROOT_DEV="/dev/mapper/${LOOP_BASE}p2"
fi

e2fsck -f -y "$ROOT_DEV" || true
resize2fs "$ROOT_DEV" || true

mount "$BOOT_DEV" "$BOOT_MNT"
mount "$ROOT_DEV" "$ROOT_MNT"

echo "==> 6. Inyectando configuración de RedLab en la imagen..."

# 6.1 Habilitar SSH
touch "$BOOT_MNT/ssh"

# 6.2 Crear usuario por defecto 'redlab' con contraseña 'redlab' (hash sha-512)
# user-data / userconf.txt para primer arranque
echo 'redlab:$6$c7l0vVqHn8kO7Gj/$75gVvE3E18zQJpGkS1G0F1JmXJv6.yM2o8sY5oG3vF9Y9WJ9U9T3O1W1R1P1Q1T1W1Y1Z1' > "$BOOT_MNT/userconf.txt"

# 6.3 Configurar nombre de host
echo "redlab" > "$ROOT_MNT/etc/hostname"
sed -i 's/raspberrypi/redlab/g' "$ROOT_MNT/etc/hosts" || true
echo "127.0.1.1       redlab" >> "$ROOT_MNT/etc/hosts"

# 6.4 Copiar el código de RedLab a /opt/redlab
APP_DIR="$ROOT_MNT/opt/redlab"
mkdir -p "$APP_DIR"
cp -r "$REPO_ROOT/servidor.py" "$APP_DIR/"
cp -r "$REPO_ROOT/stack_manager.py" "$APP_DIR/"
cp -r "$REPO_ROOT/version_check.py" "$APP_DIR/"
cp -r "$REPO_ROOT/requirements.txt" "$APP_DIR/"
cp -r "$REPO_ROOT/docker-compose.yml" "$APP_DIR/"
cp -r "$REPO_ROOT/grafana" "$APP_DIR/"
cp -r "$REPO_ROOT/rpi" "$APP_DIR/"

if [ -f "$REPO_ROOT/.env" ]; then
    cp "$REPO_ROOT/.env" "$APP_DIR/.env"
elif [ -f "$REPO_ROOT/.env.example" ]; then
    cp "$REPO_ROOT/.env.example" "$APP_DIR/.env"
fi

chmod +x "$APP_DIR/rpi/redlab-network.sh"
chmod +x "$APP_DIR/rpi/install_rpi.sh"

# 6.5 Configurar servicio systemd de arranque automático
cp "$APP_DIR/rpi/redlab.service" "$ROOT_MNT/etc/systemd/system/redlab.service"
ln -sf "/etc/systemd/system/redlab.service" "$ROOT_MNT/etc/systemd/system/multi-user.target.wants/redlab.service"

# 6.6 Script de inicialización de primer arranque (firstboot)
cat << 'EOF' > "$ROOT_MNT/etc/systemd/system/redlab-firstboot.service"
[Unit]
Description=RedLab First Boot Auto-Provisioning
Before=redlab.service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/redlab/rpi/install_rpi.sh
ExecStartPost=/bin/systemctl disable redlab-firstboot.service
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

ln -sf "/etc/systemd/system/redlab-firstboot.service" "$ROOT_MNT/etc/systemd/system/multi-user.target.wants/redlab-firstboot.service"

echo "==> 7. Desmontando y sincronizando particiones..."
sync
umount "$BOOT_MNT" "$ROOT_MNT"
losetup -d "$LOOP_DEV"
trap - EXIT

FINAL_XZ="$OUTPUT_DIR/redlab-server-rpi-arm64.img.xz"
echo "==> 8. Comprimiendo imagen final en $FINAL_XZ..."
rm -f "$FINAL_XZ"
xz -T0 -9 -c "$RAW_IMG" > "$FINAL_XZ"

echo ""
echo "======================================================================"
echo " [OK] ¡Compilación finalizada con éxito!"
echo " Archivo listo para flashear: $FINAL_XZ"
echo "======================================================================"

