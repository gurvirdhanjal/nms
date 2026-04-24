; Inno Setup script for NMS Admin Server
; Requires: Inno Setup 6+, PyInstaller output at ..\dist\NMSAdminServer\
;
; Compile:  ISCC.exe installer\NMSAdminServer_Setup.iss
; Output:   installer\Output\NMSAdminServer_Setup.exe

#define MyAppName      "NMS Admin Server"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "APL Techno"
#define MyServiceName  "NMSAdminServer"
#define MyInstallDir   "{autopf}\NMS\NMSAdminServer"
#define MyLogDir       "{commonappdata}\NMS\AdminServer"
#define MySrcDir       "..\dist\NMSAdminServer"
#define MyNSSM         "..\deploy\tools\nssm.exe"
#define MyDockerCompose "..\docker-compose.yml"
#define MyEnvTemplate  "..\deploy\config.templates\nms-server.env.template"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={#MyInstallDir}
DefaultGroupName=NMS Suite
OutputDir=Output
OutputBaseFilename=NMSAdminServer_Setup
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
WizardStyle=modern
SetupIconFile=
; Minimum Windows 10
MinVersion=10.0
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\NMSAdminServer.exe
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.DbPageCaption=Database Configuration
english.DbPageDesc=Choose how the admin server will connect to PostgreSQL.
english.ServerPageCaption=Server Configuration
english.ServerPageDesc=Set the web port and a secret key for session encryption.

[Types]
Name: "full";    Description: "Full installation (recommended)"
Name: "custom";  Description: "Custom";  Flags: iscustom

[Components]
Name: "main";   Description: "NMS Admin Server service";  Types: full custom;  Flags: fixed
Name: "docker"; Description: "PostgreSQL via Docker Compose (requires Docker Desktop)"; Types: full custom

[Files]
; Main application (PyInstaller onedir)
Source: "{#MySrcDir}\*";        DestDir: "{app}";            Flags: ignoreversion recursesubdirs createallsubdirs
; NSSM service manager
Source: "{#MyNSSM}";            DestDir: "{app}";            Flags: ignoreversion
; Docker Compose file for DB component
Source: "{#MyDockerCompose}";   DestDir: "{app}";            Components: docker;  Flags: ignoreversion
; Env template (written as .env by the wizard)
Source: "{#MyEnvTemplate}";     DestDir: "{tmp}";            Flags: ignoreversion dontcopy

[Dirs]
Name: "{#MyLogDir}";  Permissions: everyone-full

; ── Code section ─────────────────────────────────────────────────────────────
[Code]

// ---- shared vars ----------------------------------------------------------
var
  // DB page
  DbPage:          TWizardPage;
  RbDocker:        TRadioButton;
  RbNative:        TRadioButton;
  EdDbHost:        TEdit;
  EdDbPort:        TEdit;
  EdDbName:        TEdit;
  EdDbUser:        TEdit;
  EdDbPass:        TEdit;

  // Server page
  ServerPage:      TWizardPage;
  EdWebPort:       TEdit;
  EdSecretKey:     TEdit;
  EdTrackingKey:   TEdit;

// ---- helpers ---------------------------------------------------------------
function RandomHex(Len: Integer): String;
var
  HexChars: String;
  I: Integer;
begin
  HexChars := '0123456789abcdef';
  Result := '';
  for I := 1 to Len do
    Result := Result + HexChars[Random(16) + 1];
end;

procedure UpdateNativeFieldState(Sender: TObject);
begin
  EdDbHost.Enabled := RbNative.Checked;
  EdDbPort.Enabled := RbNative.Checked;
  EdDbName.Enabled := RbNative.Checked;
  EdDbUser.Enabled := RbNative.Checked;
  EdDbPass.Enabled := RbNative.Checked;
end;

// ---- wizard page creation --------------------------------------------------
procedure InitializeWizard;
var
  Lbl: TLabel;
  Y: Integer;
