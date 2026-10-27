@echo off
setlocal
cd /d "%~dp0"
title Compilador RedLab Tester
color 0A

echo ===================================================
echo      Iniciando compilacion de RedLab Tester
echo ===================================================
echo.

set "WINGET_EJECUTADO=0"

:buscar_inno
set "ISCC="

:: 1. Comprobar si ISCC ya esta disponible directamente en el PATH del sistema
where ISCC.exe >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do (
        if not defined ISCC call :set_iscc "%%~I"
    )
)
if defined ISCC goto :inno_encontrado

:: 2. Busqueda dinamica en Program Files, Program Files (x86) y LocalAppData
for %%D in (
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramW6432%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%ProgramW6432%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%ProgramW6432%\Inno Setup 5\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
    "%LocalAppData%\Programs\Inno Setup 5\ISCC.exe"
) do (
    if not defined ISCC call :check_file "%%~D"
)
if defined ISCC goto :inno_encontrado

:: 3. Busqueda en el Registro de Windows (InstallLocation)
for %%K in (
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKCU\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKCU\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    "HKCU\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
) do (
    if not defined ISCC (
        for /f "tokens=2*" %%A in ('reg query %%K /v "InstallLocation" 2^>nul ^| findstr /i "InstallLocation"') do (
            if not defined ISCC call :check_dir "%%~B"
        )
    )
)
if defined ISCC goto :inno_encontrado

:: 4. Busqueda en el Registro de Windows (Inno Setup: App Path)
for %%K in (
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKCU\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKCU\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1"
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    "HKCU\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
) do (
    if not defined ISCC (
        for /f "tokens=5*" %%A in ('reg query %%K /v "Inno Setup: App Path" 2^>nul ^| findstr /i "REG_"') do (
            if not defined ISCC call :check_dir "%%~B"
        )
    )
)
if defined ISCC goto :inno_encontrado

:: 5. Busqueda en el Registro de Windows (App Paths de ISCC.exe y Compil32.exe)
for %%K in (
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\ISCC.exe"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\ISCC.exe"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\ISCC.exe"
    "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Compil32.exe"
    "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Compil32.exe"
    "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\Compil32.exe"
) do (
    if not defined ISCC (
        for /f "tokens=2*" %%A in ('reg query %%K /ve 2^>nul ^| findstr /i "REG_"') do (
            if not defined ISCC call :check_app_path "%%~B"
        )
    )
)
if defined ISCC goto :inno_encontrado

:: 6. Si no se encontro Inno Setup, ofrecer instalacion automatica con winget
if "%WINGET_EJECUTADO%"=="1" (
    echo [ERROR] No se pudo localizar ISCC.exe incluso tras la instalacion con winget.
    echo Por favor, instala Inno Setup manualmente o agregalo a la variable de entorno PATH.
    pause
    exit /b 1
)

echo [ADVERTENCIA] No se encontro Inno Setup (ISCC.exe) en las rutas estandar ni en el Registro.
echo Inno Setup es indispensable para generar el instalador ejecutable final.
echo.
set "RESP_WINGET="
set /p RESP_WINGET="[?] Deseas instalar Inno Setup automaticamente ahora con winget? (S/N) [S]: "
if not defined RESP_WINGET set "RESP_WINGET=S"

if /i "%RESP_WINGET:~0,1%"=="S" goto :instalar_winget
if /i "%RESP_WINGET:~0,1%"=="Y" goto :instalar_winget

echo.
echo [CANCELADO] Compilacion cancelada: No se puede generar el instalador sin Inno Setup.
pause
exit /b 1

