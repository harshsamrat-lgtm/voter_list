import urllib.request
import subprocess
import os
import shutil
from pathlib import Path

url = 'https://github.com/ip7z/7zip/releases/download/26.03/7z2603-x64.msi'
dest_msi = Path('7z.msi')
if not dest_msi.exists() or dest_msi.stat().st_size < 1000:
    print('Downloading 7z MSI...')
    urllib.request.urlretrieve(url, dest_msi)
    print('Downloaded MSI size:', dest_msi.stat().st_size)

target_dir = Path(r'C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\7z_msi')
target_dir.mkdir(parents=True, exist_ok=True)

cmd = f'msiexec /a "{dest_msi.resolve()}" /qn TARGETDIR="{target_dir.resolve()}"'
print('Running:', cmd)
res = subprocess.run(cmd, shell=True)
print('msiexec code:', res.returncode)

found = list(target_dir.rglob('*.exe'))
print('Found in MSI target:', found)

seven_z = None
for exe in found:
    if '7z.exe' in exe.name.lower():
        seven_z = exe
        break

if seven_z:
    print('SUCCESS! Found 7z at:', seven_z)
    # Now unpack tesseract_setup.exe
    tess_setup = Path('tesseract_setup.exe')
    tess_target = Path(r'C:\Users\HP\.gemini\antigravity-ide\scratch\voter-list-converter\tools\tesseract')
    tess_target.mkdir(parents=True, exist_ok=True)
    
    cmd_tess = f'"{seven_z}" x "{tess_setup.resolve()}" -o"{tess_target.resolve()}" -y'
    print('Extracting tesseract...')
    res_tess = subprocess.run(cmd_tess, shell=True)
    print('Extract return code:', res_tess.returncode)
    
    tess_exe = tess_target / 'tesseract.exe'
    print('Tesseract exe exists?', tess_exe.exists())
    
    # Copy traineddata files to tools/tesseract/tessdata
    tessdata_dest = tess_target / 'tessdata'
    tessdata_dest.mkdir(parents=True, exist_ok=True)
    for f in Path('tessdata').glob('*.traineddata'):
        shutil.copyfile(f, tessdata_dest / f.name)
        print(f'Copied {f.name} to {tessdata_dest / f.name}')
        
    if tess_exe.exists():
        env = os.environ.copy()
        env['TESSDATA_PREFIX'] = str(tessdata_dest)
        t_res = subprocess.run([str(tess_exe), '--version'], capture_output=True, text=True, env=env)
        print('TESSERACT VERSION:\n', t_res.stdout)
