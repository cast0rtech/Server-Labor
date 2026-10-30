# 🍓 RedLab Tester - Servidor Central Appliance para Raspberry Pi

Este directorio contiene todo lo necesario para compilar y desplegar el **Servidor Central RedLab Tester** como un sistema embebido (*Appliance*) autónomo en una Raspberry Pi (Raspberry Pi 4, Raspberry Pi 5 o Raspberry Pi 3B+).

---

## 🚀 Características Principales del Appliance

1. **IP de inicio por DHCP + Enlace Fijo de Respaldo (Fallback):**
   * **Modo DHCP (por defecto):** Al conectar a un switch o router con DHCP, obtiene su IP automáticamente y se publica en la red mediante mDNS en:
     * **`http://redlab.local:8000`**
   * **Enlace Fijo de Respaldo (`192.168.1.200/24`):** Si estás en campo y conectas la Raspberry Pi **directamente a tu portátil con un cable de red sin switch ni router**, la Raspberry Pi siempre responde en:
     * **`http://192.168.1.200:8000`**
     *(Solo debes configurar tu adaptador de red en el rango `192.168.1.x`, por ejemplo `192.168.1.50`)*.

2. **Auto-arranque y Auto-reinicio de Contenedores (`restart: always`):**
   * Todos los contenedores de **Docker Compose** tienen configurada la política `restart: always`.
   * Si la Raspberry Pi sufre un corte de luz o se reinicia, InfluxDB, Grafana, iPerf3 y el Servidor Web levantan solos sin intervención humana.
   * Servicio supervisado por **systemd** (`redlab.service`) con recuperación automática en caso de caída.

3. **Panel Web de Administración y Setup (`/admin` o `/setup`):**
   * Accesible desde cualquier navegador en: **`http://redlab.local:8000/admin`**
   * **Selección de IP de pruebas:** Elige qué interfaz de red (DHCP, enlace fijo, Wi-Fi o Ethernet) debe comunicarse con las sondas de medición.
   * **Configuración de credenciales (.env):** Modifica tokens de InfluxDB, organizaciones, buckets y contraseñas de Grafana directamente desde la web y guárdalos con 1 clic.
   * **Control de Docker:** Botón para reiniciar los contenedores Docker en caliente.
   * **Telemetría de la Raspberry Pi:** Monitor en vivo de temperatura de CPU (°C), uso de memoria RAM, espacio libre en tarjeta SD y tiempo de actividad (*uptime*).
   * **Reinicio del sistema:** Botón para reiniciar la Raspberry Pi de forma segura.

---

## 🛠️ Métodos de Despliegue

### Método 1: Flashear la Imagen Generada (.img.xz)
1. Descarga la imagen `redlab-server-rpi-arm64.img.xz` generada.
2. Abre **Raspberry Pi Imager** o **Balena Etcher**.
3. Selecciona la imagen descargada y la tarjeta MicroSD (mínimo 16 GB recomendado).
4. Pulsa **Write / Escribir**.
5. Inserta la MicroSD en tu Raspberry Pi, conecta el cable Ethernet y enciende. En 60 segundos el sistema estará listo en `http://redlab.local:8000`.

---

### Método 2: Instalación en 1 Clic sobre cualquier Raspberry Pi OS existente
Si ya tienes una Raspberry Pi con Raspberry Pi OS (Debian Bookworm) en funcionamiento:
```bash
# 1. Clonar el repositorio
git clone https://github.com/cast0rtech/Server-Labor.git /opt/redlab

# 2. Ejecutar el instalador desatendido
cd /opt/redlab
sudo bash rpi/install_rpi.sh
```
El script instalará automáticamente Docker, Docker Compose, iPerf3, Python, Avahi mDNS, configurará la IP fija de respaldo `192.168.1.200` y activará el servicio `redlab.service`.

---

### Método 3: Compilar la Imagen ISO / IMG tú mismo
Para generar la imagen `.img.xz` desde Linux o WSL2:
```bash
sudo bash rpi/build_rpi_image.sh
```
El script descargará la imagen oficial de Raspberry Pi OS Lite Bookworm ARM64, expandirá las particiones, inyectará los servicios y generará la imagen final comprimida en `Empaquetado/redlab-server-rpi-arm64.img.xz`.

---

## 📌 Puertos y Servicios del Appliance

| Servicio | Puerto | Descripción |
| :--- | :--- | :--- |
| **Panel Servidor & Setup UI** | `8000` | Dashboard principal (`/`) y Panel de Configuración (`/admin`) |
| **Grafana Dashboards** | `7500` | Visualización de gráficos y telemetría histórica |
| **InfluxDB v2 Base de Datos** | `4000` | Almacenamiento de series temporales de mediciones |
| **Servidor iPerf3** | `5201` | Pruebas de ancho de banda y retransmisiones (TCP y UDP) |

