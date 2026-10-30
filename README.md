# RedLab Tester

RedLab Tester reúne un servidor de pruebas de red, almacenamiento de métricas y un cliente de medición para los equipos conectados a los switches. La infraestructura se ejecuta con Docker Compose; el cliente y el panel FastAPI pueden ejecutarse con Python o empaquetarse como ejecutables de Windows.

## Componentes

- **InfluxDB 2.7**: organización `red_lab`, bucket `iperf_stats` y token inicial `lab_token_secure`.
- **Grafana**: accesible en el puerto `7500`, con la fuente InfluxDB aprovisionada automáticamente.
- **iperf3 servidor**: contenedor `networkstatic/iperf3`, escuchando en TCP y UDP `5201`.
- **API FastAPI**: `servidor.py`, endpoint `GET /status` en el puerto `8000`.
- **Cliente de medición**: `cliente.py`, prueba TCP de 30 segundos y escritura síncrona de métricas a InfluxDB.

## Iniciar la infraestructura

Se necesita Docker Desktop con Docker Compose. Desde la carpeta del proyecto:

```powershell
docker compose up -d
docker compose ps
```

Interfaces y puertos:

| Servicio | Dirección |
|---|---|
| InfluxDB | `http://localhost:4000` |
| Grafana | `http://localhost:7500` |
| iperf3 | TCP/UDP `5201` |
| API RedLab | `http://localhost:8000/status` |

El usuario inicial de Grafana es `admin` y su contraseña es `admin_change_me`. InfluxDB se inicializa con el usuario `redlab` y la contraseña `redlab_change_me`. Cambia las credenciales de ejemplo antes de utilizar la instalación en una red compartida. La configuración inicial de InfluxDB solo se aplica cuando el volumen de datos está vacío.

Para detener los contenedores sin borrar los datos:

```powershell
docker compose down
```

## API del servidor

Instala las dependencias y ejecuta FastAPI:

```powershell
python -m pip install -r requirements.txt
python servidor.py
```

Comprueba el estado visitando `http://localhost:8000/status`. La respuesta esperada contiene `"status":"ready"`.

## Configurar y ejecutar el cliente

Crea una configuración local a partir de la plantilla:

```powershell
Copy-Item .env.example .env
```

Edita `.env` con los valores del laboratorio:

```dotenv
IPERF_SERVER_IP=192.168.1.10
SWITCH_ID=switch-01
INFLUX_URL=http://192.168.1.10:4000
INFLUX_ORG=red_lab
INFLUX_BUCKET=iperf_stats
INFLUX_TOKEN=lab_token_secure
```

`IPERF_SERVER_IP` es la dirección del servidor iperf3. `INFLUX_URL` es la dirección de InfluxDB accesible desde el cliente; si el cliente corre en el mismo equipo que Docker, puede ser `http://localhost:4000`. No uses `localhost` si InfluxDB está en otro equipo.

Al ejecutar `cliente.py` (o `SondaCliente.exe`), se abre la **Interfaz Gráfica (GUI)** interactiva donde puedes introducir directamente la **IP del servidor**, el puerto, el identificador del switch y la duración de la prueba, además de visualizar en tiempo real el ancho de banda (Mbps) y las retransmisiones TCP.

Si deseas ejecutarlo de forma desatendida por línea de comandos:

```powershell
python cliente.py --cli --server 192.168.1.10 --duration 30
```

## Compatibilidad de iperf3 en Windows

El cliente utiliza directamente el binario nativo Windows `iperf3.exe` (junto con `cygwin1.dll`), resolviendo la salida en formato JSON estructurado. Esto garantiza compatibilidad nativa total y alto rendimiento en Windows sin requerir bibliotecas compartidas de Linux.

## Archivos principales

```text
docker-compose.yml             Infraestructura Docker
servidor.py                    API FastAPI
cliente.py                     Cliente de medición
.env.example                   Plantilla de configuración del cliente
requirements.txt               Dependencias Python
instalador.iss                 Definición del instalador Inno Setup
build.ps1                      Automatización de compilación y empaquetado
grafana/provisioning/           Configuración automática de InfluxDB en Grafana
```
