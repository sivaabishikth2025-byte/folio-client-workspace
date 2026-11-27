from pathlib import Path
import fnmatch

ROOT = Path(__file__).resolve().parents[1]


def test_vercelignore_never_excludes_seed_files():
    patterns = [l.strip() for l in (ROOT / '.vercelignore').read_text().splitlines() if l.strip() and not l.startswith('#')]
    unanchored = [p.lower() for p in patterns if not p.startswith('/')]
    for path in (ROOT / 'seed').rglob('*'):
        for part in path.relative_to(ROOT).parts:
            hits = [p for p in unanchored if fnmatch.fnmatch(part.lower(), p)]
            assert not hits, f'.vercelignore pattern {hits} would drop {path.relative_to(ROOT)} from the deployment'
