Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strScriptDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
desktopPath = WshShell.SpecialFolders("Desktop")

pythonwPath = strScriptDir & "\.venv\Scripts\pythonw.exe"
appScriptPath = strScriptDir & "\scripts\app_browser.py"
iconPath = strScriptDir & "\outputs\app_icon.ico"

' 1. English Named Shortcut
Set s2 = WshShell.CreateShortcut(desktopPath & "\UP_Voter_Service.lnk")
s2.TargetPath = pythonwPath
s2.Arguments = """" & appScriptPath & """"
s2.WorkingDirectory = strScriptDir
s2.Description = "UP Voter List AI Converter and Dedicated Voter Service"
s2.IconLocation = iconPath & ",0"
s2.WindowStyle = 1
s2.Save

' 2. Hindi Named Shortcut
Set s1 = WshShell.CreateShortcut(desktopPath & "\मतदाता सेवा.lnk")
s1.TargetPath = pythonwPath
s1.Arguments = """" & appScriptPath & """"
s1.WorkingDirectory = strScriptDir
s1.Description = "UP Voter List AI Converter and Dedicated Voter Service"
s1.IconLocation = iconPath & ",0"
s1.WindowStyle = 1
s1.Save

WScript.Echo "SUCCESS: Created completely windowless shortcuts pointing to pythonw.exe"
