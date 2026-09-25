# -*- coding: utf-8 -*-
"""Read-only Data Management browser with an explicit version choice."""
from __future__ import unicode_literals
from . import files as f
from .cloud_sources import Graph

class Item(object):
    def __init__(self,name,value):self.Name=name;self.value=value

def label(record):
    a=record.get('attributes',{})
    return f.text(a.get('displayName') or a.get('name') or record.get('id',''))

def choose(client,select):
    """select(list[Item], title) returns an Item or None; never defaults to tip."""
    hubs=client.hubs()
    hub=select([Item(label(x),x) for x in hubs],'Autodesk account')
    if hub is None:return None
    projects=client.projects(hub.value['id'])
    project=select([Item(label(x),x) for x in projects],'Autodesk project')
    if project is None:return None
    pid=project.value['id'];base=[label(hub.value),label(project.value)]
    stack=[];current=client.top_folders(hub.value['id'],pid)
    while True:
        options=[Item('.. (up one folder)','..')] if stack else []
        for entry in current:
            kind=entry.get('type')
            if kind=='folders' or (kind=='items' and label(entry).lower().endswith('.rvt')):
                options.append(Item(('[Folder] ' if kind=='folders' else '')+label(entry),entry))
        selection=select(options,' / '.join(base+[x[0] for x in stack]))
        if selection is None:return None
        if selection.value=='..':
            _,current=stack.pop();continue
        item=selection.value
        if item.get('type')=='folders':
            stack.append((label(item),current));current=client.contents(pid,item['id']);continue
        versions=client.versions(pid,item['id'])
        choices=[]
        for version in versions:
            attrs=version.get('attributes',{})
            name='Version {0} | {1} | {2}'.format(attrs.get('versionNumber','?'),
                    attrs.get('createTime',''),version.get('id',''))
            choices.append(Item(name,version))
        chosen=select(choices,'Choose the EXACT published host version — '+label(item))
        if chosen is None:continue
        version=chosen.value['id']
        graph=Graph(pid,version,client.linked_files(pid,version),base+[x[0] for x in stack])
        return dict(graph=graph,client=client,folder_parts=base+[x[0] for x in stack],
                    display='/'.join(base+[x[0] for x in stack]+[label(item)])+' | '+version)
