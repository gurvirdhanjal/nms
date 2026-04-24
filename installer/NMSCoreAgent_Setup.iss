; Inno Setup script for NMS Core Agent (server_agent.py)
; Requires: Inno Setup 6+, PyInstaller output at ..\dist\NMSCoreAgent\
;
; Compile:  ISCC.exe installer\NMSCoreAgent_Setup.iss
; Output:   installer\Output\NMSCoreAgent_Setup.exe

#define MyAppName      "NMS Core Agent"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "APL Techno"
#define MyServiceName  "NMSCoreAgent"
#define MyInstallDir   "{autopf}\NMS\NMSCoreAgent"
#define MyDataDir      "{commonappdata}\nms-agent"
#define MySrcDir       "..\dist\NMSCoreAgent"
#define MyNSSM         "..\deploy\tools\nssm.exe"

[Setup]
AppId={{B2C3D4E5-F6A7-8901-BCDE-F12345678901}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={#MyInstallDir}
DefaultGroupName=NMS Suite
OutputDir=Output
OutputBaseFilename=NMSCoreAgent_Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
WizardStyle=modern
MinVersion=10.0
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\NMSCoreAgent.exe
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#MySrcDir}\*";  DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyNSSM}";      DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{#MyDataDir}"; Permissions: everyone-full

; ── Code ─────────────────────────────────────────────────────────────────────
[Code]

var
  ServerPage:    TWizardPage;
  EdServerUrl:   TEdit;
  EdToken:       TEdit;
  EdInterval:    TEdit;

procedure InitializeWizard;
var
  Lbl: TLabel;
  Y:   Integer;
begin
  ServerPage := CreateCustomPage(wpSelectDir,
    'Admin Server Connection',
    'Configure how this agent connects to the NMS Admin Server.');

  Y := 8;
  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := 'Admin Server URL:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 130;
  EdServerUrl := TEdit.Create(ServerPage); EdServerUrl.Parent := ServerPage.Surface;
  EdServerUrl.Text := 'http://192.168.1.100:5001/api/agent/metrics';
  EdServerUrl.Left := 140; EdServerUrl.Top := Y - 2; EdServerUrl.Width := 280;
  Y := Y + 32;

  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := 'Agent Token:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 130;
  EdToken := TEdit.Create(ServerPage); EdToken.Parent := ServerPage.Surface;
  EdToken.Text := ''; EdToken.Left := 140; EdToken.Top := Y - 2; EdToken.Width := 280;
  EdToken.PasswordChar := '*';
  Y := Y + 32;

  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := 'Poll Interval (sec):'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 130;
  EdInterval := TEdit.Create(ServerPage); EdInterval.Parent := ServerPage.Surface;
  EdInterval.Text := '30'; EdInterval.Left := 140; EdInterval.Top := Y - 2; EdInterval.Width := 80;

  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := '(Copy Tracking API Key from Admin Panel → Settings → API Keys)';
  Lbl.Font.Color := clGray; Lbl.Font.Size := 8;
  Lbl.Left := 0; Lbl.Top := 120; Lbl.Width := ServerPage.SurfaceWidth;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  IV: Integer;
begin
  Result := True;
  if CurPageID = ServerPage.ID then begin
    if Trim(EdServerUrl.Text) = '' then begin
      MsgBox('Admin Server URL is required.', mbError, MB_OK);
      Result := False; Exit;
    end;
    IV := StrToIntDef(Trim(EdInterval.Text), -1);
    if IV < 5 then begin
      MsgBox('Poll interval must be a number ≥ 5.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure WriteConfigJson;
var
  ConfigPath, Content: String;
begin
  ConfigPath := ExpandConstant('{#MyDataDir}\config.json');
  Content :=
    '{' + #13#10 +
    '  "_comment": "NMS Core Agent config — managed by installer",' + #13#10 +
    '  "nms_server_url": "' + EdServerUrl.Text + '",' + #13#10 +
    '  "agent_token": "' + EdToken.Text + '",' + #13#10 +
    '  "interval_seconds": ' + EdInterval.Text + ',' + #13#10 +
    '  "request_timeout": 5,' + #13#10 +
    '  "top_processes_limit": 5,' + #13#10 +
    '  "buffer_max_records": 1000' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(ConfigPath, Content, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteConfigJson;
end;

[Run]
Filename: "{app}\nssm.exe"; Parameters: "install {#MyServiceName} ""{app}\NMSCoreAgent.exe"""; Flags: runhidden; StatusMsg: "Registering service..."
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppDirectory ""{#MyDataDir}""";  Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} DisplayName ""NMS Core Agent"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} Description ""NMS Metrics Collection Agent"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} Start SERVICE_AUTO_START";       Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStdout ""{#MyDataDir}\stdout.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStderr ""{#MyDataDir}\stderr.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRotateFiles 1";               Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRotateBytes 10485760";        Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppExit Default Restart";        Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRestartDelay 10000";          Flags: runhidden
; Restrict stop to admins only
Filename: "{sys}\sc.exe";   Parameters: "sdset {#MyServiceName} ""D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start {#MyServiceName}"; Flags: runhidden; StatusMsg: "Starting NMS Core Agent..."

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop {#MyServiceName}";           Flags: runhidden; RunOnceId: "StopSvc"
Filename: "{app}\nssm.exe"; Parameters: "remove {#MyServiceName} confirm"; Flags: runhidden; RunOnceId: "RemoveSvc"

[Icons]
Name: "{group}\NMS Core Agent (Configure)";   Filename: "{app}\NMSCoreAgent.exe";  Parameters: "--configure"
Name: "{group}\Uninstall {#MyAppName}";        Filename: "{uninstallexe}"
