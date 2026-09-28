import os
import sys
import io
import re
import pymupdf as fitz
import pytesseract
from PIL import Image
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

tess_exe = Path(r"C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract\tesseract.exe")
tessdata_dir = Path(r"C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract\tessdata")

os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
pytesseract.pytesseract.tesseract_cmd = str(tess_exe)

pdf_path = "uploads/5e2c82e4-9012-4f61-8628-56bbd25b1479_2026-EROLLGEN-S24-111-SIR-FinalRoll-Revision1-HIN-235-WI.pdf"
doc = fitz.open(pdf_path)

def clean_hindi_str(text: str) -> str:
    if not text:
        return ""
    # Remove Latin characters and common OCR symbols
    cleaned = re.sub(r'[\d|\[\]\(\)\{\}\*\_\-\:\.\;\,\/\\\"\—\~]+', ' ', text)
    cleaned = re.sub(r'[a-zA-Z]+', ' ', cleaned)
    # Remove extra spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def parse_voter_card(card_img, default_serial, page_no):
    cw, ch = card_img.size
    
    # 1. Full card OCR with hin+eng
    full_text = pytesseract.image_to_string(card_img, lang="hin+eng", config="--psm 6")
    
    # 2. Top-right header crop specifically for EPIC
    epic_crop = card_img.crop((int(cw * 0.35), 0, cw, int(ch * 0.24)))
    epic_text = pytesseract.image_to_string(epic_crop, lang="eng", config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/")
    
    # Extract EPIC
    epic_no = ""
    ep_m = re.search(r'\b([A-Z]{2,4}\d{6,8}|UP/\d{1,2}/\d{2,3}/\d{4,8})\b', epic_text)
    if ep_m:
        epic_no = ep_m.group(1)
    else:
        ep_m_full = re.search(r'\b([A-Z]{2,4}\d{6,8}|UP/\d{1,2}/\d{2,3}/\d{4,8})\b', full_text)
        if ep_m_full:
            epic_no = ep_m_full.group(1)
            
    # Clean up common OCR EPIC errors (e.g. starting with OM -> YOM)
    if epic_no and len(epic_no) == 9 and epic_no.startswith("OM"):
        epic_no = "Y" + epic_no
        
    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
    
    name = ""
    rel_type = "पिता"
    rel_name = ""
    house_no = ""
    age = None
    gender = "पुरुष"
    
    # Detect Gender
    if re.search(r'(?:महिला|स्त्री|Female|fe\b|F:)', full_text, re.IGNORECASE):
        gender = "महिला"
    elif re.search(r'(?:अन्य|Other|Third)', full_text, re.IGNORECASE):
        gender = "अन्य"
    else:
        gender = "पुरुष"
        
    # Detect Age
    age_match = re.search(r'(?:आयु|उम्र|Age)\s*[:\-–]?\s*(\d{2,3})', full_text)
    if age_match:
        try:
            cand_age = int(age_match.group(1))
            if 18 <= cand_age <= 120:
                age = cand_age
        except ValueError:
            pass
            
    if not age:
        # Fallback 2-digit number on bottom lines
        two_digits = re.findall(r'\b(\d{2})\b', full_text)
        for d in two_digits:
            v = int(d)
            if 18 <= v <= 99 and v != default_serial:
                age = v
                break
                
    # Detect Relation Type
    if re.search(r'(?:पति\s*(?:का\s*)?नाम|पति)', full_text):
        rel_type = "पति"
    elif re.search(r'(?:माता\s*(?:का\s*)?नाम|माता)', full_text):
        rel_type = "माता"
    else:
        rel_type = "पिता"
        
    # Parse Lines
    for line in lines:
        # House No
        if re.search(r'(?:मकान\s*(?:संख्या|सं)?|गृह\s*सं|संख्या)', line) and not re.search(r'(?:आयु|लिंग)', line):
            hm = re.search(r'(?:मकान\s*(?:संख्या|सं)?|गृह\s*सं|संख्या)\s*[:\-–]?\s*([A-Za-z0-9\-/]+)', line)
            if hm:
                h_val = hm.group(1).strip()
                if h_val and h_val != str(age) and (not h_val.isalpha() or len(h_val) <= 4):
                    house_no = h_val
                    
        # Name
        if re.search(r'(?:मतदाता\s*का\s*नाम|नाम|Name)', line) and not re.search(r'(?:पिता|पति|माता|अभिभावक|अन्य|संख्या|मकान|आयु|लिंग)', line):
            cand_name = re.sub(r'^(?:मतदाता\s*का\s*नाम|नाम|Name)\s*[:\-–]?\s*', '', line)
            cand_name = clean_hindi_str(cand_name)
            if len(cand_name) >= 2:
                name = cand_name
                
        # Relation Name
        if re.search(r'(?:पिता|पति|माता|अन्य)\s*(?:का\s*)?(?:नाम)?', line) and not re.search(r'(?:मतदाता|मकान|आयु|लिंग)', line):
            cand_rel = re.sub(r'^(?:पिता\s*(?:का\s*)?नाम|पति\s*(?:का\s*)?नाम|माता\s*(?:का\s*)?नाम|अन्य)\s*[:\-–]?\s*', '', line)
            cand_rel = clean_hindi_str(cand_rel)
            if len(cand_rel) >= 2:
                rel_name = cand_rel
                
    # Positional Fallbacks if labels were partially broken
    # Filter lines that look like Hindi names
    hindi_candidates = []
    for l in lines:
        if any(kw in l for kw in ['उपलब्ध', 'फोटो', 'Photo', 'YOM', 'UP/', 'संख्या', 'आयु', 'लिंग']):
            continue
        c = clean_hindi_str(l)
        if len(c) >= 2 and c not in ['नाम', 'पिता', 'पति', 'माता', 'मकान', 'आयु', 'लिंग']:
            hindi_candidates.append(c)
            
    if not name and len(hindi_candidates) >= 1:
        name = hindi_candidates[0]
    if not rel_name and len(hindi_candidates) >= 2:
        rel_name = hindi_candidates[1]
        
    if not house_no:
        # Default house number search in digits
        h_cand = re.search(r'\b(00|\d{1,4}[A-Za-z\-/]*)\b', full_text)
        if h_cand and h_cand.group(1) != str(age) and h_cand.group(1) != str(default_serial):
            house_no = h_cand.group(1)
            
    return {
        "serial_no": default_serial,
        "name": name if name else f"मतदाता {default_serial}",
        "relation_type": rel_type,
        "relation_name": rel_name,
        "house_no": house_no if house_no else "—",
        "age": age if age else 30,
        "gender": gender,
        "epic_no": epic_no,
        "page_no": page_no
    }

print("Testing Complete Voter Card Extraction on Page 3 (30 cards)...")
print("=" * 100)

page = doc[2]
pix = page.get_pixmap(dpi=300)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
w, h = img.size

top_m = h * 0.075
bot_m = h * 0.038
left_m = w * 0.030
right_m = w * 0.030

col_w = (w - left_m - right_m) / 3.0
row_h = (h - top_m - bot_m) / 10.0

records = []
for r in range(10):
    for c in range(3):
        box_left = left_m + c * col_w
        box_top = top_m + r * row_h
        box_right = box_left + col_w
        box_bottom = box_top + row_h
        
        card_crop = img.crop((box_left, box_top, box_right, box_bottom))
        s_num = r * 3 + c + 1
        rec = parse_voter_card(card_crop, s_num, page_no=3)
        records.append(rec)
        print(f"#{rec['serial_no']:02d} | EPIC: {rec['epic_no']:12s} | नाम: {rec['name']:18s} | {rec['relation_type']}: {rec['relation_name']:20s} | मकान: {rec['house_no']:6s} | आयु: {rec['age']:3d} | लिंग: {rec['gender']}")

print(f"\nTotal extracted: {len(records)} records on Page 3.")
