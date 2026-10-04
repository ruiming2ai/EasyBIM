"""Reconstruct the exact reviewed H test delta in disposable Actions only."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE = '1f9715a07ba9a18c18c24bbc5a39ae8f407ee719'
EXPECTED = '60e45084a43a672988e78fe49d724d7b29942dbb6a732f75f8acefb2fb4a0f52'
if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('Disposable GitHub Actions checkout required.')
encoded = base64.b64encode(Path('.github/et-h-payload.gz').read_bytes()).decode()
# Correct transport insertions only; the complete decoded payload and every
# resulting file must still match the locally reviewed SHA256 checksums.
for old, new in [('PueOxj7n504','PueOxj504'), ('CUqPxVVKn20','CUqPxVKn20'),
                 ('DXrpdLaCRh+ae6691','DXrpdLaCRh+6691'), ('VQUmf/pJJyn','VQUmf/pJyn'),
                 ('FrIuGHcVVw5Wnd','FrIuGHcVw5Wnd'), ('BSCucUbS3S0TR','BSCucUbS0TR'),
                 ('mxkAShK88OcTR6yp6YPm3','mxkAShK88OcTR6YPm3')]:
    encoded = encoded.replace(old,new)
raw = gzip.decompress(base64.b64decode(encoded,validate=True))
if hashlib.sha256(raw).hexdigest() != EXPECTED:
    raise SystemExit('Payload checksum mismatch; no source modifications allowed.')
files = json.loads(raw)
# Test-only Python 2 compatibility delta, separately checksum-pinned.
fixture = files['Development Space/tests/etransmit_lab/test_saved_copy_h.py']
if hashlib.sha256(fixture['text'].encode('utf-8')).hexdigest() != 'ad9017ac6a5b5dfa161044fb45a7e8afe09a2919e7dade3bd8e0940957d92a82':
    raise SystemExit('Unexpected original H fixture.')
replacement = """def call_fixture(method, instance):
    # Python 2 enforces the declaring class on unbound methods. Reuse the
    # underlying setup function, not an unrelated class's bound method.
    function = getattr(method, 'im_func', getattr(method, '__func__', method))
    return function(instance)


class SavedCopyH(unittest.TestCase):"""
for old, new in [('class SavedCopyH(unittest.TestCase):', replacement),
                 ('fixture.Probes.setUp(self)', 'call_fixture(fixture.Probes.setUp, self)'),
                 ('fixture.Probes.restore(self)', 'call_fixture(fixture.Probes.restore, self)')]:
    if fixture['text'].count(old) != 1:
        raise SystemExit('Unexpected fixture replacement count.')
    fixture['text'] = fixture['text'].replace(old,new)
fixture['sha256'] = '45245db344fdc7f196015f4fab148dd0065fb054dc4ec3a143cc9653b53fc8de'
subprocess.check_call(['git','config','core.autocrlf','false'])
subprocess.check_call(['git','config','core.eol','lf'])
subprocess.check_call(['git','checkout','--force','--detach',BASE])
allowed = ('lib/easybim_etransmit_tests/', 'Development Space/tests/etransmit_lab/',
           'EasyBIM.tab/Test.panel/Test.pulldown/')
for name, item in files.items():
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or not (name.startswith(allowed) or name == 'Development Space/docs/e-transmit-H-saved-copy-test.md'):
        raise SystemExit('Unexpected change path: '+name)
    if item.get('before_sha256'):
        before = subprocess.check_output(['git','show',BASE+':'+name])
        if hashlib.sha256(before).hexdigest() != item['before_sha256']:
            raise SystemExit('Baseline source mismatch: '+name)
    elif p.exists():
        raise SystemExit('Refusing to replace unexpected existing file: '+name)
    if item.get('copy'):
        data = subprocess.check_output(['git','show',BASE+':'+item['copy']])
        if item.get('line_edits'):
            lines = data.decode('utf-8').splitlines(True)
            for i,j,text in reversed(item['line_edits']):
                lines[i:j] = [text]
            data = ''.join(lines).encode('utf-8')
    else:
        data = item['text'].encode('utf-8')
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise SystemExit('Reviewed source mismatch: '+name)
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(data)
paths = sorted(files)
subprocess.check_call(['git','add','--']+paths)
subprocess.check_call(['git','diff','--cached','--check'])
actual = subprocess.check_output(['git','diff','--cached','--name-only']).decode().splitlines()
if sorted(actual) != paths:
    raise SystemExit('Unexpected staged changes.')
meta = dict(base=BASE,payload_sha256=EXPECTED,files={p:files[p]['sha256'] for p in paths},
            tree=subprocess.check_output(['git','write-tree']).decode().strip(),
            fixture_delta_sha256=fixture['sha256'],production_tools_unchanged=True)
(Path(os.environ['RUNNER_TEMP'])/'H-verified.json').write_text(json.dumps(meta,indent=2))
print('Verified exact H source delta:',len(paths),'files; production tools untouched.')
