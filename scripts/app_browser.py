"""
Dedicated Desktop Browser Window for UP Voter List & Online Service.
Creates a standalone native application window (अपना ब्राउज़र) without
browser address bars, tabs, or toolbars.
Simultaneously keeps the online Cloudflare tunnel running.
"""

import os
import sys
import time
import threading
import subprocess
import re
import urllib.request
import webbrowser
try:
    import webview
    HAS_WEBVIEW = True
except Exception:
    webview = None
    HAS_WEBVIEW = False
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "outputs", "app_browser.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Safe logging for windowless pythonw.exe (no terminal window)
if sys.stdout is None or sys.stderr is None:
    try:
        log_fp = open(LOG_FILE, "a", encoding="utf-8", buffering=1, errors="replace")
        sys.stdout = log_fp
        sys.stderr = log_fp
    except Exception:
        pass
elif sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CLOUDFLARED_EXE = os.path.join(BASE_DIR, "cloudflared.exe")
PYTHON_EXE = sys.executable

# Global process holders
BACKEND_PROC = None
TUNNEL_PROC = None
TUNNEL_URL = ""

# Windows flag to prevent child processes from creating terminal windows
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def is_backend_running(port=8000):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"User-Agent": "HealthCheck"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def free_port_if_stale(port=8000):
    """If port is occupied by a dead or unresponsive process, terminate it to prevent Errno 10048."""
    if is_backend_running(port):
        return True
    try:
        cmd = f"powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess\""
        out = subprocess.check_output(cmd, shell=True, text=True, errors="replace").strip()
        pids = set(int(p) for p in out.split() if p.isdigit())
        for pid in pids:
            if pid > 0 and pid != os.getpid():
                print(f"⚠️ पोर्ट {port} पर अवरुद्ध मृत प्रोसेस (PID {pid}) को हटाया जा रहा है...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
        if pids:
            time.sleep(0.8)
    except Exception:
        pass
    return False


def start_backend():
    global BACKEND_PROC
    if is_backend_running(8000):
        print("✅ स्थानीय बैकएंड सर्वर पहले से चल रहा है (127.0.0.1:8000)")
        return True

    # 1. Clear any zombie/hung socket on port 8000
    free_port_if_stale(8000)

    # 2. Resolve Python binary (must use python.exe, not pythonw.exe, for clean uvicorn server)
    runtime_py = os.path.join(BASE_DIR, "runtime", "python.exe")
    venv_py = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    if os.path.exists(runtime_py):
        py_bin = runtime_py
    elif os.path.exists(venv_py):
        py_bin = venv_py
    else:
        py_bin = sys.executable.replace("pythonw.exe", "python.exe")
        if not os.path.exists(py_bin):
            py_bin = sys.executable

    print(f"🚀 स्थानीय बैकएंड सर्वर प्रारंभ हो रहा है ({py_bin})...")
    uvicorn_log_path = os.path.join(BASE_DIR, "outputs", "uvicorn_startup.log")
    try:
        log_out = open(uvicorn_log_path, "a", encoding="utf-8", buffering=1, errors="replace")
    except Exception:
        log_out = subprocess.DEVNULL

    cmd = [py_bin, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"]
    BACKEND_PROC = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=log_out,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW
    )

    # Wait up to 12 seconds for the server to be fully ready
    for _ in range(30):
        time.sleep(0.4)
        if is_backend_running(8000):
            print("✅ स्थानीय बैकएंड सर्वर तैयार! (127.0.0.1:8000)")
            return True

    print("⚠️ बैकएंड सर्वर 12 सेकंड में प्रत्युत्तर नहीं दे सका।")
    return is_backend_running(8000)


def is_tunnel_running():
    """Checks if a cloudflared tunnel process is already running on the system."""
    try:
        cmd = "powershell -NoProfile -Command \"Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id\""
        out = subprocess.check_output(cmd, shell=True, text=True, errors="replace").strip()
        return bool(out)
    except Exception:
        return False


def start_online_tunnel():
    global TUNNEL_PROC, TUNNEL_URL
    if not os.path.exists(CLOUDFLARED_EXE):
        print(f"⚠️ cloudflared.exe नहीं मिला: {CLOUDFLARED_EXE}")
        return

    if is_tunnel_running():
        print("🌐 ऑनलाइन Cloudflare टनल पहले से सक्रिय है।")
        return

    print("🌐 ऑनलाइन Cloudflare टनल शुरू किया जा रहा है...")
    cmd = [CLOUDFLARED_EXE, "tunnel", "--url", "http://127.0.0.1:8000"]
    TUNNEL_PROC = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW
    )

    start_t = time.time()
    while time.time() - start_t < 30:
        line = TUNNEL_PROC.stdout.readline()
        if not line:
            time.sleep(0.2)
            continue
        m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
        if m:
            TUNNEL_URL = m.group(1)
            search_url = f"{TUNNEL_URL.rstrip('/')}/search"
            print(f"\n✅ ऑनलाइन पब्लिक लिंक: {search_url}")

            # 1. Save link file
            link_file = os.path.join(BASE_DIR, "outputs", "latest_online_link.txt")
            try:
                os.makedirs(os.path.dirname(link_file), exist_ok=True)
                with open(link_file, "w", encoding="utf-8") as f:
                    f.write(TUNNEL_URL)
            except Exception:
                pass

            wa_message = (
                "🇮🇳 *मतदाता सेवा — ऑनलाइन वोटर सर्च पोर्टल* 🇮🇳\n\n"
                "डिजिटल निर्वाचक नामावली (मतदाता सूची) में अपना व अपने पूरे परिवार का नाम, भाग संख्या, व क्रम संख्या आसानी से खोजें:\n\n"
                f"🔗 *वेब लिंक:* {search_url}\n\n"
                "📱 बिना किसी ऐप के सीधे मोबाइल ब्राउज़र में खोलें और 1 सेकंड में अपनी डिजिटल मतदाता पर्ची देखें!"
            )
            wa_share_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_message)}"
            print(f"💬 व्हाट्सएप शेयर लिंक: {wa_share_url}")

            wa_info_file = os.path.join(BASE_DIR, "outputs", "whatsapp_share_info.txt")
            try:
                with open(wa_info_file, "w", encoding="utf-8") as f:
                    f.write(f"ONLINE_SEARCH_URL={search_url}\n")
                    f.write(f"WHATSAPP_SHARE_URL={wa_share_url}\n\n")
                    f.write(wa_message)
            except Exception:
                pass

            # Print QR code in terminal for mobile scanning
            print_qr_code(search_url)
            break



