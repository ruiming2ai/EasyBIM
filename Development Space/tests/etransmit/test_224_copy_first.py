# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import VERSION, files as f, revit
import test_211_ui_plugins as ui_fixtures


class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)


class ElementId(object):
    def __init__(self,value): self.Value=int(value)


class Reference(object):
    def __init__(self,path,path_type,status):
        self.path=path;self.PathType=path_type;self.status=status
    def GetPath(self): return self.path
    def GetLinkedFileStatus(self): return self.status


class TransmissionSnapshot(object):
    """A fresh read keeps last-saved data separate from desired references."""
    def __init__(self,store):
        self.last=dict(store.last);self.desired=dict(store.desired)
        self.IsTransmitted=store.transmitted;self.disposed=False
    def GetAllExternalFileReferenceIds(self):
        return [ElementId(key) for key in sorted(self.last)]
    def GetLastSavedReferenceData(self,ident):
        return Reference(*self.last[str(ident.Value)])
    def GetDesiredReferenceData(self,ident):
        desired=self.desired.get(str(ident.Value))
        if desired is None:return None
        return Reference(desired[0],desired[1],'Loaded' if desired[2] else 'Unloaded')
    def SetDesiredReferenceData(self,ident,path,path_type,loaded):
        self.desired[str(ident.Value)]=(path,path_type,bool(loaded))
    def Dispose(self): self.disposed=True


class TransmissionStore(object):
    def __init__(self,last):
        self.last=dict(last);self.desired={};self.transmitted=False
        self.reads=[];self.writes=[]
    def read(self,path):
        snapshot=TransmissionSnapshot(self)
        self.reads.append(snapshot)
        return snapshot
    def write(self,path,snapshot):
        self.desired=dict(snapshot.desired)
        self.transmitted=bool(snapshot.IsTransmitted)
        self.writes.append(path)


