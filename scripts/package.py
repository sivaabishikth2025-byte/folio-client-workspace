"""Produce a reproducible source hand-in without credentials or working data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

root = Path(__file__).resolve().parents[1]
folders = ['backend', 'src', 'public', 'scripts', 'tests', 'seed']
names = ['README.md', 'NOTES.md',
         'package.json', 'package-lock.json', 'requirements.txt', 'app.py',
         'index.html', 'vite.config.js', 'vercel.json', '.env.example',
         '.gitignore', '.vercelignore', '.python-version']
files = [root / n for n in names if (root / n).is_file()]
for folder in folders:
    files.extend(p for p in (root / folder).rglob('*')
                 if p.is_file() and not p.is_symlink()
                 and '__pycache__' not in p.parts
                 and not p.name.startswith('configure_')
                 and not (p.name.startswith('Folio-') and p.name.endswith('-access.txt')))
target = root.parent / 'Folio-source.zip'
manifest = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for p in sorted(files):
        archive.write(p, 'folio/' + p.relative_to(root).as_posix())
    archive.writestr('folio/SHA256SUMS.json', json.dumps(manifest, indent=2))
with ZipFile(target) as archive:
    assert archive.testzip() is None
    assert not any(Path(n).name in ('.env', '.env.local') for n in archive.namelist())
print(f'{target}: {len(files)} source files, {target.stat().st_size:,} bytes; integrity verified')
