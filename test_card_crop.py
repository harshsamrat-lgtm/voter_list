import sys
import io
import pymupdf as fitz
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np
from rapidocr_onnxruntime import RapidOCR

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

pdf_path = "uploads/5e2c82e4-9012-4f61-8628-56bbd25b1479_2026-EROLLGEN-S24-111-SIR-FinalRoll-Revision1-HIN-235-WI.pdf"
doc = fitz.open(pdf_path)

# Render Page 3 at 300 DPI
page = doc[2]
pix = page.get_pixmap(dpi=300)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
w, h = img.size
print(f"Page 3 300DPI size: {w}x{h}")

# Save full page 3 for inspection
img.save("page3_300dpi.png")

# Let's crop Card 1 (Row 0, Col 0)
# In 300 DPI, margins:
# top ~ 7%, bot ~ 4%, left ~ 3%, right ~ 3%
top_m = h * 0.075
bot_m = h * 0.04
left_m = w * 0.03
right_m = w * 0.03

col_w = (w - left_m - right_m) / 3.0
row_h = (h - top_m - bot_m) / 10.0

card1_crop = img.crop((left_m, top_m, left_m + col_w, top_m + row_h))
card1_crop.save("card1_300dpi.png")
print("Saved card1_300dpi.png")

# Now let's test OCR on card 1 with different preprocessings
ocr = RapidOCR(
    Det_model_path="models/ocr/det_v3.onnx",
    Rec_model_path="models/ocr/rec_hindi.onnx",
    Rec_keys_path="models/ocr/dict_hindi.txt"
)

# 1. Raw crop
res_raw, _ = ocr(np.array(card1_crop))
print("\n--- Card 1 Raw Crop OCR ---")
if res_raw:
    for b, t, s in res_raw:
        print(f"Text: '{t}', score: {s}")

# 2. Enhanced contrast & sharpness
enh = ImageEnhance.Contrast(card1_crop).enhance(2.0)
enh = ImageEnhance.Sharpness(enh).enhance(2.0)
enh.save("card1_enhanced.png")
res_enh, _ = ocr(np.array(enh))
print("\n--- Card 1 Enhanced Contrast OCR ---")
if res_enh:
    for b, t, s in res_enh:
        print(f"Text: '{t}', score: {s}")

# 3. Only the text section (left 75% of card, excluding header)
card_w, card_h = card1_crop.size
text_crop = card1_crop.crop((10, int(card_h * 0.2), int(card_w * 0.72), card_h - 10))
text_crop.save("card1_text_only.png")
res_text, _ = ocr(np.array(text_crop))
print("\n--- Card 1 Text Section Only OCR ---")
if res_text:
    for b, t, s in res_text:
        print(f"Text: '{t}', score: {s}")
