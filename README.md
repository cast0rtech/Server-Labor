# RedLab Tester

RedLab Tester reúne un servidor de pruebas de red, almacenamiento de métricas y un cliente de medición para los equipos conectados a los switches. La infraestructura se ejecuta con Docker Compose; el cliente y el panel FastAPI pueden ejecutarse con Python o empaquetarse como ejecutables de Windows.

## Componentes

- **InfluxDB 2.7**: organización `red_lab`, bucket `iperf_stats` y token inicial `lab_token_secure`.
- **Grafana**: accesible en el puerto `3000`, con la fuente InfluxDB aprovisionada automáticamente.
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
| InfluxDB | `http://localhost:8086` |
| Grafana | `http://localhost:3000` |
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
INFLUX_URL=http://192.168.1.10:8086
INFLUX_ORG=red_lab
INFLUX_BUCKET=iperf_stats
INFLUX_TOKEN=lab_token_secure
```

`IPERF_SERVER_IP` es la dirección del servidor iperf3. `INFLUX_URL` es la dirección de InfluxDB accesible desde el cliente; si el cliente corre en el mismo equipo que Docker, puede ser `http://localhost:8086`. No uses `localhost` si InfluxDB está en otro equipo.

Instala y ejecuta el cliente:

```powershell
python -m pip install -r requirements.txt
python cliente.py
```

El cliente escribe la medición `iperf_tcp` con los campos `bandwidth_mbps` y `tcp_retransmits`, y los tags `hostname` y `switch_id`.

## Compilar y crear el instalador de Windows

`build.ps1` crea el entorno `.venv`, instala las dependencias, genera `SondaCliente.exe` y `ServidorLab.exe` en modo consola y `--onefile`, y compila `instalador.iss`. El resultado esperado es:

```text
Empaquetado\RedLabTester-Setup.exe
```

Requisitos previos:

- Python disponible en `PATH`.
- Inno Setup 6 instalado; `ISCC.exe` debe estar en `PATH` o en una de las rutas estándar de Inno Setup 6.
- Un `iperf3.exe` para Windows disponible en `PATH` o en `tools\iperf3.exe`. Si solo está en `PATH`, el script lo copia a `tools` para el instalador.

Desde PowerShell, en la carpeta del proyecto:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1
```

El instalador ofrece los componentes **Instalación Completa**, **Solo Servidor** y **Solo Cliente**. Al seleccionar el servidor, agrega reglas de entrada de Windows Firewall para TCP y UDP 5201. El componente cliente instala `.env.example`; crea junto a `SondaCliente.exe` un archivo `.env` con la configuración específica de esa máquina.

## Compatibilidad de iperf3 en Windows

El módulo Python `iperf3` utiliza la biblioteca nativa `libiperf`. El proyecto oficial de iperf3 no ofrece soporte oficial para Windows, por lo que compilar `cliente.py` con PyInstaller no garantiza que funcione en Windows sin una versión compatible de esa biblioteca nativa. El instalador incluye `iperf3.exe` como herramienta independiente, pero el `cliente.py` actual utiliza el módulo Python y no invoca ese ejecutable. Valida la disponibilidad de `libiperf` en los equipos destino antes de distribuir el cliente.

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
