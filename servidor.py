"""Panel de control del servidor central de RedLab Tester.

Gestiona de forma nativa en Windows:
- Servidor iPerf3 (puerto TCP 5201)
- Base de datos InfluxDB v2 (puerto TCP 4000)
- Visualización en Grafana (puerto TCP 7500)
- Panel Web de Control (puerto TCP 8000)
"""

from __future__ import annotations

import logging
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
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse

from stack_manager import (
    GRAFANA_PASSWORD,
    GRAFANA_PORT,
    GRAFANA_USER,
    INFLUX_BUCKET,
    INFLUX_ORG,
    INFLUX_PORT,
    INFLUX_TOKEN,
    is_port_open,
    stack_manager,
)
from version_check import APP_VERSION, check_for_update

logger = logging.getLogger("RedLab.Server")

iperf_process: Optional[subprocess.Popen] = None
iperf_lock = threading.Lock()


def get_classified_ips() -> dict[str, Any]:
    """Obtiene y clasifica las IPs locales en Primaria (LAN) y Secundarias (VPN/WSL/Virtuales)."""
    ips: set[str] = set()
    primary_ip = ""

    # Determinar IP principal mediante ruta saliente (gateway default)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            ips.add(primary_ip)
    except Exception:
        pass

    # Enumerar todas las interfaces disponibles
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    if not ips:
        ips.add("127.0.0.1")
    if not primary_ip:
        primary_ip = next(iter(ips))

    classified: list[dict[str, str]] = []
    for ip in sorted(list(ips)):
        is_primary = (ip == primary_ip)
        notes = []
        if is_primary:
            notes.append("⭐ Red Local (LAN Recomendada)")
        if ip.startswith("192.168.56."):
            notes.append("VirtualBox Host-Only")
        elif ip.startswith("172.2") or ip.startswith("172.1") or ip.startswith("172.3"):
            notes.append("WSL / Hyper-V Virtual")
        elif ip.startswith("10.") and not is_primary:
            notes.append("VPN / Red Corporativa")

        tag = " - ".join(notes) if notes else "Interfaz de red"
        classified.append({"ip": ip, "is_primary": is_primary, "tag": tag})

    # Ordenar poniendo la primaria primero
    classified.sort(key=lambda x: (not x["is_primary"], x["ip"]))
    return {"primary": primary_ip, "all": classified}


def find_iperf3_binary() -> Optional[Path]:
    """Localiza el binario iperf3.exe en el sistema o empaquetado."""
    candidates: list[Path] = []

    # 1. Empaquetado dentro de PyInstaller (_MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass_dir = Path(getattr(sys, "_MEIPASS"))
        candidates.append(meipass_dir / "iperf3.exe")
        candidates.append(meipass_dir / "tools" / "iperf3.exe")

    # 2. Junto al ejecutable o en subcarpeta tools
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


def configure_windows_firewall() -> bool:
    """Habilita la regla de entrada en el Firewall de Windows para los puertos del stack."""
    if sys.platform != "win32":
        return True
    ports = f"5201,{INFLUX_PORT},{GRAFANA_PORT},8000"
    cmd = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name=RedLab Tester Suite ({ports})",
        "dir=in", "action=allow", "protocol=TCP", f"localport={ports}",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return res.returncode == 0
    except Exception:
        return False


def ensure_local_iperf_server() -> bool:
    """Inicia el servidor iperf3 nativo si el puerto 5201 no está ocupado."""
    global iperf_process
    with iperf_lock:
        if is_port_open("127.0.0.1", 5201):
            return True

        iperf_bin = find_iperf3_binary()
        if not iperf_bin:
            return False

        try:
            configure_windows_firewall()
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
        except Exception as e:
            logger.error("Error al arrancar iperf3: %s", e)
            return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida del servidor central."""
    # 1. Configurar firewall de Windows
    configure_windows_firewall()

    # 2. Iniciar iperf3
    ensure_local_iperf_server()

    # 3. Iniciar stack nativo InfluxDB + Grafana si ya están presentes
    if stack_manager.is_stack_installed():
        threading.Thread(target=stack_manager.setup_and_start_all, daemon=True).start()
    else:
        # Descarga e inicialización asistida en segundo plano
        stack_manager.install_native_stack_async()

    yield

    # Limpieza al apagar el servidor
    global iperf_process
    with iperf_lock:
        if iperf_process and iperf_process.poll() is None:
            try:
                iperf_process.terminate()
            except Exception:
                pass

    stack_manager.stop_all()


app = FastAPI(
    title="RedLab Tester",
    description="Servidor central de pruebas de rendimiento de red, telemetría y dashboards.",
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Evita registros 404 para favicon en consola."""
    return Response(status_code=204)


