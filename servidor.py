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
watchdog_running = True
iperf_watchdog_thread: Optional[threading.Thread] = None


def kill_orphan_iperf_processes() -> None:
    """Termina de forma forzada cualquier proceso iperf3 residual o colgado."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "iperf3.exe"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass


def restart_local_iperf_server(force: bool = False) -> bool:
    """Inicia o reinicia el servidor iperf3 nativo en el puerto TCP 5201."""
    global iperf_process
    with iperf_lock:
        if not force and is_port_open("127.0.0.1", 5201):
            return True

        if iperf_process and iperf_process.poll() is None:
            try:
                iperf_process.terminate()
                iperf_process.wait(timeout=1.0)
            except Exception:
                try:
                    iperf_process.kill()
                except Exception:
                    pass

        if force:
            kill_orphan_iperf_processes()
            time.sleep(0.4)

        iperf_bin = find_iperf3_binary()
        if not iperf_bin:
            logger.error("No se encontró iperf3.exe")
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
            active = is_port_open("127.0.0.1", 5201)
            if active:
                logger.info("[iPerf3] Servidor activo escuchando en puerto 5201.")
            else:
                logger.warning("[iPerf3] Proceso iniciado pero puerto 5201 aún no responde.")
            return active
        except Exception as e:
            logger.error("Error al iniciar iperf3: %s", e)
            return False


def ensure_local_iperf_server() -> bool:
    """Asegura que el servidor iperf3 esté en ejecución."""
    return restart_local_iperf_server(force=False)


def iperf_watchdog_loop() -> None:
    """Vigila continuamente el puerto 5201 y lo reactiva automáticamente si se cae."""
    while watchdog_running:
        time.sleep(2.5)
        if not watchdog_running:
            break
        # Si el puerto 5201 deja de responder o el proceso murió, reiniciar inmediatamente
        if not is_port_open("127.0.0.1", 5201):
            logger.warning("[iPerf3 Watchdog] Puerto 5201 caído o inactivo. Reactivando automáticamente...")
            restart_local_iperf_server(force=True)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida del servidor central."""
    global watchdog_running, iperf_watchdog_thread

    # 1. Configurar firewall de Windows
    configure_windows_firewall()

    # 2. Iniciar iperf3
    restart_local_iperf_server(force=False)

    # 3. Iniciar vigilancia continua (watchdog) de iperf3
    watchdog_running = True
    iperf_watchdog_thread = threading.Thread(target=iperf_watchdog_loop, daemon=True)
    iperf_watchdog_thread.start()

    # 4. Iniciar stack nativo InfluxDB + Grafana si ya están presentes
    if stack_manager.is_stack_installed():
        threading.Thread(target=stack_manager.setup_and_start_all, daemon=True).start()
    else:
        # Descarga e inicialización asistida en segundo plano
        stack_manager.install_native_stack_async()

    yield

    # Limpieza al apagar el servidor
    watchdog_running = False
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


@app.post("/api/iperf/restart")
async def api_iperf_restart() -> dict[str, Any]:
    """Reinicia forzosamente el servidor iPerf3 en el puerto 5201 y limpia procesos huérfanos."""
    ok = restart_local_iperf_server(force=True)
    return {
        "status": "ok" if ok else "error",
        "port": 5201,
        "active": ok,
        "message": "Servidor iPerf3 activado y escuchando en el puerto 5201." if ok else "No se pudo reactivar el puerto 5201. Verifica que no esté ocupado por otra aplicación.",
    }


@app.post("/api/iperf/start")
async def api_iperf_start() -> dict[str, Any]:
    """Inicia el servidor iPerf3 si está inactivo."""
    ok = restart_local_iperf_server(force=False)
    return {"status": "ok" if ok else "error", "port": 5201, "active": ok}


