@echo off
chcp 65001 >nul
echo ========================================================
echo   RedLab Tester - Iniciar Contenedores InfluxDB y Grafana
echo ========================================================
echo.

docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Docker Desktop no responde o el motor de Docker esta detenido.
    echo Intentando iniciar Docker Desktop...
    start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" 2>nul
    echo Por favor, espera a que Docker Desktop termine de arrancar (icono verde en la barra)
    echo y vuelve a ejecutar este script.
    echo.
    pause
    exit /b 1
)

echo Iniciando servicios InfluxDB (:4000) y Grafana (:7500)...
docker compose up -d influxdb grafana

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [OK] Contenedores iniciados correctamente:
    echo   - InfluxDB v2 : http://localhost:4000
    echo   - Grafana     : http://localhost:7500 (Dashboard listo sin password)
    echo.
) else (
    echo.
    echo [ERROR] Hubo un problema al iniciar los contenedores.
    echo Si hay conflictos con volumenes antiguos, ejecuta:
    echo   reiniciar_contenedores_limpios.bat
    echo.
)

pause

