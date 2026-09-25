# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os, sys, unittest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from easybim_etransmit.session import doc_version

class DocumentVersionSignature(unittest.TestCase):
    def test_document_version_static_method_receives_document(self):
        calls=[]
        class Version(object):
            VersionGUID='ABCD'
            NumberOfSaves=5
            def Dispose(self): calls.append('disposed')
        class Document(object):
            @staticmethod
            def GetDocumentVersion(document):
                calls.append(document)
                return Version()
        doc=Document()
        self.assertEqual(doc_version(doc),dict(guid='abcd',saves=5))
        self.assertEqual(calls,[doc,'disposed'])

if __name__=='__main__': unittest.main(verbosity=2)
