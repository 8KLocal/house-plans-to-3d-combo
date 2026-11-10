; Per-user House Plan 3D installer.
; build-installer.ps1 writes build-defines.iss (Payload and OutputDir) before compiling.

#include "build-defines.iss"

[Setup]
AppId={{E7A1C4D2-9B63-4F18-8A05-3C6D1E9F4B27}}
AppName=House Plan 3D
AppVersion=0.2.1
AppVerName=House Plan 3D 0.2.1
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
; Close the running app when an update passes /CLOSEAPPLICATIONS.
; Do not let Windows restart it as well: /UPDATE=1 relaunches one copy.
CloseApplications=yes
RestartApplications=no
VersionInfoVersion=0.2.1.0
VersionInfoProductName=House Plan 3D
VersionInfoProductVersion=0.2.1
VersionInfoDescription=House Plan 3D setup

; out\ and any .hp3d project the user saved are not in this list, so an
; upgrade overwrites the program and leaves those files where they are.
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; pythonw.exe is the windowed interpreter: no console window.
Name: "{autoprograms}\House Plan 3D"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"
Name: "{autodesktop}\House Plan 3D"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"

[Run]
; Ordinary setup: a checkbox, skipped for /SILENT and /VERYSILENT (the CI install).
Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"; Description: "Launch House Plan 3D"; Flags: nowait postinstall skipifsilent
; In-app update passes /UPDATE=1 and should open the app again after it quits.
Filename: "{app}\python\pythonw.exe"; Parameters: "-s -m hp3d.app"; WorkingDir: "{app}"; Flags: nowait; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
