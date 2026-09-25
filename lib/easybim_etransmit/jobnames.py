# -*- coding: utf-8 -*-
"""One shared, collision-safe model-name plan for jobs and per-model archives."""
from __future__ import unicode_literals
import ntpath
import os
import re
from . import files as f


def stem(source, display_name=None):
    value=f.text(display_name or source).replace('\\','/').rstrip('/').rsplit('/',1)[-1]
    if value.lower().endswith('.rvt'):value=value[:-4]
    if (not value or value in ('.','..') or value.rstrip(' .')!=value or
            any(ord(c)<32 or c in '<>:"/\\|?*' for c in value) or
            re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',value,re.I) or
            f.path_units(value)>255):
        raise ValueError('Model name cannot be used as a Windows job folder: '+value)
    return value


def plan(models, root, separate=True, names=None):
    models=list(models)
    if not separate:return [root]
    names=names or {}
    original=[stem(m,names.get(m)) for m in models]
    reserved=set(x.lower() for x in original)
    used=set();paths=[]
    existing=set(n.lower() for n in os.listdir(root)) if os.path.isdir(root) else set()
    for name in original:
        chosen=name;index=2
        while True:
            pair=set((chosen.lower(),(chosen+'.zip').lower()))
            # Reserve both the directory and its archive, even before creating
            # either. Suffixes cannot steal another model's original name.
            conflict=bool(pair & (used | existing))
            conflict=conflict or (chosen!=name and chosen.lower() in reserved)
            if not conflict:break
            chosen=name+' ({0})'.format(index);index+=1
        if f.path_units(chosen)>255:raise ValueError('Model job folder exceeds the Windows name limit.')
        paths.append(os.path.join(root,chosen));used.update(pair)
    return paths
