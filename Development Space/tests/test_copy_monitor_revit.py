import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
LIB=ROOT/"lib/easybim"

def load(name):
    spec=importlib.util.spec_from_file_location("easybim."+name,LIB/(name+".py"))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
package=types.ModuleType("easybim"); package.__path__=[str(LIB)]
with patch.dict(sys.modules, {"easybim":package}):
    from easybim import independent_placement as p
    from easybim import copy_monitor_state as s
    from easybim import independent_placement_revit as adapter
    from easybim import copy_monitor_storage as storage
    r=load("copy_monitor_revit")

def snapshot():
    return dict(frame=p.frame(),params={},type_revision="T",host="",independence="")

class RevitServiceTests(unittest.TestCase):
    def test_one_failed_link_check_does_not_abort_other_records(self):
        records=[s.new_record("link","doc","source","dest"+str(i),snapshot(),snapshot(),
                              mode="position",recipe=dict(relative=p.frame((i,0,0)))) for i in range(2)]
        def inspect(doc,record,cache=None,type_cache=None):
            if record is records[0]: raise ValueError("broken link transform")
            return dict(record=record,status="unchanged")
        with patch.object(adapter,"check_version"),patch.object(r,"inspect_record",side_effect=inspect):
            result=r.check_changes(None,records=records)
        self.assertEqual(2,result["completed"])
        self.assertEqual("scan_error",result["reports"][0]["status"])
        self.assertEqual("unchanged",result["reports"][1]["status"])

    def test_pairing_refuses_duplicate_mapping_before_writing_storage(self):
        doc=types.SimpleNamespace()
        linked=types.SimpleNamespace()
        source=types.SimpleNamespace(UniqueId="source",Document=types.SimpleNamespace(Equals=lambda x:True))
        destination=types.SimpleNamespace(UniqueId="new-dest")
        link=types.SimpleNamespace(UniqueId="link",GetLinkDocument=lambda:linked,GetTotalTransform=lambda:None)
        existing=s.new_record("link","doc","source","old-dest",snapshot(),snapshot(),mode="position")
        with patch.object(adapter,"check_version"),patch.object(adapter,"supported_category",return_value=True), \
             patch.object(storage,"read_records",return_value=[existing]),patch.object(r,"snapshot",return_value=snapshot()), \
             patch.object(r,"document_uid",return_value="doc"),patch.object(adapter,"name",return_value="Link"), \
             patch.object(adapter,"atomic_item") as atomic:
            result=r.monitor_existing(doc,link,source,destination)
        self.assertFalse(result["ok"])
        self.assertIn("already",result["error"])
        atomic.assert_not_called()

    def test_copy_snapshot_failure_preserves_other_successful_items(self):
        from unittest.mock import Mock
        tx=Mock()
        db=types.SimpleNamespace(TransactionGroup=Mock(return_value=tx))
        source=types.SimpleNamespace(UniqueId="source",Document=object())
        link=types.SimpleNamespace(UniqueId="link",GetTotalTransform=lambda:None)
        request=dict(reference=source,source=source,link=link,monitored=True,
                     mode="original",recipe=dict(relative=p.frame()))
        unmonitored=dict(request,monitored=False)
        with patch.object(adapter,"check_version"),patch.object(adapter,"get_db",return_value=db), \
             patch.object(r,"snapshot",side_effect=ValueError("snapshot failed")), \
             patch.object(adapter,"atomic_item",return_value=dict(ok=True,value={})) as atomic:
            result=r.copy_requests(None,[request,unmonitored],existing_records=[])
        self.assertFalse(result[0]["ok"])
        self.assertTrue(result[1]["ok"])
        atomic.assert_called_once()
        tx.RollBack.assert_not_called()
        tx.Assimilate.assert_called_once()

    def test_edited_independent_definition_is_not_reused(self):
        record=s.new_record("link","doc","source","dest",snapshot(),snapshot())
        element=types.SimpleNamespace(Symbol=object())
        doc=types.SimpleNamespace(GetElement=lambda uid:element)
        with patch.object(adapter,"family_revision",return_value="locally-edited"), \
             patch.object(adapter,"independent_reason",return_value=""):
            self.assertEqual({},r.reusable_symbols(doc,[record]))
        with patch.object(adapter,"family_revision",return_value="T"), \
             patch.object(adapter,"independent_reason",return_value=""):
            self.assertIs(element.Symbol,r.reusable_symbols(doc,[record])["T"])
