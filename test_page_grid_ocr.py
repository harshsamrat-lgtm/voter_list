import os
import sys
import io
import re
import pymupdf as fitz
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

tess_exe = Path(r"C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract\tesseract.exe")
tessdata_dir = Path(r"C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract\tessdata")

os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
pytesseract.pytesseract.tesseract_cmd = str(tess_exe)

pdf_path = "uploads/5e2c82e4-9012-4f61-8628-56bbd25b1479_2026-EROLLGEN-S24-111-SIR-FinalRoll-Revision1-HIN-235-WI.pdf"
doc = fitz.open(pdf_path)

# Page 3 (0-indexed = 2)
page = doc[2]
pix = page.get_pixmap(dpi=300)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
w, h = img.size

# Grid boundaries (at 300 DPI)
top_m = h * 0.075
bot_m = h * 0.038
left_m = w * 0.030
right_m = w * 0.030

col_w = (w - left_m - right_m) / 3.0
row_h = (h - top_m - bot_m) / 10.0

print(f"Testing Grid Extraction on Page 3 (3x10 = 30 cards)...")
print("=" * 80)

card_index = 1
for r in range(10):
    for c in range(3):
        box_left = left_m + c * col_w
        box_top = top_m + r * row_h
        box_right = box_left + col_w
        box_bottom = box_top + row_h
        
        card_crop = img.crop((box_left, box_top, box_right, box_bottom))
        
        # Run Tesseract with custom config
        raw_text = pytesseract.image_to_string(card_crop, lang="hin+eng", config="--psm 6")
        
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        
        # Parse fields
        # 1. EPIC
        epic_match = re.search(r'\b([A-Z]{3,4}\d{7}|\d{7}[A-Z]{3}|[A-Z]{2,4}\d{6,8})\b', raw_text)
        epic_no = epic_match.group(1) if epic_match else ""
        if not epic_no:
            old_epic = re.search(r'(UP/\d{1,2}/\d{2,3}/\d{4,8})', raw_text)
            if old_epic:
                epic_no = old_epic.group(1)
                
        # 2. Name (मतदाता का नाम)
        name = ""
        name_match = re.search(r'(?:नाम|Name)\s*[:\-–]?\s*([^\n|]+)', raw_text)
        if name_match:
            candidate = name_match.group(1).strip()
            # Clean trailing artifacts
            candidate = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.]+', ' ', candidate).strip()
            # Remove english junk if predominantly hindi
            if len(candidate) >= 2:
                name = candidate
                
        # 3. Relative Name (पिता/पति/माता का नाम)
        rel_type = "पिता"
        rel_name = ""
        if re.search(r'पति', raw_text):
            rel_type = "पति"
        elif re.search(r'माता', raw_text):
            rel_type = "माता"
            
        rel_match = re.search(r'(?:पिता|पति|माता|अन्य)\s*(?:का\s*)?(?:नाम)?\s*[:\-–]?\s*([^\n|]+)', raw_text)
        if rel_match:
            cand_rel = rel_match.group(1).strip()
            cand_rel = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.]+', ' ', cand_rel).strip()
            if len(cand_rel) >= 2 and cand_rel != name:
                rel_name = cand_rel
                
        # 4. House No (मकान संख्या)
        house_no = ""
        house_match = re.search(r'(?:संख्या|मकान|गृह|सं|No)\s*[:\-–]?\s*([A-Za-z0-9\-/]+)', raw_text)
        if house_match:
            h_cand = house_match.group(1).strip()
            if h_cand and not h_cand.isalpha() or len(h_cand) <= 5:
                house_no = h_cand
                
        # 5. Age & Gender
        age = ""
        gender = "पुरुष"
        age_match = re.search(r'(?:आयु|उम्र|Age)\s*[:\-–]?\s*(\d{2,3})', raw_text)
        if age_match:
            age = age_match.group(1)
            
        if re.search(r'(?:महिला|स्त्री|Female)', raw_text):
            gender = "महिला"
        elif re.search(r'(?:पुरुष|Male)', raw_text):
            gender = "पुरुष"
            
        print(f"Card {card_index:02d} [R{r}C{c}] -> EPIC: {epic_no:12s} | नाम: {name:15s} | {rel_type}: {rel_name:15s} | मकान: {house_no:6s} | आयु: {age:3s} | लिंग: {gender}")
        card_index += 1