@app.get("/status")
async def status() -> dict[str, Any]:
    """Indica el estado del servidor y puertos clave."""
    info_ips = get_classified_ips()
    return {
        "status": "ready",
        "primary_ip": info_ips["primary"],
        "iperf_port": 5201,
        "iperf_ready": is_port_open("127.0.0.1", 5201),
        "influx_port": INFLUX_PORT,
        "influx_ready": is_port_open("127.0.0.1", INFLUX_PORT),
        "grafana_port": GRAFANA_PORT,
        "grafana_ready": is_port_open("127.0.0.1", GRAFANA_PORT),
        "message": "Servidor central listo para recibir pruebas desde SondaCliente.",
    }


@app.get("/api/version")
async def version() -> dict:
    """Versión instalada y si hay una actualización disponible."""
    return {"version": APP_VERSION, "update": check_for_update()}


@app.get("/api/stack/status")
async def stack_status() -> dict[str, Any]:
    """Devuelve el estado en tiempo real del stack nativo y el progreso de instalación."""
    return {
        "iperf": is_port_open("127.0.0.1", 5201),
        "influx": is_port_open("127.0.0.1", INFLUX_PORT),
        "grafana": is_port_open("127.0.0.1", GRAFANA_PORT),
        "installed": stack_manager.is_stack_installed(),
        "download_status": stack_manager.download_status,
        "download_progress": stack_manager.download_progress,
    }


@app.post("/api/stack/install")
async def stack_install() -> dict[str, str]:
    """Inicia la descarga e instalación automática del stack nativo si no está listo."""
    stack_manager.install_native_stack_async()
    return {"status": "started", "message": "Instalación del stack iniciada en segundo plano."}


