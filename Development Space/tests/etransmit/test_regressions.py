"""Regression and API-shaped tests; these do not execute Autodesk Revit."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'lib'))
from easybim_etransmit import files as f, engine as e, revit as r

class Id:
    def __init__(self, value): self.Value = value

class TD:
    def __init__(self): self.IsTransmitted = False; self.writes = []; self.disposed = False
    def GetAllExternalFileReferenceIds(self): return [Id(100)]
    def GetLastSavedReferenceData(self, ident):
        return NS(GetPath=lambda:'relative.rvt',GetAbsolutePath=lambda:'C:\\Shared\\a.rvt',
                  GetLinkedFileStatus=lambda:'Unloaded',ExternalFileReferenceType='RevitLink')
    def GetDesiredReferenceData(self, ident):
        return NS(GetPath=lambda:'desired.rvt',GetAbsolutePath=lambda:'C:\\Consumed\\a.rvt',
                  GetLinkedFileStatus=lambda:'Loaded',ExternalFileReferenceType='RevitLink')
    def SetDesiredReferenceData(self, *args): self.writes.append(args)
    def Dispose(self): self.disposed=True

class APIBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'out'; self.root.mkdir()
        self.stage=str(self.root/'stage.rvt'); self.target=str(self.root/'host.rvt')
        Path(self.stage).write_bytes(b'original')
        self.td=TD(); self.saved=[]
        self.db=NS(ModelPathUtils=NS(ConvertUserVisiblePathToModelPath=lambda x:x,
                                      ConvertModelPathToUserVisiblePath=lambda x:x),
                   TransmissionData=NS(ReadTransmissionData=lambda p:self.td,
                                       WriteTransmissionData=lambda p,td:self.saved.append(p)),
                   PathType=NS(Absolute='Absolute',Relative='Relative'))
        self.backend=r.Backend(self.db,NS(VersionNumber='2026'),str(self.root))
    def test_guard_prevents_outside_write(self):
        with self.assertRaises(ValueError): self.backend.apply_metadata(str(self.root.parent/'source.rvt'),self.target,[])
        self.assertFalse(self.saved)
    def test_guard_rejects_external_repath_destination(self):
        rows=[dict(id='100',target=str(self.root.parent/'source.rvt'),loaded=True)]
        with self.assertRaises(ValueError): self.backend.apply_metadata(self.stage,self.target,rows)
        self.assertFalse(self.saved)
    def test_metadata_repath_retains_unloaded(self):
        rows=[dict(id='100',target=str(self.root/'Consumed'/'a.rvt'),loaded=False)]
        self.backend.apply_metadata(self.stage,self.target,rows)
        self.assertEqual(self.td.writes[0][1:],(os.path.join('Consumed','a.rvt'),'Relative',False))
        self.assertTrue(self.td.IsTransmitted); self.assertTrue(self.td.disposed)
    def test_original_transmitted_desired_source_not_overridden(self):
        self.td.IsTransmitted=True
        row=self.backend.rows('source.rvt')[0]
        self.assertEqual(row['source'],'C:\\Consumed\\a.rvt')
        self.assertTrue(row['loaded'])
    def test_rows_preserve_original_unloaded(self):
        row=self.backend.rows('source.rvt')[0]
        self.assertFalse(row['loaded']); self.assertTrue(self.td.disposed)
    def test_scan_does_not_coerce_cloud_uri_to_absolute_file(self):
        self.backend.prepare_scan(self.stage,[dict(id='100',source='BIM 360://P/Consumed/a.rvt',kind='RevitLink')])
        self.assertFalse(self.td.writes)
    def test_metadata_missing_is_detectable(self):
        self.db.TransmissionData.ReadTransmissionData=lambda p:None
        self.assertIs(self.backend.apply_metadata(self.stage,self.target,[]),False)
    def test_workshared_without_td_warns_about_original_central(self):
        self.db.TransmissionData.ReadTransmissionData=lambda p:None
        self.backend.basic=lambda p:dict(version='2026',workshared=True,central='source.rvt')
        issues=self.backend.finish(self.stage,self.target,[],f.defaults())
        self.assertTrue(any(i['code']=='WORKSHARING_COPY_NOT_DETACHED' for i in issues))
    def test_no_implicit_upgrade_for_image_repath(self):
        self.backend.basic=lambda p:dict(version='2023',workshared=False,central='')
        self.backend.open_copy=lambda *a: self.fail('Must not open for saving without upgrade consent')
        rows=[dict(id='200',target=str(self.root/'page.pdf'),source='page.pdf',special='image',loaded=True)]
        issues=self.backend.finish(self.stage,self.target,rows,f.defaults())
        self.assertTrue(any(i['code']=='UPGRADE_CONSENT_REQUIRED' for i in issues))
        self.assertEqual(Path(self.target).read_bytes(),b'original')

class PipelineRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.src=self.root/'src'; self.src.mkdir()
        self.model=str(self.src/'host.rvt'); Path(self.model).write_bytes(b'original')
        self.pdf=str(self.src/'a.pdf'); Path(self.pdf).write_bytes(b'pdf')
        self.out=str(self.root/'out')
        self.backend=NS(scan=lambda *a:dict(references=[dict(source=self.pdf,id='100')],issues=[]),
                        finish=lambda *a:[])
    def test_failed_repath_not_reported_as_successful(self):
        def fail(stage,target,rows,options):
            rows[0]['repath']='TRANSMISSION_DATA'; Path(target).write_bytes(b'mutated')
            raise RuntimeError('save failed')
        self.backend.finish=fail
        result=e.transmit([self.model],self.out,self.backend)
        self.assertEqual(result['references'][0]['repath'],'ROLLED_BACK')
        self.assertEqual(Path(result['files'][0]['target']).read_bytes(),b'original')
    def test_ambiguous_mapping_not_misclassified_using_previous_source(self):
        self.backend.scan=lambda *a:dict(references=[dict(source='BIM 360://P/x.pdf')],issues=[])
        opts=f.defaults(); opts['mappings']=[('BIM 360://P',str(self.src)),('BIM 360://P',str(self.root))]
        result=e.transmit([self.model],self.out,self.backend,opts)
        self.assertTrue(any(i['code']=='UNRESOLVED_SOURCE' for i in result['issues']))
    def test_failed_inventory_is_explicit_not_successful_scan(self):
        def fail(*args): raise ValueError('unreadable transmission data')
        self.backend.scan=fail
        result=e.transmit([self.model],self.out,self.backend)
        self.assertEqual(result['files'][0]['inventory_status'],'FAILED')
    def test_final_hash_error_still_writes_report(self):
        digest=f.digest
        intercepted=[]
        def unavailable(path,*args):
            normalized=str(path).replace('\\','/')
            if normalized.endswith('/host.rvt') and '/Sources/' in normalized:
                intercepted.append(path)
                raise IOError('verification read denied')
            return digest(path,*args)
        with patch.object(f,'digest',unavailable): result=e.transmit([self.model],self.out,self.backend)
        self.assertTrue(intercepted, 'The verification failure must be exercised on every platform')
        self.assertEqual(result['status'],'NEEDS_REVIEW')
        self.assertTrue(any(i['code']=='FINAL_VERIFICATION_FAILED' for i in result['issues']))
        self.assertTrue((Path(self.out)/'manifest.json').exists())
    def test_collaboration_cache_not_treated_as_source(self):
        for path in [r'C:\Users\u\CollaborationCache\foo.rvt',r'C:\Users\u\PacCache\foo.rvt']:
            self.assertIsNone(f.resolve_source(path))
    def test_cache_destinations_not_allowed_in_mapping(self):
        self.assertIsNone(f.resolve_source('BIM 360://P/x.rvt',mappings=[('BIM 360://P',r'C:\CollaborationCache')]))
    def test_zip_roundtrip(self):
        e.transmit([self.model],self.out,self.backend)
        archive=str(self.root/'package.zip'); e.zip_package(self.out,archive)
        import zipfile
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip()); self.assertIn('manifest.json',z.namelist())
            self.assertFalse(any('_work' in name for name in z.namelist()))
    def test_zip_existing_not_replaced(self):
        archive=self.root/'package.zip'; archive.write_bytes(b'keep')
        with self.assertRaises(IOError): e.zip_package(str(self.src),str(archive))
        self.assertEqual(archive.read_bytes(),b'keep')


class InspectionIsolation(unittest.TestCase):
    setUp=APIBoundary.setUp
    def setup_inspection(self, elements):
        self.backend.elements=lambda doc,name:elements.get(name,[])
        self.db.ExternalFileUtils=NS(GetAllExternalFileReferences=lambda doc:[])
        self.db.ExternalResourceTypes=NS(BuiltInExternalResourceTypes=NS())
        self.db.ExternalResourceUtils=NS(GetAllExternalResourceReferences=lambda doc:[])
        self.db.BuiltInCategory=NS()
    def test_bad_image_does_not_block_point_cloud(self):
        bad=NS(Id=Id(1),Source='Link')
        point=NS(Id=Id(2),GetPath=lambda:'C:\\Survey\\Survey.rcp')
        self.setup_inspection({'ImageType':[bad],'PointCloudType':[point]})
        result=dict(references=[],issues=[])
        self.backend.scan_open(NS(),r'C:\Project\host.rvt',dict(central='',workshared=False),result)
        self.assertTrue(any(x['kind']=='PointCloud' for x in result['references']))
        self.assertTrue(any(x['code']=='IMAGE_REFERENCE_UNRESOLVED' for x in result['issues']))
    def test_scan_keeps_linked_pdf_page_and_resolution(self):
        image=NS(Id=Id(1),Source='Link',Path='C:\\PDF\\Details.pdf',PageNumber=4,Resolution=300,Status='Loaded')
        self.setup_inspection({'ImageType':[image]})
        result=dict(references=[],issues=[])
        self.backend.scan_open(NS(),r'C:\Project\host.rvt',dict(central='',workshared=False),result)
        self.assertEqual(result['references'][0]['page'],4)
        self.assertEqual(result['references'][0]['resolution'],300)
        self.assertFalse(result['issues'])
    def test_imports_are_not_reconstructed(self):
        self.setup_inspection({'ImageType':[NS(Id=Id(1),Source='Import')]})
        result=dict(references=[],issues=[])
        self.backend.scan_open(NS(),r'C:\Project\host.rvt',dict(central='',workshared=False),result)
        self.assertEqual(result['references'],[])
    def test_image_options_do_not_apply_pdf_page_zero_to_png(self):
        self.db.ImageTypeSource=NS(Link='Link')
        self.db.ImageTypeOptions=lambda *args:NS(PageNumber=1)
        opts=self.backend.image_options(dict(target=str(self.root/'a.png'),kind='Image',page=0,resolution=144),False)
        self.assertEqual(opts.PageNumber,1); self.assertEqual(opts.Resolution,144)
    def test_image_options_preserve_pdf_page(self):
        self.db.ImageTypeSource=NS(Link='Link'); self.db.ImageTypeOptions=lambda *args:NS(PageNumber=1)
        opts=self.backend.image_options(dict(target=str(self.root/'a.pdf'),kind='PDF',page=5,resolution=300),True)
        self.assertEqual(opts.PageNumber,5); self.assertEqual(opts.Resolution,300)
    def test_internal_repath_uses_absolute_package_path(self):
        rows=[dict(id='100',target=str(self.root/'Consumed'/'a.rvt'),loaded=False)]
        self.backend.apply_metadata(self.stage,self.target,rows,relative=False)
        self.assertEqual(self.td.writes[0][1:],(rows[0]['target'],'Absolute',False))

if __name__=='__main__': unittest.main()
