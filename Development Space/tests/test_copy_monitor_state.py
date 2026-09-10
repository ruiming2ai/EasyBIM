import copy
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
from easybim import independent_placement as p
from easybim import copy_monitor_state as s

def snap(x=0, value=1):
    return dict(frame=p.frame((x,0,0)), params={"Length":value}, type_revision="one", host="")

def record():
    return s.new_record("link-a","doc-a","source","dest",snap(),snap(), mode="original")

class MonitorState(unittest.TestCase):
    def test_distinguishes_source_local_and_conflict(self):
        item = record()
        self.assertEqual("unchanged",s.compare(item,snap(),snap())["status"])
        self.assertEqual("source_changed",s.compare(item,snap(2),snap())["status"])
        self.assertEqual("local_changed",s.compare(item,snap(),snap(2))["status"])
        self.assertEqual("conflict",s.compare(item,snap(2),snap(3))["status"])

    def test_accept_is_not_a_relative_offset(self):
        item = record()
        accepted = s.resolve(item,"accept",snap(2),snap(3))
        self.assertEqual("accepted",s.compare(accepted,snap(2),snap(3))["status"])
        self.assertEqual([4,0,0],s.expected_frame(accepted,snap(4))["origin"])
        self.assertNotEqual("accepted",s.compare(accepted,snap(4),snap(3))["status"])

    def test_keep_relative_follows_rotation_and_position(self):
        item = s.resolve(record(),"relative",snap(10),snap(12))
        source = snap(20)
        source["frame"] = p.frame((20,0,0),(0,1,0),(-1,0,0))
        self.assertEqual([20,2,0],s.expected_frame(item,source)["origin"])
        self.assertEqual([0,1,0],s.expected_frame(item,source)["x"])

    def test_missing_states_do_not_delete_record(self):
        item = record()
        for state in ("link_unloaded","link_missing","link_replaced","source_missing","nested_link_unavailable"):
            report = s.compare(item,None,snap(),availability=state)
            self.assertEqual(state,report["status"])
            self.assertEqual("dest",item["destination_uid"])
        self.assertEqual("destination_missing",s.compare(item,snap(),None)["status"])

    def test_repeated_link_instances_have_distinct_pairing_keys(self):
        a = record()
        b = s.new_record("link-b","doc-a","source","dest-b",snap(),snap(),mode="original")
        self.assertNotEqual(s.mapping_key(a), s.mapping_key(b))

    def test_unknown_schema_or_duplicate_record_ids_are_not_silently_read(self):
        with self.assertRaises(ValueError): s.decode('{"schema_version":99}')
        item = record()
        self.assertEqual(item,s.decode(s.encode(item)))
        with self.assertRaises(ValueError): s.index_records([item,copy.deepcopy(item)])

    def test_parameter_changes_are_listed(self):
        report = s.compare(record(),snap(value=2),snap(value=3))
        self.assertEqual("conflict",report["status"])
        self.assertIn("Length", report["source_changes"])
        self.assertIn("Length", report["destination_changes"])

    def test_unaligned_recipe_keeps_orientation_but_rotates_offset(self):
        item = record()
        item["recipe"] = dict(offset=[2,0,0], align=False, orientation=p.frame())
        source = snap(10)
        source["frame"] = p.frame((10,0,0),(0,1,0),(-1,0,0))
        actual = s.expected_frame(item,source)
        self.assertEqual([10,2,0],actual["origin"])
        self.assertEqual([1,0,0],actual["x"])

    def test_postpone_remains_pending_on_recheck(self):
        item = s.resolve(record(),"postpone",snap(2),snap())
        self.assertEqual("postponed",s.compare(item,snap(2),snap())["status"])

    def test_small_float_noise_does_not_make_local_conflict(self):
        item = record()
        self.assertEqual("source_changed",s.compare(item,snap(2),snap(1e-7,1+1e-12))["status"])

    def test_stop_requires_no_available_source(self):
        item = s.resolve(record(),"stop",None,None)
        self.assertFalse(item["active"])
