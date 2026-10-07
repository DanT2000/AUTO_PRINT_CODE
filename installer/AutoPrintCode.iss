; Установщик AutoPrintCode (Inno Setup 6.3+). Обычно собирается из tools\build_exe.py:
;   ISCC /DAppVersion=0.5.0 /DAppNumVersion=0.5.0.0 /DSourceDir=..\dist\AutoPrintCode /DOutputDir=..\dist installer\AutoPrintCode.iss
;
; Ставит программу для текущего пользователя без прав администратора в %LOCALAPPDATA%\Programs\AutoPrintCode:
; туда программа может писать сама, поэтому обновляется из релизов GitHub без установщика.
; Папку data\ (образцы, настройки, журнал) не трогают ни обновление, ни удаление программы.
; Тихая установка: AutoPrintCode-X.Y.Z-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppNumVersion
  #define AppNumVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\AutoPrintCode"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef IconFile
  #define IconFile "..\build\icon.ico"
#endif

#define AppName "AutoPrintCode"
#define AppExe "AutoPrintCode.exe"
#define AppUrl "https://github.com/Recyavik/AUTO_PRINT_CODE"

[Setup]
; AppId не менять: по нему установщик находит прежнюю версию и обновляет её
AppId={{BC0222C7-A45D-4D77-A7C6-1EB7F2C4FB28}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppNumVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
; без номера: после автообновления программа сама поправит DisplayVersion в «Приложениях»
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; перед заменой файлов закрыть запущенную программу (в тихом режиме — без вопросов)
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.LaunchApp=Запустить AutoPrintCode
en.LaunchApp=Launch AutoPrintCode

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; библиотеки прежней версии убираются целиком, чтобы не смешались с новыми; data\ не трогается
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.old*"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\data"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; файлы, которые программа поставила сама при автообновлении (их нет в журнале установщика);
; data\ с образцами и настройками остаётся
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.old*"
