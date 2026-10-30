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
    else:
        try:
            subprocess.run(["pkill", "-9", "iperf3"], capture_output=True)
        except Exception:
            pass


def restart_local_iperf_server(force: bool = False) -> bool:
    """Inicia o reinicia el servidor iperf3 nativo en el puerto TCP 5201."""
    global iperf_process
    with iperf_lock:
        if not force and iperf_process and iperf_process.poll() is None and is_port_open("127.0.0.1", 5201, timeout=0.5):
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

        # Terminar siempre procesos huérfanos antes de iniciar una nueva instancia limpia
        kill_orphan_iperf_processes()
        time.sleep(0.3)

        iperf_bin = find_iperf3_binary()
        if not iperf_bin:
            logger.error("No se encontró iperf3.exe")
            return False

        try:
            configure_windows_firewall()
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            # Parámetros robustos para iperf3 en Windows:
            # -4: Solo IPv4
            # --idle-timeout 15: Si el servidor queda colgado sin cliente, se reinicia solo tras 15 s
            # --server-max-duration 35: Límite máximo de 35 s por prueba para evitar bloqueos
            iperf_process = subprocess.Popen(
                [
                    str(iperf_bin),
                    "-s",
                    "-p", "5201",
                    "-4",
                    "--idle-timeout", "15",
                    "--server-max-duration", "35",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(iperf_bin.parent),
                creationflags=creationflags,
            )
            time.sleep(0.6)
            active = is_port_open("127.0.0.1", 5201, timeout=1.0)
            if active:
                logger.info("[iPerf3] Servidor activo escuchando en puerto 5201 (IPv4, timeouts configurados).")
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
    """Supervisa que el proceso iPerf3 siga vivo sin interferir en las mediciones activas."""
    global watchdog_running, iperf_process
    while watchdog_running:
        time.sleep(3.0)
        if not watchdog_running:
            break

        # Comprobar estado del proceso sin realizar conexiones de socket intrusivas.
        # En Windows, abrir sockets periódicamente en 5201 colisiona con las mediciones
        # de los clientes y provoca abortos o desconexiones forzadas.
        with iperf_lock:
            proc_alive = (iperf_process is not None and iperf_process.poll() is None)

        if not proc_alive:
            logger.warning("[iPerf3 Watchdog] Proceso iPerf3 terminado o inactivo. Reactivando automáticamente...")
            restart_local_iperf_server(force=True)


user_selected_ip: Optional[str] = None


def get_classified_ips() -> dict[str, Any]:
    """Obtiene y clasifica las IPs locales en Primaria (LAN) y Secundarias (VPN/WSL/Virtuales)."""
    global user_selected_ip
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

    # Si el usuario ha seleccionado una IP específica, esa se convierte en la prioritaria
    if user_selected_ip and user_selected_ip in ips:
        primary_ip = user_selected_ip

    classified: list[dict[str, str]] = []
    for ip in sorted(list(ips)):
        is_primary = (ip == primary_ip)
        notes = []
        if is_primary:
            notes.append("⭐ Seleccionada para Pruebas")
        if ip.startswith("192.168.56."):
            notes.append("VirtualBox Host-Only")
        elif ip.startswith("172.2") or ip.startswith("172.1") or ip.startswith("172.3"):
            notes.append("WSL / Hyper-V Virtual")
        elif ip.startswith("10.") and not is_primary:
            notes.append("VPN / Red Corporativa")
        elif (ip.startswith("192.168.") or ip.startswith("10.")) and not is_primary:
            notes.append("Red Local (LAN)")

        tag = " - ".join(notes) if notes else "Interfaz de red"
        classified.append({"ip": ip, "is_primary": is_primary, "tag": tag})

    # Ordenar poniendo la primaria primero
    classified.sort(key=lambda x: (not x["is_primary"], x["ip"]))
    return {"primary": primary_ip, "selected": user_selected_ip or primary_ip, "all": classified}


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


def get_system_metrics() -> dict[str, Any]:
    """Obtiene métricas de hardware del sistema (temperatura, RAM, disco, uptime)."""
    metrics: dict[str, Any] = {
        "platform": sys.platform,
        "is_rpi": False,
        "rpi_model": None,
        "cpu_temp": None,
        "ram_percent": None,
        "ram_used_mb": None,
        "ram_total_mb": None,
        "disk_free_gb": None,
        "disk_total_gb": None,
        "disk_percent": None,
        "uptime_str": None,
    }

    # Detectar si es Raspberry Pi
    model_path = Path("/sys/firmware/devicetree/base/model")
    if model_path.exists():
        try:
            metrics["rpi_model"] = model_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "").strip()
            metrics["is_rpi"] = True
        except Exception:
            pass

    # Temperatura CPU en Linux/Raspberry Pi
    for t_path in [
        Path("/sys/class/thermal/thermal_zone0/temp"),
        Path("/sys/devices/virtual/thermal/thermal_zone0/temp"),
    ]:
        if t_path.exists():
            try:
                val = int(t_path.read_text().strip())
                metrics["cpu_temp"] = round(val / 1000.0, 1)
                break
            except Exception:
                pass

    # Espacio en Disco / Tarjeta SD
    try:
        check_path = "/" if sys.platform != "win32" else (Path(__file__).resolve().anchor or "C:\\")
        total, used, free = shutil.disk_usage(check_path)
        metrics["disk_total_gb"] = round(total / (1024**3), 1)
        metrics["disk_free_gb"] = round(free / (1024**3), 1)
        metrics["disk_percent"] = round((used / total) * 100, 1)
    except Exception:
        pass

    # Memoria RAM en Linux
    mem_path = Path("/proc/meminfo")
    if mem_path.exists():
        try:
            data = {}
            for line in mem_path.read_text().splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    data[parts[0].strip()] = int(parts[1].replace("kB", "").strip())
            if "MemTotal" in data and "MemAvailable" in data:
                total_kb = data["MemTotal"]
                avail_kb = data["MemAvailable"]
                used_kb = total_kb - avail_kb
                metrics["ram_percent"] = round((used_kb / total_kb) * 100, 1)
                metrics["ram_used_mb"] = round(used_kb / 1024)
                metrics["ram_total_mb"] = round(total_kb / 1024)
        except Exception:
            pass

    # Tiempo de actividad (Uptime)
    uptime_path = Path("/proc/uptime")
    if uptime_path.exists():
        try:
            secs = float(uptime_path.read_text().split()[0])
            mins = int(secs // 60)
            hours = mins // 60
            days = hours // 24
            if days > 0:
                metrics["uptime_str"] = f"{days}d {hours % 24}h {mins % 60}m"
            elif hours > 0:
                metrics["uptime_str"] = f"{hours}h {mins % 60}m"
            else:
                metrics["uptime_str"] = f"{mins} min"
        except Exception:
            pass

    return metrics


def configure_windows_firewall() -> bool:
    """Habilita reglas de entrada en el Firewall de Windows para los puertos del stack y binarios."""
    if sys.platform != "win32":
        return True
    ports = f"5201,{INFLUX_PORT},{GRAFANA_PORT},8000"
    iperf_bin = find_iperf3_binary()
    all_ok = True

    # 1. Regla TCP para todos los perfiles (Dominio, Privada, Pública)
    cmd_tcp = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name=RedLab Suite TCP ({ports})",
        "dir=in", "action=allow", "protocol=TCP", f"localport={ports}",
        "profile=any",
    ]
    # 2. Regla UDP para puerto 5201
    cmd_udp = [
        "netsh", "advfirewall", "firewall", "add", "rule",
        "name=RedLab iPerf3 UDP (5201)",
        "dir=in", "action=allow", "protocol=UDP", "localport=5201",
        "profile=any",
    ]

    for cmd in (cmd_tcp, cmd_udp):
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if res.returncode != 0:
                all_ok = False
        except Exception:
            all_ok = False

    if iperf_bin and iperf_bin.is_file():
        try:
            subprocess.run(
                [
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    "name=RedLab iPerf3 Program",
                    "dir=in", "action=allow", f"program={iperf_bin}",
                    "profile=any",
                ],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception:
            pass

    return all_ok


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida del servidor central."""
    global watchdog_running, iperf_watchdog_thread

    # 1. Configurar firewall de Windows
    configure_windows_firewall()

    # 2. Iniciar iperf3 garantizando instancia supervisada y limpia
    restart_local_iperf_server(force=False)

    # 3. Iniciar vigilancia continua (watchdog) de iperf3 si no está activa
    watchdog_running = True
    if iperf_watchdog_thread is None or not iperf_watchdog_thread.is_alive():
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


@app.post("/api/server/select-ip")
async def api_select_ip(payload: dict) -> dict[str, Any]:
    """Establece la IP seleccionada por el usuario como la IP preferente del servidor."""
    global user_selected_ip
    ip = str(payload.get("ip", "")).strip()
    if ip:
        user_selected_ip = ip
    info = get_classified_ips()
    return {
        "status": "ok",
        "selected_ip": info["selected"],
        "primary_ip": info["primary"],
        "message": f"IP preferente actualizada a {info['selected']}",
    }


@app.get("/api/info")
async def info() -> dict:
    """Información completa de diagnóstico del servidor."""
    info_ips = get_classified_ips()
    metrics = get_system_metrics()
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
        "hardware": metrics,
    }


@app.post("/api/docker/restart")
async def api_docker_restart() -> dict[str, Any]:
    """Reinicia todos los contenedores de Docker (InfluxDB, Grafana, iPerf3)."""
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        app_dir = Path(__file__).resolve().parent
        res = subprocess.run(
            ["docker", "compose", "restart"],
            capture_output=True,
            text=True,
            cwd=str(app_dir),
            timeout=60,
            creationflags=creationflags,
        )
        if res.returncode == 0:
            return {"status": "ok", "message": "Contenedores Docker reiniciados con éxito."}
        else:
            # Intentar reiniciar por nombres directos
            res2 = subprocess.run(
                ["docker", "restart", "redlab-influxdb", "redlab-grafana", "redlab-iperf3"],
                capture_output=True,
                text=True,
                timeout=45,
                creationflags=creationflags,
            )
            if res2.returncode == 0:
                return {"status": "ok", "message": "Contenedores reiniciados correctamente."}
            return {"status": "error", "message": res.stderr.strip() or res.stdout.strip()}
    except Exception as e:
        return {"status": "error", "message": f"Error al reiniciar contenedores: {e}"}


@app.post("/api/system/reboot")
async def api_system_reboot() -> dict[str, Any]:
    """Reinicia de forma segura el host (Raspberry Pi o PC servidor)."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["shutdown", "/r", "/t", "5"])
        else:
            subprocess.Popen(["sudo", "reboot"])
        return {"status": "ok", "message": "Reiniciando el sistema en 5 segundos..."}
    except Exception as e:
        return {"status": "error", "message": f"No se pudo ejecutar el reinicio: {e}"}


@app.get("/api/system/metrics")
async def api_system_metrics() -> dict[str, Any]:
    """Devuelve las métricas de hardware de la Raspberry Pi / PC."""
    return {"status": "ok", "metrics": get_system_metrics()}


@app.get("/api/admin/config")
async def api_get_admin_config() -> dict[str, Any]:
    """Devuelve la configuración actual de credenciales, puertos y red."""
    app_dir = Path(__file__).resolve().parent
    env_file = app_dir / ".env"
    env_vars: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    ips = get_classified_ips()
    metrics = get_system_metrics()
    return {
        "status": "ok",
        "influx_org": env_vars.get("INFLUX_ORG", INFLUX_ORG),
        "influx_bucket": env_vars.get("INFLUX_BUCKET", INFLUX_BUCKET),
        "influx_token": env_vars.get("INFLUX_TOKEN", INFLUX_TOKEN),
        "influx_port": env_vars.get("INFLUX_PORT", str(INFLUX_PORT)),
        "grafana_port": env_vars.get("GRAFANA_PORT", str(GRAFANA_PORT)),
        "grafana_user": env_vars.get("GRAFANA_USER", GRAFANA_USER),
        "grafana_password": env_vars.get("GRAFANA_PASSWORD", GRAFANA_PASSWORD),
        "iperf_port": 5201,
        "selected_ip": ips["selected"],
        "primary_ip": ips["primary"],
        "all_ips": ips["all"],
        "hardware": metrics,
    }


@app.post("/api/admin/config")
async def api_save_admin_config(payload: dict) -> dict[str, Any]:
    """Guarda las nuevas credenciales y configuración en el archivo .env."""
    global user_selected_ip
    try:
        app_dir = Path(__file__).resolve().parent
        env_file = app_dir / ".env"

        # Actualizar IP preferente si se envió
        new_ip = str(payload.get("selected_ip", "")).strip()
        if new_ip:
            user_selected_ip = new_ip

        # Leer o construir .env
        org = str(payload.get("influx_org", INFLUX_ORG)).strip()
        bucket = str(payload.get("influx_bucket", INFLUX_BUCKET)).strip()
        token = str(payload.get("influx_token", INFLUX_TOKEN)).strip()
        g_user = str(payload.get("grafana_user", GRAFANA_USER)).strip()
        g_pass = str(payload.get("grafana_password", GRAFANA_PASSWORD)).strip()

        env_content = f"""# Configuración de RedLab Tester
INFLUX_PORT={INFLUX_PORT}
GRAFANA_PORT={GRAFANA_PORT}
INFLUX_ORG={org}
INFLUX_BUCKET={bucket}
INFLUX_TOKEN={token}
GRAFANA_USER={g_user}
GRAFANA_PASSWORD={g_pass}
SELECTED_SERVER_IP={user_selected_ip or ''}
"""
        env_file.write_text(env_content, encoding="utf-8")
        return {"status": "ok", "message": "Configuración guardada correctamente en .env."}
    except Exception as e:
        return {"status": "error", "message": f"Error al guardar configuración: {e}"}


@app.get("/admin", response_class=HTMLResponse)
@app.get("/setup", response_class=HTMLResponse)
async def admin_dashboard() -> str:
    """Panel de configuración administrativo (Setup-Admin UI)."""
    info_ips = get_classified_ips()
    primary_ip = info_ips["primary"]
    metrics = get_system_metrics()

    iperf_ok = is_port_open("127.0.0.1", 5201)
    influx_ok = is_port_open("127.0.0.1", INFLUX_PORT)
    grafana_ok = is_port_open("127.0.0.1", GRAFANA_PORT)

    hw_info = metrics.get("rpi_model") or platform.machine()
    temp_val = f"{metrics['cpu_temp']} °C" if metrics.get("cpu_temp") else "N/D"
    ram_val = f"{metrics.get('ram_percent', '--')}% ({metrics.get('ram_used_mb', 0)} / {metrics.get('ram_total_mb', 0)} MB)" if metrics.get("ram_percent") else "N/D"
    disk_val = f"{metrics.get('disk_free_gb', '--')} GB libres de {metrics.get('disk_total_gb', '--')} GB ({metrics.get('disk_percent', '--')}% usado)"
    uptime_val = metrics.get("uptime_str") or "Desconocido"

    ip_options_html = ""
    for item in info_ips["all"]:
        is_sel = item["ip"] == primary_ip
        ip_options_html += f'<option value="{item["ip"]}" {"selected" if is_sel else ""}>{item["ip"]}  ({item["tag"]})</option>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RedLab Tester - Setup & Admin UI</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: #0B1120; color: #E2E8F0; padding: 24px 16px; min-height: 100vh; display: flex; justify-content: center; }}
        .container {{ max-width: 900px; width: 100%; }}
        .card {{ background: #131D31; border-radius: 14px; padding: 24px; margin-bottom: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #1E293B; }}
        
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }}
        h1 {{ font-size: 1.5rem; color: #F8FAFC; display: flex; align-items: center; gap: 10px; }}
        .nav-links {{ display: flex; gap: 10px; }}
        .nav-btn {{ background: #1E293B; color: #38BDF8; padding: 8px 14px; border-radius: 8px; text-decoration: none; font-size: 0.85rem; font-weight: 600; border: 1px solid #334155; transition: all 0.2s; }}
        .nav-btn:hover {{ background: #2563EB; color: #FFF; border-color: #2563EB; }}
        
        .section-title {{ font-size: 1.1rem; color: #93C5FD; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid #1E293B; padding-bottom: 8px; }}
        
        .grid-2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 16px; }}
        .form-group {{ margin-bottom: 14px; }}
        .form-group label {{ display: block; font-size: 0.82rem; font-weight: 600; color: #CBD5E1; margin-bottom: 5px; }}
        .form-control {{ width: 100%; background: #0F172A; border: 1px solid #334155; color: #F1F5F9; padding: 9px 12px; border-radius: 6px; font-size: 0.9rem; }}
        .form-control:focus {{ outline: none; border-color: #3B82F6; }}
        
        .btn {{ padding: 10px 18px; border-radius: 8px; font-size: 0.9rem; font-weight: 600; cursor: pointer; border: none; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }}
        .btn-primary {{ background: #2563EB; color: #FFF; }}
        .btn-primary:hover {{ background: #1D4ED8; }}
        .btn-success {{ background: #10B981; color: #FFF; }}
        .btn-success:hover {{ background: #059669; }}
        .btn-warning {{ background: #F59E0B; color: #1E293B; }}
        .btn-warning:hover {{ background: #D97706; }}
        .btn-danger {{ background: #EF4444; color: #FFF; }}
        .btn-danger:hover {{ background: #DC2626; }}

        .status-badge {{ padding: 4px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 700; }}
        .badge-ok {{ background: #10B981; color: #FFF; }}
        .badge-err {{ background: #EF4444; color: #FFF; }}
        
        .hardware-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
        .hw-card {{ background: #0F172A; border: 1px solid #1E293B; padding: 14px; border-radius: 8px; }}
        .hw-label {{ font-size: 0.75rem; color: #94A3B8; text-transform: uppercase; margin-bottom: 4px; }}
        .hw-val {{ font-size: 1.15rem; font-weight: bold; color: #38BDF8; font-family: monospace; }}
        
        .alert {{ padding: 12px 16px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 14px; display: none; }}
        .alert-success {{ background: rgba(16, 185, 129, 0.2); border: 1px solid #10B981; color: #6EE7B7; }}
        .alert-error {{ background: rgba(239, 68, 68, 0.2); border: 1px solid #EF4444; color: #FCA5A5; }}
        
        .hint-box {{ background: #1E293B; border-left: 4px solid #3B82F6; padding: 12px 16px; border-radius: 0 8px 8px 0; font-size: 0.84rem; color: #CBD5E1; margin-bottom: 14px; line-height: 1.5; }}
    </style>
</head>
<body>
    <div class="container">
        <!-- HEADER -->
        <div class="card">
            <div class="header">
                <div>
                    <h1>⚙️ RedLab Tester - Setup & Admin UI</h1>
                    <p style="color:#94A3B8; font-size:0.85rem; margin-top:4px;">Configuración de credenciales, selección de IP de pruebas, enlace de red y control de contenedores.</p>
                </div>
                <div class="nav-links">
                    <a href="/" class="nav-btn">📊 Dashboard</a>
                    <a href="http://{primary_ip}:{GRAFANA_PORT}" target="_blank" class="nav-btn">📈 Grafana</a>
                    <a href="http://{primary_ip}:{INFLUX_PORT}" target="_blank" class="nav-btn">🗄️ InfluxDB</a>
                </div>
            </div>
            <div id="alert-box" class="alert"></div>
        </div>

        <!-- SELECCION DE IP DE PRUEBAS & RED -->
        <div class="card">
            <div class="section-title">🌐 Configuración de Red e IP de Pruebas</div>
            <div class="hint-box">
                <strong>¿Cómo conectar con la Raspberry Pi o Servidor?</strong><br>
                • <strong>DHCP Automático:</strong> Conecta a tu switch/router y accede por la IP asignada o por nombre <code>http://redlab.local:8000</code><br>
                • <strong>Enlace Fijo Directo (Fallback sin router):</strong> Conecta un cable de red directo del PC a la Raspberry Pi. Siempre responderá en <code>http://192.168.1.200:8000</code>
            </div>

            <div class="grid-2">
                <div class="form-group">
                    <label>IP Seleccionada para que midan las sondas:</label>
                    <select id="sel-ip" class="form-control">
                        {ip_options_html}
                    </select>
                </div>
                <div class="form-group">
                    <label>Enlace Fijo de Respaldo (Fallback):</label>
                    <input type="text" class="form-control" value="192.168.1.200/24 (eth0:0)" readonly style="color:#94A3B8; background:#1E293B;">
                </div>
            </div>
            <button onclick="applySelectedIp()" class="btn btn-primary">✅ Aplicar IP de Pruebas</button>
        </div>

        <!-- CREDENCIALES DEL SISTEMA (.ENV) -->
        <div class="card">
            <div class="section-title">🔐 Credenciales de Base de Datos y Dashboards (.env)</div>
            <div class="grid-2">
                <div class="form-group">
                    <label>InfluxDB Organización:</label>
                    <input type="text" id="cfg-org" class="form-control" value="{INFLUX_ORG}">
                </div>
                <div class="form-group">
                    <label>InfluxDB Bucket de Telemetría:</label>
                    <input type="text" id="cfg-bucket" class="form-control" value="{INFLUX_BUCKET}">
                </div>
                <div class="form-group" style="grid-column: 1 / -1;">
                    <label>InfluxDB Admin Token Seguro:</label>
                    <input type="text" id="cfg-token" class="form-control" value="{INFLUX_TOKEN}" style="font-family:monospace;">
                </div>
                <div class="form-group">
                    <label>Grafana Usuario Administrador:</label>
                    <input type="text" id="cfg-guser" class="form-control" value="{GRAFANA_USER}">
                </div>
                <div class="form-group">
                    <label>Grafana Contraseña Administrador:</label>
                    <input type="password" id="cfg-gpass" class="form-control" value="{GRAFANA_PASSWORD}">
                </div>
            </div>
            <button onclick="saveCredentials()" class="btn btn-success">💾 Guardar Credenciales en .env</button>
        </div>

        <!-- ESTADO Y CONTROL DE CONTENEDORES DOCKER -->
        <div class="card">
            <div class="section-title">🐳 Servicios Integrados & Contenedores Docker</div>
            <p style="font-size:0.85rem; color:#94A3B8; margin-bottom:14px;">Todos los contenedores están configurados con la política <code>restart: always</code> para reiniciarse automáticamente tras cortes de energía o fallos.</p>
            
            <div class="hardware-grid" style="margin-bottom:16px;">
                <div class="hw-card">
                    <div class="hw-label">Servidor iPerf3 (:5201)</div>
                    <div class="hw-val"><span id="badge-iperf" class="status-badge {'badge-ok' if iperf_ok else 'badge-err'}">{'ACTIVO' if iperf_ok else 'INACTIVO'}</span></div>
                </div>
                <div class="hw-card">
                    <div class="hw-label">InfluxDB v2 (:{INFLUX_PORT})</div>
                    <div class="hw-val"><span id="badge-influx" class="status-badge {'badge-ok' if influx_ok else 'badge-err'}">{'ACTIVO' if influx_ok else 'INACTIVO'}</span></div>
                </div>
                <div class="hw-card">
                    <div class="hw-label">Grafana (:{GRAFANA_PORT})</div>
                    <div class="hw-val"><span id="badge-grafana" class="status-badge {'badge-ok' if grafana_ok else 'badge-err'}">{'ACTIVO' if grafana_ok else 'INACTIVO'}</span></div>
                </div>
                <div class="hw-card">
                    <div class="hw-label">Panel Servidor (:8000)</div>
                    <div class="hw-val"><span class="status-badge badge-ok">ACTIVO</span></div>
                </div>
            </div>

            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <button onclick="restartDockerContainers()" class="btn btn-warning" id="btn-restart-docker">🔄 Reiniciar Contenedores Docker</button>
                <button onclick="restartIperfServer()" class="btn btn-primary" id="btn-restart-iperf">🔄 Reactivar iPerf3 (:5201)</button>
            </div>
        </div>

        <!-- HARDWARE & SISTEMA -->
        <div class="card">
            <div class="section-title">⚡ Telemetría de Hardware del Servidor / Raspberry Pi</div>
            <div class="hardware-grid" style="margin-bottom:16px;">
                <div class="hw-card">
                    <div class="hw-label">Dispositivo / Modelo</div>
                    <div class="hw-val" style="font-size:0.95rem;">{hw_info}</div>
                </div>
                <div class="hw-card">
                    <div class="hw-label">Temperatura CPU</div>
                    <div class="hw-val" id="hw-temp">{temp_val}</div>
                </div>
                <div class="hw-card">
                    <div class="hw-label">Memoria RAM</div>
                    <div class="hw-val" style="font-size:0.95rem;" id="hw-ram">{ram_val}</div>
                </div>
                <div class="hw-card">
                    <div class="hw-label">Espacio en Disco</div>
                    <div class="hw-val" style="font-size:0.95rem;" id="hw-disk">{disk_val}</div>
                </div>
                <div class="hw-card" style="grid-column: 1 / -1;">
                    <div class="hw-label">Tiempo Activo (Uptime)</div>
                    <div class="hw-val" style="font-size:1rem;" id="hw-uptime">{uptime_val}</div>
                </div>
            </div>

            <button onclick="rebootSystem()" class="btn btn-danger">⚡ Reiniciar Raspberry Pi / Host</button>
        </div>
    </div>

    <script>
        function showAlert(msg, isSuccess = true) {{
            const box = document.getElementById('alert-box');
            box.className = 'alert ' + (isSuccess ? 'alert-success' : 'alert-error');
            box.innerText = msg;
            box.style.display = 'block';
            setTimeout(() => {{ box.style.display = 'none'; }}, 4000);
        }}

        async function applySelectedIp() {{
            const sel = document.getElementById('sel-ip').value;
            try {{
                const res = await fetch('/api/server/select-ip', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ ip: sel }})
                }});
                const data = await res.json();
                if (data.status === 'ok') {{
                    showAlert(data.message, true);
                }} else {{
                    showAlert('Error: ' + data.message, false);
                }}
            }} catch(e) {{
                showAlert('Fallo de red: ' + e, false);
            }}
        }}

        async function saveCredentials() {{
            const payload = {{
                influx_org: document.getElementById('cfg-org').value,
                influx_bucket: document.getElementById('cfg-bucket').value,
                influx_token: document.getElementById('cfg-token').value,
                grafana_user: document.getElementById('cfg-guser').value,
                grafana_password: document.getElementById('cfg-gpass').value,
                selected_ip: document.getElementById('sel-ip').value
            }};
            try {{
                const res = await fetch('/api/admin/config', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                const data = await res.json();
                if (data.status === 'ok') {{
                    showAlert(data.message, true);
                }} else {{
                    showAlert('Error: ' + data.message, false);
                }}
            }} catch(e) {{
                showAlert('Error al guardar credenciales: ' + e, false);
            }}
        }}

        async function restartDockerContainers() {{
            const btn = document.getElementById('btn-restart-docker');
            btn.disabled = true;
            btn.innerText = 'Reiniciando...';
            try {{
                const res = await fetch('/api/docker/restart', {{ method: 'POST' }});
                const data = await res.json();
                showAlert(data.message, data.status === 'ok');
            }} catch(e) {{
                showAlert('Error: ' + e, false);
            }} finally {{
                btn.disabled = false;
                btn.innerText = '🔄 Reiniciar Contenedores Docker';
            }}
        }}

        async function restartIperfServer() {{
            const btn = document.getElementById('btn-restart-iperf');
            btn.disabled = true;
            btn.innerText = 'Reactivando...';
            try {{
                const res = await fetch('/api/iperf/restart', {{ method: 'POST' }});
                const data = await res.json();
                showAlert(data.message, data.status === 'ok');
            }} catch(e) {{
                showAlert('Error: ' + e, false);
            }} finally {{
                btn.disabled = false;
                btn.innerText = '🔄 Reactivar iPerf3 (:5201)';
            }}
        }}

        async function rebootSystem() {{
            if (!confirm('¿Estás seguro de que deseas reiniciar la Raspberry Pi / Servidor?')) return;
            try {{
                const res = await fetch('/api/system/reboot', {{ method: 'POST' }});
                const data = await res.json();
                showAlert(data.message, true);
            }} catch(e) {{
                showAlert('Error: ' + e, false);
            }}
        }}
    </script>
</body>
</html>
"""


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

    ip_selector_pills = "".join(
        f'<button onclick="selectServerIp(\'{item["ip"]}\')" class="btn-copy" style="background:{"#2563EB" if item["is_primary"] else "#1E293B"}; color:#FFF; margin:2px; padding:5px 12px; border:1px solid {"#60A5FA" if item["is_primary"] else "#475569"}; font-size:0.8rem;">'
        f'{"● " if item["is_primary"] else ""}{item["ip"]} ({item["tag"].split(" - ")[0]})'
        f'</button>'
        for item in info_ips["all"]
    )

    ips_html = "".join(
        f'<div class="ip-row {"ip-primary" if item["is_primary"] else ""}">'
        f'  <div>'
        f'    <strong class="ip-addr">{item["ip"]}</strong>'
        f'    <span class="ip-tag">{item["tag"]}</span>'
        f'  </div>'
        f'  <div style="display:flex; gap:6px;">'
        f'    <button onclick="selectServerIp(\'{item["ip"]}\')" class="btn-copy" style="background:#2563EB;">Seleccionar</button>'
        f'    <button onclick="copyText(\'{item["ip"]}\', this)" class="btn-copy">Copiar</button>'
        f'  </div>'
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
                <div style="display:flex; gap:10px; align-items:center;">
                    <a href="/admin" class="service-btn" style="background:#4F46E5; padding:7px 14px; text-decoration:none;">⚙️ Setup / Admin</a>
                    {iperf_badge}
                </div>
            </div>
            <p class="subtitle">Este equipo opera como servidor central de mediciones (iPerf3), almacenamiento de telemetría (InfluxDB v2 nativo) y paneles de control (Grafana nativo).</p>

            <!-- IP RECOMENDADA / SELECCIONADA -->
            <div class="primary-highlight">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-bottom:8px;">
                    <h2>⭐ IP ACTIVA PARA SONDA CLIENTE</h2>
                    <span style="font-size:0.8rem; color:#93C5FD;">(Haz clic en cualquier IP abajo para cambiar la IP de pruebas)</span>
                </div>
                <div class="primary-ip-box">
                    <div>
                        <div class="primary-ip-val" id="primary-ip">{primary_ip}</div>
                        <div style="font-size: 0.85rem; color: #93C5FD; margin-top: 4px;">IP actual seleccionada para que las sondas cliente ejecuten la medición</div>
                    </div>
                    <div style="display:flex; gap:8px; flex-wrap:wrap;">
                        <button onclick="copyText('{primary_ip}', this)" class="btn-copy" style="background:#2563EB; color:#fff; font-size:0.95rem; padding:10px 18px;">📋 Copiar IP</button>
                        <button onclick="copyClientEnv('{primary_ip}', this)" class="btn-copy" style="background:#10B981; color:#fff; font-size:0.9rem; padding:10px 14px;">💾 Copiar Config .env</button>
                    </div>
                </div>

                <div style="margin-top:12px; padding-top:10px; border-top:1px solid rgba(59,130,246,0.3); display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
                    <span style="font-size:0.82rem; color:#CBD5E1; margin-right:4px;">Cambiar IP de pruebas:</span>
                    {ip_selector_pills}
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
                <p>El Firewall de Windows suele bloquear puertos entrantes por defecto en redes públicas o VPNs. Ejecuta el archivo <strong>abrir_firewall.bat como Administrador</strong> o abre <strong>PowerShell como Administrador</strong> y ejecuta:</p>
                <div class="cmd-snippet">
                    <span>netsh advfirewall firewall add rule name="RedLab Suite" dir=in action=allow protocol=TCP localport=5201,4000,7500,8000 profile=any</span>
                    <button onclick="copyText('netsh advfirewall firewall add rule name=&quot;RedLab Suite&quot; dir=in action=allow protocol=TCP localport=5201,4000,7500,8000 profile=any', this)" class="btn-copy">Copiar</button>
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

        async function selectServerIp(ip) {{
            try {{
                const res = await fetch('/api/server/select-ip', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ ip: ip }})
                }});
                const data = await res.json();
                if (data.status === 'ok') {{
                    window.location.reload();
                }}
            }} catch(e) {{
                alert('Error al seleccionar IP: ' + e);
            }}
        }}

        function copyClientEnv(ip, btn) {{
            const text = "IPERF_SERVER_IP=" + ip + "\\nIPERF_PORT=5201\\nINFLUX_URL=http://" + ip + ":4000\\nINFLUX_ORG=red_lab\\nINFLUX_BUCKET=iperf_stats\\nINFLUX_TOKEN=lab_token_secure\\n";
            copyText(text, btn);
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
    iperf_ready = restart_local_iperf_server(force=True)

    # Iniciar supervisión continua (watchdog) de iPerf3 si no está activa
    watchdog_running = True
    if iperf_watchdog_thread is None or not iperf_watchdog_thread.is_alive():
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
    print(">> Si el cliente da timeout desde otro PC, ejecuta 'abrir_firewall.bat' o:")
    print('   netsh advfirewall firewall add rule name="RedLab Suite" dir=in action=allow protocol=TCP localport=5201,4000,7500,8000 profile=any')
    print("=" * 66)

    uvicorn.run(app, host="0.0.0.0", port=8000)
