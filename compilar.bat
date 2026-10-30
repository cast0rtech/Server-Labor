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

:: 2. Detectar Python y verificar pip
set "PYTHON_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
    echo [ERROR] No se encontro Python en el PATH del sistema.
    echo Asegurate de tener Python instalado y marcar la casilla "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [OK] Python detectado: %PYTHON_CMD%
echo.

:: 3. Instalar requerimientos
echo [1/6] Instalando dependencias con %PYTHON_CMD% -m pip...
%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install pyinstaller influxdb-client python-dotenv fastapi uvicorn
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de paquetes de Python.
    pause
    exit /b 1
)
echo.

:: 4. Preparar y sincronizar dependencias de iperf3
echo [2/6] Verificando dependencias de iperf3...
if not exist tools mkdir tools
if not exist Empaquetado mkdir Empaquetado

:: 4.1 Si existe en Empaquetado pero no en tools, recuperar a tools
if not exist "tools\iperf3.exe" if exist "Empaquetado\iperf3.exe" (
    copy /Y "Empaquetado\iperf3.exe" "tools\iperf3.exe" >nul
)
if not exist "tools\cygwin1.dll" if exist "Empaquetado\cygwin1.dll" (
    copy /Y "Empaquetado\cygwin1.dll" "tools\cygwin1.dll" >nul
)

:: 4.2 Si no existe en tools, buscar en PATH
if not exist "tools\iperf3.exe" (
    for /f "delims=" %%I in ('where iperf3.exe 2^>nul') do (
        if not exist "tools\iperf3.exe" (
            copy /Y "%%~I" "tools\iperf3.exe" >nul
            if exist "%%~dpIcygwin1.dll" copy /Y "%%~dpIcygwin1.dll" "tools\cygwin1.dll" >nul
        )
    )
)

:: 4.3 Si sigue faltando, intentar descargar compilacion oficial automaticamente
if not exist "tools\iperf3.exe" (
    echo [INFO] Descargando binarios de iperf3 para Windows...
    powershell -NoProfile -Command "try { $u='https://github.com/ar51an/iperf3-win-builds/releases/download/3.22/iperf-3.22-win64.zip'; $z='iperf3_temp.zip'; Invoke-WebRequest -Uri $u -OutFile $z -UseBasicParsing; Expand-Archive -Path $z -DestinationPath 'tools' -Force; Remove-Item $z -Force } catch { }" >nul 2>&1
)

:: 4.4 Sincronizar hacia Empaquetado
if exist "tools\iperf3.exe" copy /Y "tools\iperf3.exe" "Empaquetado\iperf3.exe" >nul
if exist "tools\cygwin1.dll" copy /Y "tools\cygwin1.dll" "Empaquetado\cygwin1.dll" >nul

:: 4.5 Si todavia falta, pausar y verificar obligatoriamente
if not exist "tools\iperf3.exe" (
    color 0E
    echo.
    echo [ADVERTENCIA] No se encontro "iperf3.exe" en la carpeta "tools" ni "Empaquetado".
    echo Inno Setup y PyInstaller requieren "iperf3.exe" para poder generar el instalador.
    echo Por favor, copia "iperf3.exe" y "cygwin1.dll" en la carpeta "tools" o "Empaquetado".
    echo Presiona una tecla tras haberlo copiado para continuar...
    pause
    if exist "Empaquetado\iperf3.exe" copy /Y "Empaquetado\iperf3.exe" "tools\iperf3.exe" >nul
    if not exist "tools\iperf3.exe" (
        color 0C
        echo.
        echo [ERROR] Sigue sin encontrarse "iperf3.exe". Compilacion abortada.
        pause
        exit /b 1
    )
    if exist "tools\iperf3.exe" copy /Y "tools\iperf3.exe" "Empaquetado\iperf3.exe" >nul
    if exist "tools\cygwin1.dll" copy /Y "tools\cygwin1.dll" "Empaquetado\cygwin1.dll" >nul
    color 0A
)
echo [OK] iperf3 y librerias listos.
echo.

:: 5. Limpieza previa
echo [3/6] Limpiando carpetas de compilaciones anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del /q *.spec
echo.

:: 6. Compilar con PyInstaller invocando el modulo de Python directamente
echo [4/6] Compilando cliente.py (con GUI) y servidor.py a .exe...
set "ADD_BIN="
if exist "tools\iperf3.exe" set "ADD_BIN=--add-binary tools/iperf3.exe;."
if exist "tools\cygwin1.dll" set "ADD_BIN=%ADD_BIN% --add-binary tools/cygwin1.dll;."

%PYTHON_CMD% -m PyInstaller --noconfirm --clean --onefile --noconsole --name SondaCliente %ADD_BIN% cliente.py
if errorlevel 1 (
    echo [ERROR] PyInstaller fallo al compilar el Cliente.
    pause
    exit /b 1
)

%PYTHON_CMD% -m PyInstaller --noconfirm --clean --onefile --console --name ServidorLab %ADD_BIN% servidor.py
if errorlevel 1 (
    echo [ERROR] PyInstaller fallo al compilar el Servidor.
    pause
    exit /b 1
)
echo.

:: 7. Mover a la carpeta Empaquetado
echo [5/6] Moviendo archivos ejecutables...
if not exist Empaquetado mkdir Empaquetado

if exist dist\SondaCliente.exe (
    move /Y dist\SondaCliente.exe Empaquetado\SondaCliente.exe >nul
) else (
    echo [ERROR] No se encontro el binario compilado del Cliente.
    pause
    exit /b 1
)

if exist dist\ServidorLab.exe (
    move /Y dist\ServidorLab.exe Empaquetado\ServidorLab.exe >nul
) else (
    echo [ERROR] No se encontro el binario compilado del Servidor.
    pause
    exit /b 1
)
echo Ejecutables listos en carpeta Empaquetado.
echo.

:: Asegurar sincronizacion final de binarios auxiliares
if exist "tools\iperf3.exe" copy /Y "tools\iperf3.exe" "Empaquetado\iperf3.exe" >nul
if exist "tools\cygwin1.dll" copy /Y "tools\cygwin1.dll" "Empaquetado\cygwin1.dll" >nul

:: 8. Crear el instalador final
echo [6/6] Generando instalador .exe con Inno Setup...
"%ISCC%" instalador.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la generacion del instalador con Inno Setup.
    pause
    exit /b 1
)
echo.

:: 7. Instaladores de actualizacion (Servidor y Sonda)
for %%U in (update_servidor.iss update_sonda.iss) do (
    if exist "%%U" (
        echo Generando %%U...
        "%ISCC%" "%%U"
        if errorlevel 1 (
            echo [ERROR] Fallo la generacion de %%U.
            pause
            exit /b 1
        )
    )
)
echo.

:: 7b. Limpieza final
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