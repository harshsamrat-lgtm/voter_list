import urllib.request
import urllib.parse
import json
import time
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"
pdf_path = Path("uploads/5e2c82e4-9012-4f61-8628-56bbd25b1479_2026-EROLLGEN-S24-111-SIR-FinalRoll-Revision1-HIN-235-WI.pdf")

# 1. Upload PDF with multipart form-data
boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
with open(pdf_path, "rb") as f:
    file_bytes = f.read()

body = bytearray()
body.extend(f"--{boundary}\r\n".encode("utf-8"))
body.extend(f'Content-Disposition: form-data; name="file"; filename="{pdf_path.name}"\r\n'.encode("utf-8"))
body.extend(b"Content-Type: application/pdf\r\n\r\n")
body.extend(file_bytes)
body.extend(b"\r\n")
body.extend(f"--{boundary}--\r\n".encode("utf-8"))

req = urllib.request.Request(
    f"{BASE_URL}/api/upload",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
)

res = urllib.request.urlopen(req)
upload_data = json.loads(res.read())
job_id = upload_data["job_id"]
print(f"1. Uploaded PDF! Job ID: {job_id}, Total Pages: {upload_data['inspection']['total_pages']}")

# 2. Trigger AI extraction (Pages 3 to 4)
req = urllib.request.Request(f"{BASE_URL}/api/process/{job_id}?start_page=3&end_page=4", data=b"", method="POST")
res = urllib.request.urlopen(req)
proc_data = json.loads(res.read())
print(f"2. Started processing: {proc_data}")

# 3. Poll status
print("3. Polling status...")
for _ in range(30):
    time.sleep(3)
    s_res = urllib.request.urlopen(f"{BASE_URL}/api/status/{job_id}")
    s_data = json.loads(s_res.read())
    print(f"Status: {s_data['status']} | Progress: {s_data['progress_percent']}% | Pages: {s_data['processed_pages']}/{s_data['total_pages']} | Voters: {s_data['total_voters_extracted']}")
    if s_data['status'] in ['completed', 'error']:
        break

# 4. Preview
p_res = urllib.request.urlopen(f"{BASE_URL}/api/preview/{job_id}?limit=60")
p_data = json.loads(p_res.read())
records = p_data.get("records", [])

with open("e2e_extracted_preview.json", "w", encoding="utf-8") as f:
    json.dump(p_data, f, ensure_ascii=False, indent=2)

print("\n4. Extracted Voter Records Preview (Total:", len(records), "):")
print("=" * 110)
for r in records[:20]:
    print(f"#{r['serial_no']:03d} | EPIC: {r['epic_no']:12s} | नाम: {r['name']:20s} | {r['relation_type']}: {r['relation_name']:20s} | मकान: {r['house_no']:6s} | आयु: {str(r['age']):4s} | लिंग: {r['gender']}")

# 5. Download Excel
d_res = urllib.request.urlopen(f"{BASE_URL}/api/download/{job_id}")
excel_content = d_res.read()
print(f"\n5. Excel download success! Size: {len(excel_content)} bytes")
