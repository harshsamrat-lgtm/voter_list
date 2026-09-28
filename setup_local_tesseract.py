import urllib.request
import py7zr
import subprocess
import os
import shutil
from pathlib import Path

extra_url = "https://github.com/ip7z/7zip/releases/download/26.03/7z2603-extra.7z"
extra_7z = Path("7z_extra.7z")
tools_7z = Path("tools/7z")
tools_7z.mkdir(parents=True, exist_ok=True)

if not (tools_7z / "7za.exe").exists():
    print("Downloading 7z extra from github...")
    urllib.request.urlretrieve(extra_url, extra_7z)
    print("Downloaded 7z extra:", extra_7z.stat().st_size)

    print("Extracting 7z extra using py7zr...")
    with py7zr.SevenZipFile(extra_7z, mode='r') as z:
        z.extractall(path=tools_7z)
    print("7z extra extracted!")

seven_za = tools_7z / "7za.exe"
if not seven_za.exists():
    seven_za = tools_7z / "x64" / "7za.exe"

print("Using 7za:", seven_za)

# Now extract tesseract_setup.exe
tess_setup = Path("tesseract_setup.exe")
tess_target = Path("tools/tesseract")
tess_target.mkdir(parents=True, exist_ok=True)

if not (tess_target / "tesseract.exe").exists():
    print("Extracting tesseract installer using 7za...")
    cmd = f'"{seven_za}" x "{tess_setup}" -o"{tess_target}" -y'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print("7za return code:", res.returncode)

tess_exe = tess_target / "tesseract.exe"
print("Tesseract exe exists?", tess_exe.exists())

# Copy traineddata files to tools/tesseract/tessdata
tessdata_dest = tess_target / "tessdata"
tessdata_dest.mkdir(parents=True, exist_ok=True)
for f in Path("tessdata").glob("*.traineddata"):
    shutil.copyfile(f, tessdata_dest / f.name)
    print(f"Copied {f.name} to {tessdata_dest / f.name}")

# Test running tesseract version
if tess_exe.exists():
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = str(tessdata_dest)
    res = subprocess.run([str(tess_exe), "--version"], capture_output=True, text=True, env=env)
    print("Tesseract Version Output:\n", res.stdout)
