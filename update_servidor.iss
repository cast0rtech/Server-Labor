; Actualización de RedLab Tester - SERVIDOR (solo reemplaza ServidorLab.exe).
; No toca .env ni la configuración. Requiere tener RedLab Tester instalado.
#define MyAppName "RedLab Tester - Actualización Servidor"
#define MyAppVersion "1.0.1"
#define MyExe "ServidorLab.exe"
#define MainAppId "{A901CED6-41A7-4B64-9C48-000000000001}"

[Setup]
AppId={{A901CED6-41A7-4B64-9C48-0000000000A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=RedLab
DefaultDirName={code:GetInstallDir}
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
CreateUninstallRegKey=no
Uninstallable=no
OutputDir=Empaquetado
OutputBaseFilename=RedLabTester-Update-Servidor-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin

[Files]
Source: "Empaquetado\{#MyExe}"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "{app}\{#MyExe}"; Description: "Iniciar RedLab Tester - Servidor"; Flags: postinstall nowait skipifsilent

[Code]
function GetInstallDir(Param: String): String;
var
  Dir: String;
begin
  Result := ExpandConstant('{autopf}\RedLab Tester');
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MainAppId}_is1', 'InstallLocation', Dir) and (Dir <> '') then
    Result := RemoveBackslashUnlessRoot(Dir);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not DirExists(GetInstallDir('')) then
  begin
    MsgBox('No se encontró una instalación de RedLab Tester. Instala primero RedLabTester-Setup.exe.', mbError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyExe}', '', SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(800);
  Result := '';
end;

