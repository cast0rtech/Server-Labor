[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot '.venv'
$PythonExe = Join-Path $VenvPath 'Scripts\python.exe'
$ActivateScript = Join-Path $VenvPath 'Scripts\Activate.ps1'
$OutputDir = Join-Path $ProjectRoot 'Empaquetado'
$DistDir = Join-Path $ProjectRoot 'dist'

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )

    Write-Host "`n==> $Description" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description falló con código de salida $LASTEXITCODE."
    }
}

try {
    Set-Location -LiteralPath $ProjectRoot

    foreach ($RequiredFile in @('cliente.py', 'servidor.py', 'instalador.iss')) {
        $RequiredPath = Join-Path $ProjectRoot $RequiredFile
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "Falta el archivo requerido: $RequiredPath"
        }
    }

    $ToolsDir = Join-Path $ProjectRoot 'tools'
    New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

    $IperfExe = Join-Path $ToolsDir 'iperf3.exe'
    $CygwinDll = Join-Path $ToolsDir 'cygwin1.dll'

    # 1. Recuperar de Empaquetado si ya existe allí
    if (-not (Test-Path -LiteralPath $IperfExe -PathType Leaf)) {
        $EmpaquetadoIperf = Join-Path $OutputDir 'iperf3.exe'
        if (Test-Path -LiteralPath $EmpaquetadoIperf -PathType Leaf) {
            Copy-Item -LiteralPath $EmpaquetadoIperf -Destination $IperfExe -Force
        }
    }
    if (-not (Test-Path -LiteralPath $CygwinDll -PathType Leaf)) {
        $EmpaquetadoCygwin = Join-Path $OutputDir 'cygwin1.dll'
        if (Test-Path -LiteralPath $EmpaquetadoCygwin -PathType Leaf) {
            Copy-Item -LiteralPath $EmpaquetadoCygwin -Destination $CygwinDll -Force
        }
    }

    # 2. Buscar en PATH o rutas comunes de WinGet
    if (-not (Test-Path -LiteralPath $IperfExe -PathType Leaf)) {
        $InstalledIperf = Get-Command 'iperf3.exe' -ErrorAction SilentlyContinue
        if ($InstalledIperf) {
            Copy-Item -LiteralPath $InstalledIperf.Source -Destination $IperfExe -Force
            $CandidateDll = Join-Path (Split-Path -Parent $InstalledIperf.Source) 'cygwin1.dll'
            if (Test-Path -LiteralPath $CandidateDll -PathType Leaf) {
                Copy-Item -LiteralPath $CandidateDll -Destination $CygwinDll -Force
            }
        } else {
            $WingetSearch = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Filter 'iperf3.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($WingetSearch) {
                Copy-Item -LiteralPath $WingetSearch.FullName -Destination $IperfExe -Force
                $CandidateDll = Join-Path (Split-Path -Parent $WingetSearch.FullName) 'cygwin1.dll'
                if (Test-Path -LiteralPath $CandidateDll -PathType Leaf) {
                    Copy-Item -LiteralPath $CandidateDll -Destination $CygwinDll -Force
                }
            }
        }
    }

    # 3. Descargar automáticamente si sigue faltando
    if (-not (Test-Path -LiteralPath $IperfExe -PathType Leaf)) {
        Write-Host "No se encontró iperf3.exe localmente. Descargando compilación oficial para Windows..." -ForegroundColor Yellow
        try {
            $ZipUrl = 'https://github.com/ar51an/iperf3-win-builds/releases/download/3.22/iperf-3.22-win64.zip'
            $TempZip = Join-Path $ProjectRoot 'iperf3_temp.zip'
            Invoke-WebRequest -Uri $ZipUrl -OutFile $TempZip -UseBasicParsing
            Expand-Archive -Path $TempZip -DestinationPath $ToolsDir -Force
            Remove-Item -LiteralPath $TempZip -Force -ErrorAction SilentlyContinue
            Write-Host "iperf3.exe y dependencias descargadas correctamente en 'tools\'." -ForegroundColor Green
        } catch {
            Write-Host "No se pudo descargar iperf3 automáticamente: $($_.Exception.Message)" -ForegroundColor Red
        }
    }

    if (-not (Test-Path -LiteralPath $IperfExe -PathType Leaf)) {
        throw "Falta 'tools\iperf3.exe' y no se pudo obtener automáticamente. Por favor, coloca 'iperf3.exe' y 'cygwin1.dll' en la carpeta 'tools\' o 'Empaquetado\'."
    }

    # Mantener sincronizado con Empaquetado
    Copy-Item -LiteralPath $IperfExe -Destination (Join-Path $OutputDir 'iperf3.exe') -Force
    if (Test-Path -LiteralPath $CygwinDll -PathType Leaf) {
        Copy-Item -LiteralPath $CygwinDll -Destination (Join-Path $OutputDir 'cygwin1.dll') -Force
    }

    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        Write-Host 'Creando entorno virtual .venv...' -ForegroundColor Cyan
        & python -m venv $VenvPath
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo crear .venv (código $LASTEXITCODE). Comprueba que Python esté instalado y en PATH."
        }
    }

    if (-not (Test-Path -LiteralPath $ActivateScript -PathType Leaf)) {
        throw "No se encontró el activador del entorno virtual: $ActivateScript"
    }

    . $ActivateScript
    if ($env:VIRTUAL_ENV -ne $VenvPath) {
        throw 'No se pudo activar el entorno virtual .venv.'
    }

    Invoke-CheckedCommand -Description 'Actualizando pip' -Command {
        & $PythonExe -m pip install --upgrade pip
    }

    Invoke-CheckedCommand -Description 'Instalando dependencias de compilación y ejecución' -Command {
        & $PythonExe -m pip install pyinstaller influxdb-client python-dotenv fastapi uvicorn
    }

    $PyInstallerAddBinary = @()
    if (Test-Path "tools\iperf3.exe") {
        $PyInstallerAddBinary += '--add-binary', 'tools/iperf3.exe;.'
    }
    if (Test-Path "tools\cygwin1.dll") {
        $PyInstallerAddBinary += '--add-binary', 'tools/cygwin1.dll;.'
    }

    Invoke-CheckedCommand -Description 'Compilando cliente.py como SondaCliente.exe con GUI' -Command {
        & $PythonExe -m PyInstaller --noconfirm --clean --onefile --noconsole --name SondaCliente @PyInstallerAddBinary cliente.py
    }

    Invoke-CheckedCommand -Description 'Compilando servidor.py como ServidorLab.exe' -Command {
        & $PythonExe -m PyInstaller --noconfirm --clean --onefile --console --name ServidorLab @PyInstallerAddBinary servidor.py
    }

    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    foreach ($ExecutableName in @('SondaCliente.exe', 'ServidorLab.exe')) {
        $BuiltExecutable = Join-Path $DistDir $ExecutableName
        if (-not (Test-Path -LiteralPath $BuiltExecutable -PathType Leaf)) {
            throw "PyInstaller terminó, pero no generó el archivo esperado: $BuiltExecutable"
        }
        $Destination = Join-Path $OutputDir $ExecutableName
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Force
        }
        Move-Item -LiteralPath $BuiltExecutable -Destination $Destination -Force
    }

    # Asegurar iperf3 y cygwin1 en la carpeta Empaquetado para ejecucion portable
    if (Test-Path "tools\iperf3.exe") {
        Copy-Item -LiteralPath "tools\iperf3.exe" -Destination (Join-Path $OutputDir "iperf3.exe") -Force
    }
    if (Test-Path "tools\cygwin1.dll") {
        Copy-Item -LiteralPath "tools\cygwin1.dll" -Destination (Join-Path $OutputDir "cygwin1.dll") -Force
    }

    $CompilerCandidates = @()
    foreach ($InstallRoot in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LocalAppData 'Programs'))) {
        if ($InstallRoot) {
            foreach ($InnoDir in @('Inno Setup 6', 'Inno Setup 7', 'Inno Setup 5')) {
                $Candidate = Join-Path $InstallRoot "$InnoDir\ISCC.exe"
                if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
                    $CompilerCandidates += $Candidate
                }
            }
        }
    }

    $CompilerCommand = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($CompilerCommand) {
        $ISCC = $CompilerCommand.Source
    } elseif ($CompilerCandidates) {
        $ISCC = $CompilerCandidates[0]
    } else {
        throw 'No se encontró ISCC.exe. Instala Inno Setup 6 o agrega ISCC.exe al PATH.'
    }

    $InstallerScript = Join-Path $ProjectRoot 'instalador.iss'
    if (-not (Test-Path -LiteralPath $InstallerScript -PathType Leaf)) {
        throw "No se encontró el script de Inno Setup: $InstallerScript"
    }

    Invoke-CheckedCommand -Description 'Compilando instalador con Inno Setup' -Command {
        & $ISCC $InstallerScript
    }

    $InstallerPath = Join-Path $OutputDir 'RedLabTester-Setup.exe'
    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "ISCC terminó, pero no generó el instalador esperado: $InstallerPath"
    }

    foreach ($UpdateScript in @('update_servidor.iss', 'update_sonda.iss')) {
        $UpdatePath = Join-Path $ProjectRoot $UpdateScript
        if (Test-Path -LiteralPath $UpdatePath -PathType Leaf) {
            Invoke-CheckedCommand -Description "Compilando $UpdateScript" -Command {
                & $ISCC $UpdatePath
            }
        }
    }

    # Limpiar solamente temporales generados por PyInstaller en la raíz del proyecto.
    foreach ($TemporaryDirectory in @('build', 'dist')) {
        $TemporaryPath = Join-Path $ProjectRoot $TemporaryDirectory
        if (Test-Path -LiteralPath $TemporaryPath -PathType Container) {
            Remove-Item -LiteralPath $TemporaryPath -Recurse -Force
        }
    }
    Get-ChildItem -LiteralPath $ProjectRoot -Filter '*.spec' -File -ErrorAction SilentlyContinue |
        Remove-Item -Force

    Write-Host "`nCompilación y empaquetado completados correctamente." -ForegroundColor Green
    Write-Host "Instalador: $InstallerPath" -ForegroundColor Green
} catch {
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($env:VIRTUAL_ENV -eq $VenvPath) {
        deactivate
    }
}
