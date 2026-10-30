FROM python:3.11-slim

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    iperf3 \
    curl \
    iproute2 \
    procps \
    docker.io \
    docker-compose \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requerimientos e instalar paquetes Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del servidor
COPY servidor.py stack_manager.py version_check.py .env.example ./
COPY grafana ./grafana

EXPOSE 8000

CMD ["python", "servidor.py"]

