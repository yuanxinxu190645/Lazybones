#define MyAppName "Lazybones"
#define MyAppVersion "1.3.7"
#define MyAppPublisher "xinyuan xu"
#define MyAppId "{{17D604BC-FFE1-49EF-971B-BBB89535B525}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userpf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=Lazybones_安装包_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\icon.ico
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern

[Files]
; --onefile 模式用这行；--onedir 模式改为 dist\Lazybones\* + recursesubdirs
Source: "dist\Lazybones.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "prompts\v1.0_basic.yaml"; DestDir: "{app}\prompts"; Flags: ignoreversion
Source: "Readme.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\Lazybones.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\Lazybones.exe"

[Run]
Filename: "{app}\Lazybones.exe"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
