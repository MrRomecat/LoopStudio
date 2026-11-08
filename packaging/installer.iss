; Установщик LoopStudio (Inno Setup 6).
; Сборка: сначала PyInstaller (см. scripts/build_dist.ps1), затем:
;   iscc packaging\installer.iss

#define AppName "LoopStudio"
#define AppVersion "1.5.4"
#define AppPublisher "LoopStudio"
#define AppExe "LoopStudio.exe"

[Setup]
AppId={{7C2E8B14-5A14-4B7E-9A0E-52F5A5C2D9B1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist\installer
OutputBaseFilename=LoopStudio-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=..\assets\icon.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
; обновление поверх: файлы СТАРОЙ версии, которых нет в новой (переименованные
; DLL Qt, старые .pyd), не должны оставаться — их подхват ломает запуск
Type: filesandordirs; Name: "{app}\PySide6"
Type: filesandordirs; Name: "{app}\shiboken6"
Type: filesandordirs; Name: "{app}\cryptography"
Type: filesandordirs; Name: "{app}\cryptography-*.dist-info"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\python*.dll"
; пользовательские данные не трогаем: они в {userappdata}\LoopStudio

[Files]
Source: "..\dist\LoopStudio\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
