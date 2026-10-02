# -*- coding: utf-8 -*-
"""Regression: missing RVTs must not veto delivered PDF/CAD repairs.

Stateful file-backed API fixtures are not Autodesk Revit integration tests.
"""
from __future__ import unicode_literals
import copy
import io
import os
import unittest
import test_225_normal_host_api as api
import test_226_host_copy_delivery as delivery
from test_224_copy_flow import Reference
from easybim_etransmit import engine, files as f, worker


class AvailableReferenceDelivery(unittest.TestCase):
    setUp = delivery.HostCopyDelivery.__dict__['setUp']
    prepare = delivery.HostCopyDelivery.__dict__['prepare']
    setup_case = delivery.HostCopyDelivery.__dict__['setup_case']
    run_case = delivery.HostCopyDelivery.__dict__['run_case']

    def test_missing_rvt_reaches_worker_and_keeps_saved_cad_repair(self):
        case=self.setup_case(missing=True)
        case[2].partial_repath_supported=True
        def repair(app,stage,target,rows,options,**kwargs):
            case[5].append(True)
            self.assertTrue(options.get('allow_partial_repath'))
            td=case[3].read(stage)
            for row in rows:
                if row.get('target'):
                    td.saved[row['id']]=Reference(os.path.relpath(row['target'],os.path.dirname(target)),
                                                  row['kind'],True,'Relative')
                    row.update(repath='TRANSMISSION_DATA',verification='SAVED_REFERENCE_CHECKED')
                else:
                    row.update(repath='NOT_REPATHED_UNAVAILABLE',package_loaded=False,
                               verification='UNAVAILABLE_LINK_UNLOADED_CHECKED')
            td.desired={};td.IsTransmitted=False;case[3].write(target,td)
            return dict(issues=[],host_finalized=True,saved_references_checked=True,
                        normal_open_verified=True,unavailable_links_checked=True,
                        worker_repaired=True,independent_package_central=True)
        worker.run_separate_revit=repair
        result,host=self.run_case(case,f.defaults())
        self.assertEqual(case[5],[True],repr(result['issues']))
        self.assertTrue(host['host_finalized'],repr(result['issues']))
        self.assertFalse(case[3].read(host['target']).IsTransmitted)
        self.assertEqual(host['transmission_status'],'NOT_TRANSMITTED')
        cad=next(row for row in result['references'] if row['kind']=='CADLink')
        self.assertEqual(cad['verification'],'SAVED_REFERENCE_CHECKED')
        self.assertIn('Links',case[3].read(host['target']).saved[cad['id']].GetPath())
        self.assertFalse(any(i['code']=='HOST_COPY_PRESERVED' for i in result['issues']))
        self.assertEqual(result['status'],'NEEDS_REVIEW','Missing RVT is not silently resolved')
        with io.open(os.path.join(case[1],'START_HERE.txt'),encoding='utf-8') as inp:report=inp.read()
        self.assertIn('REFERENCE REPATH RESULTS:',report)
        self.assertIn('SAVED_REFERENCE_CHECKED',report)
        self.assertEqual(dict((p,(f.digest(p),os.stat(p).st_mtime)) for p in case[4]),case[4])

    def test_worker_without_partial_verification_is_not_accepted(self):
        case=self.setup_case(missing=True);case[2].partial_repath_supported=True
        def not_verified(app,stage,target,rows,options,**kwargs):
            case[5].append(True)
            return dict(issues=[],host_finalized=True,saved_references_checked=True)
        worker.run_separate_revit=not_verified
        result,host=self.run_case(case,f.defaults())
        self.assertEqual(case[5],[True])
        self.assertFalse(host.get('host_finalized'))
        self.assertEqual(f.digest(host['target']),f.digest(self.original))

    def test_strict_mode_does_not_opt_into_partial_repairs(self):
        case=self.setup_case(missing=True);case[2].partial_repath_supported=True
        opts=f.defaults();opts['simple_repath']=False
        result,host=self.run_case(case,opts)
        self.assertEqual(case[5],[])
        self.assertFalse(host.get('host_finalized'))


class Link(api.Obj):
    def __init__(self,doc,key):
        self.doc=doc;self.key=key;self.Id=api.Obj(Value=int(key));self.IsNestedLink=False
        self.values=doc.model.get('external',{}).get(key,doc.model['saved'].get(key))
    def GetLinkedFileStatus(self):
        if not self.values[2]:return 'Unloaded'
        return 'InClosedWorkset' if self.doc.closed_worksets else 'Loaded'
    def Unload(self,callback):
        if self.doc.runtime.fail_unload:raise RuntimeError('Injected unload failure')
        self.values[2]=False
        self.doc.runtime.events.append(('unload',self.key,callback))


