#ifndef AppVersion
  #define AppVersion "0.4.1"
#endif
#ifndef BundleDir
  #define BundleDir "..\release\bundle"
#endif
#ifndef OutputDir
  #define OutputDir "..\release\dist"
#endif

[Setup]
AppId={{A4187743-8302-46E3-916E-C38630C85132}
AppName=木构造戗角生成器
AppVersion={#AppVersion}
AppVerName=木构造戗角生成器 {#AppVersion}
AppPublisher=MuGouzao
DefaultDirName={localappdata}\Programs\MuGouzao
DefaultGroupName=MuGouzao
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir={#OutputDir}
OutputBaseFilename=MuGouzao-Setup-{#AppVersion}-win64
UninstallDisplayName=木构造戗角生成器 {#AppVersion}
UninstallDisplayIcon={app}\runtime\pythonw.exe
Compression=lzma2/fast
SolidCompression=yes
LZMAUseSeparateProcess=yes
CloseApplications=no
RestartApplications=no
DisableWelcomePage=no
DisableDirPage=no
DisableFinishedPage=no
VersionInfoVersion=0.4.1.0
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany=MuGouzao
VersionInfoProductName=木构造戗角生成器
VersionInfoDescription=MuGouzao offline Windows x64 installer

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MuGouzao\戗角生成器"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\packaging\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\runtime\pythonw.exe"
Name: "{autoprograms}\MuGouzao\停止戗角生成器"; Filename: "{app}\MuGouzao.cmd"; Parameters: "--stop"; WorkingDir: "{app}"
Name: "{autoprograms}\MuGouzao\命令行启动器"; Filename: "{app}\MuGouzao.cmd"; WorkingDir: "{app}"
Name: "{autodesktop}\戗角生成器"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\packaging\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\runtime\pythonw.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\packaging\launcher.py"""; WorkingDir: "{app}"; Description: "启动木构造戗角生成器"; Flags: postinstall nowait skipifsilent

[Code]
function StopInstalledService: Boolean;
var
  ResultCode: Integer;
  Parameters: String;
begin
  Result := True;
  if not FileExists(ExpandConstant('{app}\runtime\python.exe')) then
    exit;

  Parameters := '"' + ExpandConstant('{app}\packaging\launcher.py') +
    '" --stop --data-dir "' + ExpandConstant('{localappdata}\MuGouzao') + '"';
  if not Exec(ExpandConstant('{app}\runtime\python.exe'), Parameters,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := False;
    exit;
  end;
  Result := ResultCode = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Result := '';
  if FileExists(ExpandConstant('{app}\runtime\python.exe')) then
    if not StopInstalledService then
      Result := '后台仍有生成任务，或无法确认服务属于此安装。请等待任务完成后重试。';
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\runtime\python.exe')) then
    if not StopInstalledService then
    begin
      MsgBox('后台仍有生成任务，或无法确认服务属于此安装。请等待任务完成后重试卸载。',
        mbError, MB_OK);
      Result := False;
    end;
end;
