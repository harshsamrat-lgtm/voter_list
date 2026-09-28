import urllib.request
import os
import subprocess
import time
from pathlib import Path

installer_url = 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe'
installer_path = Path(r'C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tesseract_setup.exe')
target_dir = Path(r'C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract')
target_dir.mkdir(parents=True, exist_ok=True)

if not installer_path.exists() or installer_path.stat().st_size < 10000:
    print('Downloading Tesseract installer (~45MB)...')
    urllib.request.urlretrieve(installer_url, installer_path)
    print('Downloaded setup exe, size:', installer_path.stat().st_size)
else:
    print('Installer already downloaded, size:', installer_path.stat().st_size)

print('Running silent installation to:', str(target_dir))
cmd = f'"{installer_path}" /S /D={target_dir}'
proc = subprocess.run(cmd, shell=True)
print('Process launched with code:', proc.returncode)

# Wait up to 20 seconds for files to be written
for _ in range(20):
    tess_exe = target_dir / 'tesseract.exe'
    if tess_exe.exists() and tess_exe.stat().st_size > 1000:
        print('SUCCESS! Tesseract exe found at:', str(tess_exe))
        break
    time.sleep(1)

# Copy hin.traineddata and eng.traineddata to target_dir/tessdata
tessdata_src = Path(r'C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tessdata')
tessdata_dest = target_dir / 'tessdata'
tessdata_dest.mkdir(parents=True, exist_ok=True)

import shutil
for f in tessdata_src.glob('*.traineddata'):
    dest_file = tessdata_dest / f.name
    shutil.copyfile(f, dest_file)
    print(f'Copied {f.name} to {dest_file}')
