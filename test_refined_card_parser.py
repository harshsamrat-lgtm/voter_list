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

page = doc[2] # Page 3
pix = page.get_pixmap(dpi=300)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
w, h = img.size

top_m = h * 0.075
bot_m = h * 0.038
left_m = w * 0.030
right_m = w * 0.030

col_w = (w - left_m - right_m) / 3.0
row_h = (h - top_m - bot_m) / 10.0

def parse_card(card_img, serial_default):
    cw, ch = card_img.size
    
    # 1. Header crop (top 20% of card)
    header_crop = card_img.crop((0, 0, cw, int(ch * 0.22)))
    # Serial on top-left (left 30%)
    serial_crop = header_crop.crop((0, 0, int(cw * 0.35), int(ch * 0.22)))
    # EPIC on top-right (right 65%)
    epic_crop = header_crop.crop((int(cw * 0.35), 0, cw, int(ch * 0.22)))
    
    serial_text = pytesseract.image_to_string(serial_crop, config="--psm 7 -c tessedit_char_whitelist=0123456789")
    serial_match = re.search(r'(\d+)', serial_text)
    serial_no = int(serial_match.group(1)) if serial_match else serial_default
    
    epic_text = pytesseract.image_to_string(epic_crop, lang="eng", config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/")
    epic_match = re.search(r'([A-Z]{2,4}\d{6,8}|UP/\d{1,2}/\d{2,3}/\d{4,8})', epic_text)
    epic_no = epic_match.group(1) if epic_match else ""
    
    # 2. Text Body crop (left 75% width, from 18% height to 100%)
    body_crop = card_img.crop((4, int(ch * 0.18), int(cw * 0.74), ch - 2))
    
    # Run Hindi + English OCR on body
    body_text = pytesseract.image_to_string(body_crop, lang="hin+eng", config="--psm 6")
    
    # If EPIC was missed in header, search full card/body
    if not epic_no:
        full_card_text = pytesseract.image_to_string(card_img, lang="hin+eng", config="--psm 6")
        ep_match = re.search(r'\b([A-Z]{2,4}\d{6,8}|UP/\d{1,2}/\d{2,3}/\d{4,8})\b', full_card_text)
        if ep_match:
            epic_no = ep_match.group(1)
            
    # Parse Body Lines
    name = ""
    relation_type = "पिता"
    relation_name = ""
    house_no = ""
    age = None
    gender = "पुरुष"
    
    lines = [l.strip() for l in body_text.split('\n') if l.strip()]
    
    # Identify lines by keyword or line position
    for idx, l in enumerate(lines):
        # Name
        if re.search(r'(?:नाम|Name)', l) and not re.search(r'(?:पिता|पति|माता|अभिभावक|अन्य)', l):
            cleaned = re.sub(r'^(?:मतदाता\s*का\s*नाम|नाम|Name)\s*[:\-–]?\s*', '', l)
            cleaned = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.]+', ' ', cleaned).strip()
            if len(cleaned) >= 2:
                name = cleaned
        # Relation
        elif re.search(r'(?:पिता|पति|माता|अन्य)', l):
            if 'पति' in l:
                relation_type = "पति"
            elif 'माता' in l:
                relation_type = "माता"
            else:
                relation_type = "पिता"
            cleaned = re.sub(r'^(?:पिता\s*(?:का\s*)?नाम|पति\s*(?:का\s*)?नाम|माता\s*(?:का\s*)?नाम|अन्य)\s*[:\-–]?\s*', '', l)
            cleaned = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.]+', ' ', cleaned).strip()
            if len(cleaned) >= 2:
                relation_name = cleaned
        # House No
        elif re.search(r'(?:मकान|गृह|संख्या|सं)', l) and not re.search(r'(?:आयु|लिंग)', l):
            h_match = re.search(r'(?:मकान\s*(?:संख्या|सं)?|गृह\s*सं|संख्या)\s*[:\-–]?\s*([A-Za-z0-9\-/]+)', l)
            if h_match:
                house_no = h_match.group(1)
        # Age and Gender
        if re.search(r'(?:आयु|उम्र|Age)', l) or re.search(r'(?:लिंग|Gender)', l):
            a_match = re.search(r'(?:आयु|उम्र|Age)\s*[:\-–]?\s*(\d{2,3})', l)
            if a_match:
                try:
                    cand_age = int(a_match.group(1))
                    if 18 <= cand_age <= 120:
                        age = cand_age
                except ValueError:
                    pass
            if re.search(r'(?:महिला|स्त्री|Female)', l):
                gender = "महिला"
            elif re.search(r'(?:पुरुष|Male)', l):
                gender = "पुरुष"
                
    # Fallbacks if lines didn't have explicit labels
    if not name and len(lines) >= 1:
        c0 = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.]+', ' ', lines[0]).strip()
        if len(c0) >= 2:
            name = c0
    if not relation_name and len(lines) >= 2:
        c1 = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.]+', ' ', lines[1]).strip()
        if len(c1) >= 2 and c1 != name:
            relation_name = c1
    if not house_no and len(lines) >= 3:
        h_cand = re.search(r'(\d+[A-Za-z\-/]*)', lines[2])
        if h_cand:
            house_no = h_cand.group(1)
            
    return {
        "serial": serial_no,
        "epic": epic_no,
        "name": name,
        "rel_type": relation_type,
        "rel_name": relation_name,
        "house_no": house_no,
        "age": age,
        "gender": gender
    }

print("Running Refined Card Parser on Page 3...")
print("-" * 90)
for r in range(10):
    for c in range(3):
        box_left = left_m + c * col_w
        box_top = top_m + r * row_h
        box_right = box_left + col_w
        box_bottom = box_top + row_h
        
        card_crop = img.crop((box_left, box_top, box_right, box_bottom))
        s_num = r * 3 + c + 1
        res = parse_card(card_crop, s_num)
        print(f"Card {res['serial']:02d} | EPIC: {res['epic']:12s} | नाम: {res['name']:15s} | {res['rel_type']}: {res['rel_name']:18s} | मकान: {res['house_no']:6s} | आयु: {str(res['age']):4s} | लिंग: {res['gender']}")
