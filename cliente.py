"""Ejecuta una prueba TCP de iperf3 y guarda sus métricas en InfluxDB."""

from __future__ import annotations

import logging
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import iperf3
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("redlab-client")

MEASUREMENT = "iperf_tcp"
DEFAULT_INFLUX_URL = "http://localhost:8086"
DEFAULT_INFLUX_ORG = "red_lab"
DEFAULT_INFLUX_BUCKET = "iperf_stats"
TEST_DURATION_SECONDS = 30


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Falta la variable {name} en el archivo .env")
    return value


def run_test() -> None:
    # En el .exe busca .env junto al binario; en desarrollo, junto a este archivo.
    app_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    load_dotenv(app_dir / ".env")
    server_ip = required_env("IPERF_SERVER_IP")
    influx_token = required_env("INFLUX_TOKEN")
    switch_id = required_env("SWITCH_ID")
    influx_url = os.getenv("INFLUX_URL", DEFAULT_INFLUX_URL).strip()
    influx_org = os.getenv("INFLUX_ORG", DEFAULT_INFLUX_ORG).strip()
    influx_bucket = os.getenv("INFLUX_BUCKET", DEFAULT_INFLUX_BUCKET).strip()
    hostname = platform.node()

    if not hostname:
        raise RuntimeError("No se pudo determinar el hostname de este cliente")

    logger.info("Iniciando prueba TCP de %d s contra %s", TEST_DURATION_SECONDS, server_ip)
    test = iperf3.Client()
    test.server_hostname = server_ip
    test.port = 5201
    test.duration = TEST_DURATION_SECONDS
    test.protocol = "TCP"

    result = test.run()
    if result is None or result.error:
        detail = getattr(result, "error", None) or "iperf3 no devolvió un resultado"
        raise ConnectionError(f"Falló la prueba iperf3: {detail}")

    # La biblioteca iperf3 expone el ancho de banda recibido en Mbps y el
    # contador de retransmisiones TCP en el resultado del cliente.
    bandwidth_mbps = float(result.received_Mbps)
    retransmits = int(result.retransmits or 0)
    logger.info(
        "Resultado: %.2f Mbps, %d retransmisiones TCP",
        bandwidth_mbps,
        retransmits,
    )

    point = (
        Point(MEASUREMENT)
        .tag("hostname", hostname)
        .tag("switch_id", switch_id)
        .field("bandwidth_mbps", bandwidth_mbps)
        .field("tcp_retransmits", retransmits)
        .time(datetime.now(timezone.utc), WritePrecision.NS)
    )

    # Context managers garantizan el cierre del cliente y el vaciado del
    # WriteApi síncrono antes de terminar el proceso.
    with InfluxDBClient(url=influx_url, token=influx_token, org=influx_org) as db:
        with db.write_api(write_options=SYNCHRONOUS) as writer:
            writer.write(bucket=influx_bucket, org=influx_org, record=point)

    logger.info("Métricas enviadas a InfluxDB (%s/%s)", influx_org, influx_bucket)


if __name__ == "__main__":
    try:
        run_test()
    except (ConnectionError, TimeoutError, OSError) as exc:
        logger.error("Error de conexión durante la prueba o el envío: %s", exc)
        sys.exit(2)
    except Exception:
        # Incluye fallos del cliente iperf3 y errores del API de InfluxDB,
        # conservando el traceback para diagnosticar cortes de enlace.
        logger.exception("No se pudo completar la prueba RedLab Tester")
        sys.exit(1)
