# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, struct, tempfile, shutil, zipfile, unittest, hashlib
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))
from easybim_etransmit import files as f
try:
    from easybim_etransmit import model_payload as p
except ImportError:
    p=None

def compound(version='2024', central='Autodesk Docs://Project/Host.rvt', suffix=''):
    """Synthetic MS-CFB file with one miniature BasicFileInfo stream; no real project data."""
    data=('Format: '+version+suffix+'\r\nWorksharing: Central\r\nCentral Model Path: '+central+'\r\n').encode('utf-16-le')
    chunks=(len(data)+63)//64
    header=bytearray(512);header[:8]=bytearray(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
    struct.pack_into('<HHHHH',header,24,62,3,65534,9,6)
    struct.pack_into('<9I',header,40,0,1,1,0,4096,2,1,0xfffffffe,0)
    struct.pack_into('<109I',header,76,0,*([0xffffffff]*108))
    fat=struct.pack('<128I',0xfffffffd,0xfffffffe,0xfffffffe,0xfffffffe,*([0xffffffff]*124))
    def entry(name,typ,child,start,size):
        d=bytearray(128);n=(name+'\0').encode('utf-16-le');d[:len(n)]=bytearray(n)
        struct.pack_into('<HBBIII',d,64,len(n),typ,1,0xffffffff,0xffffffff,child)
        struct.pack_into('<IQ',d,116,start,size)
        return bytes(d) if sys.version_info[0]>=3 else str(d)
    directory=entry('Root Entry',5,1,3,chunks*64)+entry('BasicFileInfo',2,0xffffffff,0,len(data))+b'\0'*256
    mf=[i+1 for i in range(chunks)];mf[-1]=0xfffffffe;mf += [0xffffffff]*(128-chunks)
    return (bytes(header) if sys.version_info[0]>=3 else str(header))+fat+directory+struct.pack('<128I',*mf)+data+(b'\0'*(512-len(data)))

class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(p,'model payload validation/acquisition is missing')
        self.root=tempfile.mkdtemp(prefix='ET_payload_');self.addCleanup(shutil.rmtree,self.root)
    def put(self,name,data):
        path=os.path.join(self.root,name);parent=os.path.dirname(path)
        if not os.path.isdir(parent):os.makedirs(parent)
        with open(path,'wb') as out:out.write(data)
        return path
    def archive(self,entries,name='Host.rvt'):
        path=os.path.join(self.root,name)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for key,data in entries:z.writestr(key,data)
        return path
    def test_compound_metadata_is_not_confused_with_zip(self):
        src=self.put('Host.rvt',compound());before=f.digest(src)
        info=p.probe(src)
        self.assertEqual(info['container'],'CFB');self.assertEqual(info['version'],'2024')
        self.assertTrue(info['workshared']);self.assertEqual(f.digest(src),before)
    def test_native_metadata_fields_can_be_adjacent(self):
        src=self.put('Host.rvt',compound(suffix='Build: 20250918_1515(x64)'))
        self.assertEqual(p.probe(src)['version'],'2024')
    def test_zip_rvt_is_extracted_and_original_kept(self):
        src=self.archive([('Model/Host.rvt',compound()),('Model/Links/Arch.rvt',compound()),('Model/Details.pdf',b'%PDF')])
        before=f.digest(src);store=p.Store(os.path.join(self.root,'work'))
        target=os.path.join(self.root,'out','Host.rvt');meta=store.copy(src,target)
        self.assertEqual(p.probe(target)['container'],'CFB');self.assertEqual(f.digest(src),before)
        self.assertEqual(meta['acquisition_container'],'ZIP');self.assertEqual(meta['archive_member'],'Model/Host.rvt')
        logical=os.path.join(self.root,'Links','Arch.rvt')
        linked=os.path.join(self.root,'out','Links','Arch.rvt')
        lm=store.copy(logical,linked,owner=src)
        self.assertEqual(lm['archive_member'],'Model/Links/Arch.rvt')
        with open(linked,'rb') as inp: self.assertEqual(inp.read(),compound())
    def test_archive_is_not_a_basename_search_for_other_source_directories(self):
        src=self.archive([('Host.rvt',compound()),('Arch.rvt',compound())])
        store=p.Store(os.path.join(self.root,'work'));store.copy(src,os.path.join(self.root,'out','Host.rvt'))
        with self.assertRaises((IOError,OSError,ValueError)):
            store.copy(os.path.join(self.root,'Elsewhere','Arch.rvt'),os.path.join(self.root,'out','bad.rvt'),owner=src)
    def test_unsafe_archive_entries_are_rejected_before_extraction(self):
        for i,name in enumerate(('../escape.rvt','C:/escape.rvt','/escape.rvt','a/../escape.rvt','a\\..\\escape.rvt','a:stream')):
            src=self.archive([('Host.rvt',compound()),(name,b'bad')],str(i)+'.rvt')
            with self.assertRaises(p.PayloadError):p.prepare(src,os.path.join(self.root,'scratch'+str(i)),expected_name='Host.rvt')
    def test_duplicate_case_paths_rejected(self):
        src=self.archive([('Host.rvt',compound()),('HOST.rvt',compound())])
        with self.assertRaises(p.PayloadError):p.prepare(src,os.path.join(self.root,'work'))
    def test_two_possible_host_members_rejected(self):
        src=self.archive([('A/Host.rvt',compound()),('B/Host.rvt',compound())])
        with self.assertRaises(p.PayloadError):p.prepare(src,os.path.join(self.root,'work'))
    def test_no_matching_host_is_not_guessed(self):
        src=self.archive([('Different.rvt',compound())])
        with self.assertRaises(p.PayloadError):p.prepare(src,os.path.join(self.root,'work'))
    def test_unknown_bytes_fail_before_basicfileinfo(self):
        src=self.put('Host.rvt',b'<html>login</html>')
        with self.assertRaises(p.PayloadError):p.prepare(src,os.path.join(self.root,'work'))
    def test_truncated_compound_rejected(self):
        src=self.put('Host.rvt',compound()[:520])
        with self.assertRaises(p.PayloadError):p.probe(src)
    def test_archive_crc_checked(self):
        src=self.archive([('Host.rvt',compound())])
        with open(src,'r+b') as out:
            out.seek(50);v=out.read(1);out.seek(50);out.write(b'X' if v!=b'X' else b'Y')
        with self.assertRaises(p.PayloadError):p.prepare(src,os.path.join(self.root,'work'))
    def test_cancel_does_not_publish_host(self):
        src=self.put('Host.rvt',compound());store=p.Store(os.path.join(self.root,'work'))
        target=os.path.join(self.root,'out','Host.rvt')
        with self.assertRaises(f.Cancelled):store.copy(src,target,cancelled=lambda:True)
        self.assertFalse(os.path.exists(target))

if __name__=='__main__': unittest.main(verbosity=2)
