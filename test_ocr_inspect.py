import sys
import io
import json
import pymupdf as fitz
from PIL import Image
import numpy as np
from rapidocr_onnxruntime import RapidOCR

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

pdf_path = "uploads/5e2c82e4-9012-4f61-8628-56bbd25b1479_2026-EROLLGEN-S24-111-SIR-FinalRoll-Revision1-HIN-235-WI.pdf"
doc = fitz.open(pdf_path)
print(f"Total pages in PDF: {len(doc)}")

# Test on page 3 (index 2)
page = doc[2]
pix = page.get_pixmap(dpi=200)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
w, h = img.size
print(f"Page 3 dimension: {w}x{h}")

# Test RapidOCR with Hindi model
det_path = "models/ocr/det_v3.onnx"
rec_path = "models/ocr/rec_hindi.onnx"
dict_path = "models/ocr/dict_hindi.txt"

ocr_hi = RapidOCR(
    Det_model_path=det_path,
    Rec_model_path=rec_path,
    Rec_keys_path=dict_path
)
res_hi, _ = ocr_hi(np.array(img))

output_data = []
if res_hi:
    for idx, item in enumerate(res_hi):
        box, text, score = item
        pts = np.array(box)
        output_data.append({
            "idx": idx,
            "text": text,
            "score": float(score) if isinstance(score, (int, float, str)) else 0.0,
            "center_x": float(pts[:, 0].mean()),
            "center_y": float(pts[:, 1].mean()),
            "box": [[float(p[0]), float(p[1])] for p in box]
        })

with open("ocr_page3_debug.json", "w", encoding="utf-8") as f:
    json.dump(output_data, f, ensure_ascii=False, indent=2)

print(f"Successfully saved {len(output_data)} OCR text boxes to ocr_page3_debug.json")
