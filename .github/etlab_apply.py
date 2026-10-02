"""Reconstruct reviewed ADDITIONS ONLY in an ephemeral Actions checkout."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE='675b8067b51e29b84f150a99b4105592ed33daa9'
EXPECTED='cd0b622ae371f430165c91a23f347f4aabfa7d3481bcf49814b53f6c6a321ffb'
if os.environ.get('GITHUB_ACTIONS')!='true':
    raise SystemExit('Disposable Actions checkout required.')
encoded=''.join(Path('.github/etlab-parts/%d.txt'%i).read_text().strip() for i in range(39))
raw=gzip.decompress(base64.b64decode(encoded,validate=True))
if hashlib.sha256(raw).hexdigest()!=EXPECTED:raise SystemExit('Reviewed payload checksum mismatch.')
files=json.loads(raw)
subprocess.check_call(['git','config','core.autocrlf','false'])
subprocess.check_call(['git','config','core.eol','lf'])
subprocess.check_call(['git','checkout','--force','--detach',BASE])
allowed=('EasyBIM.tab/Test.panel/','lib/easybim_etransmit_tests/','Development Space/tests/etransmit_lab/')
for name,item in files.items():
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or not (name.startswith(allowed) or name=='Development Space/docs/e-transmit-test-lab.md'):
        raise SystemExit('Unexpected addition path: '+name)
    before=subprocess.run(['git','cat-file','-e',BASE+':'+name],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if before.returncode==0 or p.exists():raise SystemExit('Refusing to modify any existing path: '+name)
    if 'copy' in item:
        data=subprocess.check_output(['git','show',BASE+':'+item['copy']])
        if item.get('line_edits'):
            lines=data.decode('utf-8').splitlines(True)
            for i,j,text in reversed(item['line_edits']):lines[i:j]=[text]
            data=''.join(lines).encode('utf-8')
    else:data=item['text'].encode('utf-8')
    if hashlib.sha256(data).hexdigest()!=item['sha256']:raise SystemExit('Source hash mismatch: '+name)
    p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
paths=sorted(files)
subprocess.check_call(['git','add','--']+paths)
subprocess.check_call(['git','diff','--cached','--check'])
changed=subprocess.check_output(['git','diff','--cached','--name-status']).decode('utf-8').splitlines()
if sorted(changed)!=sorted('A\t'+p for p in paths):raise SystemExit('Non-additive or unexpected changes detected.')
meta=dict(base=BASE,payload_sha256=EXPECTED,sha256={k:v['sha256'] for k,v in files.items()},
          tree=subprocess.check_output(['git','write-tree']).decode().strip(),all_existing_files_unchanged=True)
(Path(os.environ['RUNNER_TEMP'])/'etlab-verified.json').write_text(json.dumps(meta,indent=2))
print('Verified',len(paths),'additions. All existing tracked files unchanged. Tree:',meta['tree'])
