"""Reproduce the exact reviewed repair in an ephemeral Actions checkout."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('Only an isolated GitHub Actions checkout may run this script.')
root = Path.cwd()
meta = json.loads((root / '.github/etransmit228-manifest.json').read_text(encoding='utf-8'))
encoded = ''.join((root / ('.github/etransmit228-parts/%d.txt' % i)).read_text(encoding='utf-8') for i in range(2))
patch = gzip.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(patch).hexdigest() != meta['patch_sha256']:
    raise SystemExit('Reviewed patch checksum mismatch.')
subprocess.check_call(['git', 'config', 'core.autocrlf', 'false'])
subprocess.check_call(['git', 'checkout', '--detach', meta['base']])
paths = sorted(meta['sha256'])
for name in paths:
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or p.parts[0] == '.git':
        raise SystemExit('Unexpected patch path.')
    before = subprocess.run(['git', 'show', meta['base'] + ':' + name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if before.returncode == 0:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(before.stdout)
    elif p.exists():
        raise SystemExit('A new patch path already exists: ' + name)
work = Path(os.environ['RUNNER_TEMP'])
patch_path = work / 'etransmit228-reviewed.patch'
patch_path.write_bytes(patch)
subprocess.check_call(['git', 'apply', '--check', str(patch_path)])
subprocess.check_call(['git', 'apply', str(patch_path)])
for name, expected in meta['sha256'].items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
        raise SystemExit('Reviewed source mismatch: ' + name)
subprocess.check_call(['git', 'add', '--'] + paths)
subprocess.check_call(['git', 'diff', '--cached', '--check'])
changed = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z']).decode('utf-8').rstrip('\0').split('\0')
if sorted(changed) != paths:
    raise SystemExit('Unexpected staged paths: ' + repr(changed))
meta['tree'] = subprocess.check_output(['git', 'write-tree']).decode('ascii').strip()
(work / 'etransmit228-source-hashes.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
print('Exact repair verified. Tree:', meta['tree'])