@app.get("/api/info")
async def info() -> dict:
    """Información completa de diagnóstico del servidor."""
    info_ips = get_classified_ips()
    return {
        "status": "ready",
        "hostname": platform.node(),
        "primary_ip": info_ips["primary"],
        "all_ips": info_ips["all"],
        "iperf_port": 5201,
        "iperf_active": is_port_open("127.0.0.1", 5201),
        "influx_port": INFLUX_PORT,
        "influx_active": is_port_open("127.0.0.1", INFLUX_PORT),
        "grafana_port": GRAFANA_PORT,
        "grafana_active": is_port_open("127.0.0.1", GRAFANA_PORT),
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Panel de control visual web accesible desde el navegador."""
    info_ips = get_classified_ips()
    primary_ip = info_ips["primary"]
    iperf_ok = is_port_open("127.0.0.1", 5201)
    influx_ok = is_port_open("127.0.0.1", INFLUX_PORT)
    grafana_ok = is_port_open("127.0.0.1", GRAFANA_PORT)

    iperf_badge = (
        '<span class="badge badge-success">● ACTIVO (:5201)</span>'
        if iperf_ok
        else '<span class="badge badge-danger">▲ Inactivo (:5201)</span>'
    )
    influx_badge = (
        '<span class="badge badge-success">● ACTIVO (:4000)</span>'
        if influx_ok
        else '<span class="badge badge-warning">○ En espera / Descargando</span>'
    )
    grafana_badge = (
        '<span class="badge badge-success">● ACTIVO (:7500)</span>'
        if grafana_ok
        else '<span class="badge badge-warning">○ En espera / Descargando</span>'
    )

    ips_html = "".join(
        f'<div class="ip-row {"ip-primary" if item["is_primary"] else ""}">'
        f'  <div>'
        f'    <strong class="ip-addr">{item["ip"]}</strong>'
        f'    <span class="ip-tag">{item["tag"]}</span>'
        f'  </div>'
        f'  <button onclick="copyText(\'{item["ip"]}\', this)" class="btn-copy">Copiar IP</button>'
        f'</div>'
        for item in info_ips["all"]
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RedLab Tester - Servidor Central</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: #0B1120; color: #E2E8F0; padding: 24px 16px; min-height: 100vh; display: flex; justify-content: center; }}
        .container {{ max-width: 860px; width: 100%; }}
        .card {{ background: #131D31; border-radius: 14px; padding: 26px; margin-bottom: 20px; box-shadow: 0 10px 20px rgba(0,0,0,0.5); border: 1px solid #1E293B; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px; }}
        h1 {{ font-size: 1.6rem; color: #F8FAFC; display: flex; align-items: center; gap: 10px; }}
        p.subtitle {{ color: #94A3B8; font-size: 0.95rem; line-height: 1.5; margin-bottom: 20px; }}
        
        .badge {{ padding: 6px 14px; border-radius: 999px; font-weight: 700; font-size: 0.8rem; letter-spacing: 0.5px; }}
        .badge-success {{ background: #10B981; color: #FFFFFF; }}
        .badge-danger {{ background: #EF4444; color: #FFFFFF; }}
        .badge-warning {{ background: #F59E0B; color: #1E293B; }}

        .primary-highlight {{ background: linear-gradient(135deg, #1E3A8A 0%, #0F172A 100%); border: 2px solid #3B82F6; padding: 20px; border-radius: 12px; margin-bottom: 22px; }}
        .primary-highlight h2 {{ font-size: 1.05rem; color: #93C5FD; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
        .primary-ip-box {{ display: flex; justify-content: space-between; align-items: center; background: #0B1120; padding: 14px 18px; border-radius: 8px; border: 1px solid #2563EB; flex-wrap: wrap; gap: 10px; }}
        .primary-ip-val {{ font-family: monospace; font-size: 1.8rem; color: #38BDF8; font-weight: bold; }}
        
        .services-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin-bottom: 24px; }}
        .service-card {{ background: #0F172A; border: 1px solid #334155; padding: 16px; border-radius: 10px; display: flex; flex-direction: column; justify-content: space-between; }}
        .service-title {{ font-size: 1.05rem; font-weight: 600; color: #F1F5F9; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; }}
        .service-desc {{ font-size: 0.85rem; color: #94A3B8; margin-bottom: 12px; }}
        .service-btn {{ display: block; text-align: center; background: #2563EB; color: #FFF; padding: 9px 14px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 600; transition: background 0.2s; border: none; cursor: pointer; }}
        .service-btn:hover {{ background: #1D4ED8; }}
        .service-btn.disabled {{ background: #475569; cursor: not-allowed; opacity: 0.7; }}

        .ip-card {{ background: #0F172A; border: 1px solid #334155; padding: 18px; border-radius: 12px; margin-bottom: 22px; }}
        .ip-row {{ background: #1E293B; border: 1px solid #334155; padding: 12px 16px; border-radius: 8px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }}
        .ip-row.ip-primary {{ border-color: #3B82F6; background: #172554; }}
        .ip-addr {{ font-family: monospace; font-size: 1.15rem; color: #38BDF8; margin-right: 10px; }}
        .ip-tag {{ font-size: 0.8rem; color: #94A3B8; }}
        
        .btn-copy {{ background: #334155; color: #F1F5F9; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.82rem; font-weight: 600; transition: all 0.2s; }}
        .btn-copy:hover {{ background: #2563EB; color: #FFF; }}

        .firewall-box {{ background: #1E293B; border: 1px dashed #F59E0B; padding: 16px; border-radius: 10px; margin-bottom: 20px; }}
        .firewall-box h4 {{ color: #FBBF24; font-size: 0.92rem; margin-bottom: 6px; }}
        .firewall-box p {{ font-size: 0.85rem; color: #CBD5E1; margin-bottom: 8px; line-height: 1.4; }}
        .cmd-snippet {{ background: #0F172A; padding: 10px 14px; border-radius: 6px; font-family: monospace; font-size: 0.82rem; color: #38BDF8; display: flex; justify-content: space-between; align-items: center; overflow-x: auto; }}

        #stack-progress-container {{ display: none; background: #1E293B; border: 1px solid #3B82F6; padding: 14px; border-radius: 8px; margin-bottom: 20px; }}
        .progress-bar-bg {{ background: #0F172A; border-radius: 999px; height: 12px; overflow: hidden; margin-top: 8px; }}
        .progress-bar-fill {{ background: #10B981; height: 100%; width: 0%; transition: width 0.3s; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>📡 RedLab Tester - Servidor Central</h1>
                {iperf_badge}
            </div>
            <p class="subtitle">Este equipo opera como servidor central de mediciones (iPerf3), almacenamiento de telemetría (InfluxDB v2 nativo) y paneles de control (Grafana nativo).</p>

            <!-- IP RECOMENDADA -->
            <div class="primary-highlight">
                <h2>⭐ Introduce esta IP en SondaCliente.exe</h2>
                <div class="primary-ip-box">
                    <div>
                        <div class="primary-ip-val" id="primary-ip">{primary_ip}</div>
                        <div style="font-size: 0.85rem; color: #93C5FD; margin-top: 4px;">IP de Red Local detectada para clientes de la misma red</div>
                    </div>
                    <button onclick="copyText('{primary_ip}', this)" class="btn-copy" style="background:#2563EB; color:#fff; font-size:0.95rem; padding:10px 18px;">📋 Copiar IP</button>
                </div>
            </div>

            <!-- PROGRESO DESCARGA STACK -->
            <div id="stack-progress-container">
                <div style="display:flex; justify-content:space-between; font-size:0.85rem;">
                    <span id="stack-status-text">Descargando Stack Nativo (InfluxDB + Grafana)...</span>
                    <strong id="stack-percent-text">0%</strong>
                </div>
                <div class="progress-bar-bg">
                    <div id="stack-progress-fill" class="progress-bar-fill"></div>
                </div>
            </div>

            <!-- GRID SERVICIOS -->
            <h3 style="color:#CBD5E1; font-size:1.05rem; margin-bottom:12px;">Servicios Integrados</h3>
            <div class="services-grid">
                <!-- iPerf3 -->
                <div class="service-card">
                    <div>
                        <div class="service-title">
                            <span>🚀 iPerf3 Server</span>
                            {iperf_badge}
                        </div>
                        <p class="service-desc">Escuchando en puerto <strong>5201</strong> para pruebas de ancho de banda y pérdida de paquetes.</p>
                    </div>
                    <span style="font-size:0.8rem; color:#64748B;">Listo para conexiones de clientes</span>
                </div>

                <!-- InfluxDB -->
                <div class="service-card">
                    <div>
                        <div class="service-title">
                            <span>🗄️ InfluxDB v2</span>
                            {influx_badge}
                        </div>
                        <p class="service-desc">Puerto <strong>{INFLUX_PORT}</strong>. Base de datos de series temporales para almacenar telemetría de red.</p>
                    </div>
                    <a href="http://localhost:{INFLUX_PORT}" target="_blank" class="service-btn" id="btn-influx">Abrir InfluxDB (:4000)</a>
                </div>

                <!-- Grafana -->
                <div class="service-card">
                    <div>
                        <div class="service-title">
                            <span>📊 Grafana OSS</span>
                            {grafana_badge}
                        </div>
                        <p class="service-desc">Puerto <strong>{GRAFANA_PORT}</strong>. Visualización interactiva y dashboards en tiempo real.</p>
                    </div>
                    <a href="http://localhost:{GRAFANA_PORT}" target="_blank" class="service-btn" id="btn-grafana">Abrir Grafana (:7500)</a>
                </div>
            </div>

            <!-- TODAS LAS IPS DISPONIBLES -->
            <div class="ip-card">
                <h3 style="color:#60A5FA; font-size:0.95rem; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:12px;">
                    Todas las IPs detectadas en este equipo:
                </h3>
                {ips_html}
            </div>

            <!-- FIREWALL HELP -->
            <div class="firewall-box">
                <h4>⚠️ ¿El cliente da timeout o error de conexión desde otro PC?</h4>
                <p>El Firewall de Windows suele bloquear puertos entrantes por defecto. Si la sonda no puede conectar, abre <strong>PowerShell como Administrador</strong> en este equipo y ejecuta:</p>
                <div class="cmd-snippet">
                    <span>netsh advfirewall firewall add rule name="RedLab Suite" dir=in action=allow protocol=TCP localport=5201,4000,7500,8000</span>
                    <button onclick="copyText('netsh advfirewall firewall add rule name=&quot;RedLab Suite&quot; dir=in action=allow protocol=TCP localport=5201,4000,7500,8000', this)" class="btn-copy">Copiar</button>
                </div>
            </div>

            <div style="display:flex; justify-content:space-between; align-items:center; margin-top:20px; font-size:0.85rem; color:#64748B;">
                <span>RedLab Tester Servidor v{APP_VERSION}</span>
                <div>
                    <a href="/docs" target="_blank" style="color:#38BDF8; margin-right:16px; text-decoration:none;">Documentación API</a>
                    <a href="/status" target="_blank" style="color:#38BDF8; text-decoration:none;">Diagnóstico JSON</a>
                </div>
            </div>
        </div>
    </div>

    <script>
        function copyText(text, btn) {{
            navigator.clipboard.writeText(text);
            const original = btn.innerText;
            btn.innerText = '¡Copiado!';
            setTimeout(() => {{ btn.innerText = original; }}, 1500);
        }}

        // Monitoreo dinámico del estado del stack (InfluxDB y Grafana nativos)
        async function checkStackStatus() {{
            try {{
                const res = await fetch('/api/stack/status');
                const data = await res.json();
                
                const progContainer = document.getElementById('stack-progress-container');
                const progText = document.getElementById('stack-status-text');
                const progPct = document.getElementById('stack-percent-text');
                const progFill = document.getElementById('stack-progress-fill');

                const isDownloading = (data.download_status.influxdb === 'downloading' || 
                                       data.download_status.grafana === 'downloading' ||
                                       data.download_status.influxdb === 'extracting' ||
                                       data.download_status.grafana === 'extracting');

                if (isDownloading) {{
                    progContainer.style.display = 'block';
                    const maxPct = Math.max(data.download_progress.influxdb || 0, data.download_progress.grafana || 0);
                    progPct.innerText = Math.round(maxPct) + '%';
                    progFill.style.width = maxPct + '%';
                    progText.innerText = 'Instalando componentes nativos InfluxDB y Grafana... (' + Math.round(maxPct) + '%)';
                }} else if (data.influx && data.grafana) {{
                    progContainer.style.display = 'none';
                }}
            }} catch(e) {{
                console.error(e);
            }}
        }}

        setInterval(checkStackStatus, 3000);
        checkStackStatus();
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    ips_info = get_classified_ips()
    primary = ips_info["primary"]

    # Asegurar regla de firewall e iniciar servicios
    configure_windows_firewall()
    iperf_ready = ensure_local_iperf_server()

    # Iniciar stack nativo InfluxDB + Grafana
    if stack_manager.is_stack_installed():
        stack_manager.setup_and_start_all()
    else:
        stack_manager.install_native_stack_async()

    influx_ready = is_port_open("127.0.0.1", INFLUX_PORT)
    grafana_ready = is_port_open("127.0.0.1", GRAFANA_PORT)

    print("=" * 66)
    print("                 RedLab Tester - Servidor Central")
    print("=" * 66)
    print(f"Versión: {APP_VERSION}")
    _upd = check_for_update()
    if _upd:
        print(f"[ACTUALIZACIÓN] Nueva versión {_upd['version']}: {_upd['url']}")

    print("-" * 66)
    print(f" ⭐ IP RECOMENDADA PARA SondaCliente: {primary}")
    print(f"    (Introduce '{primary}' en el campo IP del cliente)")
    print("-" * 66)
    print("ESTADO DE SERVICIOS NATIVOS:")
    print(f" [●] Servidor iPerf3 : Puerto 5201 {'[OK - ACTIVO]' if iperf_ready else '[INACTIVO]'}")
    print(f" [●] InfluxDB v2     : http://localhost:{INFLUX_PORT} {'[OK]' if influx_ready else '[Iniciando/Descargando...]'}")
    print(f" [●] Grafana         : http://localhost:{GRAFANA_PORT} {'[OK]' if grafana_ready else '[Iniciando/Descargando...]'}")
    print(f" [●] Panel Web       : http://localhost:8000")
    print("-" * 66)
    print("Todas las interfaces detectadas en este servidor:")
    for item in ips_info["all"]:
        mark = "⭐ (Recomendada)" if item["is_primary"] else f"({item['tag']})"
        print(f"  -> {item['ip']:<16} {mark}")
    print("-" * 66)
    print(">> Si el cliente da timeout desde otro PC, ejecuta como Administrador:")
    print('   netsh advfirewall firewall add rule name="RedLab Suite" dir=in action=allow protocol=TCP localport=5201,4000,7500,8000')
    print("=" * 66)

    uvicorn.run(app, host="0.0.0.0", port=8000)
