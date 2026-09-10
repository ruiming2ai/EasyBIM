import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
# Other existing discovery modules install a stub easybim package. Give that
# package a scoped search path while importing this adapter, then restore it.
import easybim
with patch.object(easybim, "__path__", [str(pathlib.Path(__file__).resolve().parents[2] / "lib/easybim")], create=True):
    from easybim import independent_placement_revit as r

class FakeTransaction:
    history = []
    def __init__(self, doc, name): self.name = name
    def Start(self): self.history.append(("start",self.name))
    def Commit(self): self.history.append(("commit",self.name)); return "Committed"
    def Assimilate(self): self.history.append(("assimilate",self.name))
    def RollBack(self): self.history.append(("rollback",self.name))
class FakeDB:
    Transaction = TransactionGroup = FakeTransaction
    class TransactionStatus:
        Committed = "Committed"

class AdapterTests(unittest.TestCase):
    def setUp(self): FakeTransaction.history = []
    def test_failure_rolls_back_creation_and_prepared_family(self):
        def fail(): raise ValueError("coordinate mismatch")
        with patch.object(r, "get_db", return_value=FakeDB):
            result = r.atomic_item(None, "test", lambda: "prepared", fail)
        self.assertFalse(result["ok"])
        self.assertIn("coordinate mismatch", result["error"])
        self.assertEqual(("rollback","test"), FakeTransaction.history[-1])
        self.assertNotIn(("assimilate","test"), FakeTransaction.history)

    def test_post_commit_verification_failure_rolls_back_group(self):
        def verify(value): raise ValueError("host attached at commit")
        with patch.object(r, "get_db", return_value=FakeDB):
            result = r.atomic_item(None, "test", lambda: None, lambda: {"id":1}, verify)
        self.assertFalse(result["ok"])
        self.assertIn(("commit","test changes"), FakeTransaction.history)
        self.assertEqual(("rollback","test"), FakeTransaction.history[-1])

    def test_success_returns_committed_result(self):
        with patch.object(r, "get_db", return_value=FakeDB):
            result = r.atomic_item(None, "test", lambda: None, lambda: {"id":7})
        self.assertTrue(result["ok"])
        self.assertEqual(7, result["value"]["id"])
        self.assertEqual(("assimilate","test"), FakeTransaction.history[-1])

    def test_semantic_material_reference_cannot_match_wrong_material(self):
        source = dict(kind="Material",name="Metal",properties={"color":[1,2,3]})
        candidates = [dict(kind="Material",name="Metal",properties={"color":[3,2,1]})]
        self.assertIsNone(r.match_reference(source,candidates))
        candidates.append(dict(source))
        self.assertEqual(candidates[1], r.match_reference(source,candidates))
        candidates.append(dict(source))
        self.assertIsNone(r.match_reference(source,candidates))

class VerificationTests(unittest.TestCase):
    def parameter(self,value,storage="Double"):
        return dict(key="length",name="Length",spec="length",storage=storage,
                    writable=True,value=value,placement=False)
    def test_rejected_numeric_driver_is_not_successful_placement(self):
        with patch.object(r,"parameter_records",side_effect=[
                [self.parameter(12)], [self.parameter(3)]]):
            with self.assertRaises(ValueError):
                r.verify_parameter_values(object(),object())
    def test_unchanged_numeric_driver_passes_read_back(self):
        with patch.object(r,"parameter_records",side_effect=[
                [self.parameter(12)], [self.parameter(12)]]):
            self.assertEqual([],r.verify_parameter_values(object(),object()))
    def test_half_turn_has_a_valid_axis(self):
        from easybim import independent_placement as p
        axis, angle=r._rotation(p.frame(),p.frame((0,0,0),(-1,0,0),(0,-1,0)))
        self.assertAlmostEqual(3.141592653589793,angle)
        self.assertAlmostEqual(1,abs(axis[2]))

    def test_mirror_modifies_original_without_creating_an_extra_instance(self):
        import types
        from unittest.mock import Mock
        from easybim import independent_placement as p
        original=p.frame(); mirrored=p.frame(x=(-1,0,0))
        transforms=types.SimpleNamespace(MirrorElements=Mock())
        db=types.SimpleNamespace(ElementTransformUtils=transforms,ElementId=int,
            Plane=types.SimpleNamespace(CreateByNormalAndOrigin=lambda n,o: "plane"))
        generic=types.ModuleType("System.Collections.Generic")
        class List:
            def __class_getitem__(cls,item): return list
        generic.List=List
        instance=types.SimpleNamespace(Id=17)
        with patch.dict(sys.modules,{"System.Collections.Generic":generic}), \
             patch.object(r,"get_db",return_value=db),patch.object(r,"xyz",side_effect=lambda x:x), \
             patch.object(r,"mutation_reason",return_value=""),patch.object(r,"independent_reason",return_value=""), \
             patch.object(r,"connected",return_value=False),patch.object(r,"verify_instance"), \
             patch.object(r,"instance_frame",side_effect=[original,mirrored,mirrored]):
            doc=types.SimpleNamespace(Regenerate=Mock())
            r.move_to_frame(doc,instance,mirrored)
        transforms.MirrorElements.assert_called_once_with(doc,[17],"plane",False)
