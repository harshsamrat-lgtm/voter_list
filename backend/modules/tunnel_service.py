"""
Live Cloudflare Tunnel Manager Service for Online Voter Search Portal.
Manages automatic starting, monitoring, refreshing, and QR code generation
for the trycloudflare.com public HTTPS URL.
"""

import os
import sys
import re
import time
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLOUDFLARED_EXE = BASE_DIR / "cloudflared.exe"
OUTPUT_DIR = BASE_DIR / "outputs"


class TunnelService:
    _lock = threading.Lock()
    _process: Optional[subprocess.Popen] = None
    _current_url: Optional[str] = None
    _is_online: bool = False
    _last_started: Optional[float] = None

    @classmethod
    def get_local_ip(cls) -> str:
        """Finds the local network IPv4 address for WiFi access fallback."""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @classmethod
    def is_process_alive(cls) -> bool:
        """Returns True if the cloudflared child process is actively running."""
        return cls._process is not None and cls._process.poll() is None

    @classmethod
    def read_saved_link(cls) -> Optional[str]:
        """Reads latest online link from file if it exists."""
        link_file = OUTPUT_DIR / "latest_online_link.txt"
        if link_file.exists():
            try:
                txt = link_file.read_text(encoding="utf-8").strip()
                if txt.startswith("https://") and "trycloudflare.com" in txt:
                    return txt
            except Exception:
                pass
        return None

    @classmethod
    def get_status(cls, auto_start: bool = True) -> Dict[str, Any]:
        """
        Returns the current live tunnel status.
        If tunnel process died or is not running, attempts auto-start if auto_start=True.
        """
        with cls._lock:
            # Check if active process is alive
            if cls.is_process_alive() and cls._current_url:
                search_url = f"{cls._current_url.rstrip('/')}/search"
                return {
                    "status": "success",
                    "is_online": True,
                    "tunnel_url": cls._current_url,
                    "search_url": search_url,
                    "qr_endpoint": f"/api/portal-qr?url={search_url}",
                    "mode": "cloudflare_tunnel",
                    "uptime_seconds": int(time.time() - (cls._last_started or time.time()))
                }

            # If not running, check if saved link exists
            saved_url = cls.read_saved_link()
            if saved_url and not auto_start:
                search_url = f"{saved_url.rstrip('/')}/search"
                return {
                    "status": "success",
                    "is_online": True,
                    "tunnel_url": saved_url,
                    "search_url": search_url,
                    "qr_endpoint": f"/api/portal-qr?url={search_url}",
                    "mode": "cloudflare_tunnel"
                }

            # If auto_start is requested and cloudflared exists, start now
            if auto_start and CLOUDFLARED_EXE.exists():
                return cls._start_tunnel_locked(timeout=18)

            # Fallback to local network IP
            local_ip = cls.get_local_ip()
            local_url = f"http://{local_ip}:8000"
            search_url = f"{local_url}/search"
            return {
                "status": "success",
                "is_online": False,
                "tunnel_url": local_url,
                "search_url": search_url,
                "qr_endpoint": f"/api/portal-qr?url={search_url}",
                "mode": "local_network"
            }

    @classmethod
    def refresh_tunnel(cls) -> Dict[str, Any]:
        """
        Forcibly kills any existing tunnel process and starts a fresh new
        Cloudflare Quick Tunnel session, saving the new link and returning status.
        """
        with cls._lock:
            return cls._start_tunnel_locked(timeout=25, force=True)

    @classmethod
    def _start_tunnel_locked(cls, timeout: int = 20, force: bool = False) -> Dict[str, Any]:
        """Internal worker method under lock."""
        # Terminate any existing process
        if cls._process is not None:
            try:
                cls._process.terminate()
                cls._process.wait(timeout=2)
            except Exception:
                try:
                    cls._process.kill()
                except Exception:
                    pass
            cls._process = None
            cls._current_url = None
            cls._is_online = False

        if not CLOUDFLARED_EXE.exists():
            local_ip = cls.get_local_ip()
            local_url = f"http://{local_ip}:8000"
            return {
                "status": "warning",
                "is_online": False,
                "tunnel_url": local_url,
                "search_url": f"{local_url}/search",
                "message": "cloudflared.exe not found on server",
                "mode": "local_network"
            }

        cmd = [str(CLOUDFLARED_EXE), "tunnel", "--url", "http://127.0.0.1:8000"]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            cls._process = proc
            cls._last_started = time.time()

            # Read stdout line by line until trycloudflare URL appears
            found_url = None
            start_t = time.time()
            while time.time() - start_t < timeout:
                if proc.poll() is not None:
                    break
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
                if m:
                    found_url = m.group(1).strip()
                    break

            if found_url:
                cls._current_url = found_url
                cls._is_online = True

                # Save to latest_online_link.txt
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                link_file = OUTPUT_DIR / "latest_online_link.txt"
                link_file.write_text(found_url, encoding="utf-8")

                search_url = f"{found_url.rstrip('/')}/search"

                # Update WhatsApp text file
                wa_message = (
                    "🇮🇳 *मतदाता सेवा — ऑनलाइन वोटर सर्च पोर्टल* 🇮🇳\n\n"
                    "उत्तर प्रदेश निर्वाचक नामावली (वोटर लिस्ट) में अपना व अपने पूरे परिवार का नाम, भाग संख्या, व क्रम संख्या आसानी से खोजें:\n\n"
                    f"🔗 *वेब लिंक:* {search_url}\n\n"
                    "📱 बिना किसी ऐप के सीधे मोबाइल ब्राउज़र में खोलें और 1 सेकंड में अपनी डिजिटल मतदाता पर्ची देखें!"
                )
                wa_file = OUTPUT_DIR / "whatsapp_share_info.txt"
                wa_file.write_text(wa_message, encoding="utf-8")

                # Generate QR code PNG
                try:
                    import qrcode
                    qr = qrcode.QRCode(border=1)
                    qr.add_data(search_url)
                    qr.make(fit=True)
                    qr_png = OUTPUT_DIR / "voter_search_qr.png"
                    qr.make_image(fill_color="black", back_color="white").save(str(qr_png))
                except Exception:
                    pass

                print(f"[TUNNEL] Fresh Cloudflare Tunnel Live: {found_url} -> {search_url}")

                return {
                    "status": "success",
                    "is_online": True,
                    "tunnel_url": found_url,
                    "search_url": search_url,
                    "qr_endpoint": f"/api/portal-qr?url={search_url}",
                    "mode": "cloudflare_tunnel",
                    "message": "नया ऑनलाइन टनल सफलतापूर्वक सक्रिय हो गया है।"
                }
            else:
                print("[TUNNEL] Timeout or error obtaining trycloudflare URL")
        except Exception as e:
            print(f"[TUNNEL] Error starting cloudflared: {e}")

        # Fallback to local network IP
        local_ip = cls.get_local_ip()
        local_url = f"http://{local_ip}:8000"
        return {
            "status": "fallback",
            "is_online": False,
            "tunnel_url": local_url,
            "search_url": f"{local_url}/search",
            "qr_endpoint": f"/api/portal-qr?url={local_url}/search",
            "mode": "local_network",
            "message": "ऑनलाइन टनल प्राप्त नहीं हो सका; स्थानीय नेटवर्क लिंक सक्रिय है।"
        }
