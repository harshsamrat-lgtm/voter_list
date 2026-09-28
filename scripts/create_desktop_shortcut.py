import os
import sys
import subprocess

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_BAT = os.path.join(BASE_DIR, "start_app_browser.bat")
APP_ICON = os.path.join(BASE_DIR, "outputs", "app_icon.ico")

PYTHONW_EXE = os.path.join(BASE_DIR, ".venv", "Scripts", "pythonw.exe")
TARGET_SCRIPT = os.path.join(BASE_DIR, "scripts", "app_browser.py")

desktop_path = os.path.expanduser(r"~\Desktop")
if not os.path.exists(desktop_path):
    desktop_path = r"C:\Users\HP\Desktop"

print(f"Desktop path: {desktop_path}")
print(f"PythonW: {PYTHONW_EXE}")
print(f"Script: {TARGET_SCRIPT}")
print(f"App Icon: {APP_ICON}")

# PowerShell script to create completely windowless shortcuts with custom Bhagwa icon
ps_script = f'''
$wsh = New-Object -ComObject WScript.Shell

# 1. Hindi Named Shortcut (मतदाता सेवा.lnk)
$link1 = Join-Path -Path '{desktop_path}' -ChildPath 'मतदाता सेवा.lnk'
$s1 = $wsh.CreateShortcut($link1)
$s1.TargetPath = '{PYTHONW_EXE}'
$s1.Arguments = '"{TARGET_SCRIPT}"'
$s1.WorkingDirectory = '{BASE_DIR}'
$s1.Description = 'UP Voter List AI Converter and Dedicated Voter Service'
$s1.IconLocation = '{APP_ICON},0'
$s1.WindowStyle = 1
$s1.Save()

# 2. English Named Shortcut (UP_Voter_Service.lnk)
$link2 = Join-Path -Path '{desktop_path}' -ChildPath 'UP_Voter_Service.lnk'
$s2 = $wsh.CreateShortcut($link2)
$s2.TargetPath = '{PYTHONW_EXE}'
$s2.Arguments = '"{TARGET_SCRIPT}"'
$s2.WorkingDirectory = '{BASE_DIR}'
$s2.Description = 'UP Voter List AI Converter and Dedicated Voter Service'
$s2.IconLocation = '{APP_ICON},0'
$s2.WindowStyle = 1
$s2.Save()

Write-Host "SUCCESS"
'''

res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, encoding="utf-8")
print("PowerShell Output:", res.stdout.strip())
if res.stderr:
    print("PowerShell Stderr:", res.stderr.strip())

print(f"✅ डेस्कटॉप शॉर्टकट नए भगवा आइकन के साथ अपडेट हो गए!")
