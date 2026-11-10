; Per-user House Plan 3D installer.
; build-installer.ps1 writes build-defines.iss (Payload and OutputDir) before compiling.

#include "build-defines.iss"

[Setup]
AppId={{E7A1C4D2-9B63-4F18-8A05-3C6D1E9F4B27}}
AppName=House Plan 3D
AppVersion=0.1.0
AppVerName=House Plan 3D 0.1.0
AppPublisher=8K by AK
DefaultDirName={localappdata}\House Plan 3D
DisableProgramGroupPage=yes
; Never asks for an administrator password.
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=House-Plan-3D-Setup
; normal, not ultra64: ultra64 on a multi-GB payload sits for over an hour with no output.
Compression=lzma2/normal
SolidCompression=no
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DiskSpanning=no
UninstallDisplayName=House Plan 3D
UninstallDisplayIcon={app}\python\pythonw.exe
InfoBeforeFile=INSTALL_INFO.txt
CloseApplications=no
RestartApplications=no
VersionInfoVersion=0.1.0.0
VersionInfoProductName=House Plan 3D
VersionInfoProductVersion=0.1.0
VersionInfoDescription=House Plan 3D setup

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; pythonw.exe is the windowed interpreter: no console window.
Name: "{autoprograms}\House Plan 3D"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"
Name: "{autodesktop}\House Plan 3D"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"; Description: "Launch House Plan 3D"; Flags: nowait postinstall skipifsilent
