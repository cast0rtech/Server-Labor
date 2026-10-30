#!/usr/bin/env bash
# ==============================================================================
# RedLab Tester - Configuración de Red para Raspberry Pi
# 1. DHCP automático por defecto en eth0 / end0 / wlan0
# 2. IP fija de respaldo (Fallback / Alias): 192.168.1.200/24
# 3. mDNS activo: http://redlab.local:8000
# ==============================================================================
set -euo pipefail

FALLBACK_IP="192.168.1.200/24"
HOSTNAME="redlab"

echo "[RedLab Network] Configurando nombre de host '$HOSTNAME'..."
hostnamectl set-hostname "$HOSTNAME" || hostname "$HOSTNAME"

# 1. Asegurar Avahi Daemon (mDNS) para resolver redlab.local
if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now avahi-daemon 2>/dev/null || true
fi

# 2. Detectar interfaz Ethernet activa (eth0 en RPi 3/4 o end0 en RPi 5)
ETH_IFACE=""
for iface in eth0 end0 enp1s0; do
    if ip link show "$iface" >/dev/null 2>&1; then
        ETH_IFACE="$iface"
        break
    fi
done

if [ -z "$ETH_IFACE" ]; then
    echo "[RedLab Network] Aviso: No se detectó interfaz Ethernet estándar. Verificando interfaces disponibles..."
    ETH_IFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep -v 'lo' | head -n1 || true)
fi

echo "[RedLab Network] Interfaz principal: ${ETH_IFACE:-desconocida}"

# 3. Configurar NetworkManager si está disponible (estándar en Raspberry Pi OS Bookworm)
if command -v nmcli >/dev/null 2>&1; then
    echo "[RedLab Network] Configurando NetworkManager para DHCP + IP fija ($FALLBACK_IP)..."
    CONN_NAME=$(nmcli -t -f NAME,DEVICE con show --active | grep ":${ETH_IFACE}$" | cut -d: -f1 || true)
    if [ -z "$CONN_NAME" ]; then
        CONN_NAME="Wired connection 1"
    fi

    # Configurar método DHCP y agregar IP adicional fija
    nmcli con mod "$CONN_NAME" ipv4.method auto 2>/dev/null || true
    # Agregar dirección de fallback sin pisar la de DHCP
    nmcli con mod "$CONN_NAME" +ipv4.addresses "$FALLBACK_IP" 2>/dev/null || true
    nmcli con up "$CONN_NAME" 2>/dev/null || true
fi

# 4. Asegurar alias persistente mediante comando 'ip' si la interfaz está UP
if [ -n "$ETH_IFACE" ]; then
    echo "[RedLab Network] Verificando alias estático en $ETH_IFACE ($FALLBACK_IP)..."
    if ! ip addr show dev "$ETH_IFACE" | grep -q "192.168.1.200"; then
        ip addr add "$FALLBACK_IP" dev "$ETH_IFACE" label "${ETH_IFACE}:0" 2>/dev/null || true
        echo "[RedLab Network] Alias estático 192.168.1.200 asignado a ${ETH_IFACE}:0."
    else
        echo "[RedLab Network] Alias estático ya presente."
    fi
fi

echo "======================================================================"
echo " [OK] Red configurada correctamente:"
echo "      - DHCP: Asignado automáticamente por router/switch"
echo "      - IP Fija de Respaldo: 192.168.1.200 (enlace directo a PC)"
echo "      - Nombre de Red: http://redlab.local:8000"
echo "======================================================================"

