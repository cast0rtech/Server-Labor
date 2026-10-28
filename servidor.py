"""Panel de control del servidor central de RedLab Tester."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="RedLab Tester",
    description="Panel de control del servidor central de pruebas EMI.",
    version="0.2.0",
)

iperf_process: Optional[subprocess.Popen] = None
iperf_lock = threading.Lock()


def get_local_ips() -> list[str]:
    """Obtiene todas las IPs locales IPv4 de las interfaces de red."""
    ips: set[str] = set()
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    # Conexión UDP de prueba para determinar la interfaz de salida principal
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            main_ip = s.getsockname()[0]
            ips.add(main_ip)
    except Exception:
        pass

    if not ips:
        ips.add("127.0.0.1")
    return sorted(list(ips))


def is_port_open(host: str, port: int) -> bool:
    """Verifica si un puerto TCP está abierto y respondiendo."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def find_iperf3_binary() -> Optional[Path]:
    """Localiza el binario iperf3.exe."""
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    candidates = [
        app_dir / "iperf3.exe",
        app_dir / "tools" / "iperf3.exe",
        Path(__file__).parent / "tools" / "iperf3.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    which = shutil.which("iperf3") or shutil.which("iperf3.exe")
    if which:
        return Path(which).resolve()
    return None


def ensure_local_iperf_server() -> bool:
    """Inicia el servidor iperf3 nativo si el puerto 5201 no está ocupado."""
    global iperf_process
    with iperf_lock:
        if is_port_open("127.0.0.1", 5201):
            return True  # Ya hay un servidor (Docker u otro) escuchando

        iperf_bin = find_iperf3_binary()
        if not iperf_bin:
            return False

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            iperf_process = subprocess.Popen(
                [str(iperf_bin), "-s", "-p", "5201"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return True
        except Exception:
            return False


@app.on_event("startup")
async def startup_event() -> None:
    """Intenta asegurar el servidor iperf3 en segundo plano al arrancar."""
    threading.Thread(target=ensure_local_iperf_server, daemon=True).start()


@app.get("/status")
async def status() -> dict[str, str]:
    """Indica que el servidor central está listo para recibir pruebas."""
    return {
        "status": "ready",
        "message": "Servidor central listo para recibir pruebas de los clientes.",
    }


@app.get("/api/info")
async def info() -> dict:
    """Información de diagnóstico del servidor."""
    local_ips = get_local_ips()
    iperf_active = is_port_open("127.0.0.1", 5201)
    return {
        "status": "ready",
        "hostname": platform.node(),
        "local_ips": local_ips,
        "iperf_port": 5201,
        "iperf_active": iperf_active,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Panel de control visual web accesible desde el navegador."""
    local_ips = get_local_ips()
    primary_ip = local_ips[0] if local_ips else "127.0.0.1"
    iperf_ok = is_port_open("127.0.0.1", 5201)

    status_badge = (
        '<span style="background:#10B981; color:#fff; padding:4px 12px; border-radius:999px; font-weight:600; font-size:0.85rem;">● ACTIVO (Puerto 5201 listo)</span>'
        if iperf_ok
        else '<span style="background:#F59E0B; color:#fff; padding:4px 12px; border-radius:999px; font-weight:600; font-size:0.85rem;">▲ Sin detector en 5201</span>'
    )

    ips_html = "".join(
        f'<div style="background:#F3F4F6; padding:10px 14px; border-radius:8px; font-family:monospace; font-size:1.1rem; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">'
        f'<strong>{ip}</strong>'
        f'<button onclick="navigator.clipboard.writeText(\'{ip}\'); this.innerText=\'¡Copiado!\'; setTimeout(()=>this.innerText=\'Copiar\', 1500);" style="background:#2563EB; color:#fff; border:none; padding:4px 12px; border-radius:6px; cursor:pointer; font-size:0.8rem;">Copiar</button>'
        f'</div>'
        for ip in local_ips
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RedLab Tester - Servidor Central</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: #0F172A; color: #E2E8F0; padding: 32px 16px; min-height: 100vh; display: flex; justify-content: center; }}
        .container {{ max-width: 760px; width: 100%; }}
        .card {{ background: #1E293B; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); border: 1px solid #334155; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
        h1 {{ font-size: 1.5rem; color: #F8FAFC; display: flex; align-items: center; gap: 8px; }}
        p.subtitle {{ color: #94A3B8; font-size: 0.95rem; margin-bottom: 16px; }}
        .ip-card {{ background: #0F172A; border: 1px solid #3B82F6; padding: 18px; border-radius: 10px; margin: 16px 0; }}
        .links-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 16px; }}
        .link-btn {{ background: #334155; color: #F1F5F9; padding: 12px; border-radius: 8px; text-decoration: none; font-weight: 500; display: flex; align-items: center; justify-content: space-between; transition: all 0.2s; }}
        .link-btn:hover {{ background: #2563EB; color: #FFFFFF; transform: translateY(-1px); }}
        .info-pill {{ font-size: 0.8rem; color: #94A3B8; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>📡 RedLab Tester Central</h1>
                {status_badge}
            </div>
            <p class="subtitle">Servidor central de métricas y pruebas de rendimiento de red para switches y clientes.</p>
            
            <div class="ip-card">
                <h3 style="color:#60A5FA; font-size:0.9rem; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">
                    IP(s) de este servidor para configurar en el Cliente:
                </h3>
                <p style="color:#94A3B8; font-size:0.85rem; margin-bottom:12px;">
                    Escribe cualquiera de estas IPs en el campo <strong>"IP del Servidor"</strong> de la aplicación <strong>SondaCliente</strong>:
                </p>
                <div style="color:#0F172A;">
                    {ips_html}
                </div>
            </div>

            <h3 style="color:#CBD5E1; font-size:1rem; margin-top:20px; margin-bottom:8px;">Servicios Asociados</h3>
            <div class="links-grid">
                <a href="http://localhost:3000" target="_blank" class="link-btn">
                    <span>📊 Grafana</span>
                    <span class="info-pill">:3000</span>
                </a>
                <a href="http://localhost:8086" target="_blank" class="link-btn">
                    <span>🗄️ InfluxDB</span>
                    <span class="info-pill">:8086</span>
                </a>
                <a href="/docs" target="_blank" class="link-btn">
                    <span>📖 API Docs (Swagger)</span>
                    <span class="info-pill">/docs</span>
                </a>
                <a href="/status" target="_blank" class="link-btn">
                    <span>🔍 Estado JSON</span>
                    <span class="info-pill">/status</span>
                </a>
            </div>
        </div>
    </div>
</body>
</html>
"""


if __name__ == "__main__":
    local_ips = get_local_ips()
    print("=" * 60)
    print("       RedLab Tester - Servidor Central")
    print("=" * 60)
    print(f"Panel Web disponible en: http://localhost:8000")
    print(f"Dirección IP(s) para clientes: {', '.join(local_ips)}")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
