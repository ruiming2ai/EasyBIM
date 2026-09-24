# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import os, sys, shutil, tempfile, unittest
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))

from easybim_etransmit import files as f, engine as e, model_payload as p
from easybim_etransmit.revit import Backend

class Obj(object):
    def __init__(self,**kw): self.__dict__.update(kw)

class HostOnlyAdcResolution(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_209_')
        self.addCleanup(shutil.rmtree,self.root)

    def test_native_adc_staging_link_resolves_only_to_exact_owner_sibling(self):
        owner=r'C:\Users\tester\DC\ACCDocs\Firm\Project\Project Files\MEP\Host.rvt'
        stage=r'C:\Temp\EasyBIM_ET_x\stage.rvt'
        temporary=r'C:\Temp\EasyBIM_ET_x\Arch.rvt'
        expected=r'C:\Users\tester\DC\ACCDocs\Firm\Project\Project Files\MEP\Arch.rvt'
        store=p.Store(os.path.join(self.root,'payloads'))
        store.bind_stage(stage,owner)
        old=getattr(f,'connector_file_exists',None)
        f.connector_file_exists=lambda path: f.canonical(path)==f.canonical(expected)
        try:
            self.assertEqual(store.resolve(temporary,owner),expected)
        finally:
            if old is None: delattr(f,'connector_file_exists')
            else: f.connector_file_exists=old

    def test_native_adc_staging_link_is_not_guessed_if_exact_sibling_is_absent(self):
        owner=r'C:\Users\tester\DC\ACCDocs\Firm\Project\Project Files\MEP\Host.rvt'
        stage=r'C:\Temp\EasyBIM_ET_x\stage.rvt'
        temporary=r'C:\Temp\EasyBIM_ET_x\Arch.rvt'
        store=p.Store(os.path.join(self.root,'payloads'))
        store.bind_stage(stage,owner)
        old=getattr(f,'connector_file_exists',None)
        f.connector_file_exists=lambda path: False
        try:
            self.assertEqual(store.resolve(temporary,owner),temporary)
        finally:
            if old is None: delattr(f,'connector_file_exists')
            else: f.connector_file_exists=old


class PortableImageAliases(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.mkdtemp(prefix='ET_209_alias_')
        self.addCleanup(shutil.rmtree,self.root)

    def test_long_image_keeps_mirrored_copy_but_revit_uses_short_alias(self):
        host=os.path.join(self.root,'Host.rvt')
        with open(host,'wb') as out: out.write(b'host')
        deep=self.root
        for i in range(9):
            deep=os.path.join(deep,'Very Long Original Folder %02d'%i)
        os.makedirs(deep)
        image=os.path.join(deep,'Original Drawing With A Long But Unchanged Name.pdf')
        with open(image,'wb') as out: out.write(b'%PDF-test')
        seen=[]
        class B(object):
            def scan(self,source,stage,opts):
                return dict(references=[dict(id='900',element_id='900',source=image,
                                             kind='PDF',special='image',page=1,
                                             resolution=300,loaded=True,td=False)],
                            issues=[],version='2024',opened_in_revit=True)
            def finish(self,stage,target,rows,opts):
                if os.path.basename(target)=='Host.rvt':
                    ref=rows[0];seen.append(dict(ref))
                    if not os.path.isfile(ref['target']):
                        raise AssertionError('portable alias was not created')
                    if f.path_units(ref['target'])>240:
                        raise AssertionError('portable alias is still too long')
                    if os.path.basename(ref['target'])!=os.path.basename(image):
                        raise AssertionError('original filename changed')
                    if not os.path.isfile(ref['mirror_target']):
                        raise AssertionError('original hierarchy mirror was not preserved')
                shutil.copyfile(stage,target)
                return []
        result=e.transmit([host],os.path.join(self.root,'out'),B())
        self.assertEqual(len(seen),1,repr(result['issues']))
        ref=seen[0]
        self.assertNotEqual(f.canonical(ref['target']),f.canonical(ref['mirror_target']))
        self.assertEqual(open(ref['target'],'rb').read(),open(ref['mirror_target'],'rb').read())
        self.assertTrue(ref.get('portable_alias'))

    def test_short_image_does_not_get_duplicate_alias(self):
        helper=getattr(e,'portable_image_alias',None)
        self.assertTrue(callable(helper),'portable image alias helper is missing')
        row=dict(target=os.path.join(self.root,'short.pdf'),element_id='1',special='image')
        self.assertIsNone(helper(self.root,row))


class ExternalImageAndNoise(unittest.TestCase):
    def setUp(self):
        builtin=Obj(RevitLink='RVT',SystemsAnalysisReport='Report',Image='Image')
        self.db=Obj(ModelPathUtils=Obj(ConvertModelPathToUserVisiblePath=lambda p:p,
                                       ConvertUserVisiblePathToModelPath=lambda p:p),
                    ExternalFileUtils=Obj(GetAllExternalFileReferences=lambda d:[]),
                    ExternalResourceTypes=Obj(BuiltInExternalResourceTypes=builtin),
                    ExternalResourceUtils=Obj(GetAllExternalResourceReferences=lambda d:[]),
                    BuiltInCategory=Obj())
        self.b=Backend(self.db,Obj(VersionNumber='2024'),'C:\Out')
        self.b.staging_root='C:\Temp\ET'
        self.b.elements=lambda d,k:[]

    def test_external_only_image_is_repathable_image_with_page_resolution(self):
        ident=Obj(Value=42)
        path=r'N:\Project\Details.pdf'
        resource=Obj(InSessionPath='',ServerId='provider',Version='1/2/2024 1:00 PM',
                     GetReferenceInformation=lambda:{'Path':path,'PathType':'Absolute'})
        image=Obj(IsNestedLink=False,GetExternalResourceReferences=lambda:{'Image':resource},
                  PageNumber=2,Resolution=450,Status='Loaded')
        self.db.ExternalResourceUtils.GetAllExternalResourceReferences=lambda d:[ident]
        result=dict(references=[],issues=[])
        self.b.scan_open(Obj(GetElement=lambda i:image),'C:\Host.rvt',
                         dict(central='',workshared=False),result)
        self.assertEqual(len(result['references']),1)
        row=result['references'][0]
        self.assertEqual(row['special'],'image')
        self.assertEqual(row['page'],2)
        self.assertEqual(row['resolution'],450.0)
        self.assertTrue(row['loaded'])
        self.assertFalse(any(i['code']=='EXTERNAL_RESOURCE_VERSION_UNVERIFIED'
                             for i in result['issues']),
                         'local/network resource versions should not flood cloud warnings')


class OptionalAnalysis(unittest.TestCase):
    def test_missing_system_analysis_directory_is_warning_not_collection_failure(self):
        root=tempfile.mkdtemp(prefix='ET_209_analysis_')
        self.addCleanup(shutil.rmtree,root)
        host=os.path.join(root,'Host.rvt')
        with open(host,'wb') as out: out.write(b'host')
        missing=os.path.join(root,'not-installed-report-folder')
        class B(object):
            def scan(self,*args):
                return dict(references=[dict(id='77',element_id='77',source=missing,
                                             kind='SystemsAnalysisReport',special='external',
                                             category='analysis',td=False)],
                            issues=[],version='2024',opened_in_revit=True)
            def finish(self,stage,target,rows,opts):
                shutil.copyfile(stage,target);return []
        result=e.transmit([host],os.path.join(root,'out'),B())
        codes=[x['code'] for x in result['issues']]
        self.assertIn('ANALYSIS_RESOURCE_UNAVAILABLE',codes)
        self.assertNotIn('COLLECTION_FAILED',codes)
        self.assertFalse(any(x['severity']=='error' for x in result['issues']))


if __name__=='__main__':
    unittest.main(verbosity=2)
