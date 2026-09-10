import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
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
