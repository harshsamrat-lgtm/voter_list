import os
import subprocess

desktop = r"C:\Users\HP\Desktop"
s1 = os.path.join(desktop, "मतदाता सेवा.lnk")
s2 = os.path.join(desktop, "UP_Voter_Service.lnk")

print(f"Shortcut 1 exists: {os.path.exists(s1)}")
print(f"Shortcut 2 exists: {os.path.exists(s2)}")

ps_code = """
$wsh = New-Object -ComObject WScript.Shell
$s = $wsh.CreateShortcut('C:\\Users\\HP\\Desktop\\UP_Voter_Service.lnk')
Write-Host "Target:" $s.TargetPath
Write-Host "WorkDir:" $s.WorkingDirectory
"""
res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_code], capture_output=True, text=True)
print(res.stdout)
assert "pythonw.exe" in res.stdout.lower() or "start_silent.vbs" in res.stdout.lower()
print("Desktop shortcut points to silent launcher successfully!")