class CopyFirstTransmission(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET224_copy_')
        self.addCleanup(shutil.rmtree,self.root)
        self.target=os.path.join(self.root,'Host.rvt')
        self.stage=os.path.join(self.root,'stage.rvt')
        self.cad=os.path.join(self.root,'Links','CAD','Plan.dwg')
        self.link=os.path.join(self.root,'Links','Revit','Architecture.rvt')
        self.relative_cad=os.path.join('Links','CAD','Plan.dwg')
        self.relative_link=os.path.join('Links','Revit','Architecture.rvt')
        for path in (self.target,self.stage,self.cad,self.link):
            directory=os.path.dirname(path)
            if not os.path.isdir(directory):os.makedirs(directory)
            with open(path,'wb') as out:out.write(b'packaged-file')
        self.last={
            '1':('N:/source/Plan.dwg','Absolute','Loaded'),
            '2':('N:/source/Architecture.rvt','Absolute','Loaded'),
            '3':('../Shared/Unknown.txt','Relative','Unloaded')}
        self.store=TransmissionStore(self.last)
        self.db=Obj(
            ModelPathUtils=Obj(ConvertUserVisiblePathToModelPath=lambda path:path,
                               ConvertModelPathToUserVisiblePath=lambda path:path),
            TransmissionData=Obj(ReadTransmissionData=self.store.read,
                                 WriteTransmissionData=self.store.write,
                                 IsDocumentTransmitted=lambda path:self.store.transmitted),
            PathType=Obj(Absolute='Absolute',Relative='Relative'),
            ElementId=ElementId)
        self.backend=revit.Backend(self.db,Obj(VersionNumber='2024'),self.root)
        self.backend.basic=lambda path:dict(version='2024',workshared=True)

    def native_rows(self):
        return [
            dict(id='1',element_id='1',kind='CADLink',td=True,special='native',
                 source='N:/source/Plan.dwg',target=self.cad,loaded=True),
            dict(id='2',element_id='2',kind='RevitLink',td=True,special='native',
                 source='N:/source/Architecture.rvt',target=self.link,
                 loaded=True,package_loaded=False)]

    def test_final_transmit_preserves_package_paths_after_untransmitted_metadata_write(self):
        rows=self.native_rows()
        self.assertTrue(self.backend.apply_metadata(
            self.target,self.target,rows,relative=True,mark_transmitted=False))
        self.assertFalse(self.store.transmitted)
        self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
        self.assertEqual(self.store.last['1'][0],'N:/source/Plan.dwg')

        self.assertTrue(self.backend.mark_transmitted_package(self.target,rows))

        self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
        self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))
        self.assertEqual(self.store.desired['3'],('../Shared/Unknown.txt','Relative',False))
        self.assertTrue(self.store.transmitted)
        self.assertEqual(len(self.store.reads),2)
        self.assertIsNot(self.store.reads[0],self.store.reads[1])
        self.assertTrue(all(snapshot.disposed for snapshot in self.store.reads))

    def test_final_transmit_can_overlay_copied_native_paths_without_worker_write(self):
        self.assertTrue(self.backend.mark_transmitted_package(self.target,self.native_rows()))
        self.assertEqual(self.store.desired['1'],(self.relative_cad,'Relative',True))
        self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))

    def test_failed_manual_and_skipped_rows_keep_original_reference_and_status(self):
        for state in ('FAILED','ROLLED_BACK','ROLLBACK_FAILED','REMOVED_BY_CLEANUP',
                      'MANUAL_REPAIR_REQUIRED','PLUGIN_RECONNECT_REQUIRED'):
            row=self.native_rows()[0]
            row['repath']=state
            self.store.desired={};self.store.transmitted=False
            self.assertTrue(self.backend.apply_metadata(
                self.target,self.target,[row],relative=True,mark_transmitted=False))
            self.assertEqual(row['repath'],state)
            self.assertNotIn('1',self.store.desired)
            self.assertTrue(self.backend.mark_transmitted_package(self.target,[row]))
            self.assertEqual(self.store.desired['1'],('N:/source/Plan.dwg','Absolute',True))
            self.assertEqual(row['repath'],state)

        row=self.native_rows()[0]
        row['skip_repath']=True
        self.store.desired={};self.store.transmitted=False
        self.backend.apply_metadata(self.target,self.target,[row],mark_transmitted=False)
        self.assertNotIn('repath',row)
        self.assertNotIn('1',self.store.desired)
        self.backend.mark_transmitted_package(self.target,[row])
        self.assertEqual(self.store.desired['1'],('N:/source/Plan.dwg','Absolute',True))

    def test_unconverted_external_or_cloud_link_keeps_saved_reference(self):
        cloud=dict(project_guid='11111111-1111-1111-1111-111111111111',
                   model_guid='22222222-2222-2222-2222-222222222222',region='US')
        for updates in (dict(cloud_identity=cloud),dict(td=False,special='external')):
            row=self.native_rows()[1]
            row.update(updates)
            row.pop('package_loaded')
            self.store.desired={};self.store.transmitted=False
            self.backend.apply_metadata(self.target,self.target,[row],mark_transmitted=False)
            self.assertNotIn('2',self.store.desired)
            self.assertNotIn('repath',row)
            self.backend.mark_transmitted_package(self.target,[row])
            self.assertEqual(self.store.desired['2'],('N:/source/Architecture.rvt','Absolute',True))

    def test_converted_cloud_link_retains_successful_relative_local_reference(self):
        row=self.native_rows()[1]
        row.update(cloud_identity=dict(
            project_guid='11111111-1111-1111-1111-111111111111',
            model_guid='22222222-2222-2222-2222-222222222222',region='US'),
            repath='API_LOCAL_LINK_RELATIVE')
        self.backend.mark_transmitted_package(self.target,[row])
        self.assertEqual(self.store.desired['2'],(self.relative_link,'Relative',False))

    def materializing_revit_fixture(self,workshared=True):
        try:import System
        except ImportError:
            system=types.ModuleType('System');system.Int64=int;system.Int32=int
            sys.modules['System']=system
            self.addCleanup(sys.modules.pop,'System',None)
        self.backend.basic=lambda path:dict(version='2024',workshared=workshared,
                    is_central=workshared,central=self.target if workshared else '')
        opened=[];closed=[];saved=[]
        def reference(document,ident):
            value,path_type,status=document.references[str(ident.Value)]
            absolute=os.path.join(self.root,value) if path_type=='Relative' else value
            return Obj(GetAbsolutePath=lambda:absolute,GetPath=lambda:value)
        self.db.ExternalFileUtils=Obj(GetExternalFileReference=reference)
        self.db.RevitLinkType=Obj(IsLoaded=lambda doc,ident:
                    doc.references[str(ident.Value)][2]=='Loaded')
        def open_copy(path,discard=False,**kwargs):
            opened.append(path)
            references=dict(self.store.last)
            if self.store.transmitted:
                references.update(dict((key,(value[0],value[1],
                    'Loaded' if value[2] else 'Unloaded')) for key,value in self.store.desired.items()))
            doc=Obj(PathName=path,IsDetached=False,IsModified=False,
                    references=references,Save=lambda:saved.append(('save',False)),
                    Close=lambda save:closed.append(save) or True)
            def get_element(ident):
                return Obj(GetExternalFileReference=lambda:reference(doc,ident),
                           LoadFrom=lambda path:self.fail('native references must consume staged metadata'))
            doc.GetElement=get_element
            return doc
        def save_as(document,path,clear_transmitted=False):
            saved.append((path,clear_transmitted))
            self.store.last=dict(document.references)
            if clear_transmitted:
                self.store.transmitted=False;self.store.desired={}
            shutil.copyfile(self.stage,path)
        self.backend.open_copy=open_copy
        self.backend._save_as_independent_package_central=save_as
        return opened,closed,saved

    def test_failed_cad_api_repair_fails_independent_finalization(self):
        opened,closed,saved=self.materializing_revit_fixture()
        row=self.native_rows()[0]
        row.update(id='4',element_id='4',td=False,special='external')
        open_copy=self.backend.open_copy
        def open_with_failed_cad(path,discard):
            doc=open_copy(path,discard)
            def rejected(path):raise RuntimeError('CAD reload rejected')
            doc.GetElement=lambda ident:Obj(LoadFrom=rejected)
            return doc
        self.backend.open_copy=open_with_failed_cad

        with self.assertRaises(RuntimeError) as caught:
            self.backend.finish_independent(
                self.stage,self.target,[row],dict(repath=True,cleanup=False,upgrade=False))

        self.assertIn('CAD reload rejected',str(caught.exception))
        self.assertEqual(row['repath'],'FAILED')
        self.assertTrue(closed)
        self.assertTrue(all(value is False for value in closed))
        self.assertEqual(self.store.last['1'],self.last['1'])
        self.assertFalse(self.store.transmitted)

    def test_native_references_are_saved_in_normal_workshared_and_project_hosts(self):
        for workshared in (True,False):
            self.store.last=dict(self.last);self.store.desired={};self.store.transmitted=False
            opened,closed,saved=self.materializing_revit_fixture(workshared)
            rows=self.native_rows()
            def write_between_opens(path,snapshot):
                self.assertEqual(closed,[] if path==self.stage else [False])
                self.store.write(path,snapshot)
            self.db.TransmissionData.WriteTransmissionData=write_between_opens

            result=self.backend.finish_independent(
                self.stage,self.target,rows,dict(repath=True,cleanup=False,upgrade=False))

            self.assertEqual(result['issues'],[])
            self.assertTrue(result['host_finalized'])
            self.assertTrue(result['saved_references_checked'])
            self.assertFalse(result['verified_in_process'])
            self.assertEqual(result['verification_status'],'SAVED_REFERENCES_CHECKED')
            self.assertFalse(self.store.transmitted)
            self.assertEqual(self.store.last['1'],(self.relative_cad,'Relative','Loaded'))
            self.assertEqual(self.store.last['2'],(self.relative_link,'Relative','Unloaded'))
            self.assertEqual(rows[0]['verification'],'SAVED_REFERENCE_CHECKED')
            self.assertEqual(rows[1]['verification'],'SAVED_REFERENCE_CHECKED')
            self.assertEqual(opened,[self.stage,self.target,self.target])
            self.assertEqual(closed,[False,False,False])
            self.assertEqual(saved,[(self.target,True),(self.target,True)])


