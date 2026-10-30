"""Panel de control del servidor central de RedLab Tester."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

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
    """Localiza el binario iperf3.exe en el sistema o empaquetado."""
    candidates: list[Path] = []

    # 1. Empaquetado dentro de PyInstaller (_MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass_dir = Path(getattr(sys, "_MEIPASS"))
        candidates.append(meipass_dir / "iperf3.exe")
        candidates.append(meipass_dir / "tools" / "iperf3.exe")

    # 2. Junto al ejecutable o en su subcarpeta tools
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    candidates.extend([
        app_dir / "iperf3.exe",
        app_dir / "tools" / "iperf3.exe",
        Path(__file__).resolve().parent / "tools" / "iperf3.exe",
        Path(__file__).resolve().parent / "iperf3.exe",
    ])

    # 3. Instalación de WinGet en AppData
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_path = (
            Path(local_app_data)
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "ar51an.iPerf3_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "iperf3.exe"
        )
        candidates.append(winget_path)

    for c in candidates:
        if c.is_file():
            return c.resolve()

    which = shutil.which("iperf3.exe") or shutil.which("iperf3")
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
                cwd=str(iperf_bin.parent),
                creationflags=creationflags,
            )
            time.sleep(0.5)
            return is_port_open("127.0.0.1", 5201)
        except Exception:
            return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación con lifespan."""
    ensure_local_iperf_server()
    yield
    global iperf_process
    with iperf_lock:
        if iperf_process and iperf_process.poll() is None:
            try:
                iperf_process.terminate()
            except Exception:
                pass