begin
  // ── DB page ───────────────────────────────────────────────────────────────
  DbPage := CreateCustomPage(wpSelectComponents,
    ExpandConstant('{cm:DbPageCaption}'),
    ExpandConstant('{cm:DbPageDesc}'));

  RbDocker := TRadioButton.Create(DbPage);
  with RbDocker do begin
    Parent  := DbPage.Surface;
    Caption := 'Use Docker Compose  (starts a postgres:15 container on port 5432)';
    Checked := True;
    Left := 0; Top := 8; Width := DbPage.SurfaceWidth;
    OnClick := @UpdateNativeFieldState;
  end;

  RbNative := TRadioButton.Create(DbPage);
  with RbNative do begin
    Parent  := DbPage.Surface;
    Caption := 'Use existing PostgreSQL server';
    Left := 0; Top := 32; Width := DbPage.SurfaceWidth;
    OnClick := @UpdateNativeFieldState;
  end;

  Y := 60;
  // Host
  Lbl := TLabel.Create(DbPage); Lbl.Parent := DbPage.Surface;
  Lbl.Caption := 'Host:';  Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 100;
  EdDbHost := TEdit.Create(DbPage); EdDbHost.Parent := DbPage.Surface;
  EdDbHost.Text := '127.0.0.1'; EdDbHost.Left := 110; EdDbHost.Top := Y - 2; EdDbHost.Width := 160;
  Y := Y + 28;
  // Port
  Lbl := TLabel.Create(DbPage); Lbl.Parent := DbPage.Surface;
  Lbl.Caption := 'Port:';  Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 100;
  EdDbPort := TEdit.Create(DbPage); EdDbPort.Parent := DbPage.Surface;
  EdDbPort.Text := '5432'; EdDbPort.Left := 110; EdDbPort.Top := Y - 2; EdDbPort.Width := 80;
  Y := Y + 28;
  // Database
  Lbl := TLabel.Create(DbPage); Lbl.Parent := DbPage.Surface;
  Lbl.Caption := 'Database:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 100;
  EdDbName := TEdit.Create(DbPage); EdDbName.Parent := DbPage.Surface;
  EdDbName.Text := 'monitoring_db'; EdDbName.Left := 110; EdDbName.Top := Y - 2; EdDbName.Width := 160;
  Y := Y + 28;
  // Username
  Lbl := TLabel.Create(DbPage); Lbl.Parent := DbPage.Surface;
  Lbl.Caption := 'Username:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 100;
  EdDbUser := TEdit.Create(DbPage); EdDbUser.Parent := DbPage.Surface;
  EdDbUser.Text := 'monitoring_man'; EdDbUser.Left := 110; EdDbUser.Top := Y - 2; EdDbUser.Width := 160;
  Y := Y + 28;
  // Password
  Lbl := TLabel.Create(DbPage); Lbl.Parent := DbPage.Surface;
  Lbl.Caption := 'Password:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 100;
  EdDbPass := TEdit.Create(DbPage); EdDbPass.Parent := DbPage.Surface;
  EdDbPass.Text := ''; EdDbPass.Left := 110; EdDbPass.Top := Y - 2; EdDbPass.Width := 160;
  EdDbPass.PasswordChar := '*';

  UpdateNativeFieldState(nil);

  // ── Server config page ───────────────────────────────────────────────────
  ServerPage := CreateCustomPage(DbPage.ID,
    ExpandConstant('{cm:ServerPageCaption}'),
    ExpandConstant('{cm:ServerPageDesc}'));

  Y := 8;
  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := 'Web Port:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 120;
  EdWebPort := TEdit.Create(ServerPage); EdWebPort.Parent := ServerPage.Surface;
  EdWebPort.Text := '5001'; EdWebPort.Left := 130; EdWebPort.Top := Y - 2; EdWebPort.Width := 80;
  Y := Y + 32;

  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := 'Secret Key:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 120;
  EdSecretKey := TEdit.Create(ServerPage); EdSecretKey.Parent := ServerPage.Surface;
  EdSecretKey.Text := RandomHex(64);
  EdSecretKey.Left := 130; EdSecretKey.Top := Y - 2; EdSecretKey.Width := 280;
  Y := Y + 32;

  Lbl := TLabel.Create(ServerPage); Lbl.Parent := ServerPage.Surface;
  Lbl.Caption := 'Tracking API Key:'; Lbl.Left := 0; Lbl.Top := Y; Lbl.Width := 120;
  EdTrackingKey := TEdit.Create(ServerPage); EdTrackingKey.Parent := ServerPage.Surface;
  EdTrackingKey.Text := RandomHex(32);
  EdTrackingKey.Left := 130; EdTrackingKey.Top := Y - 2; EdTrackingKey.Width := 280;
end;