:instalar_winget
echo.
echo ===================================================
echo     Instalando Inno Setup mediante winget...
echo ===================================================
echo.
where winget >nul 2>&1
if errorlevel 1 (
    echo [ERROR] El comando winget no esta disponible en este sistema Windows.
    echo Por favor, descarga e instala Inno Setup manualmente desde:
    echo https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

set "WINGET_EJECUTADO=1"
echo Ejecutando: winget install -e --id JRSoftware.InnoSetup
winget install -e --id JRSoftware.InnoSetup
echo.
echo Reescaneando el sistema para detectar la nueva instalacion de Inno Setup...
echo.
goto :buscar_inno

:inno_encontrado
echo [OK] Inno Setup detectado en:
echo      "%ISCC%"
echo.

:: 2. Instalar requerimientos
echo [1/5] Instalando dependencias de Python...
pip install pyinstaller iperf3 influxdb-client python-dotenv
echo.

:: 3. Limpieza previa
echo [2/5] Limpiando carpetas de compilaciones anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del /q *.spec
echo.

:: 4. Compilar con PyInstaller
echo [3/5] Compilando cliente.py y servidor.py a .exe...
pyinstaller --onefile --name SondaCliente cliente.py
pyinstaller --onefile --name ServidorLab servidor.py
echo.

:: 5. Mover a la carpeta Empaquetado
echo [4/5] Moviendo archivos ejecutables...
if not exist Empaquetado mkdir Empaquetado

if exist dist\SondaCliente.exe (
    move /Y dist\SondaCliente.exe Empaquetado\SondaCliente.exe >nul
) else (
    echo [ERROR] Fallo la compilacion del Cliente.
    pause
    exit /b 1
)

if exist dist\ServidorLab.exe (
    move /Y dist\ServidorLab.exe Empaquetado\ServidorLab.exe >nul
) else (
    echo [ERROR] Fallo la compilacion del Servidor.
    pause
    exit /b 1
)
echo Ejecutables listos en carpeta Empaquetado.
echo.

:: RECORDATORIO Y SINCRONIZACION IPERF3
if exist "Empaquetado\iperf3.exe" (
    if not exist tools mkdir tools
    copy /Y "Empaquetado\iperf3.exe" "tools\iperf3.exe" >nul
)
if exist "tools\iperf3.exe" (
    if not exist Empaquetado mkdir Empaquetado
    copy /Y "tools\iperf3.exe" "Empaquetado\iperf3.exe" >nul
)

if not exist "tools\iperf3.exe" if not exist "Empaquetado\iperf3.exe" (
    color 0E
    echo.
    echo [ADVERTENCIA] No se encontro "iperf3.exe" en la carpeta "tools" ni "Empaquetado".
    echo Inno Setup requiere "iperf3.exe" para poder generar el instalador.
    echo Por favor, copia "iperf3.exe" en la carpeta "Empaquetado" o "tools".
    echo Presiona una tecla tras copiarlo para continuar...
    pause
    if exist "Empaquetado\iperf3.exe" (
        if not exist tools mkdir tools
        copy /Y "Empaquetado\iperf3.exe" "tools\iperf3.exe" >nul
    )
    if exist "tools\iperf3.exe" (
        if not exist Empaquetado mkdir Empaquetado
        copy /Y "tools\iperf3.exe" "Empaquetado\iperf3.exe" >nul
    )
    color 0A
    echo.
)

:: 6. Crear el instalador final
echo [5/5] Generando instalador .exe con Inno Setup...
"%ISCC%" instalador.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la generacion del instalador con Inno Setup.
    pause
    exit /b 1
)
echo.

:: 7. Limpieza final
echo Limpiando basura temporal...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del /q *.spec

color 0A
echo ===================================================
echo   [EXITO] El instalador final ha sido generado.
echo ===================================================
pause
exit /b 0

:: ===================================================
:: Subrutinas auxiliares de busqueda
:: ===================================================
:check_file
if exist "%~1" set "ISCC=%~1"
exit /b

:check_dir
if exist "%~1\ISCC.exe" set "ISCC=%~1\ISCC.exe"
exit /b

:check_app_path
if exist "%~1" (
    if /i "%~nx1"=="ISCC.exe" (
        set "ISCC=%~1"
    ) else (
        if exist "%~dp1ISCC.exe" set "ISCC=%~dp1ISCC.exe"
    )
)
exit /b

:set_iscc
if exist "%~1" set "ISCC=%~1"
exit /b