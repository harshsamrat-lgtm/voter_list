$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$desktopPath = [System.Environment]::GetFolderPath('Desktop')
$baseDir = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path -Path $baseDir -ChildPath ".venv\Scripts\pythonw.exe"
$appScript = Join-Path -Path $baseDir -ChildPath "scripts\app_browser.py"
$iconPath = Join-Path -Path $baseDir -ChildPath "outputs\app_icon.ico"

$wsh = New-Object -ComObject WScript.Shell

# 1. Hindi Named Shortcut (मतदाता सेवा.lnk)
$link1 = Join-Path -Path $desktopPath -ChildPath 'मतदाता सेवा.lnk'
$s1 = $wsh.CreateShortcut($link1)
$s1.TargetPath = $pythonw
$s1.Arguments = "`"$appScript`""
$s1.WorkingDirectory = $baseDir
$s1.Description = 'UP Voter List AI Converter and Dedicated Voter Service'
$s1.IconLocation = "$iconPath,0"
$s1.WindowStyle = 1
$s1.Save()

# 2. English Named Shortcut (UP_Voter_Service.lnk)
$link2 = Join-Path -Path $desktopPath -ChildPath 'UP_Voter_Service.lnk'
$s2 = $wsh.CreateShortcut($link2)
$s2.TargetPath = $pythonw
$s2.Arguments = "`"$appScript`""
$s2.WorkingDirectory = $baseDir
$s2.Description = 'UP Voter List AI Converter and Dedicated Voter Service'
$s2.IconLocation = "$iconPath,0"
$s2.WindowStyle = 1
$s2.Save()

Write-Output "SUCCESS: Both shortcuts created successfully with zero terminal (pythonw.exe)"