class Document(api.Document):
    def __init__(self,runtime,path,model,options):
        api.Document.__init__(self,runtime,path,model)
        self.closed_worksets=getattr(options,'worksets','All')=='Closed'
        self.IsDetached=bool(self.IsWorkshared and (model.get('transmitted') or
                                     getattr(options,'DetachFromCentralOption',None)=='Preserve'))
    def GetElement(self,ident):
        key=str(ident.Value)
        if key in self.model.get('external',{}) or key in self.model.get('native_missing',[]):return Link(self,key)
        return api.Document.GetElement(self,ident)
    def SaveAs(self,target,options):
        api.Document.SaveAs(self,target,options);self.IsDetached=False
    def Close(self,save):
        if self.runtime.lose_image_on_close and self.PathName.endswith('Host.rvt'):
            model=self.runtime.read(self.PathName)
            if '3' in model['saved']:
                model['saved']['3']=['old.pdf','Absolute',True]
                self.runtime.write(self.PathName,model)
        return api.Document.Close(self,save)


class Runtime(api.Runtime):
    def __init__(self):
        api.Runtime.__init__(self);self.fail_unload=False;self.lose_image_on_close=False
    def read_td(self,path):
        td=api.Runtime.read_td(self,path)
        if td is None:return None
        original=td.GetLastSavedReferenceData
        def saved(ident):
            ref=original(ident);ref.ExternalFileReferenceType=('RevitLink' if str(ident.Value) in self.read(path).get('native_missing',[]) else 'CADLink')
            return ref
        td.GetLastSavedReferenceData=saved
        return td
    def open(self,path,options):
        self.events.append(('open',path,getattr(options,'DetachFromCentralOption',None),
                            getattr(options,'worksets','All')))
        return Document(self,path,self.read(path),options)


