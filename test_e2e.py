import sys
sys.stdout.reconfigure(encoding='utf-8')
import urllib.request
import json
import time
import os
from pathlib import Path

pdf_path = r'C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\uploads\5e2c82e4-9012-4f61-8628-56bbd25b1479_2026-EROLLGEN-S24-111-SIR-FinalRoll-Revision1-HIN-235-WI.pdf'

# Read file bytes and upload via multipart form-data
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
with open(pdf_path, 'rb') as f:
    file_bytes = f.read()

body = (
    f'--{boundary}\r\n'
    f'Content-Disposition: form-data; name="file"; filename="voter_list_up.pdf"\r\n'
    f'Content-Type: application/pdf\r\n\r\n'
).encode('utf-8') + file_bytes + f'\r\n--{boundary}--\r\n'.encode('utf-8')

headers = {
    'Content-Type': f'multipart/form-data; boundary={boundary}'
}

print("Uploading voter list PDF...")
req = urllib.request.Request("http://127.0.0.1:8000/api/upload", data=body, headers=headers, method="POST")
res = urllib.request.urlopen(req)
upload_data = json.loads(res.read().decode())
job_id = upload_data["job_id"]
print("Upload successful! Job ID:", job_id, "Total Pages:", upload_data["inspection"]["total_pages"], "Requires OCR:", upload_data["inspection"]["requires_ocr"])

# Process first 5 pages
print("\nStarting AI Extraction on pages 1 to 5...")
req2 = urllib.request.Request(f"http://127.0.0.1:8000/api/process/{job_id}?start_page=1&end_page=5", data=b"", method="POST")
res2 = urllib.request.urlopen(req2)
print("Processing triggered:", json.loads(res2.read().decode()))

for i in range(25):
    time.sleep(2)
    res_status = urllib.request.urlopen(f"http://127.0.0.1:8000/api/status/{job_id}")
    status_data = json.loads(res_status.read().decode())
    pct = status_data.get("progress_percent", 0)
    pages = status_data.get("processed_pages", 0)
    voters = status_data.get("total_voters_extracted", 0)
    st = status_data.get("status", "")
    print(f"[{i+1}] Progress: {pct}% | Pages: {pages}/5 | Voters Extracted: {voters} | Status: {st}")
    if st in ["completed", "error"]:
        break

res_prev = urllib.request.urlopen(f"http://127.0.0.1:8000/api/preview/{job_id}?page=1&limit=10")
preview_data = json.loads(res_prev.read().decode())
print(f"\n🎉 SUCCESS! Total Records Extracted: {preview_data['total_records']}")
for v in preview_data['records'][:6]:
    print(f"  Voter #{v['serial_no']} | Name: {v['name']} | EPIC: {v['epic_no']} | Gender: {v['gender']} | Age: {v['age']} | House: {v['house_no']}")