def print_qr_code(url: str):
    if not url:
        return
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        
        # Save QR code image for user reference
        qr_img_path = os.path.join(BASE_DIR, "outputs", "voter_search_qr.png")
        os.makedirs(os.path.dirname(qr_img_path), exist_ok=True)
        qr.make_image(fill_color="black", back_color="white").save(qr_img_path)

        print("\n" + "=" * 54)
        print("   📱 मोबाइल से स्कैन करने हेतु QR कोड (Scan with Phone):")
        print("=" * 54)
        qr.print_ascii(invert=True)
        print("=" * 54)
        print(f"🔗 ऑनलाइन लिंक: {url}")
        print(f"📁 QR इमेज फाइल: outputs/voter_search_qr.png")
        print("=" * 54 + "\n")
    except Exception as e:
        print(f"⚠️ QR कोड प्रदर्शित करने में त्रुटि: {e}")


def on_window_closed():
    """Cleanup processes on browser window close."""
    print("\n🛑 ऐप्लिकेशन ब्राउज़र बंद हो रहा है...")
    if TUNNEL_PROC:
        try:
            TUNNEL_PROC.terminate()
            TUNNEL_PROC.wait(timeout=2)
        except Exception:
            pass
    if BACKEND_PROC:
        try:
            BACKEND_PROC.terminate()
            BACKEND_PROC.wait(timeout=2)
        except Exception:
            pass
    free_port_if_stale(8000)


def main():
    print("=" * 68)
    print("        🇮🇳 मतदाता सेवा एवं कनवर्टर (समर्पित डेस्कटॉप ब्राउज़र) 🇮🇳")
    print("=" * 68)

    # 1. Start backend server and ensure it is responsive before launching browser
    ready = start_backend()
    if not ready:
        print("⏳ सर्वर सक्रिय होने की प्रतीक्षा की जा रही है...")
        for _ in range(12):
            time.sleep(0.5)
            if is_backend_running(8000):
                ready = True
                break

    # 2. Start online tunnel in background thread
    tunnel_thread = threading.Thread(target=start_online_tunnel, daemon=True)
    tunnel_thread.start()

    # 3. Target URL for the custom browser window (Open /activate if unactivated)
    try:
        from backend.modules.license_guard import LicenseGuard
        lic = LicenseGuard.get_license_status()
        if not lic.get("is_activated"):
            app_url = "http://127.0.0.1:8000/activate"
            print("🔒 सॉफ्टवेयर अभी सक्रिय नहीं है। एक्टिवेशन स्क्रीन खोली जा रही है...")
        else:
            app_url = "http://127.0.0.1:8000/admin"
    except Exception:
        app_url = "http://127.0.0.1:8000/admin"

    print(f"🖥️ समर्पित ऐप ब्राउज़र विंडो खुल रही है ({app_url})...")

    # 4. Launch Custom Browser Window (pywebview -> Edge/Chrome App Mode -> Default Browser)
    launched = False
    if HAS_WEBVIEW and webview:
        try:
            window = webview.create_window(
                title="मतदाता सेवा मास्टर",
                url=app_url,
                width=1320,
                height=860,
                min_size=(960, 640),
                resizable=True,
                confirm_close=False,
                text_select=True
            )
            window.events.closed += on_window_closed
            webview.start(debug=False)
            launched = True
        except Exception as e:
            print(f"⚠️ PyWebView विंडो में समस्या: {e}")

    if not launched:
        print("🚀 स्टैंडअलोन ऐप मोड (Fallback App Mode) में खोला जा रहा है...")
        candidate_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
            "msedge.exe",
            "chrome.exe"
        ]
        app_bin = None
        for p in candidate_paths:
            if os.path.exists(p):
                app_bin = p
                break
        
        if app_bin:
            try:
                fallback_cmd = [app_bin, f"--app={app_url}", "--window-size=1320,860"]
                p = subprocess.Popen(fallback_cmd)
                p.wait()
                launched = True
            except Exception as e:
                print(f"⚠️ स्टैंडअलोन ब्राउज़र त्रुटि: {e}")

        if not launched:
            print(f"🌐 डिफ़ॉल्ट सिस्टम ब्राउज़र में खोला जा रहा है: {app_url}")
            webbrowser.open(app_url)
            # Keep parent process alive while server runs
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        on_window_closed()


if __name__ == "__main__":
    main()
