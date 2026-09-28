import os
import sys
import io
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

tess_exe = Path(r"C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract\tesseract.exe")
tessdata_dir = Path(r"C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract\tessdata")

os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
pytesseract.pytesseract.tesseract_cmd = str(tess_exe)

card_img = Image.open("card1_300dpi.png")

# Test 1: Full card with hin+eng
text1 = pytesseract.image_to_string(card_img, lang="hin+eng")
print("=== TEST 1: Full Card hin+eng ===")
print(text1)

# Test 2: Text portion (left 75%, body)
w, h = card_img.size
body_img = card_img.crop((10, int(h * 0.18), int(w * 0.72), h - 10))
body_img.save("card1_body_tess.png")
text2 = pytesseract.image_to_string(body_img, lang="hin+eng")
print("\n=== TEST 2: Body Only hin+eng ===")
print(text2)

# Test 3: Enhanced contrast on body
enh = ImageEnhance.Contrast(body_img).enhance(2.0)
enh = ImageEnhance.Sharpness(enh).enhance(2.0)
text3 = pytesseract.image_to_string(enh, lang="hin+eng")
print("\n=== TEST 3: Enhanced Body hin+eng ===")
print(text3)
