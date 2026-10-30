@echo off
chcp 65001 >nul
:: Comprobar si se ejecuta como Administrador
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Solicitando permisos de administrador...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo ======================================================================
echo           CONFIGURACIÓN DEL FIREWALL DE WINDOWS - REDLAB
echo ======================================================================
echo.
echo 1. Abriendo puertos TCP 5201, 4000, 7500, 8000 en todos los perfiles...
netsh advfirewall firewall delete rule name="RedLab Suite TCP (5201,4000,7500,8000)" >nul 2>&1
netsh advfirewall firewall add rule name="RedLab Suite TCP (5201,4000,7500,8000)" dir=in action=allow protocol=TCP localport=5201,4000,7500,8000 profile=any

echo 2. Abriendo puerto UDP 5201 en todos los perfiles...
netsh advfirewall firewall delete rule name="RedLab iPerf3 UDP (5201)" >nul 2>&1
netsh advfirewall firewall add rule name="RedLab iPerf3 UDP (5201)" dir=in action=allow protocol=UDP localport=5201 profile=any

set "TOOLS_IPERF=%~dp0tools\iperf3.exe"
if exist "%TOOLS_IPERF%" (
    echo 3. Autorizando binario iperf3 (%TOOLS_IPERF%)...
    netsh advfirewall firewall delete rule name="RedLab iPerf3 Binary" >nul 2>&1
    netsh advfirewall firewall add rule name="RedLab iPerf3 Binary" dir=in action=allow program="%TOOLS_IPERF%" profile=any
)

set "APP_SERVER=%~dp0ServidorLab.exe"
if exist "%APP_SERVER%" (
    echo 4. Autorizando ejecutable ServidorLab (%APP_SERVER%)...
    netsh advfirewall firewall delete rule name="RedLab ServidorLab App" >nul 2>&1
    netsh advfirewall firewall add rule name="RedLab ServidorLab App" dir=in action=allow program="%APP_SERVER%" profile=any
)

echo.
echo ======================================================================
echo  [OK] Reglas del Firewall de Windows aplicadas correctamente.
echo       Los puertos 5201, 4000, 7500 y 8000 están permitidos
echo       en perfiles Privado, Público y Dominio.
echo ======================================================================
echo.
pause