@app.post("/api/iperf/stop")
async def api_iperf_stop() -> dict[str, Any]:
    """Detiene el servidor iPerf3."""
    global iperf_process
    with iperf_lock:
        if iperf_process and iperf_process.poll() is None:
            try:
                iperf_process.terminate()
            except Exception:
                pass
    kill_orphan_iperf_processes()
    return {"status": "ok", "port": 5201, "active": is_port_open("127.0.0.1", 5201)}


@app.post("/api/docker/start")
async def api_docker_start() -> dict[str, Any]:
    """Intenta levantar los contenedores de Docker (InfluxDB y Grafana)."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        test = subprocess.run(["docker", "info"], capture_output=True, timeout=6, creationflags=creationflags)
        if test.returncode != 0:
            return {
                "status": "error",
                "message": "Docker Desktop no está en ejecución. Por favor abre Docker Desktop en Windows y espera a que inicie el motor.",
            }

        app_dir = Path(__file__).resolve().parent
        res = subprocess.run(
            ["docker", "compose", "up", "-d", "influxdb", "grafana"],
            capture_output=True,
            text=True,
            cwd=str(app_dir),
            timeout=120,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            return {"status": "ok", "message": "Contenedores Docker iniciados correctamente en puertos 4000 y 7500."}
        else:
            return {"status": "error", "message": f"Docker error: {res.stderr.strip() or res.stdout.strip()}"}
    except Exception as e:
        return {"status": "error", "message": f"Error al ejecutar docker compose: {e}"}


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
                <div class="service-card" id="card-iperf">
                    <div>
                        <div class="service-title">
                            <span>🚀 iPerf3 Server</span>
                            <span id="badge-iperf">{iperf_badge}</span>
                        </div>
                        <p class="service-desc">Puerto <strong>5201</strong> (TCP/UDP). Recibe las mediciones de velocidad desde las sondas cliente.</p>
                        <div style="font-size:0.8rem; color:#94A3B8; margin-bottom:12px;">
                            Supervisión: <strong style="color:#10B981;">Auto-recuperación activa</strong> (se reactiva solo si se cierra)
                        </div>
                    </div>
                    <button onclick="restartIperf(this)" class="service-btn" id="btn-restart-iperf" style="background:#2563EB;">
                        🔄 Reactivar / Reiniciar Puerto 5201
                    </button>
                </div>

                <!-- InfluxDB -->
                <div class="service-card">
                    <div>
                        <div class="service-title">
                            <span>🗄️ InfluxDB v2</span>
                            <span id="badge-influx">{influx_badge}</span>
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
                            <span id="badge-grafana">{grafana_badge}</span>
                        </div>
                        <p class="service-desc">Puerto <strong>{GRAFANA_PORT}</strong>. Visualización interactiva y dashboards en tiempo real.</p>
                    </div>
                    <a href="http://localhost:{GRAFANA_PORT}" target="_blank" class="service-btn" id="btn-grafana">Abrir Grafana (:7500)</a>
                </div>
            </div>

            <!-- ACCIONES PARA INICIAR EL STACK DE DATOS -->
            <div style="background:#1E293B; border:1px solid #334155; padding:16px 20px; border-radius:10px; margin-bottom:22px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <strong style="color:#F1F5F9; font-size:0.95rem;">¿InfluxDB o Grafana están inactivos?</strong>
                    <p style="font-size:0.82rem; color:#94A3B8; margin-top:2px;">Elige cómo quieres iniciarlos (vía Docker o nativo independiente):</p>
                </div>
                <div style="display:flex; gap:10px; flex-wrap:wrap;">
                    <button onclick="startDockerStack(this)" class="btn-copy" style="background:#0284C7; color:#FFF; padding:9px 16px;">🐳 Iniciar con Docker</button>
                    <button onclick="startNativeStack(this)" class="btn-copy" style="background:#10B981; color:#FFF; padding:9px 16px;">⚡ Iniciar Stack Nativo</button>
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

        async function restartIperf(btn) {{
            const originalText = btn.innerText;
            btn.disabled = true;
            btn.innerText = '⏳ Reactivando puerto 5201...';
            try {{
                const res = await fetch('/api/iperf/restart', {{ method: 'POST' }});
                const data = await res.json();
                if (data.active) {{
                    btn.innerText = '✅ Puerto 5201 Activo';
                    btn.style.background = '#10B981';
                }} else {{
                    alert(data.message || 'Error al reactivar el puerto 5201.');
                }}
            }} catch(e) {{
                alert('Error al comunicar con el servidor: ' + e);
            }} finally {{
                setTimeout(() => {{
                    btn.innerText = originalText;
                    btn.style.background = '#2563EB';
                    btn.disabled = false;
                    checkStackStatus();
                }}, 1500);
            }}
        }}

        async function startDockerStack(btn) {{
            const originalText = btn.innerText;
            btn.disabled = true;
            btn.innerText = '⏳ Iniciando Docker...';
            try {{
                const res = await fetch('/api/docker/start', {{ method: 'POST' }});
                const data = await res.json();
                alert(data.message);
                checkStackStatus();
            }} catch(e) {{
                alert('Error al comunicar con Docker: ' + e);
            }} finally {{
                btn.innerText = originalText;
                btn.disabled = false;
            }}
        }}

        async function startNativeStack(btn) {{
            const originalText = btn.innerText;
            btn.disabled = true;
            btn.innerText = '⏳ Iniciando Nativo...';
            try {{
                const res = await fetch('/api/stack/install', {{ method: 'POST' }});
                const data = await res.json();
                alert(data.message || 'Instalación de stack nativo iniciada.');
                checkStackStatus();
            }} catch(e) {{
                alert('Error al iniciar stack nativo: ' + e);
            }} finally {{
                btn.innerText = originalText;
                btn.disabled = false;
            }}
        }}

        // Monitoreo dinámico del estado del stack (iPerf3, InfluxDB y Grafana)
        async function checkStackStatus() {{
            try {{
                const res = await fetch('/api/stack/status');
                const data = await res.json();
                
                // Actualizar Badge iPerf3
                const badgeIperf = document.getElementById('badge-iperf');
                const btnIperf = document.getElementById('btn-restart-iperf');
                if (badgeIperf) {{
                    if (data.iperf) {{
                        badgeIperf.innerHTML = '<span class="badge badge-success">● ACTIVO (:5201)</span>';
                        if (btnIperf && !btnIperf.disabled) {{
                            btnIperf.style.background = '#2563EB';
                            btnIperf.innerText = '🔄 Reiniciar Puerto 5201';
                        }}
                    }} else {{
                        badgeIperf.innerHTML = '<span class="badge badge-danger">▲ INACTIVO (:5201)</span>';
                        if (btnIperf && !btnIperf.disabled) {{
                            btnIperf.style.background = '#DC2626';
                            btnIperf.innerText = '▶ Activar Puerto 5201 Ahora';
                        }}
                    }}
                }}

                // Actualizar Badges InfluxDB y Grafana
                const badgeInflux = document.getElementById('badge-influx');
                if (badgeInflux) {{
                    badgeInflux.innerHTML = data.influx ? '<span class="badge badge-success">● ACTIVO (:4000)</span>' : '<span class="badge badge-warning">○ Inactivo (:4000)</span>';
                }}
                const badgeGrafana = document.getElementById('badge-grafana');
                if (badgeGrafana) {{
                    badgeGrafana.innerHTML = data.grafana ? '<span class="badge badge-success">● ACTIVO (:7500)</span>' : '<span class="badge badge-warning">○ Inactivo (:7500)</span>';
                }}

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

        setInterval(checkStackStatus, 2500);
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
    iperf_ready = restart_local_iperf_server(force=False)

    # Iniciar supervisión continua (watchdog) de iPerf3
    watchdog_running = True
    iperf_watchdog_thread = threading.Thread(target=iperf_watchdog_loop, daemon=True)
    iperf_watchdog_thread.start()

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
    print(f"     Supervisión     : [Watchdog Activo - Auto-recuperación]")
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
