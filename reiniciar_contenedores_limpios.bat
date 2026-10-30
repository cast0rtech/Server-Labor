@echo off
chcp 65001 >nul
echo ========================================================
echo   RedLab Tester - Reinicio Limpio de Contenedores Docker
echo ========================================================
echo.
echo Este script elimina contenedores y volumenes antiguos de RedLab
echo para garantizar que InfluxDB y Grafana se configuren desde cero.
echo.

docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Docker Desktop no responde. Asegurate de que Docker Desktop este abierto.
    pause
    exit /b 1
)

echo Deteniendo y eliminando volumenes anteriores...
docker compose down -v

echo.
echo Recreando contenedores InfluxDB (:4000) y Grafana (:7500)...
docker compose up -d influxdb grafana

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [OK] Contenedores recreados con exito:
    echo   - InfluxDB v2 : http://localhost:4000
    echo   - Grafana     : http://localhost:7500
    echo.
) else (
    echo [ERROR] No se pudieron iniciar los contenedores.
)

pause

