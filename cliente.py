"""Cliente de medición RedLab Tester con Interfaz Gráfica (GUI) y CLI.

Ejecuta pruebas de rendimiento de red TCP con iperf3 y registra
opcionalmente las métricas en InfluxDB.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# InfluxDB es opcional para que la sonda funcione incluso si la librería no está lista
try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    INFLUX_AVAILABLE = True
except ImportError:
    INFLUX_AVAILABLE = False


# Constantes y valores por defecto
APP_TITLE = "RedLab Tester - Sonda de Medición"
MEASUREMENT = "iperf_tcp"
DEFAULT_PORT = 5201
DEFAULT_DURATION = 10  # Por defecto 10 s para pruebas rápidas, configurable a 30 s
DEFAULT_INFLUX_URL = "http://localhost:8086"
DEFAULT_INFLUX_ORG = "red_lab"
DEFAULT_INFLUX_BUCKET = "iperf_stats"
DEFAULT_INFLUX_TOKEN = "lab_token_secure"

logger = logging.getLogger("redlab-client")


def get_app_dir() -> Path:
    """Devuelve el directorio base de la aplicación (junto al ejecutable o script)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_iperf3_binary() -> Optional[Path]:
    """Localiza el binario iperf3.exe en el sistema o empaquetado."""
    candidates: list[Path] = []

    # 1. Empaquetado dentro de PyInstaller (_MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        meipass_dir = Path(getattr(sys, "_MEIPASS"))
        candidates.append(meipass_dir / "iperf3.exe")
        candidates.append(meipass_dir / "tools" / "iperf3.exe")

    app_dir = get_app_dir()
    # 2. Junto al ejecutable o en su subcarpeta tools
    candidates.append(app_dir / "iperf3.exe")
    candidates.append(app_dir / "tools" / "iperf3.exe")

    # 3. Raíz del repositorio o proyecto
    project_root = Path(__file__).resolve().parent if not getattr(sys, "frozen", False) else app_dir
    candidates.append(project_root / "tools" / "iperf3.exe")
    candidates.append(project_root.parent / "tools" / "iperf3.exe")

    # 4. Instalación de WinGet en AppData
    local_app_data = os.environ.get("LOCALAPPDATA", "")
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

    # Comprobar candidatos directos
    for c in candidates:
        if c.is_file():
            return c.resolve()

    # 5. PATH del sistema
    which_iperf = shutil.which("iperf3.exe") or shutil.which("iperf3")
    if which_iperf:
        return Path(which_iperf).resolve()

    return None


