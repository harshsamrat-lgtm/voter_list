import os
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def setup_startup(enable=True):
    startup_dir = os.path.join(os.environ['APPDATA'], r'Microsoft\Windows\Start Menu\Programs\Startup')
    shortcut_path = os.path.join(startup_dir, 'UP_Voter_Service.lnk')
    
    if not enable:
        if os.path.exists(shortcut_path):
            os.remove(shortcut_path)
            print(" ऑटो-स्टार्ट शॉर्टकट सफलतापूर्वक हटा दिया गया है।")
        else:
            print("⚠️ ऑटो-स्टार्ट पहले से सक्रिय नहीं था।")
        return

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pythonw = os.path.join(base_dir, ".venv", "Scripts", "pythonw.exe")
    app_script = os.path.join(base_dir, "scripts", "app_browser.py")
    icon_path = os.path.join(base_dir, "outputs", "app_icon.ico")

    import subprocess
    ps_cmd = f'''
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut('{shortcut_path}')
    $sc.TargetPath = '{pythonw}'
    $sc.Arguments = '"{app_script}"'
    $sc.WorkingDirectory = '{base_dir}'
    $sc.Description = 'UP Voter Service Auto-Start'
    if (Test-Path '{icon_path}') {{
        $sc.IconLocation = '{icon_path},0'
    }}
    $sc.WindowStyle = 1
    $sc.Save()
    '''
    subprocess.run(['powershell', '-NoProfile', '-Command', ps_cmd], check=True)
    print("✅ कम्प्यूटर रीस्टार्ट होने पर ऐप अपने आप शुरू होने के लिए सेट हो गया है!")
    print(f"📁 शॉर्टकट स्थान: {shortcut_path}")

if __name__ == "__main__":
    enable = "--disable" not in sys.argv
    setup_startup(enable)
