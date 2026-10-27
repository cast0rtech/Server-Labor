#define MyAppName "RedLab Tester"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "RedLab"

[Setup]
AppId={{A901CED6-41A7-4B64-9C48-000000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RedLab Tester
DefaultGroupName=RedLab Tester
DisableProgramGroupPage=yes
OutputDir=Empaquetado
OutputBaseFilename=RedLabTester-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\ServidorLab.exe

[Types]
Name: "full"; Description: "Instalación Completa"
Name: "server"; Description: "Solo Servidor"
Name: "client"; Description: "Solo Cliente"

[Components]
Name: "server"; Description: "Servidor (API central)"; Types: full server
Name: "client"; Description: "Cliente (pruebas desde el switch)"; Types: full client

[Files]
Source: "Empaquetado\ServidorLab.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: server
Source: "Empaquetado\SondaCliente.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: client
Source: "tools\iperf3.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: server or client
Source: ".env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion; Components: client

[Icons]
Name: "{group}\RedLab Tester - Servidor"; Filename: "{app}\ServidorLab.exe"; Components: server
Name: "{group}\RedLab Tester - Cliente"; Filename: "{app}\SondaCliente.exe"; WorkingDir: "{app}"; Components: client
Name: "{group}\Desinstalar RedLab Tester"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""RedLab iperf3 TCP 5201"" dir=in action=allow protocol=TCP localport=5201"; Flags: runhidden; StatusMsg: "Abriendo el puerto TCP 5201 en el Firewall de Windows..."; Components: server
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""RedLab iperf3 UDP 5201"" dir=in action=allow protocol=UDP localport=5201"; Flags: runhidden; StatusMsg: "Abriendo el puerto UDP 5201 en el Firewall de Windows..."; Components: server
Filename: "{app}\ServidorLab.exe"; Description: "Iniciar el panel de control RedLab Tester"; Flags: postinstall nowait skipifsilent; Components: server