def run_iperf3_test(
    server_ip: str,
    port: int = DEFAULT_PORT,
    duration: int = DEFAULT_DURATION,
    stop_event: Optional[threading.Event] = None,
    log_callback=None,
    progress_callback=None,
) -> dict[str, Any]:
    """Ejecuta una prueba iperf3 en formato JSON usando el binario nativo."""
    iperf_bin = find_iperf3_binary()
    if not iperf_bin:
        raise FileNotFoundError(
            "No se encontró el ejecutable 'iperf3.exe'. "
            "Asegúrate de que 'iperf3.exe' esté en la carpeta 'tools' o en el PATH."
        )

    if log_callback:
        log_callback(f"Conectando a {server_ip}:{port} (duración: {duration}s)...")

    cmd = [
        str(iperf_bin),
        "-c", server_ip,
        "-p", str(port),
        "-t", str(duration),
        "--connect-timeout", "4000",
        "-J",  # Salida JSON estructurada
    ]

    # Evita abrir ventanas de consola negras en Windows
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(iperf_bin.parent),
        text=True,
        creationflags=creationflags,
    )

    max_timeout = duration + 10
    start_time = time.time()
    last_second = 0
    stdout = ""
    stderr = ""

    while True:
        if stop_event and stop_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
            raise InterruptedError("La prueba fue cancelada por el usuario.")

        try:
            out_chunk, err_chunk = process.communicate(timeout=0.5)
            stdout = out_chunk
            stderr = err_chunk
            break
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            curr_second = int(elapsed)
            if curr_second > last_second and curr_second <= duration:
                last_second = curr_second
                if progress_callback:
                    progress_callback(curr_second, duration)

            if elapsed > max_timeout:
                process.kill()
                process.communicate()
                raise TimeoutError(
                    f"La prueba de iperf3 superó el tiempo máximo ({max_timeout}s) sin responder."
                )

    if not stdout and stderr:
        raise ConnectionError(f"Error en iperf3: {stderr.strip()}")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        error_msg = stderr.strip() or stdout.strip() or "iperf3 no devolvió datos legibles"
        raise ConnectionError(f"Fallo al interpretar resultados de iperf3: {error_msg}")

    if "error" in data:
        err_raw = str(data["error"])
        if "timed out" in err_raw.lower() or "timeout" in err_raw.lower():
            msg = (
                f"Tiempo de espera agotado al conectar con {server_ip}:{port}. "
                "Comprueba que la IP sea correcta y que el Firewall de Windows en el servidor permita el puerto TCP 5201."
            )
        elif "refused" in err_raw.lower():
            msg = (
                f"Conexión rechazada en {server_ip}:{port}. "
                "El servidor iperf3 no está iniciado en esa IP o el puerto 5201 no está escuchando."
            )
        else:
            msg = f"Fallo reportado por iperf3: {err_raw}"
        raise ConnectionError(msg)

    # Extraer métricas clave
    end_data = data.get("end", {})
    sum_received = end_data.get("sum_received", {})
    sum_sent = end_data.get("sum_sent", {})

    bits_per_sec = sum_received.get("bits_per_second")
    if bits_per_sec is None:
        bits_per_sec = sum_sent.get("bits_per_second", 0.0)

    bandwidth_mbps = round(float(bits_per_sec) / 1_000_000.0, 2)
    retransmits = int(sum_sent.get("retransmits", 0) or 0)

    return {
        "bandwidth_mbps": bandwidth_mbps,
        "retransmits": retransmits,
        "raw": data,
    }


def send_metrics_to_influx(
    server_ip: str,
    switch_id: str,
    bandwidth_mbps: float,
    retransmits: int,
    influx_url: str = DEFAULT_INFLUX_URL,
    influx_org: str = DEFAULT_INFLUX_ORG,
    influx_bucket: str = DEFAULT_INFLUX_BUCKET,
    influx_token: str = DEFAULT_INFLUX_TOKEN,
) -> None:
    """Envía el punto de medición a InfluxDB."""
    if not INFLUX_AVAILABLE:
        raise RuntimeError("El cliente de InfluxDB no está instalado en este entorno.")

    hostname = platform.node() or "desconocido"
    point = (
        Point(MEASUREMENT)
        .tag("hostname", hostname)
        .tag("switch_id", switch_id)
        .tag("server_ip", server_ip)
        .field("bandwidth_mbps", bandwidth_mbps)
        .field("tcp_retransmits", retransmits)
        .time(datetime.now(timezone.utc), WritePrecision.NS)
    )

    with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org) as db:
        with db.write_api(write_options=SYNCHRONOUS) as writer:
            writer.write(bucket=influx_bucket, org=influx_org, record=point)


# =============================================================================
# INTERFAZ GRÁFICA (GUI) CON TKINTER
# =============================================================================

