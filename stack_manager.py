"""Gestor del stack nativo de InfluxDB y Grafana para Windows sin Docker.

Descarga, aprovisiona, inicia y detiene InfluxDB 2.x (puerto 4000) y Grafana (puerto 7500)
de forma 100% nativa y transparente en Windows.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("RedLab.Stack")

INFLUX_PORT = 4000
GRAFANA_PORT = 7500

INFLUX_ORG = "red_lab"
INFLUX_BUCKET = "iperf_stats"
INFLUX_TOKEN = "lab_token_secure"
INFLUX_USER = "redlab"
INFLUX_PASSWORD = "redlab_change_me"

GRAFANA_USER = "admin"
GRAFANA_PASSWORD = "admin_change_me"

# URLs oficiales directas de binarios portables para Windows 64-bit
INFLUX_ZIP_URL = "https://dl.influxdata.com/influxdb/releases/influxdb2-2.7.11-windows.zip"
GRAFANA_ZIP_URL = "https://dl.grafana.com/oss/release/grafana-11.2.0.windows-amd64.zip"


def is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    """Comprueba si un puerto TCP está respondiendo."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def get_base_tools_dir() -> Path:
    """Devuelve la ruta al directorio tools base."""
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    candidates = [
        app_dir / "tools",
        Path(__file__).resolve().parent / "tools",
        app_dir,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    d = app_dir / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


class NativeStackManager:
    """Gestiona los procesos nativos de InfluxDB y Grafana en Windows."""

    def __init__(self, tools_dir: Optional[Path] = None):
        self.tools_dir = tools_dir or get_base_tools_dir()
        self.influx_dir = self.tools_dir / "influxdb"
        self.grafana_dir = self.tools_dir / "grafana"
        self.influx_data_dir = self.influx_dir / "data"

        self.influx_proc: Optional[subprocess.Popen] = None
        self.grafana_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.download_status: dict[str, str] = {
            "influxdb": "idle",
            "grafana": "idle",
        }
        self.download_progress: dict[str, float] = {
            "influxdb": 0.0,
            "grafana": 0.0,
        }

    # -------------------------------------------------------------------------
    # Localización de ejecutables
    # -------------------------------------------------------------------------
    def find_influxd(self) -> Optional[Path]:
        """Localiza influxd.exe."""
        if (self.influx_dir / "influxd.exe").is_file():
            return self.influx_dir / "influxd.exe"
        # Búsqueda en subcarpetas tras extracción de zip
        matches = list(self.influx_dir.glob("**/influxd.exe"))
        if matches:
            return matches[0]
        which = shutil.which("influxd.exe") or shutil.which("influxd")
        return Path(which).resolve() if which else None

    def find_grafana_server(self) -> Optional[Path]:
        """Localiza grafana-server.exe."""
        direct = self.grafana_dir / "bin" / "grafana-server.exe"
        if direct.is_file():
            return direct
        matches = list(self.grafana_dir.glob("**/grafana-server.exe"))
        if matches:
            return matches[0]
        which = shutil.which("grafana-server.exe") or shutil.which("grafana-server")
        return Path(which).resolve() if which else None

    def is_stack_installed(self) -> bool:
        """Indica si ambos ejecutables están disponibles localmente."""
        return self.find_influxd() is not None and self.find_grafana_server() is not None

    # -------------------------------------------------------------------------
    # Descarga e instalación asistida
    # -------------------------------------------------------------------------
    def download_and_extract_file(
        self,
        url: str,
        dest_dir: Path,
        component_name: str,
        status_cb: Optional[Callable[[str, float], None]] = None,
    ) -> bool:
        """Descarga un archivo ZIP y lo extrae en dest_dir con reporte de progreso."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        zip_path = dest_dir / f"{component_name}_download.zip"

        try:
            self.download_status[component_name] = "downloading"
            logger.info("Descargando %s desde %s...", component_name, url)

            req = urllib.request.Request(url, headers={"User-Agent": "RedLabTester-Downloader/1.0"})
            with urllib.request.urlopen(req, timeout=30) as response, open(zip_path, "wb") as out_file:
                total_size = int(response.info().get("Content-Length", 0))
                downloaded = 0
                chunk_size = 1024 * 1024  # 1 MB

                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    pct = (downloaded / total_size * 100.0) if total_size > 0 else 0.0
                    self.download_progress[component_name] = pct
                    if status_cb:
                        status_cb(f"Descargando {component_name}: {pct:.1f}%", pct)

            self.download_status[component_name] = "extracting"
            if status_cb:
                status_cb(f"Extrayendo {component_name}...", 100.0)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(dest_dir)

            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass

            # Si el ZIP extrajo en una subcarpeta (ej: grafana-11.2.0/), mover al directorio raíz
            subdirs = [p for p in dest_dir.iterdir() if p.is_dir()]
            for sd in subdirs:
                if (sd / "bin" / "grafana-server.exe").is_file():
                    # Mover contenido de la subcarpeta arriba
                    for item in sd.iterdir():
                        target = dest_dir / item.name
                        if not target.exists():
                            shutil.move(str(item), str(target))
                elif (sd / "influxd.exe").is_file():
                    target = dest_dir / "influxd.exe"
                    if not target.exists():
                        shutil.move(str(sd / "influxd.exe"), str(target))

            self.download_status[component_name] = "ready"
            return True
        except Exception as e:
            logger.error("Error al descargar/extraer %s: %s", component_name, e)
            self.download_status[component_name] = f"error: {e}"
            return False

    def install_native_stack_async(self, on_finish: Optional[Callable[[bool], None]] = None) -> None:
        """Ejecuta la descarga e instalación en segundo plano."""
        def _worker():
            ok_influx = True
            ok_grafana = True
            if not self.find_influxd():
                ok_influx = self.download_and_extract_file(
                    INFLUX_ZIP_URL, self.influx_dir, "influxdb"
                )
            if not self.find_grafana_server():
                ok_grafana = self.download_and_extract_file(
                    GRAFANA_ZIP_URL, self.grafana_dir, "grafana"
                )
            success = ok_influx and ok_grafana
            if success:
                self.setup_and_start_all()
            if on_finish:
                on_finish(success)

        threading.Thread(target=_worker, daemon=True).start()

    # -------------------------------------------------------------------------
    # Configuración y aprovisionamiento automático
    # -------------------------------------------------------------------------
    def setup_influx_initial_data(self) -> bool:
        """Inicializa InfluxDB v2 vía API con org, bucket y token si es una instalación nueva."""
        setup_url = f"http://127.0.0.1:{INFLUX_PORT}/api/v2/setup"
        payload = {
            "username": INFLUX_USER,
            "password": INFLUX_PASSWORD,
            "org": INFLUX_ORG,
            "bucket": INFLUX_BUCKET,
            "token": INFLUX_TOKEN,
        }
        try:
            req = urllib.request.Request(
                setup_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in (200, 201):
                    logger.info("InfluxDB inicializado automáticamente con token y bucket 'iperf_stats'.")
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 409 or e.code == 400:
                # Ya estaba configurado
                return True
        except Exception:
            pass
        return False

    def provision_grafana_config(self) -> None:
        """Crea el archivo custom.ini y la configuración del origen de datos InfluxDB en Grafana."""
        conf_dir = self.grafana_dir / "conf"
        conf_dir.mkdir(parents=True, exist_ok=True)
        custom_ini = conf_dir / "custom.ini"

        # 1. custom.ini para puerto 7500 y credenciales
        ini_content = f"""[server]
http_port = {GRAFANA_PORT}
protocol = http

[security]
admin_user = {GRAFANA_USER}
admin_password = {GRAFANA_PASSWORD}
allow_embedding = true

[auth.anonymous]
enabled = true
org_role = Admin

[users]
allow_sign_up = false
"""
        custom_ini.write_text(ini_content, encoding="utf-8")

        # 2. Aprovisionamiento automático de InfluxDB como DataSource
        prov_dir = conf_dir / "provisioning" / "datasources"
        prov_dir.mkdir(parents=True, exist_ok=True)
        ds_file = prov_dir / "influxdb.yaml"

        ds_content = f"""apiVersion: 1

datasources:
  - name: InfluxDB_v2
    type: influxdb
    access: proxy
    url: http://127.0.0.1:{INFLUX_PORT}
    jsonData:
      version: Flux
      organization: {INFLUX_ORG}
      defaultBucket: {INFLUX_BUCKET}
    secureJsonData:
      token: {INFLUX_TOKEN}
    isDefault: true
    editable: true
"""
        ds_file.write_text(ds_content, encoding="utf-8")

        # 3. Aprovisionamiento de dashboards
        dash_prov_dir = conf_dir / "provisioning" / "dashboards"
        dash_prov_dir.mkdir(parents=True, exist_ok=True)
        dash_prov_file = dash_prov_dir / "dashboards.yaml"
        dashboards_store_dir = self.grafana_dir / "dashboards"
        dashboards_store_dir.mkdir(parents=True, exist_ok=True)

        dash_yaml = f"""apiVersion: 1

providers:
  - name: 'RedLab Tester'
    orgId: 1
    folder: 'RedLab'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: {str(dashboards_store_dir).replace(os.sep, '/')}
"""
        dash_prov_file.write_text(dash_yaml, encoding="utf-8")

        # 4. Dashboard predefinido para RedLab Tester
        dashboard_json_file = dashboards_store_dir / "redlab_dashboard.json"
        if not dashboard_json_file.exists():
            dashboard_json_file.write_text(self.get_default_dashboard_json(), encoding="utf-8")

    def get_default_dashboard_json(self) -> str:
        """Devuelve la definición JSON de un dashboard completo para RedLab Tester."""
        return json.dumps({
            "title": "RedLab Tester - Métricas de Ancho de Banda",
            "tags": ["redlab", "iperf3"],
            "timezone": "browser",
            "schemaVersion": 36,
            "refresh": "5s",
            "panels": [
                {
                    "title": "Ancho de Banda (Mbps) por Switch / Equipo",
                    "type": "timeseries",
                    "gridPos": {"h": 9, "w": 24, "x": 0, "y": 0},
                    "datasource": {"type": "influxdb", "uid": "InfluxDB_v2"},
                    "targets": [
                        {
                            "query": f'from(bucket: "{INFLUX_BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r["_measurement"] == "iperf_measurement")\n  |> filter(fn: (r) => r["_field"] == "bandwidth_mbps")\n  |> yield(name: "bandwidth")',
                            "refId": "A"
                        }
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "Mbits",
                            "color": {"mode": "palette-classic"},
                            "custom": {"drawStyle": "line", "lineInterpolation": "smooth"}
                        }
                    }
                },
                {
                    "title": "Retransmisiones TCP",
                    "type": "timeseries",
                    "gridPos": {"h": 8, "w": 24, "x": 0, "y": 9},
                    "datasource": {"type": "influxdb", "uid": "InfluxDB_v2"},
                    "targets": [
                        {
                            "query": f'from(bucket: "{INFLUX_BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r["_measurement"] == "iperf_measurement")\n  |> filter(fn: (r) => r["_field"] == "tcp_retransmits")\n  |> yield(name: "retransmits")',
                            "refId": "A"
                        }
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "unit": "short",
                            "color": {"mode": "fixed", "fixedColor": "red"},
                            "custom": {"drawStyle": "bars"}
                        }
                    }
                }
            ]
        }, indent=2)

    # -------------------------------------------------------------------------
    # Arranque y parada de procesos
    # -------------------------------------------------------------------------
    def start_influxdb(self) -> bool:
        """Arranca InfluxDB nativo si no está ya escuchando en el puerto 4000."""
        with self._lock:
            if is_port_open("127.0.0.1", INFLUX_PORT):
                return True

            influx_bin = self.find_influxd()
            if not influx_bin:
                return False

            self.influx_data_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                str(influx_bin),
                f"--http-bind-address=:{INFLUX_PORT}",
                f"--bolt-path={str(self.influx_data_dir / 'influxd.bolt')}",
                f"--engine-path={str(self.influx_data_dir / 'engine')}",
            ]

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                self.influx_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=str(influx_bin.parent),
                    creationflags=creationflags,
                )
            except Exception as e:
                logger.error("No se pudo iniciar InfluxDB: %s", e)
                return False

        # Esperar hasta 5 segundos a que abra el puerto
        for _ in range(10):
            time.sleep(0.5)
            if is_port_open("127.0.0.1", INFLUX_PORT):
                # Intentar inicialización automática
                self.setup_influx_initial_data()
                return True
        return False

    def start_grafana(self) -> bool:
        """Arranca Grafana nativo si no está ya escuchando en el puerto 7500."""
        with self._lock:
            if is_port_open("127.0.0.1", GRAFANA_PORT):
                return True

            grafana_bin = self.find_grafana_server()
            if not grafana_bin:
                return False

            self.provision_grafana_config()
            home_path = grafana_bin.parent.parent  # Directorio raíz de Grafana
            custom_ini = self.grafana_dir / "conf" / "custom.ini"

            cmd = [
                str(grafana_bin),
                f"--homepath={str(home_path)}",
                f"--config={str(custom_ini)}",
            ]

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                self.grafana_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=str(home_path),
                    creationflags=creationflags,
                )
            except Exception as e:
                logger.error("No se pudo iniciar Grafana: %s", e)
                return False

        # Esperar hasta 8 segundos a que abra el puerto
        for _ in range(16):
            time.sleep(0.5)
            if is_port_open("127.0.0.1", GRAFANA_PORT):
                return True
        return False

    def setup_and_start_all(self) -> dict[str, bool]:
        """Arranca ambos servicios nativos y devuelve su estado."""
        influx_ok = self.start_influxdb()
        grafana_ok = self.start_grafana()
        return {
            "influxdb": influx_ok,
            "grafana": grafana_ok,
        }

    def stop_all(self) -> None:
        """Detiene limpiamente los subprocesos de InfluxDB y Grafana."""
        with self._lock:
            for proc, name in [(self.influx_proc, "InfluxDB"), (self.grafana_proc, "Grafana")]:
                if proc and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=2.0)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            self.influx_proc = None
            self.grafana_proc = None


# Instancia única para importar
stack_manager = NativeStackManager()
