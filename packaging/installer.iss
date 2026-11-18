; 回溯 0.0.1 installer (Inno Setup 6)
#define MyAppName "回溯"
#define MyAppVersion "0.0.1"
#define MyAppPublisher "回溯"
#define MyAppExeName "回溯.exe"

[Setup]
AppId={{374DCA54-AA22-4FC3-AE8A-F8E2B6B28648}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=回溯-0.0.1-win64-setup
SetupIconFile=..\web\favicon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=0.0.1.0
VersionInfoDescription=回溯 0.0.1
VersionInfoProductName=回溯

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "..\dist\回溯\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "bin\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在准备内嵌 WebView2 运行时…"; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "启动回溯"; Flags: nowait postinstall skipifsilent