def launch_gui(initial_env_file: Optional[Path] = None) -> None:
    """Inicia la interfaz de usuario con Tkinter."""
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    # Soporte DPI de alta resolución en Windows
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    env_path = initial_env_file or (get_app_dir() / ".env")
    if env_path.is_file():
        load_dotenv(env_path)

    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("680x750")
    root.minsize(600, 680)

    # Configuración de estilo visual
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # Colores
    BG_MAIN = "#F3F4F6"
    BG_CARD = "#FFFFFF"
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    SUCCESS = "#10B981"
    TEXT_MAIN = "#1F2937"
    TEXT_MUTED = "#6B7280"

    root.configure(bg=BG_MAIN)

    # Variables de estado
    server_ip_var = tk.StringVar(value=os.getenv("IPERF_SERVER_IP", "127.0.0.1"))
    port_var = tk.StringVar(value=os.getenv("IPERF_PORT", str(DEFAULT_PORT)))
    switch_id_var = tk.StringVar(value=os.getenv("SWITCH_ID", f"switch-{platform.node()[:8]}"))
    duration_var = tk.StringVar(value=str(DEFAULT_DURATION))

    send_influx_var = tk.BooleanVar(value=True)
    influx_url_var = tk.StringVar(value=os.getenv("INFLUX_URL", DEFAULT_INFLUX_URL))
    influx_org_var = tk.StringVar(value=os.getenv("INFLUX_ORG", DEFAULT_INFLUX_ORG))
    influx_bucket_var = tk.StringVar(value=os.getenv("INFLUX_BUCKET", DEFAULT_INFLUX_BUCKET))
    influx_token_var = tk.StringVar(value=os.getenv("INFLUX_TOKEN", DEFAULT_INFLUX_TOKEN))

    speed_result_var = tk.StringVar(value="-- Mbps")
    retransmit_result_var = tk.StringVar(value="--")
    influx_status_var = tk.StringVar(value="En espera")
    status_msg_var = tk.StringVar(value="Listo para iniciar prueba.")

    stop_event: Optional[threading.Event] = None
    is_running_lock = threading.Lock()
    is_running = False

    # Cabecera
    header_frame = tk.Frame(root, bg=PRIMARY, padx=20, pady=16)
    header_frame.pack(fill=tk.X)

    title_label = tk.Label(
        header_frame,
        text="📡 RedLab Tester - Sonda de Medición",
        font=("Segoe UI", 15, "bold"),
        fg="#FFFFFF",
        bg=PRIMARY,
    )
    title_label.pack(anchor="w")

    subtitle_label = tk.Label(
        header_frame,
        text="Pruebas de ancho de banda y diagnóstico de red con iperf3 e InfluxDB",
        font=("Segoe UI", 9),
        fg="#DBEAFE",
        bg=PRIMARY,
    )
    subtitle_label.pack(anchor="w", pady=(2, 0))

    # Contenedor con scroll para adaptarse a pantallas pequeñas
    canvas = tk.Canvas(root, bg=BG_MAIN, highlightthickness=0)
    scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas, bg=BG_MAIN, padx=16, pady=12)

    scroll_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

    def _on_canvas_resize(event):
        canvas.itemconfig(canvas_window, width=event.width)

    canvas.bind("<Configure>", _on_canvas_resize)
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 1. TARJETA: Parámetros del Servidor (iPerf3)
    card_server = tk.LabelFrame(
        scroll_frame,
        text="  Parámetros de Red y Servidor  ",
        font=("Segoe UI", 10, "bold"),
        bg=BG_CARD,
        fg=TEXT_MAIN,
        padx=14,
        pady=10,
    )
    card_server.pack(fill=tk.X, pady=(0, 10))

    # Campo IP
    tk.Label(
        card_server,
        text="IP o Hostname del Servidor:",
        font=("Segoe UI", 9, "bold"),
        bg=BG_CARD,
        fg=TEXT_MAIN,
    ).grid(row=0, column=0, sticky="w", pady=(4, 2))

    ip_entry = ttk.Entry(card_server, textvariable=server_ip_var, font=("Segoe UI", 10))
    ip_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

    # Puerto
    tk.Label(
        card_server,
        text="Puerto:",
        font=("Segoe UI", 9),
        bg=BG_CARD,
        fg=TEXT_MAIN,
    ).grid(row=0, column=2, sticky="w", padx=(10, 0), pady=(4, 2))

    port_entry = ttk.Entry(card_server, textvariable=port_var, font=("Segoe UI", 10), width=8)
    port_entry.grid(row=1, column=2, sticky="w", padx=(10, 0), pady=(0, 8))

    # Switch ID
    tk.Label(
        card_server,
        text="Identificador de Switch / Cliente:",
        font=("Segoe UI", 9),
        bg=BG_CARD,
        fg=TEXT_MAIN,
    ).grid(row=2, column=0, sticky="w", pady=(4, 2))

    switch_entry = ttk.Entry(card_server, textvariable=switch_id_var, font=("Segoe UI", 10))
    switch_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 4))

    # Duración
    tk.Label(
        card_server,
        text="Duración (segundos):",
        font=("Segoe UI", 9),
        bg=BG_CARD,
        fg=TEXT_MAIN,
    ).grid(row=2, column=2, sticky="w", padx=(10, 0), pady=(4, 2))

    duration_combo = ttk.Combobox(
        card_server,
        textvariable=duration_var,
        values=["5", "10", "20", "30", "60"],
        width=7,
        state="readonly",
    )
    duration_combo.grid(row=3, column=2, sticky="w", padx=(10, 0), pady=(0, 4))

    card_server.columnconfigure(0, weight=1)

    # 2. TARJETA: InfluxDB (Desplegable / Opcional)
    card_influx = tk.LabelFrame(
        scroll_frame,
        text="  Registro de Métricas en InfluxDB  ",
        font=("Segoe UI", 10, "bold"),
        bg=BG_CARD,
        fg=TEXT_MAIN,
        padx=14,
        pady=8,
    )
    card_influx.pack(fill=tk.X, pady=(0, 10))

    influx_check = ttk.Checkbutton(
        card_influx,
        text="Habilitar envío automático de telemetría a InfluxDB",
        variable=send_influx_var,
    )
    influx_check.pack(anchor="w", pady=(0, 6))

    influx_details_frame = tk.Frame(card_influx, bg=BG_CARD)
    influx_details_frame.pack(fill=tk.X)

    # Influx URL y Bucket en dos columnas
    tk.Label(influx_details_frame, text="URL InfluxDB:", font=("Segoe UI", 8), bg=BG_CARD).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Entry(influx_details_frame, textvariable=influx_url_var, font=("Segoe UI", 9)).grid(
        row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 4)
    )

    tk.Label(influx_details_frame, text="Bucket:", font=("Segoe UI", 8), bg=BG_CARD).grid(
        row=0, column=1, sticky="w"
    )
    ttk.Entry(influx_details_frame, textvariable=influx_bucket_var, font=("Segoe UI", 9)).grid(
        row=1, column=1, sticky="ew", pady=(0, 4)
    )

    tk.Label(influx_details_frame, text="Organización:", font=("Segoe UI", 8), bg=BG_CARD).grid(
        row=2, column=0, sticky="w"
    )
    ttk.Entry(influx_details_frame, textvariable=influx_org_var, font=("Segoe UI", 9)).grid(
        row=3, column=0, sticky="ew", padx=(0, 8), pady=(0, 4)
    )

    tk.Label(influx_details_frame, text="Token InfluxDB:", font=("Segoe UI", 8), bg=BG_CARD).grid(
        row=2, column=1, sticky="w"
    )
    ttk.Entry(influx_details_frame, textvariable=influx_token_var, font=("Segoe UI", 9), show="*").grid(
        row=3, column=1, sticky="ew", pady=(0, 4)
    )

    influx_details_frame.columnconfigure(0, weight=1)
    influx_details_frame.columnconfigure(1, weight=1)

    # 3. BOTONES DE ACCIÓN
    actions_frame = tk.Frame(scroll_frame, bg=BG_MAIN)
    actions_frame.pack(fill=tk.X, pady=(0, 10))

    btn_start = tk.Button(
        actions_frame,
        text="▶ Iniciar Prueba",
        font=("Segoe UI", 10, "bold"),
        bg=PRIMARY,
        fg="#FFFFFF",
        activebackground=PRIMARY_HOVER,
        activeforeground="#FFFFFF",
        padx=18,
        pady=8,
        relief=tk.FLAT,
        cursor="hand2",
    )
    btn_start.pack(side=tk.LEFT, padx=(0, 8))

    btn_stop = tk.Button(
        actions_frame,
        text="⏹ Detener",
        font=("Segoe UI", 10),
        bg="#EF4444",
        fg="#FFFFFF",
        activebackground="#DC2626",
        activeforeground="#FFFFFF",
        padx=14,
        pady=8,
        relief=tk.FLAT,
        state=tk.DISABLED,
        cursor="hand2",
    )
    btn_stop.pack(side=tk.LEFT, padx=(0, 8))

    def save_env_action():
        env_content = (
            f"IPERF_SERVER_IP={server_ip_var.get().strip()}\n"
            f"IPERF_PORT={port_var.get().strip()}\n"
            f"SWITCH_ID={switch_id_var.get().strip()}\n"
            f"INFLUX_URL={influx_url_var.get().strip()}\n"
            f"INFLUX_ORG={influx_org_var.get().strip()}\n"
            f"INFLUX_BUCKET={influx_bucket_var.get().strip()}\n"
            f"INFLUX_TOKEN={influx_token_var.get().strip()}\n"
        )
        try:
            target_env = get_app_dir() / ".env"
            target_env.write_text(env_content, encoding="utf-8")
            log(f"Configuración guardada en: {target_env}")
            messagebox.showinfo("Guardado", f"Configuración guardada correctamente en:\n{target_env}")
        except Exception as ex:
            messagebox.showerror("Error", f"No se pudo guardar el archivo .env:\n{ex}")

    btn_save = tk.Button(
        actions_frame,
        text="💾 Guardar en .env",
        font=("Segoe UI", 9),
        bg="#E5E7EB",
        fg=TEXT_MAIN,
        padx=10,
        pady=8,
        relief=tk.FLAT,
        cursor="hand2",
        command=save_env_action,
    )
    btn_save.pack(side=tk.RIGHT)

    # 4. TARJETA: Resultados Clave
    card_results = tk.LabelFrame(
        scroll_frame,
        text="  Resultados de la Medición  ",
        font=("Segoe UI", 10, "bold"),
        bg=BG_CARD,
        fg=TEXT_MAIN,
        padx=14,
        pady=10,
    )
    card_results.pack(fill=tk.X, pady=(0, 10))

    # Medidores en tarjetas individuales
    col_speed = tk.Frame(card_results, bg="#EFF6FF", padx=12, pady=8, relief=tk.GROOVE, bd=1)
    col_speed.grid(row=0, column=0, sticky="ew", padx=(0, 6))

    tk.Label(col_speed, text="ANCHO DE BANDA", font=("Segoe UI", 8, "bold"), fg=PRIMARY, bg="#EFF6FF").pack(
        anchor="w"
    )
    speed_lbl = tk.Label(
        col_speed,
        textvariable=speed_result_var,
        font=("Segoe UI", 14, "bold"),
        fg="#1E40AF",
        bg="#EFF6FF",
    )
    speed_lbl.pack(anchor="w")

    col_ret = tk.Frame(card_results, bg="#FEF3C7", padx=12, pady=8, relief=tk.GROOVE, bd=1)
    col_ret.grid(row=0, column=1, sticky="ew", padx=(0, 6))

    tk.Label(col_ret, text="RETRANSMISIONES TCP", font=("Segoe UI", 8, "bold"), fg="#B45309", bg="#FEF3C7").pack(
        anchor="w"
    )
    ret_lbl = tk.Label(
        col_ret,
        textvariable=retransmit_result_var,
        font=("Segoe UI", 14, "bold"),
        fg="#92400E",
        bg="#FEF3C7",
    )
    ret_lbl.pack(anchor="w")

    col_inf = tk.Frame(card_results, bg="#ECFDF5", padx=12, pady=8, relief=tk.GROOVE, bd=1)
    col_inf.grid(row=0, column=2, sticky="ew")

    tk.Label(col_inf, text="ESTADO TELEMETRÍA", font=("Segoe UI", 8, "bold"), fg="#047857", bg="#ECFDF5").pack(
        anchor="w"
    )
    inf_lbl = tk.Label(
        col_inf,
        textvariable=influx_status_var,
        font=("Segoe UI", 11, "bold"),
        fg="#065F46",
        bg="#ECFDF5",
    )
    inf_lbl.pack(anchor="w")

    card_results.columnconfigure(0, weight=1)
    card_results.columnconfigure(1, weight=1)
    card_results.columnconfigure(2, weight=1)

    # Barra de progreso
    progress_bar = ttk.Progressbar(scroll_frame, mode="indeterminate")
    progress_bar.pack(fill=tk.X, pady=(0, 4))

    status_lbl = tk.Label(
        scroll_frame,
        textvariable=status_msg_var,
        font=("Segoe UI", 9, "italic"),
        bg=BG_MAIN,
        fg=TEXT_MUTED,
        anchor="w",
    )
    status_lbl.pack(fill=tk.X, pady=(0, 8))

    # 5. REGISTRO DE EVENTOS (LOG)
    log_frame = tk.LabelFrame(
        scroll_frame,
        text="  Registro de Actividad  ",
        font=("Segoe UI", 9, "bold"),
        bg=BG_CARD,
        fg=TEXT_MAIN,
        padx=6,
        pady=6,
    )
    log_frame.pack(fill=tk.BOTH, expand=True)

    log_box = ScrolledText(
        log_frame,
        height=8,
        font=("Consolas", 9),
        bg="#111827",
        fg="#E5E7EB",
        insertbackground="#FFFFFF",
        relief=tk.FLAT,
    )
    log_box.pack(fill=tk.BOTH, expand=True)

    def log(message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}\n"

        def _append():
            log_box.insert(tk.END, entry)
            log_box.see(tk.END)

        root.after(0, _append)

    # Comprobación de iperf3 al arrancar la UI
    found_iperf = find_iperf3_binary()
    if found_iperf:
        log(f"Motor iperf3 detectado: {found_iperf}")
    else:
        log("ADVERTENCIA: No se encontró iperf3.exe en el sistema.")

    # Lógica de ejecución de la prueba en hilo secundario
    def run_worker():
        nonlocal is_running, stop_event
        server_ip = server_ip_var.get().strip()
        switch_id = switch_id_var.get().strip()
        try:
            port = int(port_var.get().strip() or "5201")
        except ValueError:
            port = DEFAULT_PORT

        try:
            duration = int(duration_var.get().strip() or str(DEFAULT_DURATION))
        except ValueError:
            duration = DEFAULT_DURATION

        send_to_db = send_influx_var.get()
        influx_url = influx_url_var.get().strip()
        influx_org = influx_org_var.get().strip()
        influx_bucket = influx_bucket_var.get().strip()
        influx_token = influx_token_var.get().strip()

        root.after(0, lambda: progress_bar.start(10))
        root.after(0, lambda: status_msg_var.set(f"Conectando a {server_ip}:{port}..."))
        log(f"Iniciando prueba TCP hacia {server_ip}:{port} ({duration} s)")

        def on_test_progress(curr, total):
            root.after(0, lambda: status_msg_var.set(f"Midiendo ancho de banda ({curr}/{total} s)..."))

        try:
            result = run_iperf3_test(
                server_ip=server_ip,
                port=port,
                duration=duration,
                stop_event=stop_event,
                log_callback=log,
                progress_callback=on_test_progress,
            )

            mbps = result["bandwidth_mbps"]
            ret = result["retransmits"]
            log(f"Resultado iperf3: {mbps:.2f} Mbps, {ret} retransmisiones TCP.")

            root.after(0, lambda: speed_result_var.set(f"{mbps:.2f} Mbps"))
            root.after(0, lambda: retransmit_result_var.set(str(ret)))

            if send_to_db:
                log(f"Enviando telemetría a InfluxDB ({influx_url} - {influx_bucket})...")
                root.after(0, lambda: status_msg_var.set("Enviando métricas a InfluxDB..."))
                try:
                    send_metrics_to_influx(
                        server_ip=server_ip,
                        switch_id=switch_id,
                        bandwidth_mbps=mbps,
                        retransmits=ret,
                        influx_url=influx_url,
                        influx_org=influx_org,
                        influx_bucket=influx_bucket,
                        influx_token=influx_token,
                    )
                    log("Métricas registradas exitosamente en InfluxDB.")
                    root.after(0, lambda: influx_status_var.set("Registrado OK"))
                except Exception as ex_db:
                    log(f"Aviso InfluxDB: No se pudo enviar ({ex_db}). La prueba fue completada.")
                    root.after(0, lambda: influx_status_var.set("Error envío"))
            else:
                log("Envío a InfluxDB omitido por configuración.")
                root.after(0, lambda: influx_status_var.set("Desactivado"))

            root.after(0, lambda: status_msg_var.set(f"Prueba completada: {mbps:.2f} Mbps"))
            log("Prueba finalizada con éxito.")

        except InterruptedError:
            log("Prueba cancelada por el usuario.")
            root.after(0, lambda: status_msg_var.set("Prueba cancelada."))
        except Exception as ex:
            log(f"ERROR: {ex}")
            root.after(0, lambda: status_msg_var.set(f"Fallo en la prueba: {ex}"))
            root.after(0, lambda: messagebox.showerror("Error en la prueba", str(ex)))
        finally:
            with is_running_lock:
                is_running = False
            root.after(0, lambda: progress_bar.stop())
            root.after(0, lambda: btn_start.config(state=tk.NORMAL))
            root.after(0, lambda: btn_stop.config(state=tk.DISABLED))

    def on_start_clicked():
        nonlocal is_running, stop_event
        server_ip = server_ip_var.get().strip()
        if not server_ip:
            messagebox.showwarning("Falta IP", "Por favor introduce la IP o nombre de host del servidor.")
            ip_entry.focus_set()
            return

        with is_running_lock:
            if is_running:
                return
            is_running = True

        stop_event = threading.Event()
        btn_start.config(state=tk.DISABLED)
        btn_stop.config(state=tk.NORMAL)
        speed_result_var.set("Midiendo...")
        retransmit_result_var.set("...")
        influx_status_var.set("Pendiente")

        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

    def on_stop_clicked():
        nonlocal stop_event
        if stop_event:
            stop_event.set()
            log("Solicitando cancelación de la prueba...")

    btn_start.config(command=on_start_clicked)
    btn_stop.config(command=on_stop_clicked)

    # Centrar ventana en pantalla
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")

    root.mainloop()