app = FastAPI(
    title="RedLab Tester",
    description="Panel de control del servidor central de pruebas de rendimiento de red.",
    version="0.2.1",
    lifespan=lifespan,
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Evita registros 404 para favicon en consola."""
    return Response(status_code=204)


@app.get("/status")
async def status() -> dict[str, Any]:
    """Indica que el servidor central está listo para recibir pruebas."""
    return {
        "status": "ready",
        "iperf_port": 5201,
        "iperf_ready": is_port_open("127.0.0.1", 5201),
        "message": "Servidor central listo para recibir pruebas desde SondaCliente.",
    }


@app.get("/api/info")
async def info() -> dict:
    """Información de diagnóstico del servidor."""
    local_ips = get_local_ips()
    iperf_active = is_port_open("127.0.0.1", 5201)
    influx_active = is_port_open("127.0.0.1", 8086)
    grafana_active = is_port_open("127.0.0.1", 3000)
    return {
        "status": "ready",
        "hostname": platform.node(),
        "local_ips": local_ips,
        "iperf_port": 5201,
        "iperf_active": iperf_active,
        "influx_active": influx_active,
        "grafana_active": grafana_active,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Panel de control visual web accesible desde el navegador."""
    local_ips = get_local_ips()
    iperf_ok = is_port_open("127.0.0.1", 5201)
    influx_ok = is_port_open("127.0.0.1", 8086)
    grafana_ok = is_port_open("127.0.0.1", 3000)

    status_badge = (
        '<span style="background:#10B981; color:#fff; padding:6px 14px; border-radius:999px; font-weight:600; font-size:0.85rem;">● SERVIDOR IPERF3 ACTIVO (Puerto 5201)</span>'
        if iperf_ok
        else '<span style="background:#EF4444; color:#fff; padding:6px 14px; border-radius:999px; font-weight:600; font-size:0.85rem;">▲ Puerto 5201 inactivo</span>'
    )

    ips_html = "".join(
        f'<div style="background:#1E293B; border:1px solid #334155; padding:12px 16px; border-radius:8px; font-family:monospace; font-size:1.15rem; color:#38BDF8; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">'
        f'<strong>{ip}</strong>'
        f'<button onclick="navigator.clipboard.writeText(\'{ip}\'); this.innerText=\'¡Copiado!\'; setTimeout(()=>this.innerText=\'Copiar\', 1500);" style="background:#2563EB; color:#fff; border:none; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:0.85rem; font-weight:600;">Copiar</button>'
        f'</div>'
        for ip in local_ips
    )

    influx_status_text = '<span style="color:#10B981;">● Online (:8086)</span>' if influx_ok else '<span style="color:#94A3B8;">○ Inactivo (Docker opcional)</span>'
    grafana_status_text = '<span style="color:#10B981;">● Online (:3000)</span>' if grafana_ok else '<span style="color:#94A3B8;">○ Inactivo (Docker opcional)</span>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RedLab Tester - Servidor Central</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: #0B1120; color: #E2E8F0; padding: 32px 16px; min-height: 100vh; display: flex; justify-content: center; }}
        .container {{ max-width: 820px; width: 100%; }}
        .card {{ background: #131D31; border-radius: 14px; padding: 28px; margin-bottom: 20px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5); border: 1px solid #1E293B; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 10px; }}
        h1 {{ font-size: 1.6rem; color: #F8FAFC; display: flex; align-items: center; gap: 10px; }}
        p.subtitle {{ color: #94A3B8; font-size: 0.95rem; line-height: 1.5; margin-bottom: 20px; }}
        
        .instruction-box {{ background: linear-gradient(135deg, #1E3A8A 0%, #1E293B 100%); border: 1px solid #3B82F6; padding: 20px; border-radius: 12px; margin-bottom: 24px; }}
        .instruction-box h2 {{ font-size: 1.15rem; color: #60A5FA; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
        .instruction-box p {{ font-size: 0.92rem; color: #DBEAFE; line-height: 1.5; margin-bottom: 12px; }}
        .steps {{ margin-left: 20px; font-size: 0.9rem; color: #E2E8F0; line-height: 1.7; }}

        .ip-card {{ background: #0F172A; border: 1px solid #334155; padding: 20px; border-radius: 12px; margin-bottom: 24px; }}
        .links-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 16px; }}
        .link-btn {{ background: #1E293B; color: #F1F5F9; padding: 14px; border-radius: 10px; text-decoration: none; font-weight: 500; display: flex; align-items: center; justify-content: space-between; border: 1px solid #334155; transition: all 0.2s; }}
        .link-btn:hover {{ background: #2563EB; border-color: #3B82F6; color: #FFFFFF; transform: translateY(-2px); }}
        .info-pill {{ font-size: 0.8rem; font-weight: 600; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>📡 RedLab Tester - Servidor Central</h1>
                {status_badge}
            </div>
            <p class="subtitle">Este equipo está funcionando como <strong>receptor central de mediciones</strong>. El motor iperf3 está listo en el puerto 5201 para medir ancho de banda y latencia.</p>

            <div class="instruction-box">
                <h2>🚀 ¿Cómo realizar una prueba de red?</h2>
                <p>Las pruebas <strong>no se inician desde esta web</strong>, sino desde los equipos o switches que deseas medir:</p>
                <ol class="steps">
                    <li>Abre el programa <strong>SondaCliente.exe</strong> (o ejecuta <code>python cliente.py</code>) en el equipo cliente.</li>
                    <li>En el campo <strong>"IP del Servidor"</strong>, introduce una de las direcciones IP que aparecen abajo.</li>
                    <li>Pulsa el botón azul <strong>"Iniciar Medición"</strong> en la interfaz de SondaCliente.</li>
                    <li>Verás el ancho de banda (Mbps) y las retransmisiones en tiempo real.</li>
                </ol>
            </div>
            
            <div class="ip-card">
                <h3 style="color:#60A5FA; font-size:0.95rem; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px;">
                    IP(s) de este Servidor (Copia una para pegarla en SondaCliente):
                </h3>
                {ips_html}
            </div>

            <h3 style="color:#CBD5E1; font-size:1rem; margin-top:20px; margin-bottom:8px;">Estado de Servicios Asociados</h3>
            <div class="links-grid">
                <a href="http://localhost:3000" target="_blank" class="link-btn">
                    <span>📊 Grafana</span>
                    <span class="info-pill">{grafana_status_text}</span>
                </a>
                <a href="http://localhost:8086" target="_blank" class="link-btn">
                    <span>🗄️ InfluxDB</span>
                    <span class="info-pill">{influx_status_text}</span>
                </a>
                <a href="/docs" target="_blank" class="link-btn">
                    <span>📖 API Docs (Swagger)</span>
                    <span class="info-pill" style="color:#38BDF8;">/docs</span>
                </a>
                <a href="/status" target="_blank" class="link-btn">
                    <span>🔍 Estado JSON</span>
                    <span class="info-pill" style="color:#38BDF8;">/status</span>
                </a>
            </div>
        </div>
    </div>
</body>
</html>
"""


if __name__ == "__main__":
    local_ips = get_local_ips()
    iperf_ready = ensure_local_iperf_server()
    influx_ready = is_port_open("127.0.0.1", 8086)
    grafana_ready = is_port_open("127.0.0.1", 3000)

    print("=" * 64)
    print("           RedLab Tester - Servidor Central")
    print("=" * 64)
    if iperf_ready:
        print("[OK] Receptor iperf3: ACTIVO en puerto 5201 (Listo para pruebas)")
    else:
        print("[ADVERTENCIA] No se pudo levantar iperf3 en el puerto 5201.")

    print(f"[OK] Panel Web:        http://localhost:8000")
    if influx_ready and grafana_ready:
        print("[OK] Docker Stack:     InfluxDB (:8086) y Grafana (:3000) ONLINE")
    else:
        print("[INFO] Docker Stack:    Desconectado (Opcional: 'docker compose up -d')")

    print("-" * 64)
    print("Dirección(es) IP para configurar en SondaCliente:")
    for ip in local_ips:
        print(f"  -> {ip}")
    print("-" * 64)
    print(">> Para ejecutar pruebas de red, abre 'SondaCliente.exe'")
    print("   o ejecuta: python cliente.py")
    print("=" * 64)

    uvicorn.run(app, host="0.0.0.0", port=8000)
