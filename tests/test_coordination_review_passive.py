import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "rltools"
    / "coordination_review_passive.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coordination_review_passive", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeElementId(object):
    def __init__(self, value):
        self.IntegerValue = value
        self.Value = value

    def __int__(self):
        return int(self.IntegerValue)

    def __str__(self):
        return str(self.IntegerValue)


class FakeLinkElement(object):
    def __init__(self, element_id, name):
        self.Id = FakeElementId(element_id)
        self.Name = name

    def GetLinkDocument(self):
        return object()


class FakeDocument(object):
    def __init__(self, title="Coordination_Model", path="C:/Models/Coordination_Model.rvt", elements=None):
        self.Title = title
        self.PathName = path
        self._elements = dict(elements or {})

    def GetElement(self, element_id):
        return self._elements.get(int(element_id))


class FakeFailure(object):
    def __init__(self, failure_id, failing_ids=None, description=""):
        self._failure_id = failure_id
        self._failing_ids = list(failing_ids or [])
        self._description = description

    def GetFailureDefinitionId(self):
        return self._failure_id

    def GetFailingElementIds(self):
        return [FakeElementId(value) for value in self._failing_ids]

    def GetDescriptionText(self):
        return self._description


class PassiveCoordinationReviewTests(unittest.TestCase):
    def test_detects_coordination_review_failure_and_ignores_unrelated_ids(self):
        module = _load_module()

        self.assertTrue(
            module.is_coordination_review_failure(
                FakeFailure("Autodesk.Revit.DB.BuiltInFailures.LinkFailures.LinkInstanceNeedsReconcile")
            )
        )
        self.assertFalse(module.is_coordination_review_failure(FakeFailure("RoomNotEnclosed")))

    def test_records_link_instance_id_and_name_from_failing_elements(self):
        module = _load_module()
        doc = FakeDocument(elements={42: FakeLinkElement(42, "ARCH_Model.rvt")})
        failure = FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[42])

        records = module.record_coordination_review_failure(doc, failure, timestamp=100.0)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["link_key"], "42")
        self.assertEqual(records[0]["link_name"], "ARCH_Model.rvt")
        self.assertEqual(records[0]["element_id"], 42)
        self.assertEqual(records[0]["status"], "needs_coordination_review")

    def test_falls_back_to_generic_problem_when_link_mapping_fails(self):
        module = _load_module()
        doc = FakeDocument()
        failure = FakeFailure(
            "LinkInstanceNeedsReconcile",
            description="Instance of linked .rvt file needs Coordination Review",
        )

        module.record_coordination_review_failure(doc, failure, timestamp=100.0)
        report = module.build_passive_coordination_report(doc, consume=False)

        self.assertFalse(report.get("detection_error"))
        self.assertEqual(report["link_map"][module.GENERIC_LINK_KEY]["name"], "Linked model needs Coordination Review")
        self.assertIn("Needs Coordination Review", report["grouped"][module.GENERIC_LINK_KEY])

    def test_dedupes_repeated_warnings_for_same_document_link(self):
        module = _load_module()
        doc = FakeDocument(elements={42: FakeLinkElement(42, "ARCH_Model.rvt")})
        failure = FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[42])

        module.record_coordination_review_failure(doc, failure, timestamp=100.0)
        records = module.record_coordination_review_failure(doc, failure, timestamp=101.0)

        self.assertEqual(len(records), 1)
        report = module.build_passive_coordination_report(doc, consume=False)
        self.assertEqual(report["total_matching_warnings"], 1)
        self.assertEqual(report["link_totals"]["42"], 1)

    def test_builds_problem_link_report_compatible_with_view_model(self):
        module = _load_module()
        doc = FakeDocument(elements={42: FakeLinkElement(42, "ARCH_Model.rvt")})
        failure = FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[42])

        module.record_coordination_review_failure(doc, failure, timestamp=100.0)
        report = module.build_passive_coordination_report(doc, consume=False)

        self.assertEqual(report["source"], "passive_coordination_review_warning")
        self.assertEqual(report["total_matching_warnings"], 1)
        self.assertEqual(report["total_link_assignments"], 1)
        self.assertEqual(report["grouped"]["42"]["Needs Coordination Review"]["count"], 1)
        self.assertEqual(report["grouped"]["42"]["Needs Coordination Review"]["instance_ids"], set([42]))

    def test_builds_detection_error_report_when_no_records_exist(self):
        module = _load_module()

        report = module.build_passive_coordination_report(FakeDocument(), consume=False)

        self.assertTrue(report["detection_error"])
        self.assertEqual(report["source"], "passive_coordination_review_warning")
        self.assertEqual(report["total_matching_warnings"], 0)
        self.assertEqual(report["grouped"], {})

    def test_build_report_consumes_records_when_requested(self):
        module = _load_module()
        doc = FakeDocument(elements={42: FakeLinkElement(42, "ARCH_Model.rvt")})
        failure = FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[42])
        module.record_coordination_review_failure(doc, failure, timestamp=100.0)

        report = module.build_passive_coordination_report(doc, consume=True)
        followup = module.build_passive_coordination_report(doc, consume=False)

        self.assertFalse(report.get("detection_error"))
        self.assertTrue(followup["detection_error"])


if __name__ == "__main__":
    unittest.main()
