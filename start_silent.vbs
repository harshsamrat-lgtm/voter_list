Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strScriptDir

pythonwPath = strScriptDir & "\.venv\Scripts\pythonw.exe"
appScriptPath = strScriptDir & "\scripts\app_browser.py"

' Run windowless python with SW_HIDE (0) so absolutely zero console/terminal window appears
WshShell.Run Chr(34) & pythonwPath & Chr(34) & " " & Chr(34) & appScriptPath & Chr(34), 0, False
