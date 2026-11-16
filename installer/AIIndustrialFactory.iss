; AI Industrial Factory · Windows 安装器（Inno Setup 6）
; 构建：ISCC.exe installer\AIIndustrialFactory.iss
; 输出：dist\AIIndustrialFactory-Setup-1.1.1.exe

#define MyAppName "AI 数字工厂"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "AIC Factory Studio"
#define MyAppId "{{7E2F6C2A-9B41-4E8D-A6C7-5F1B2D9E3A10}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=AI 驱动的工业数字工厂仿真与智能调度平台（本地 Web 版）
DefaultDirName={localappdata}\Programs\AIIndustrialFactory
DefaultGroupName=AI 数字工厂
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=..\dist
OutputBaseFilename=AIIndustrialFactory-Setup-{#MyAppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\assets\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=1.1.1.0
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
MinVersion=10.0
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "添加桌面网页启动快捷方式（可选）"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; 应用入口与文档
Source: "..\launcher.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\factory_app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ui_cache.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ui_shared.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\legacy_pages.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\run_platform.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\public_api.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\run_public_api.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\DEPLOY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\.streamlit\*"; DestDir: "{app}\.streamlit"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\app.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

; 业务模块
Source: "..\integration\*"; DestDir: "{app}\integration"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*,*.log"
Source: "..\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\network_sim\*"; DestDir: "{app}\network_sim"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\scheduler\*"; DestDir: "{app}\scheduler"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\experiments\*"; DestDir: "{app}\experiments"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\generator\*"; DestDir: "{app}\generator"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\tester\*"; DestDir: "{app}\tester"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"

; OIP 三维编辑器静态构建（建模 / 动画 / 联动，运行时无需 Node）
Source: "..\frontend\oip\dist\*"; DestDir: "{app}\frontend\oip\dist"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\frontend\oip\package.json"; DestDir: "{app}\frontend\oip"; Flags: ignoreversion
Source: "..\frontend\oip\LICENSE"; DestDir: "{app}\frontend\oip"; Flags: ignoreversion
Source: "..\frontend\oip\THIRD_PARTY_NOTICES.md"; DestDir: "{app}\frontend\oip"; Flags: ignoreversion

; 演示项目种子（首次启动复制到用户数据目录）
Source: "..\demo_seed\*"; DestDir: "{app}\demo_seed"; Flags: ignoreversion recursesubdirs createallsubdirs

; RFTwin 仿真引擎、后端、配置、资产与已构建三维前端
Source: "..\rftwin\apps\*"; DestDir: "{app}\rftwin\apps"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\rftwin\packages\*"; DestDir: "{app}\rftwin\packages"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__\*"
Source: "..\rftwin\config\*"; DestDir: "{app}\rftwin\config"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\rftwin\assets\*"; DestDir: "{app}\rftwin\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\rftwin\schemas\*"; DestDir: "{app}\rftwin\schemas"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\rftwin\frontend\dist\*"; DestDir: "{app}\rftwin\frontend\dist"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\rftwin\LICENSE"; DestDir: "{app}\rftwin"; Flags: ignoreversion

; 内置 Python 运行时（评委电脑无需安装 Python）
Source: "..\build\python\*"; DestDir: "{app}\runtime\python"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.pdb,*.pyc,__pycache__\*,Lib\test\*,Lib\turtledemo\*,include\*,libs\*.lib"

[Icons]
Name: "{autodesktop}\AI 数字工厂"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"; Tasks: desktopicon
Name: "{group}\AI 数字工厂"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"
Name: "{group}\停止 AI 数字工厂"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launcher.py"" --stop"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"
Name: "{group}\AI 数字工厂（诊断控制台）"; Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\app.ico"
Name: "{group}\评委使用说明"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\docs\评委使用说明.md"""; WorkingDir: "{app}"
Name: "{group}\演示流程"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\docs\演示流程.md"""; WorkingDir: "{app}"
Name: "{group}\卸载 AI 数字工厂"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; Description: "在默认浏览器打开 AI 数字工厂"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure StopRunningApp();
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\runtime\python\pythonw.exe')) and
     FileExists(ExpandConstant('{app}\launcher.py')) then
  begin
    Exec(ExpandConstant('{app}\runtime\python\pythonw.exe'),
         ExpandConstant('"{app}\launcher.py" --stop'),
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopRunningApp();
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('用户数据已保留：' + ExpandConstant('{localappdata}\AIIndustrialFactory') + #13#10 +
           '如需彻底删除，请手动删除该目录。', mbInformation, MB_OK);
end;
