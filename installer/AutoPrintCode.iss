; Установщик AutoPrintCode (Inno Setup 6.3+). Обычно собирается из tools\build_exe.py:
;   ISCC /DAppVersion=0.5.0 /DAppNumVersion=0.5.0.0 /DSourceDir=..\dist\AutoPrintCode /DOutputDir=..\dist installer\AutoPrintCode.iss
;
; Первая страница — «Как установить»:
; - Установить (по умолчанию). Для текущего пользователя, без прав администратора, в
;   %LOCALAPPDATA%\Programs\AutoPrintCode (папку можно выбрать): ярлык в «Пуске», удаление через
;   «Параметры → Приложения». В папку программы можно писать — она обновляется из релизов GitHub сама.
; - Если программа уже установлена — «Обновить установленную версию»: та же папка, без лишних вопросов.
; - Портативная версия: просто папка с программой (например, на флешке). Ничего не прописывается в Windows —
;   ни ярлыков, ни записи в «Приложениях»; образцы и настройки — в папке data рядом с программой.
; Папку data\ (образцы, настройки, журнал) не трогают ни обновление, ни удаление программы.
;
; Тихая установка (и обновление поверх): AutoPrintCode-X.Y.Z-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
; Тихо и портативно: ... /VERYSILENT /PORTABLE=1 /DIR="E:\AutoPrintCode"

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
; AppId не менять: по нему установщик находит прежнюю версию и обновляет её
#define AppGuid "{BC0222C7-A45D-4D77-A7C6-1EB7F2C4FB28}"

[Setup]
AppId={#StringChange(AppGuid, "{", "{{")}
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
; папку спрашиваем сами: при обновлении страница пропускается (ShouldSkipPage)
DisableDirPage=no
UsePreviousAppDir=yes
DirExistsWarning=no
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
; портативная версия — без деинсталлятора и без записи в «Приложениях»
Uninstallable=not IsPortable
CreateUninstallRegKey=not IsPortable
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; перед заменой файлов закрыть запущенную программу (в тихом режиме — без вопросов)
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.LaunchApp=Запустить AutoPrintCode
en.LaunchApp=Launch AutoPrintCode
ru.ModeCaption=Как установить
en.ModeCaption=How to install
ru.ModeDescription=Обычная установка или портативная версия
en.ModeDescription=Regular installation or portable copy
ru.ModeText=Установка: AutoPrintCode появится в меню «Пуск», будет обновляться сам, удаляется через «Параметры → Приложения». Права администратора не нужны.%n%nПортативная версия: просто папка с программой — например, на флешке. В Windows ничего не прописывается; образцы и настройки хранятся в папке data рядом с программой.
en.ModeText=Install: AutoPrintCode appears in the Start menu, updates itself and is removed via Settings → Apps. No administrator rights needed.%n%nPortable: just a folder with the program, e.g. on a USB stick. Nothing is registered in Windows; samples and settings live in the data folder next to the program.
ru.ModeInstall=Установить (рекомендуется)
en.ModeInstall=Install (recommended)
ru.ModeUpdate=Обновить установленную версию %1 → {#AppVersion}
en.ModeUpdate=Update the installed version %1 → {#AppVersion}
ru.ModePortable=Портативная версия — распаковать в папку
en.ModePortable=Portable — unpack to a folder
ru.NotWritable=В папку%n%n%1%n%nпрограмма не сможет записывать образцы, настройки и обновления (нужны права администратора).%n%nВыберите папку в своём профиле, например:%n%2
en.NotWritable=AutoPrintCode will not be able to write samples, settings and updates to%n%n%1%n%n(administrator rights required).%n%nPlease choose a folder in your profile, for example:%n%2

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Check: not IsPortable

[InstallDelete]
; библиотеки прежней версии убираются целиком, чтобы не смешались с новыми; data\ не трогается
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.old*"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\data"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Check: not IsPortable
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; файлы, которые программа поставила сама при автообновлении (их нет в журнале установщика);
; data\ с образцами и настройками остаётся
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.old*"

[Code]
var
  ModePage: TInputOptionWizardPage;
  Installed: Boolean;
  InstalledDir, InstalledVersion: String;
  AutoDir: String;   // папка, которую подставили сами (если человек её не менял — меняем при смене режима)

function UninstallKey: String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1';
end;

function IsPortable: Boolean;
begin
  if (ModePage = nil) or WizardSilent then
    Result := ExpandConstant('{param:portable|0}') = '1'
  else
    Result := ModePage.SelectedValueIndex = 1;
end;

function DefaultInstallDir: String;
begin
  if Installed then
    Result := InstalledDir
  else
    Result := ExpandConstant('{localappdata}\Programs\{#AppName}');
end;

function DefaultPortableDir: String;
begin
  Result := ExpandConstant('{userdesktop}\{#AppName}');
end;

procedure InitializeWizard;
var
  Line: String;
begin
  Installed := RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', InstalledDir)
    or RegQueryStringValue(HKLM, UninstallKey, 'InstallLocation', InstalledDir);
  if Installed then begin
    InstalledDir := RemoveBackslashUnlessRoot(InstalledDir);
    Installed := FileExists(AddBackslash(InstalledDir) + '{#AppExe}');   // запись осталась, а программы нет
  end;
  if Installed then begin
    if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', InstalledVersion) then
      if not RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', InstalledVersion) then
        InstalledVersion := '';
    Line := FmtMessage(CustomMessage('ModeUpdate'), [InstalledVersion]);
  end else
    Line := CustomMessage('ModeInstall');
  ModePage := CreateInputOptionPage(wpWelcome, CustomMessage('ModeCaption'), CustomMessage('ModeDescription'),
    CustomMessage('ModeText'), True, False);
  ModePage.Add(Line);
  ModePage.Add(CustomMessage('ModePortable'));
  if ExpandConstant('{param:portable|0}') = '1' then
    ModePage.SelectedValueIndex := 1
  else
    ModePage.SelectedValueIndex := 0;
  AutoDir := WizardForm.DirEdit.Text;
end;

function DirWritable(Dir: String): Boolean;
var
  Probe: String;
  Created: Boolean;
begin
  Created := not DirExists(Dir);
  if Created and not ForceDirectories(Dir) then begin
    Result := False;
    exit;
  end;
  Probe := AddBackslash(Dir) + '.autoprintcode-write-test';
  Result := SaveStringToFile(Probe, 'test', False);
  if Result then
    DeleteFile(Probe);
  if Created then
    RemoveDir(Dir);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Want: String;
begin
  Result := True;
  if CurPageID = ModePage.ID then begin
    // папку по режиму подставляем, только если человек не вписал свою — ни в мастере, ни в /DIR=
    // (NextButtonClick вызывается и при тихой установке: там папку не трогаем вовсе)
    if IsPortable then Want := DefaultPortableDir else Want := DefaultInstallDir;
    if not WizardSilent and (ExpandConstant('{param:dir|}') = '')
        and ((WizardForm.DirEdit.Text = AutoDir) or (WizardForm.DirEdit.Text = '')) then begin
      WizardForm.DirEdit.Text := Want;
      AutoDir := Want;
    end;
  end else if CurPageID = wpSelectDir then begin
    if not DirWritable(WizardDirValue) then begin
      if IsPortable then Want := DefaultPortableDir else Want := ExpandConstant('{localappdata}\Programs\{#AppName}');
      MsgBox(FmtMessage(CustomMessage('NotWritable'), [WizardDirValue, Want]), mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // обновление установленной версии — в ту же папку, страницу выбора не показываем
  Result := (PageID = wpSelectDir) and Installed and not IsPortable;
end;
