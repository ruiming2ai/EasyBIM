"""Apply only the exact reviewed repair in a disposable GitHub Actions checkout."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path.cwd()
meta = json.loads((root / '.github/etransmit227-manifest.json').read_text(encoding='utf-8'))
encoded = ''.join((root / ('.github/etransmit227-parts/%d.txt' % i)).read_text(encoding='utf-8') for i in range(4))
patch = gzip.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(patch).hexdigest() != meta['patch_sha256']:
    raise SystemExit('Patch checksum mismatch.')

# This script runs only in an isolated ephemeral CI checkout, never in a user installation.
if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('Disposable GitHub Actions checkout required.')
subprocess.check_call(['git', 'config', 'core.autocrlf', 'false'])
subprocess.check_call(['git', 'config', 'core.eol', 'lf'])
subprocess.check_call(['git', 'checkout', '--detach', meta['base']])
paths = sorted(meta['sha256'])
for name in paths:
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or p.parts[0] == '.git':
        raise SystemExit('Unexpected patch path.')
    before = subprocess.run(['git', 'show', meta['base'] + ':' + name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if before.returncode == 0:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(before.stdout)  # canonical LF bytes on Windows too
    elif p.exists():
        raise SystemExit('A new patch path already exists: ' + name)
work = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir()))
patch_path = work / 'etransmit227-reviewed.patch'
patch_path.write_bytes(patch)
subprocess.check_call(['git', 'apply', '--check', str(patch_path)])
subprocess.check_call(['git', 'apply', str(patch_path)])
for name, expected in meta['sha256'].items():
    actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit('Reviewed source mismatch: ' + name)
subprocess.check_call(['git', 'add', '--'] + paths)
subprocess.check_call(['git', 'diff', '--cached', '--check'])
changed = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z']).decode('utf-8').rstrip('\0').split('\0')
if sorted(changed) != paths:
    raise SystemExit('Unexpected staged paths: ' + repr(changed))
meta['tree'] = subprocess.check_output(['git', 'write-tree']).decode('ascii').strip()
(work / 'etransmit227-source-hashes.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
print('Verified patch SHA256:', meta['patch_sha256'])
print('Candidate tree:', meta['tree'])
print('Changed files:', len(paths))
