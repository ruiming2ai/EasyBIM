"""Apply the pinned, locally tested repair in an isolated Actions checkout."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE = '4a8714b1f3e211448e914d05514b5149f07d4d24'
EXPECTED = 'e76a5ee8f3798c62fafa5f0d1665b42965045dec340afa54b571d7c29ff1892d'
parts = sorted(Path('.github/etransmit226-patch').glob('part-*'))
assert len(parts) == 6, 'Incomplete patch transfer'
patch = gzip.decompress(b''.join(p.read_bytes() for p in parts))
assert hashlib.sha256(patch).hexdigest() == EXPECTED, 'Patch checksum mismatch'
root = Path(os.environ['RUNNER_TEMP'])
patch_path = root / 'etransmit226.patch'
patch_path.write_bytes(patch)

def git(*args):
    return subprocess.check_output(['git'] + list(args), text=True).strip()

git('config', 'core.autocrlf', 'false')
git('checkout', '--detach', '--force', BASE)
git('checkout-index', '--all', '--force')
git('apply', '--index', '--unidiff-zero', str(patch_path))
git('diff', '--cached', '--check')
paths = git('diff', '--cached', '--name-only', '-z').strip('\0').split('\0')
assert len(paths) == 24, 'Unexpected changed-file set'
assert all(p.startswith(('lib/easybim_etransmit/', 'Development Space/tests/etransmit/')) or p in (
    'Development Space/docs/e-transmit-2.1.26.md',
    'EasyBIM.tab/Links.panel/e-transmit.pushbutton/window.xaml') for p in paths)
hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
(root / 'etransmit226-source-hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
print('Verified repair patch:', EXPECTED)
print('Candidate tree:', git('write-tree'))
print('Changed files:', len(paths))
