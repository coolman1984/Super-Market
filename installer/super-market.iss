; Inno Setup script of Super Market (built by tools/build_windows.py), same design as the Yousef app's installer.
; The same SuperMarket-Setup.exe installs on a new PC and updates a PC that already has it: only the program in
; Program Files is replaced; the shop's data in %ProgramData%\SuperMarket is never touched (not by an update, not by an uninstall).

#ifndef AppChannel
  #define AppChannel "commercial"
#endif
#if AppChannel == "ci-test"
  #define MyAppId "{{F0796D9F-F271-4C64-90BD-C22394A64FB2}"
  #define MyInstallFolder "SuperMarket-CITest"
  #define AppDisplayName "Super Market - CI TEST - NOT FOR CUSTOMERS"
  #define AppFileSuffix "-CI-TEST-NOT-FOR-CUSTOMERS"
  #define MyAppName "Super Market CI Test"
#else
  #define MyAppId "{{BB069238-D918-45A6-8F6B-109C821636BD}"
  #define MyInstallFolder "SuperMarket"
  #define AppDisplayName "Super Market"
  #define AppFileSuffix ""
  #define MyAppName "Super Market"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersion4
  #define AppVersion4 "0.0.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "Mohamed Fawzy"
#endif
#ifndef AppCopyright
  #define AppCopyright "(c) 2026 Mohamed Fawzy"
#endif

[Setup]
AppId={#MyAppId}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppVerName={#AppDisplayName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright={#AppCopyright}
VersionInfoVersion={#AppVersion4}
DefaultDirName={autopf}\{#MyInstallFolder}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
DisableDirPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=SuperMarket-Setup-{#AppVersion}{#AppFileSuffix}
SetupIconFile=..\build\supermarket.ico
UninstallDisplayIcon={app}\SuperMarket.exe
UninstallDisplayName={#AppDisplayName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
arabic.DesktopIcon=ضع أيقونة على سطح المكتب
english.DesktopIcon=Put an icon on the desktop
arabic.AutoStart=التشغيل مع ويندوز (موصى به)
english.AutoStart=Start with Windows (recommended)
arabic.Lan=أجهزة كاشير أخرى على شبكة المحل تدخل إلى البرنامج
english.Lan=Other tills on the shop network can connect
arabic.Training=السوبر ماركت - تدريب
english.Training=Super Market - training
arabic.Stop=إيقاف السوبر ماركت
english.Stop=Stop Super Market
arabic.Recovery=استعادة دخول صاحب المحل
english.Recovery=Owner sign-in recovery
arabic.Protect=ضبط حماية بيانات المحل...
english.Protect=Protecting the shop data...
arabic.OpenNow=افتح السوبر ماركت الآن
english.OpenNow=Open Super Market now

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"
Name: "autostart"; Description: "{cm:AutoStart}"
; by default the program serves this PC only; other tills are an explicit choice (firewall: private networks only)
Name: "lan"; Description: "{cm:Lan}"; Flags: unchecked

[Dirs]
; data, backups and logs: kept when the program is updated or removed. Access is narrowed in [Code] (icacls): SYSTEM, the
; administrators and ONE operator account: the Windows account that STARTED Setup (found with ExecAsOriginalUser, so a
; normal user who types an administrator's password in the UAC window still gets the access), or /OPERATOR=DOMAIN\name.
Name: "{commonappdata}\SuperMarket"; Permissions: admins-full system-full; Flags: uninsneveruninstall

[Files]
Source: "..\build\launcher.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\SuperMarket.exe"; Parameters: "{code:LanArg}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\SuperMarket.exe"; Parameters: "{code:LanArg}"; Tasks: desktopicon
Name: "{autoprograms}\{cm:Training}"; Filename: "{app}\SuperMarket.exe"; Parameters: "training"
Name: "{autoprograms}\{cm:Stop}"; Filename: "{app}\SuperMarket.exe"; Parameters: "stop"
Name: "{autoprograms}\{cm:Recovery}"; Filename: "{cmd}"; Parameters: "/k ""{app}\SuperMarket.exe"" owner-recovery"
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\SuperMarket.exe"; Parameters: "--background {code:LanArg}"; Tasks: autostart

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#MyAppName}"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#MyAppName}"" dir=in action=allow program=""{app}\SuperMarket.exe"" enable=yes profile=private,domain"; Flags: runhidden; Tasks: lan
Filename: "{app}\SuperMarket.exe"; Parameters: "{code:LanArg}"; Description: "{cm:OpenNow}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM SuperMarket.exe"; Flags: runhidden; RunOnceId: "StopSuperMarket"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#MyAppName}"""; Flags: runhidden; RunOnceId: "FirewallRule"

[Messages]
arabic.FinishedLabel=تم تثبيت البرنامج. بيانات المحل محفوظة في %ProgramData%\SuperMarket وبتفضل موجودة بعد التحديث وبعد إزالة البرنامج.%n%nالبرنامج يفتح في نافذة خاصة به. إذا أغلقت النافذة يظل البرنامج يعمل؛ لإيقافه استخدم «إيقاف السوبر ماركت» من قائمة ابدأ.
english.FinishedLabel=The program is installed. The shop data is kept in %ProgramData%\SuperMarket and stays after updates and after removing the program.%n%nThe program opens in its own window. Closing the window keeps it running; to stop it use "Stop Super Market" in the Start menu.

[Code]
function LanArg(Param: String): String;
begin
  if WizardIsTaskSelected('lan') then Result := 'lan' else Result := '';
end;

{ The Windows account that started Setup - not the administrator whose password was typed in the UAC window
  ({username} is the account running Setup after elevation; review on PR #13). }
function OperatorAccount(): String;
var
  Code: Integer;
  Out: String;
  Raw: AnsiString;
begin
  Result := ExpandConstant('{param:OPERATOR|}');
  if Result <> '' then exit;
  Out := ExpandConstant('{tmp}\operator.txt');
  if ExecAsOriginalUser(ExpandConstant('{cmd}'), '/c whoami > "' + Out + '"', '', SW_HIDE, ewWaitUntilTerminated, Code)
     and LoadStringFromFile(Out, Raw) then
    Result := Trim(String(Raw));
  if Result = '' then Result := ExpandConstant('{username}');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  { an update: stop the running program first so its files can be replaced; every saved sale stays }
  if CurStep = ssInstall then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM SuperMarket.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { the data folder: SYSTEM, administrators and the operator only; other Windows accounts are removed }
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := CustomMessage('Protect');
    Exec(ExpandConstant('{sys}\icacls.exe'), '"' + ExpandConstant('{commonappdata}\SuperMarket') + '" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "'
      + OperatorAccount() + ':(OI)(CI)M" /remove:g "*S-1-5-32-545" "*S-1-5-11"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;