class PartialRepathAPI(unittest.TestCase):
    setUp = api.IndependentHostAPI.__dict__['setUp']

    def configure(self):
        self.runtime=Runtime()
        self.model['saved'].pop('1')
        self.model['saved']['3']=['old.pdf','Absolute',True]
        self.model['external']={'7':['cloud://original/architecture','Absolute',True]}
        self.runtime.write(self.stage,self.model)
        self.db.BasicFileInfo.Extract=self.runtime.basic
        self.db.TransmissionData.ReadTransmissionData=self.runtime.read_td
        self.db.TransmissionData.WriteTransmissionData=self.runtime.write_td
        self.db.TransmissionData.IsDocumentTransmitted=lambda path:self.runtime.read(path)['transmitted']
        self.db.WorksetConfigurationOption.CloseAllWorksets='Closed'
        self.db.WorksetConfiguration=lambda value:api.Obj(mode=value)
        class OpenOptions(api.Obj):
            def SetOpenWorksetsConfiguration(self,value):self.worksets=value.mode
        self.db.OpenOptions=OpenOptions
        self.app.OpenDocumentFile=self.runtime.open
        self.db.RevitLinkType.IsLoaded=lambda doc,ident:(False if doc.closed_worksets else
                   doc.model.get('external',{}).get(str(ident.Value),doc.model['saved'].get(str(ident.Value),[None,None,False]))[2])
        self.backend.elements=lambda doc,name:([Link(doc,k) for k in list(doc.model.get('external',{}))+doc.model.get('native_missing',[])]
                                               if name=='RevitLinkType' else [])
        self.rows=[self.rows[1],dict(id='7:0',element_id='7',kind='RevitLink',td=False,
                   special='external',source='cloud://original/architecture',status='UNRESOLVED',
                   saved_membership_unverified=True,package_loaded=False,loaded=False),
                   dict(id='3',element_id='3',kind='Image',special='image',source='old.pdf',loaded=True,
                        td=False,target=os.path.join(self.root,'Links','PDF','drawing.pdf'))]
        self.options['allow_partial_repath']=True
        def repair(doc,rows):
            for row in rows:
                doc.model['saved'][row['element_id']]=[os.path.relpath(row['target'],os.path.dirname(doc.PathName)),
                                                        'Relative',True]
                row['repath']='API_IMAGE_RELATIVE'
            return [],True
        self.backend.repath_images=repair

    def test_missing_cloud_link_is_unloaded_before_normal_open_and_pdf_cad_persist(self):
        self.configure()
        result=self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertFalse(self.runtime.read(self.target)['external']['7'][2],
                         'Closed-workset state is not a persisted unload')
        self.assertTrue(result.get('normal_open_verified'))
        self.assertTrue(result.get('unavailable_links_checked'))
        opens=[event for event in self.runtime.events if event[0]=='open']
        self.assertEqual(opens[0][3],'Closed','First copied-model open must not load external links')
        self.assertIsNone(opens[-1][2],'Final output must be reopened without Detach')
        self.assertEqual(opens[-1][3],'All')
        self.assertFalse(self.runtime.read(self.target)['transmitted'])
        self.assertEqual(self.runtime.read(self.target)['saved']['3'],
                         [os.path.join('Links','PDF','drawing.pdf'),'Relative',True])
        self.assertEqual(self.runtime.read(self.target)['saved']['2'],
                         [os.path.join('CAD','site.dxf'),'Relative',True])
        self.assertEqual(self.rows[1]['verification'],'UNAVAILABLE_LINK_UNLOADED_CHECKED')
        self.assertNotIn(self.rows[1].get('repath'),('API_LOCAL_LINK_RELATIVE','TRANSMISSION_DATA'))
        self.assertEqual(self.rows[2]['verification'],'SAVED_REFERENCE_CHECKED')

    def test_failed_unload_stops_before_final_host_is_published(self):
        self.configure();self.runtime.fail_unload=True
        with self.assertRaises(RuntimeError):self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertFalse(os.path.exists(self.target))

    def test_live_only_missing_reference_is_not_invented_in_saved_host(self):
        self.configure();self.model['external']={};self.runtime.write(self.stage,self.model)
        result=self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertTrue(result['host_finalized'])
        self.assertEqual(self.rows[1].get('verification'),'NOT_PRESENT_IN_SAVED_HOST')
        self.assertEqual(self.runtime.read(self.target)['external'],{})

    def test_image_that_looks_repaired_in_memory_but_reverts_on_close_is_rejected(self):
        self.configure();self.runtime.lose_image_on_close=True
        self.model['has_td']=False;self.model['saved'].pop('2');self.rows=self.rows[1:]
        self.runtime.write(self.stage,self.model)
        with self.assertRaises(RuntimeError):self.backend.finish(self.stage,self.target,self.rows,self.options)

    def test_missing_native_rvt_is_unloaded_through_scratch_metadata_only(self):
        self.configure();self.model['external']={}
        self.model['native_missing']=['7'];self.model['saved']['7']=['N:/missing/Arch.rvt','Absolute',True]
        self.rows[1].update(td=True,special='native',source='N:/missing/Arch.rvt')
        self.runtime.write(self.stage,self.model)
        result=self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertTrue(result['normal_open_verified'])
        self.assertEqual(self.runtime.read(self.target)['saved']['7'],['N:/missing/Arch.rvt','Absolute',False])
        self.assertEqual(self.rows[1]['verification'],'UNAVAILABLE_LINK_UNLOADED_CHECKED')
        self.assertEqual(self.runtime.events[0][0],'metadata')
        self.assertEqual(self.runtime.events[0][1],self.stage)
        self.assertEqual([e for e in self.runtime.events if e[0]=='unload'],[])

    def test_already_globally_unloaded_cloud_link_does_not_need_another_unload(self):
        self.configure();self.runtime.fail_unload=True
        self.model['external']['7'][2]=False;self.runtime.write(self.stage,self.model)
        result=self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertTrue(result['normal_open_verified'])
        self.assertEqual([e for e in self.runtime.events if e[0]=='unload'],[])

    def test_not_found_is_not_verified_as_persistently_unloaded(self):
        self.configure()
        fake=api.Obj(GetElement=lambda ident:api.Obj(GetLinkedFileStatus=lambda:'NotFound'))
        self.db.RevitLinkType.IsLoaded=lambda doc,ident:False
        with self.assertRaises(RuntimeError):
            self.backend._verify_unavailable_links(fake,self.rows,['7'],mark_verified=True)
        self.assertFalse(self.rows[1].get('verification'))

    def test_final_reopen_still_detached_is_rejected(self):
        self.configure();original=self.backend.open_copy
        def bad_final(path,discard=False,**kwargs):
            doc=original(path,discard,**kwargs)
            if kwargs.get('detach') is False:doc.IsDetached=True
            return doc
        self.backend.open_copy=bad_final
        with self.assertRaises(RuntimeError):self.backend.finish(self.stage,self.target,self.rows,self.options)

    def test_final_reopen_loads_missing_cloud_reference_is_rejected(self):
        self.configure();original=self.backend.open_copy
        def bad_final(path,discard=False,**kwargs):
            doc=original(path,discard,**kwargs)
            if kwargs.get('detach') is False:doc.model['external']['7'][2]=True
            return doc
        self.backend.open_copy=bad_final
        with self.assertRaises(RuntimeError):self.backend.finish(self.stage,self.target,self.rows,self.options)

    def test_nonworkshared_external_missing_link_cannot_bypass_isolation(self):
        self.configure();self.model['workshared']=False;self.runtime.write(self.stage,self.model)
        with self.assertRaises(RuntimeError):self.backend.finish(self.stage,self.target,self.rows,self.options)
        self.assertEqual([e for e in self.runtime.events if e[0]=='open'],[])


if __name__=='__main__':unittest.main(verbosity=2)
