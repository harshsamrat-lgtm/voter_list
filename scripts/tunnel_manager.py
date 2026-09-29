"""
Tunnel & Server Manager for UP Voter Search Online Service.
Runs the FastAPI server and Cloudflare Quick Tunnel together,
displaying the public HTTPS URL and a terminal QR code for easy mobile access.
"""

import os
import sys
import time
import subprocess
import re
import urllib.request
import webbrowser

# Ensure UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOUDFLARED_EXE = os.path.join(BASE_DIR, "cloudflared.exe")
PYTHON_EXE = sys.executable


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
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
        if pids:
            time.sleep(0.8)
    except Exception:
        pass
    return False


def start_backend():
    if is_backend_running(8000):
        print("✅ स्थानीय बैकएंड सर्वर पहले से चल रहा है (127.0.0.1:8000)")
        return None

    free_port_if_stale(8000)

    venv_py = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    py_bin = venv_py if os.path.exists(venv_py) else PYTHON_EXE

    print("🚀 स्थानीय बैकएंड सर्वर प्रारंभ हो रहा है (127.0.0.1:8000)...")
    cmd = [py_bin, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"]
    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    # Wait until healthy
    for _ in range(25):
        time.sleep(0.4)
        if is_backend_running():
            print("✅ स्थानीय सर्वर सफलतापूर्वक सक्रिय (127.0.0.1:8000)")
            return proc
    return proc


def print_qr_code(url: str):
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


def run_tunnel():
    if not os.path.exists(CLOUDFLARED_EXE):
        print(f"❌ cloudflared.exe नहीं मिला: {CLOUDFLARED_EXE}")
        return

    # Ensure backend is up
    backend_proc = None
    if not is_backend_running():
        backend_proc = start_backend()
    else:
        print("✅ स्थानीय बैकएंड सर्वर पहले से चल रहा है (127.0.0.1:8000)")

    print("\n🌐 Cloudflare सुरक्षित टनल प्रारंभ किया जा रहा है...")
    print("   (बिना स्टैटिक IP के सुरक्षित HTTPS लिंक जनरेट हो रहा है)")

    cmd = [CLOUDFLARED_EXE, "tunnel", "--url", "http://127.0.0.1:8000"]
    tunnel_proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    tunnel_url = None
    start_time = time.time()

    # Read output until trycloudflare URL appears
    while time.time() - start_time < 30:
        line = tunnel_proc.stdout.readline()
        if not line:
            time.sleep(0.2)
            continue
        m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
        if m:
            tunnel_url = m.group(1)
            break

    if not tunnel_url:
        print("⚠️ टनल URL स्वतः प्राप्त नहीं हो सका। कृपया नेटवर्क कनेक्शन जांचें।")
        return

    search_url = f"{tunnel_url.rstrip('/')}/search"

    # 1. Save link files for backend and frontend reference
    outputs_dir = os.path.join(BASE_DIR, "outputs")
    os.makedirs(outputs_dir, exist_ok=True)

    link_file = os.path.join(outputs_dir, "latest_online_link.txt")
    try:
        with open(link_file, "w", encoding="utf-8") as f:
            f.write(tunnel_url)
    except Exception:
        pass

    # 2. Build WhatsApp Share message & link
    wa_message = (
        "🇮🇳 *मतदाता सेवा — ऑनलाइन वोटर सर्च पोर्टल* 🇮🇳\n\n"
        "निर्वाचक नामावली (वोटर लिस्ट) में अपना व अपने पूरे परिवार का नाम, भाग संख्या, व क्रम संख्या आसानी से खोजें:\n\n"
        f"🔗 *वेब लिंक:* {search_url}\n\n"
        "📱 बिना किसी ऐप के सीधे मोबाइल ब्राउज़र में खोलें और 1 सेकंड में अपनी डिजिटल मतदाता पर्ची देखें!"
    )
    import urllib.parse
    wa_share_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_message)}"

    wa_info_file = os.path.join(outputs_dir, "whatsapp_share_info.txt")
    try:
        with open(wa_info_file, "w", encoding="utf-8") as f:
            f.write(f"ONLINE_SEARCH_URL={search_url}\n")
            f.write(f"WHATSAPP_SHARE_URL={wa_share_url}\n\n")
            f.write("--- संदेश प्रारूप (WhatsApp Message) ---\n")
            f.write(wa_message)
    except Exception:
        pass

    # 3. Display clean announcement banner
    print("\n" + "=" * 72)
    print("        🇮🇳 मतदाता सेवा एवं ऑनलाइन सर्वर सक्रिय है 🇮🇳")
    print("=" * 72)
    print(f"\n🌍 ऑनलाइन पब्लिक सर्च लिंक (Public Voter Search Link):")
    print(f"   👉  \033[1;32m{search_url}\033[0m")
    print(f"\n💬 व्हाट्सएप पर सीधे शेयर करने हेतु लिंक (WhatsApp Share Link):")
    print(f"   👉  \033[1;36m{wa_share_url}\033[0m")
    print(f"\n🔒 लोकल एडमिन कनवर्टर (इस कंप्यूटर पर PDF कनवर्टर व डेटाबेस प्रबंधन):")
    print(f"   👉  \033[1;34mhttp://127.0.0.1:8000/admin\033[0m")
    print("\n🛡️ सुरक्षा स्थिति (Security Status):")
    print("   • ऑनलाइन यूज़र्स केवल मतदाता खोज (Search) व पर्ची देख सकते हैं।")
    print("   • भारी PDF कन्वर्शन एवं डेटा डिलीट करने की अनुमतियां सुरक्षित/ब्लॉक हैं।")
    print("=" * 72)

    # Print ASCII QR code in terminal
    print_qr_code(search_url)

    # Automatically launch browser on the host PC
    try:
        webbrowser.open("http://127.0.0.1:8000/admin")
    except Exception:
        pass

    print("\n💡 ऐप और ऑनलाइन सर्विस दोनों चालू हैं! इस विंडो को बंद न करें।")
    print("   👉 WhatsApp Web पर लिंक साझा करने के लिए 'W' दबाकर Enter करें")
    print("   👉 सर्विस बंद करने के लिए Ctrl + C दबाएं\n")

    def listen_keyboard():
        try:
            while True:
                user_cmd = input().strip().lower()
                if user_cmd == 'w':
                    print("🚀 WhatsApp Web खोला जा रहा है...")
                    webbrowser.open(wa_share_url)
        except Exception:
            pass

    import threading
    kb_thread = threading.Thread(target=listen_keyboard, daemon=True)
    kb_thread.start()

    try:
        # Keep monitoring
        while True:
            line = tunnel_proc.stdout.readline()
            if not line and tunnel_proc.poll() is not None:
                print("⚠️ टनल प्रोसेस बंद हो गई। पुनः प्रयास हो रहा है...")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 सर्वर बंद किया जा रहा है...")
        tunnel_proc.terminate()
        if backend_proc:
            backend_proc.terminate()
        print("अलविदा!")


if __name__ == "__main__":
    run_tunnel()

