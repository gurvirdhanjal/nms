; Inno Setup script for NMS Tracking Agent (service.py)
; Requires: Inno Setup 6+, PyInstaller output at ..\dist\NMSTrackingAgent\
;
; Compile:  ISCC.exe installer\NMSTrackingAgent_Setup.iss
; Output:   installer\Output\NMSTrackingAgent_Setup.exe

#define MyAppName      "NMS Tracking Agent"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "APL Techno"
#define MyServiceName  "NMSTrackingAgent"
#define MyInstallDir   "{autopf}\NMS\NMSTrackingAgent"
#define MyDataDir      "{commonappdata}\NMS\TrackingAgent"
#define MySrcDir       "..\dist\NMSTrackingAgent"
#define MyNSSM         "..\deploy\tools\nssm.exe"

[Setup]
AppId={{C3D4E5F6-A7B8-9012-CDEF-123456789012}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={#MyInstallDir}
DefaultGroupName=NMS Suite
OutputDir=Output
OutputBaseFilename=NMSTrackingAgent_Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
WizardStyle=modern
MinVersion=10.0
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\NMSTrackingAgent.exe
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
  ConnPage:        TWizardPage;
  EdAdminUrl:      TEdit;
  EdApiKey:        TEdit;
  EdAgentPort:     TEdit;

  FeatPage:        TWizardPage;
  CbKeystroke:     TCheckBox;
  CbCamera:        TCheckBox;

procedure InitializeWizard;
var
  Lbl: TLabel;
  Y:   Integer;
begin
  // ── Connection page ───────────────────────────────────────────────────────
  ConnPage := CreateCustomPage(wpSelectDir,
    'Admin Server Connection',
    'Configure how this tracking agent connects to the NMS Admin Server.');

  Y := 8;
  Lbl := TLabel.Create(ConnPage); Lbl.Parent := ConnPage.Surface;
  Lbl.Caption := 'Admin Server URL:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 130;
  EdAdminUrl := TEdit.Create(ConnPage); EdAdminUrl.Parent := ConnPage.Surface;
  EdAdminUrl.Text := 'http://192.168.1.100:5001';
  EdAdminUrl.Left := 140; EdAdminUrl.Top := Y - 2; EdAdminUrl.Width := 280;
  Y := Y + 32;

  Lbl := TLabel.Create(ConnPage); Lbl.Parent := ConnPage.Surface;
  Lbl.Caption := 'Tracking API Key:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 130;
  EdApiKey := TEdit.Create(ConnPage); EdApiKey.Parent := ConnPage.Surface;
  EdApiKey.Text := ''; EdApiKey.Left := 140; EdApiKey.Top := Y - 2; EdApiKey.Width := 280;
  EdApiKey.PasswordChar := '*';
  Y := Y + 32;

  Lbl := TLabel.Create(ConnPage); Lbl.Parent := ConnPage.Surface;
  Lbl.Caption := 'Agent Port:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 130;
  EdAgentPort := TEdit.Create(ConnPage); EdAgentPort.Parent := ConnPage.Surface;
  EdAgentPort.Text := '5002'; EdAgentPort.Left := 140; EdAgentPort.Top := Y - 2; EdAgentPort.Width := 80;

  Lbl := TLabel.Create(ConnPage); Lbl.Parent := ConnPage.Surface;
  Lbl.Caption := '(Get Tracking API Key from Admin Panel → Settings → API Keys)';
  Lbl.Font.Color := clGray; Lbl.Font.Size := 8;
  Lbl.Left := 0; Lbl.Top := 120; Lbl.Width := ConnPage.SurfaceWidth;

  // ── Features page ────────────────────────────────────────────────────────
  FeatPage := CreateCustomPage(ConnPage.ID,
    'Monitoring Features',
    'Choose which monitoring capabilities to enable on this workstation.');

  Y := 16;
  CbKeystroke := TCheckBox.Create(FeatPage); CbKeystroke.Parent := FeatPage.Surface;
  CbKeystroke.Caption := 'Keystroke monitoring (logs typed text, encrypted at rest)';
  CbKeystroke.Checked := True;
  CbKeystroke.Left := 0; CbKeystroke.Top := Y; CbKeystroke.Width := FeatPage.SurfaceWidth;
  Y := Y + 28;

  CbCamera := TCheckBox.Create(FeatPage); CbCamera.Parent := FeatPage.Surface;
  CbCamera.Caption := 'Camera monitoring (periodic screenshot / webcam capture)';
  CbCamera.Checked := True;
  CbCamera.Left := 0; CbCamera.Top := Y; CbCamera.Width := FeatPage.SurfaceWidth;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ConnPage.ID then begin
    if Trim(EdAdminUrl.Text) = '' then begin
      MsgBox('Admin Server URL is required.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure WriteConfigEnv;
var
  ConfigPath, Content: String;
  KS, CAM: String;
begin
  ConfigPath := ExpandConstant('{#MyDataDir}\config.env');

  if CbKeystroke.Checked then KS := 'true' else KS := 'false';
  if CbCamera.Checked    then CAM := 'true' else CAM := 'false';

  Content :=
    '# NMS Tracking Agent config — managed by installer' + #13#10 +
    'ADMIN_SERVER_URL=' + EdAdminUrl.Text + #13#10 +
    'TRACKING_API_KEY=' + EdApiKey.Text + #13#10 +
    'TRACKING_AGENT_PORT=' + EdAgentPort.Text + #13#10 +
    'ENABLE_AI_KEYSTROKE_SCAN=' + KS + #13#10 +
    'ENABLE_CAMERA_MONITORING=' + CAM + #13#10;

  SaveStringToFile(ConfigPath, Content, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteConfigEnv;
end;

[Run]
Filename: "{app}\nssm.exe"; Parameters: "install {#MyServiceName} ""{app}\NMSTrackingAgent.exe"""; Flags: runhidden; StatusMsg: "Registering service..."
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppDirectory ""{app}""";             Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} DisplayName ""NMS Tracking Agent"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} Description ""NMS Employee Monitoring Agent"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} Start SERVICE_AUTO_START";           Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStdout ""{#MyDataDir}\stdout.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStderr ""{#MyDataDir}\stderr.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRotateFiles 1";                   Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRotateBytes 10485760";            Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppExit Default Restart";            Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRestartDelay 10000";              Flags: runhidden
; Restrict stop/pause to admins only — standard users cannot stop this service
Filename: "{sys}\sc.exe";   Parameters: "sdset {#MyServiceName} ""D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "start {#MyServiceName}"; Flags: runhidden; StatusMsg: "Starting NMS Tracking Agent..."

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop {#MyServiceName}";           Flags: runhidden; RunOnceId: "StopSvc"
Filename: "{app}\nssm.exe"; Parameters: "remove {#MyServiceName} confirm"; Flags: runhidden; RunOnceId: "RemoveSvc"

[Icons]
Name: "{group}\NMS Tracking Agent (Configure)";  Filename: "{app}\NMSTrackingAgent.exe"; Parameters: "--configure"
Name: "{group}\Uninstall {#MyAppName}";           Filename: "{uninstallexe}"
