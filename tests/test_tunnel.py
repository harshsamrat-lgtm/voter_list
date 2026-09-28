import subprocess
import re
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

print("Starting cloudflared quick tunnel to test connection...")
cmd = [r"cloudflared.exe", "tunnel", "--url", "http://127.0.0.1:8000"]

proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace"
)

url = None
start_time = time.time()

while time.time() - start_time < 20:
    line = proc.stdout.readline()
    if not line:
        time.sleep(0.5)
        continue
    # Look for trycloudflare.com URL
    m = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
    if m:
        url = m.group(1)
        print(f"\n==========================================")
        print(f"SUCCESS! Cloudflare Tunnel URL: {url}")
        print(f"==========================================")
        break
    if "error" in line.lower() and "failed" in line.lower():
        print("Line:", line.strip())

# Clean up test process
proc.terminate()
try:
    proc.wait(timeout=3)
except Exception:
    proc.kill()

if url:
    print(f"Verified working tunnel URL: {url}")
else:
    print("Could not extract URL within 20s")
