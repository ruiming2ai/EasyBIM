# -*- coding: utf-8 -*-
"""Append-only evidence; event handlers observe but NEVER modify or save a model."""
from __future__ import unicode_literals
import io
import json
import os
import time
import traceback
from .base import files as f
from .base.revit import eid

class Evidence(object):
    def __init__(self,root):
        self.root=root;self.phase='initializing';self.events=[];self.subscriptions=[];self.documents=[]
        if not os.path.isdir(root):os.makedirs(root)
    def write(self,event,**values):
        data=dict(event=event,time=time.time(),phase=self.phase);data.update(values)
        self.events.append(data)
        with io.open(os.path.join(self.root,'events.jsonl'),'a',encoding='utf-8') as out:
            out.write(f.text(json.dumps(data,ensure_ascii=False,default=f.text))+'\n')
        return data
    def track(self,doc):
        if not any(doc is old or doc==old for old in self.documents):self.documents.append(doc)
    def snapshot(self,doc,where):
        data={}
        for key in ('PathName','Title','IsModified','IsDetached','IsWorkshared','IsModelInCloud','IsReadOnly','IsModifiable'):
            try:data[key]=getattr(doc,key)
            except Exception as exc:data[key]='UNAVAILABLE: '+f.text(exc)
        self.write('document_state',where=where,state=data)
        return data
    def subscribe(self,uiapp,owned_root):
        from System import EventHandler
        from Autodesk.Revit.DB import Events
        app=uiapp.Application
        def owned(doc):
            if any(doc is old or doc==old for old in self.documents):return True
            try:return f.canonical(doc.PathName).startswith(f.canonical(owned_root).rstrip('\\/')+('\\' if f.is_windows(owned_root) else '/'))
            except Exception:return False
        def changed(sender,args):
            try:
                doc=args.GetDocument()
                if not owned(doc):return
                def ids(method):
                    return [eid(i) for i in method()]
                self.write('DocumentChanged',path=f.text(doc.PathName),
                    transactions=[f.text(x) for x in args.GetTransactionNames()],
                    added=ids(args.GetAddedElementIds),modified=ids(args.GetModifiedElementIds),deleted=ids(args.GetDeletedElementIds))
            except Exception as exc:self.write('event_capture_error',message=f.text(exc))
        def saved(sender,args):
            try:
                doc=args.Document
                if not owned(doc):return
                self.snapshot(doc,'save event')
                self.write(f.text(args.GetType().Name),status=f.text(getattr(args,'Status','UNAVAILABLE')))
            except Exception as exc:self.write('event_capture_error',message=f.text(exc))
        events=[('DocumentChanged','DocumentChangedEventArgs',changed),
                ('DocumentSavingAs','DocumentSavingAsEventArgs',saved),
                ('DocumentSavedAs','DocumentSavedAsEventArgs',saved),
                ('DocumentSaving','DocumentSavingEventArgs',saved),
                ('DocumentSaved','DocumentSavedEventArgs',saved)]
        for name,type_name,callback in events:
            try:
                handler=EventHandler[getattr(Events,type_name)](callback)
                getattr(app,name).__iadd__(handler)
                self.subscriptions.append((app,name,handler))
            except Exception as exc:self.write('subscription_unavailable',event_name=name,message=f.text(exc))
    def close(self):
        for app,name,handler in reversed(self.subscriptions):
            try:getattr(app,name).__isub__(handler)
            except Exception as exc:self.write('unsubscribe_error',event_name=name,message=f.text(exc))
        self.subscriptions=[]
    def census(self,uiapp):
        """Observation, not add-in disabling or proof of a minimal environment."""
        apps=[];assemblies=[]
        try:
            for app in uiapp.LoadedApplications:
                apps.append(dict(type=f.text(app.GetType().FullName),assembly=f.text(app.GetType().Assembly.FullName)))
        except Exception as exc:apps.append(dict(unavailable=f.text(exc)))
        try:
            from System import AppDomain
            assemblies=[f.text(x.FullName) for x in AppDomain.CurrentDomain.GetAssemblies()]
        except Exception as exc:assemblies=['UNAVAILABLE: '+f.text(exc)]
        self.write('loaded_environment',applications=apps,assemblies=sorted(assemblies),
            isolation='Normal installed add-ins. No add-ins were disabled; no clean-profile claim.')


def write_json(path,data):
    with io.open(path,'w',encoding='utf-8') as out:
        out.write(f.text(json.dumps(data,ensure_ascii=False,indent=2,default=f.text)))