// ---- page validation -------------------------------------------------------
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DbPage.ID then begin
    if RbNative.Checked then begin
      if (Trim(EdDbHost.Text) = '') or (Trim(EdDbPort.Text) = '') or
         (Trim(EdDbName.Text) = '') or (Trim(EdDbUser.Text) = '') then begin
        MsgBox('Please fill in all database fields.', mbError, MB_OK);
        Result := False;
      end;
    end;
  end;
  if CurPageID = ServerPage.ID then begin
    if (Trim(EdWebPort.Text) = '') or (Trim(EdSecretKey.Text) = '') then begin
      MsgBox('Web Port and Secret Key are required.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

// ---- write .env after files are installed ----------------------------------
procedure WriteEnvFile;
var
  DbUrl, EnvContent, EnvPath: String;
begin
  EnvPath := ExpandConstant('{app}\.env');

  if RbDocker.Checked then
    DbUrl := 'postgresql+psycopg2://monitoring_man:admin123@127.0.0.1:5432/monitoring_db'
  else
    DbUrl := 'postgresql+psycopg2://' + EdDbUser.Text + ':' + EdDbPass.Text +
             '@' + EdDbHost.Text + ':' + EdDbPort.Text + '/' + EdDbName.Text;

  EnvContent :=
    'SECRET_KEY=' + EdSecretKey.Text + #13#10 +
    'TRACKING_API_KEY=' + EdTrackingKey.Text + #13#10 +
    'DATABASE_URL=' + DbUrl + #13#10 +
    'WEB_PORT=' + EdWebPort.Text + #13#10 +
    'WEB_OPEN_BROWSER=0' + #13#10 +
    'SESSION_COOKIE_SECURE=False' + #13#10 +
    'REDIS_SSE_ENABLED=false' + #13#10 +
    'GEMINI_REPORT_INSIGHTS_ENABLED=false' + #13#10;

  SaveStringToFile(EnvPath, EnvContent, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Res: Integer;
begin
  if CurStep = ssPostInstall then begin
    WriteEnvFile;

    // Start PostgreSQL container if Docker chosen
    if RbDocker.Checked then begin
      Exec(ExpandConstant('{cmd}'),
           '/c docker compose -f "' + ExpandConstant('{app}\docker-compose.yml') + '" up -d --no-build',
           '', SW_HIDE, ewWaitUntilTerminated, Res);
    end;
  end;
end;

[Run]
; Register and start the service via NSSM
Filename: "{app}\nssm.exe"; Parameters: "install {#MyServiceName} ""{app}\NMSAdminServer.exe""";     Flags: runhidden; StatusMsg: "Registering service..."
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppDirectory ""{app}""";               Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} DisplayName ""NMS Admin Server""";     Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} Description ""NMS Flask Dashboard — Waitress"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} Start SERVICE_AUTO_START";             Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStdout ""{#MyLogDir}\stdout.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppStderr ""{#MyLogDir}\stderr.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRotateFiles 1";                     Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRotateBytes 10485760";              Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppExit Default Restart";              Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#MyServiceName} AppRestartDelay 15000";                Flags: runhidden
; Restrict stop/pause permissions to Administrators + SYSTEM only (standard users cannot stop the service)
Filename: "{sys}\sc.exe";   Parameters: "sdset {#MyServiceName} ""D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)"""; Flags: runhidden
; Start the service
Filename: "{app}\nssm.exe"; Parameters: "start {#MyServiceName}"; Flags: runhidden; StatusMsg: "Starting NMS Admin Server..."
; Open browser to dashboard (shown to user at the end of install)
Filename: "{#MyInstallDir}\NMSAdminServer.exe"; Description: "Open NMS Dashboard in browser"; Flags: postinstall nowait skipifsilent shellexec; Parameters: ""; WorkingDir: "{app}"

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop {#MyServiceName}";           Flags: runhidden; RunOnceId: "StopSvc"
Filename: "{app}\nssm.exe"; Parameters: "remove {#MyServiceName} confirm"; Flags: runhidden; RunOnceId: "RemoveSvc"

[Icons]
Name: "{group}\NMS Admin Server";         Filename: "{app}\NMSAdminServer.exe"
Name: "{group}\Configure Admin Server";   Filename: "{app}\NMSAdminServer.exe";  Parameters: "--configure"
Name: "{group}\View Logs";                Filename: "{#MyLogDir}\stdout.log"
Name: "{group}\Uninstall {#MyAppName}";   Filename: "{uninstallexe}"