class CopyFirstPackageUI(unittest.TestCase):
    # Copy functions, not Python 2 methods bound to the original fixture class.
    setUp=ui_fixtures.UI.__dict__['setUp']
    cleanup=ui_fixtures.UI.__dict__['cleanup']
    form=ui_fixtures.UI.__dict__['form']

    def loaded_dialog(self):
        xaml=os.path.join(ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
        name='{http://schemas.microsoft.com/winfx/2006/xaml}Name'
        class Window(object):
            @staticmethod
            def __init__(window,path):
                for node in ET.parse(path).getroot().iter():
                    control_name=node.get(name)
                    if control_name:
                        setattr(window,control_name,Obj(
                            IsChecked=node.get('IsChecked')=='True',Text=node.get('Text','')))
        old_window=self.ui.forms.WPFWindow;old_db=self.ui.DB
        old_appdata=os.environ.get('APPDATA')
        self.ui.forms.WPFWindow=Window;self.ui.DB=Obj(Document=Obj())
        os.environ['APPDATA']=self.root
        try:
            return self.ui.Dialog(Obj(Application=Obj(VersionNumber='2024',Documents=[]),
                                      ActiveUIDocument=None),xaml)
        finally:
            self.ui.forms.WPFWindow=old_window;self.ui.DB=old_db
            if old_appdata is None:os.environ.pop('APPDATA',None)
            else:os.environ['APPDATA']=old_appdata

    def test_copy_first_is_enabled_in_actual_dialog_by_default(self):
        self.assertTrue(f.defaults()['simple_repath'])
        self.assertTrue(self.loaded_dialog().SimpleRepath.IsChecked)
        dialog=self.form()
        dialog.transmit_click(None,None)
        self.assertTrue(dialog.result[2]['simple_repath'])

    def test_copy_first_preferences_are_applied_and_saved(self):
        settings=os.path.join(self.root,'EasyBIM','e-transmit','settings.json')
        os.makedirs(os.path.dirname(settings))
        for enabled in (False,True):
            with io.open(settings,'w',encoding='utf-8') as out:
                out.write(json.dumps(dict(simple_repath=enabled,repath=True)))
            loaded=self.loaded_dialog()
            self.assertEqual(loaded.SimpleRepath.IsChecked,enabled)
            self.assertTrue(loaded.Repath.IsChecked)
            dialog=self.form()
            # Both modes remain explicit choices and must round-trip.
            dialog.SimpleRepath=Obj(IsChecked=enabled)
            dialog.SaveSettings.IsChecked=True
            dialog.settings=settings
            dialog.transmit_click(None,None)
            self.assertEqual(dialog.result[2]['simple_repath'],enabled)
            with io.open(settings,encoding='utf-8') as inp:saved=json.load(inp)
            self.assertEqual(saved['simple_repath'],enabled)
            self.assertTrue(saved['repath'])

    def test_older_preferences_missing_option_preserve_repath_choice(self):
        settings=os.path.join(self.root,'EasyBIM','e-transmit','settings.json')
        os.makedirs(os.path.dirname(settings))
        with io.open(settings,'w',encoding='utf-8') as out:
            out.write(json.dumps(dict(repath=False,file_structure='categories')))
        dialog=self.loaded_dialog()
        self.assertTrue(dialog.SimpleRepath.IsChecked)
        self.assertFalse(dialog.Repath.IsChecked)

    def test_dialog_help_distinguishes_copy_first_from_independent_saving(self):
        xaml=os.path.join(ROOT,'EasyBIM.tab','Links.panel','e-transmit.pushbutton','window.xaml')
        labels=' '.join(node.get('Text','') for node in ET.parse(xaml).getroot().iter()).lower()
        for phrase in ('unchanged','normally saved host','not delivered in a detached or transmitted state','independent-model completion'):
            self.assertIn(phrase,labels)
        self.assertFalse(self.loaded_dialog().DiscardWorksets.IsChecked)

    def test_provenance_is_recorded_before_dialog_and_batch(self):
        provenance=dict(version=VERSION,installation_root=self.root,
                        module_path=self.ui.__file__,button_path=os.path.join(self.root,'script.py'),
                        git_commit='0123456789abcdef0123456789abcdef01234567')
        journal=[];observed=[]
        uiapp=Obj(Application=Obj(VersionNumber='2024',
                    WriteJournalComment=lambda message,stamp:journal.append(message)))
        run_root=os.path.join(self.root,'export')
        opts=f.defaults()
        choices=[self.ui.Choice('Saved host',source=os.path.join(self.root,'Host.rvt'),mode='SAVED_FILE')]
        class Dialog(object):
            def __init__(dialog,application,xaml):
                observed.append(('dialog',list(journal)))
                dialog.result=(choices,run_root,opts,[])
            def ShowDialog(dialog):pass
        class Progress(object):
            available=True;mode='TEST';cancelled=False
            def __enter__(progress):return progress
            def __exit__(progress,*args):pass
            def set_phase(progress,label):pass
            def update_progress(progress,current,total):pass
        def run_batch(sources,root,backend,options,extras,cancel,pulse,model_names=None):
            with io.open(os.path.join(root,'ET_TRACE.txt'),encoding='utf-8') as inp:
                observed.append(('batch',inp.read()))
            self.assertEqual(options['runtime_provenance'],provenance)
            return [dict(status='READY',issues=[])]
        def replace(obj,name,value):
            previous=getattr(obj,name)
            setattr(obj,name,value)
            self.addCleanup(setattr,obj,name,previous)
        replace(self.ui,'Dialog',Dialog)
        replace(self.ui,'DockableTransferProgress',Progress)
        replace(self.ui,'Registry',lambda *args,**kwargs:Obj(close=lambda:None))
        replace(self.ui.preflight,'save_selected',lambda *args:[])
        replace(self.ui.batch,'run_batch',run_batch)
        replace(self.ui.engine,'completion_message',lambda *args,**kwargs:'ready')
        collected=[]
        def collect_provenance(**kwargs):
            collected.append(kwargs)
            return provenance
        replace(self.ui.provenance,'collect',collect_provenance)
        old_startfile=getattr(self.ui.os,'startfile',None)
        self.ui.os.startfile=lambda path:None
        if old_startfile is None:self.addCleanup(delattr,self.ui.os,'startfile')
        else:self.addCleanup(setattr,self.ui.os,'startfile',old_startfile)

        self.ui.run(uiapp,os.path.join(self.root,'window.xaml'))

        self.assertEqual(collected,[dict(button_path=os.path.join(self.root,'script.py'),
                                         module_path=self.ui.__file__)])
        self.assertEqual([entry[0] for entry in observed],['dialog','batch'])
        marker='ET_INSTALLATION_PROVENANCE | '
        def recorded_provenance(messages):
            return [json.loads(message.split(marker,1)[1]) for message in messages
                    if marker in message]
        self.assertEqual(recorded_provenance(observed[0][1]),[provenance])
        before_batch=recorded_provenance(observed[1][1].splitlines())
        self.assertEqual(len(before_batch),1)
        self.assertEqual(set(before_batch[0]),set(('version','installation_root',
                         'module_path','button_path','git_commit')))
        self.assertEqual(before_batch[0],provenance)
        self.assertEqual(before_batch[0]['version'],VERSION)


class InstallationProvenance(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET224_provenance_')
        self.addCleanup(shutil.rmtree,self.root)
        self.provenance=importlib.import_module('easybim_etransmit.provenance')
        self.module_path=self.write('lib/easybim_etransmit/ui.py','# fixture module\n')
        self.button_path=self.write('EasyBIM.tab/Links.panel/e-transmit.pushbutton/script.py','# fixture button\n')
        self.commit='0123456789abcdef0123456789abcdef01234567'
        # Deployment folders must be inspectable without Git or child processes.
        def denied(*args,**kwargs):self.fail('provenance must read local Git metadata without subprocesses')
        for name in ('Popen','call','check_call','check_output','run'):
            if hasattr(subprocess,name):
                original=getattr(subprocess,name)
                setattr(subprocess,name,denied)
                self.addCleanup(setattr,subprocess,name,original)

    def write(self,relative,content):
        path=os.path.join(self.root,*relative.split('/'))
        parent=os.path.dirname(path)
        if not os.path.isdir(parent):os.makedirs(parent)
        with io.open(path,'w',encoding='utf-8') as out:out.write(content)
        return path

    def collect(self):
        return self.provenance.collect(button_path=self.button_path,module_path=self.module_path)

    def test_zip_installation_reports_runtime_paths_and_version_without_git(self):
        result=self.collect()
        self.assertEqual(set(result),set(('version','installation_root','module_path','button_path','git_commit')))
        self.assertEqual(result['version'],VERSION)
        self.assertEqual(result['installation_root'],self.root)
        self.assertEqual(result['module_path'],self.module_path)
        self.assertEqual(result['button_path'],self.button_path)
        self.assertEqual(result['git_commit'],'')

    def test_default_arguments_identify_the_module_loaded_in_this_installation(self):
        result=self.provenance.collect()
        module=os.path.abspath(self.provenance.__file__)
        installation=os.path.dirname(os.path.dirname(os.path.dirname(module)))
        self.assertEqual(result['module_path'],module)
        self.assertEqual(result['installation_root'],installation)
        self.assertEqual(result['button_path'],os.path.join(installation,
                'EasyBIM.tab','Links.panel','e-transmit.pushbutton','script.py'))
        self.assertEqual(result['version'],VERSION)

    def test_detached_head_reports_exact_commit(self):
        self.write('.git/HEAD',self.commit+'\n')
        self.assertEqual(self.collect()['git_commit'],self.commit)

    def test_loose_branch_ref_precedes_stale_packed_ref(self):
        self.write('.git/HEAD','ref: refs/heads/codex/repath\n')
        self.write('.git/refs/heads/codex/repath',self.commit+'\n')
        self.write('.git/packed-refs','f'*40+' refs/heads/codex/repath\n')
        self.assertEqual(self.collect()['git_commit'],self.commit)

    def test_packed_branch_ref_is_resolved_without_git_executable(self):
        self.write('.git/HEAD','ref: refs/heads/codex/repath\n')
        self.write('.git/packed-refs','# pack-refs with: peeled fully-peeled sorted\n'+
                   'f'*40+' refs/tags/old\n^'+'e'*40+'\n'+
                   self.commit+' refs/heads/codex/repath\n')
        self.assertEqual(self.collect()['git_commit'],self.commit)

    def test_worktree_gitdir_and_commondir_resolve_common_ref(self):
        self.write('.git','gitdir: metadata/worktrees/repath\n')
        self.write('metadata/worktrees/repath/HEAD','ref: refs/heads/codex/repath\n')
        self.write('metadata/worktrees/repath/commondir','../..\n')
        self.write('metadata/refs/heads/codex/repath',self.commit+'\n')
        result=self.collect()
        self.assertEqual(result['git_commit'],self.commit)
        self.assertEqual(result['installation_root'],self.root)

    def test_worktree_absolute_gitdir_resolves_common_packed_ref(self):
        gitdir=os.path.join(self.root,'metadata','worktrees','repath')
        self.write('.git','gitdir: '+gitdir+'\n')
        self.write('metadata/worktrees/repath/HEAD','ref: refs/heads/codex/repath\n')
        self.write('metadata/worktrees/repath/commondir','../..\n')
        self.write('metadata/packed-refs',self.commit+' refs/heads/codex/repath\n')
        self.assertEqual(self.collect()['git_commit'],self.commit)

    def test_malformed_head_and_outside_reference_fail_closed(self):
        for head in ('not-a-commit\n','ref: ../outside\n',
                     'ref: refs/heads/../../outside\n',
                     'ref: refs/heads/missing\n'):
            self.write('.git/HEAD',head)
            self.write('outside',self.commit+'\n')
            self.assertEqual(self.collect()['git_commit'],'')

    def test_oversized_head_and_packed_refs_are_rejected(self):
        self.write('.git/HEAD',self.commit+'\n'+' '*4096)
        self.assertEqual(self.collect()['git_commit'],'')
        self.write('.git/HEAD','ref: refs/heads/codex/repath\n')
        self.write('.git/packed-refs','#'+'x'*(2*1024*1024)+'\n'+
                   self.commit+' refs/heads/codex/repath\n')
        self.assertEqual(self.collect()['git_commit'],'')


if __name__=='__main__':unittest.main(verbosity=2)
