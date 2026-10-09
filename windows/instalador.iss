; Inno Setup: gera OrganizadorFiscal-Setup.exe a partir da pasta criada pelo PyInstaller.
; Gerado por windows/construir.sh.

#define Nome "Organizador de Documentos Fiscais"
#define Versao "1.0.1"
#define Exe "OrganizadorFiscal.exe"

[Setup]
AppId={{6B8F2C1E-4D7A-4E59-9F31-0C2A7B5E8D14}
AppName={#Nome}
AppVersion={#Versao}
AppPublisher=FPFtech
DefaultDirName={autopf}\OrganizadorFiscal
DefaultGroupName={#Nome}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=OrganizadorFiscal-Setup
SetupIconFile=organizador.ico
UninstallDisplayIcon={app}\{#Exe}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; o organizador roda na bandeja: fecha antes de atualizar/desinstalar
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "atalho"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "build\dist\OrganizadorFiscal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\tesseract\*"; DestDir: "{app}\tesseract"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Nome}"; Filename: "{app}\{#Exe}"
Name: "{group}\Desinstalar {#Nome}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Nome}"; Filename: "{app}\{#Exe}"; Tasks: atalho

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#Exe} /F"; Flags: runhidden; RunOnceId: "EncerrarOrganizador"

[Run]
Filename: "{app}\{#Exe}"; Description: "Abrir o {#Nome} agora"; Flags: nowait postinstall skipifsilent
