"""Provenance hashes for the actual lab code in parent and worker processes."""
from __future__ import unicode_literals
import hashlib
import os

def code_hashes():
    root=os.path.dirname(__file__);result={}
    for folder,dirs,names in os.walk(root):
        dirs[:]=[d for d in dirs if d!='__pycache__']
        for name in names:
            if not name.endswith(('.py','.dll','.xaml')):continue
            path=os.path.join(folder,name)
            with open(path,'rb') as inp:data=inp.read()
            result[os.path.relpath(path,root).replace('\\','/')]=hashlib.sha256(data).hexdigest()
    return result
