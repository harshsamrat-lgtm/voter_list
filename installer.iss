; Inno Setup Script for UP Voter Service & AI Converter
; Creates a professional standalone Windows installer

#define MyAppName "उत्तर प्रदेश मतदाता सेवा"
#define MyAppEnglishName "UP Voter Service"
#define MyAppVersion "1.0.5"
#define MyAppPublisher "UP Voter Tech"
#define MyAppExeName "start_app.bat"
#define MyIconFile "dist_staging\app_icon.ico"

[Setup]
; Unique application GUID for Windows Registry & Uninstall tracking
AppId={{D37F8E41-620A-4F9D-8339-E4D84F76B1C2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName=C:\UP_Voter_Service
DefaultGroupName={#MyAppEnglishName}
AllowNoIcons=yes
OutputDir=dist_output
OutputBaseFilename=UP_Voter_Service_Setup_v1.0
SetupIconFile={#MyIconFile}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=auto
ChangesAssociations=no
UninstallDisplayIcon={app}\app_icon.ico
UninstallDisplayName={#MyAppName} ({#MyAppEnglishName})

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "डेस्कटॉप पर शॉर्टकट बनाएँ (Create Desktop Shortcut)"; GroupDescription: "अतिरिक्त विकल्प:"
Name: "startupicon"; Description: "विंडोज़ चालू होते ही बैकग्राउंड में प्रारंभ करें (Start with Windows)"; GroupDescription: "स्वचालित स्टार्टअप:"; Flags: unchecked

[Files]
; Copy all staging files recursively
Source: "dist_staging\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Desktop Shortcuts
Name: "{autodesktop}\मतदाता सेवा"; Filename: "{app}\start_app.bat"; IconFilename: "{app}\app_icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autodesktop}\{#MyAppEnglishName}"; Filename: "{app}\start_app.bat"; IconFilename: "{app}\app_icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon

; Start Menu Shortcuts
Name: "{group}\मतदाता सेवा (UP Voter Seva)"; Filename: "{app}\start_app.bat"; IconFilename: "{app}\app_icon.ico"; WorkingDir: "{app}"
Name: "{group}\लाइसेंस स्थिति (License Status)"; Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\manage_license.py"" status"; WorkingDir: "{app}"
Name: "{group}\सॉफ्टवेयर अनइन्स्टाल करें (Uninstall)"; Filename: "{uninstallexe}"

; Windows Startup Shortcut (if selected)
Name: "{userstartup}\UP_Voter_Service"; Filename: "{app}\start_silent.vbs"; WorkingDir: "{app}"; Tasks: startupicon

[Run]
; Option to launch application immediately after installation
Filename: "{app}\start_app.bat"; Description: "मतदाता सेवा सॉफ्टवेयर तुरंत चालू करें (Launch UP Voter Service Now)"; Flags: postinstall nowait skipifsilent unchecked