# =============================================================================
# MODO CONSOLA / CLI (HEADLESS)
# =============================================================================

def run_cli(args: argparse.Namespace) -> int:
    """Ejecuta una prueba por consola sin abrir la interfaz gráfica."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    app_dir = get_app_dir()
    env_file = Path(args.env) if args.env else (app_dir / ".env")
    if env_file.is_file():
        load_dotenv(env_file)

    server_ip = args.server or os.getenv("IPERF_SERVER_IP", "").strip()
    if not server_ip:
        logger.error("Debe especificar la IP del servidor con --server o en el archivo .env")
        return 1

    port = args.port or int(os.getenv("IPERF_PORT", str(DEFAULT_PORT)))
    duration = args.duration or int(os.getenv("IPERF_DURATION", str(DEFAULT_DURATION)))
    switch_id = args.switch or os.getenv("SWITCH_ID", f"switch-{platform.node()[:8]}")

    logger.info("Iniciando prueba TCP contra %s:%d durante %d segundos...", server_ip, port, duration)
    try:
        res = run_iperf3_test(server_ip=server_ip, port=port, duration=duration)
        mbps = res["bandwidth_mbps"]
        ret = res["retransmits"]
        logger.info("Resultado: %.2f Mbps, %d retransmisiones TCP", mbps, ret)

        if not args.no_influx:
            influx_url = os.getenv("INFLUX_URL", DEFAULT_INFLUX_URL).strip()
            influx_org = os.getenv("INFLUX_ORG", DEFAULT_INFLUX_ORG).strip()
            influx_bucket = os.getenv("INFLUX_BUCKET", DEFAULT_INFLUX_BUCKET).strip()
            influx_token = os.getenv("INFLUX_TOKEN", DEFAULT_INFLUX_TOKEN).strip()
            send_metrics_to_influx(
                server_ip=server_ip,
                switch_id=switch_id,
                bandwidth_mbps=mbps,
                retransmits=ret,
                influx_url=influx_url,
                influx_org=influx_org,
                influx_bucket=influx_bucket,
                influx_token=influx_token,
            )
            logger.info("Métricas enviadas exitosamente a InfluxDB (%s/%s)", influx_org, influx_bucket)

        return 0
    except Exception as exc:
        logger.exception("Error durante la prueba: %s", exc)
        return 2


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--cli", action="store_true", help="Ejecutar en modo consola sin abrir la UI")
    parser.add_argument("--server", type=str, help="IP o host del servidor iperf3")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Puerto del servidor iperf3")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="Duración en segundos")
    parser.add_argument("--switch", type=str, help="Identificador del switch")
    parser.add_argument("--env", type=str, help="Ruta alternativa al archivo .env")
    parser.add_argument("--no-influx", action="store_true", help="Desactivar el envío a InfluxDB")

    args = parser.parse_args()

    # Si se pide --cli o se especifica --server por parámetro, ejecutar CLI
    if args.cli or args.server:
        sys.exit(run_cli(args))
    else:
        # Por defecto abre la interfaz gráfica
        launch_gui(Path(args.env) if args.env else None)


if __name__ == "__main__":
    main()
