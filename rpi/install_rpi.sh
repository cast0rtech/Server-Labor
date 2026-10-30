#!/usr/bin/env bash
# ==============================================================================
# RedLab Tester - Instalador Automático para Raspberry Pi (Debian / RPi OS)
# Convierte cualquier Raspberry Pi en un Appliance de Servidor RedLab en 2 minutos.
# ==============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Este script debe ejecutarse como root (con sudo)." >&2
    exit 1
fi

echo "======================================================================"
echo "    INSTALADOR APPLIANCE REDLAB TESTER PARA RASPBERRY PI"
echo "======================================================================"
echo ""

APP_DIR="/opt/redlab"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> 1. Actualizando repositorios e instalando paquetes necesarios..."
apt-get update -y
apt-get install -y --no-install-recommends \
    curl \
    git \
    iperf3 \
    avahi-daemon \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates

# Instalar Docker si no está presente
if ! command -v docker >/dev/null 2>&1; then
    echo "==> Instalando motor Docker oficial..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
else
    echo "==> Docker ya está instalado."
fi

# Instalar Docker Compose Plugin si falta
if ! docker compose version >/dev/null 2>&1; then
    echo "==> Instalando Docker Compose Plugin..."
    apt-get install -y docker-compose-plugin || apt-get install -y docker-compose
fi

echo "==> 2. Desplegando archivos de RedLab en $APP_DIR..."
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

echo "==> 3. Instalando dependencias de Python..."
python3 -m pip install --upgrade pip --break-system-packages 2>/dev/null || true
python3 -m pip install -r "$APP_DIR/requirements.txt" --break-system-packages 2>/dev/null || \
    python3 -m pip install -r "$APP_DIR/requirements.txt"

echo "==> 4. Descargando imágenes Docker de InfluxDB v2, Grafana e iPerf3..."
docker pull influxdb:2.7
docker pull grafana/grafana:latest
docker pull networkstatic/iperf3

echo "==> 5. Configurando red (DHCP + Fallback 192.168.1.200 + mDNS)..."
bash "$APP_DIR/rpi/redlab-network.sh"

echo "==> 6. Registrando y activando servicio systemd redlab.service..."
cp "$APP_DIR/rpi/redlab.service" /etc/systemd/system/redlab.service
systemctl daemon-reload
systemctl enable redlab.service
systemctl restart redlab.service

echo ""
echo "======================================================================"
echo " [OK] ¡RedLab Tester Appliance instalado y activo con éxito!"
echo "======================================================================"
echo ""
echo "Accesos disponibles en tu red:"
echo " • Por mDNS:                     http://redlab.local:8000"
echo " • Por enlace directo fijo:       http://192.168.1.200:8000"
echo " • Panel de Setup y Admin:        http://redlab.local:8000/admin"
echo " • Grafana Dashboards:           http://redlab.local:7500"
echo " • InfluxDB v2:                  http://redlab.local:4000"
echo " • Servidor iPerf3:              Puerto TCP/UDP 5201 activo"
echo ""
echo "Todos los contenedores y servicios se iniciarán y reiniciarán automáticamente."
echo "======================================================================"

