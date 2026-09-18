import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
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

    def test_matches_records_when_report_document_is_detached_copy(self):
        module = _load_module()
        source_doc = FakeDocument(
            title="24091_Belmont Village Westwood_P_2025",
            path=(
                "C:/Users/rliu/Downloads/SAR-007 Belmont Village Senior Living-Study/"
                "24091_Belmont Village_WWP_AR_2025/"
                "24091_Belmont Village Westwood_P_2025.rvt"
            ),
            elements={2385092: FakeLinkElement(2385092, "24091_Belmont Village_WWP_AR_2025.rvt : 5")},
        )
        detached_doc = FakeDocument(
            title="24091_Belmont Village Westwood_P_2025_detached",
            path="24091_Belmont Village Westwood_P_2025_detached.rvt",
        )
        failure = FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[2385092])

        module.record_coordination_review_failure(source_doc, failure, timestamp=100.0)
        report = module.build_passive_coordination_report(detached_doc, consume=True)
        followup = module.build_passive_coordination_report(detached_doc, consume=False)

        self.assertFalse(report.get("detection_error"))
        self.assertEqual(report["total_matching_warnings"], 1)
        self.assertIn("2385092", report["link_map"])
        self.assertTrue(followup["detection_error"])

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

    def test_empty_report_carries_capture_evidence(self):
        module = _load_module()
        report = module.build_passive_coordination_report(FakeDocument(), consume=False)

        self.assertTrue(report["detection_error"])
        capture = report["capture"]
        self.assertIn("doc_key", capture)
        self.assertIn("registered_now", capture)
        self.assertEqual(capture["stored_doc_keys"], [])

    def test_capture_evidence_reports_the_stored_key_of_another_document(self):
        """The cloud-model case: warnings filed under one identity, the report
        asking under another."""
        module = _load_module()
        other = FakeDocument(title="Other", path="C:/Models/Other.rvt")
        module.record_coordination_review_failure(
            other,
            FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[42]),
            timestamp=100.0,
        )

        evidence = module.capture_evidence(FakeDocument())

        self.assertEqual(evidence["doc_key"], "path:c:/models/coordination_model.rvt")
        self.assertIn("path:c:/models/other.rvt", evidence["stored_doc_keys"])


class FakeApplication(object):
    """Records what a Revit application's FailuresProcessing event receives."""

    def __init__(self):
        self.handlers = []
        self.FailuresProcessing = self

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)
        return self


class RegistrationTests(unittest.TestCase):
    """The listener must survive a detach that happened in another pyRevit
    engine: the report unregisters it, and the next document open re-attaches
    it from a hook whose own module globals still looked attached."""

    def setUp(self):
        self.module = _load_module()
        self.app = FakeApplication()
        self.module._revit_application = lambda uiapp=None: self.app

    def test_register_attaches_exactly_one_handler(self):
        self.assertTrue(self.module.register_passive_detector(source="startup"))
        self.assertEqual(len(self.app.handlers), 1)
        self.assertTrue(self.module.is_registered())

    def test_reregistering_after_an_unregister_reattaches(self):
        self.module.register_passive_detector(source="startup")
        self.module.unregister_passive_detector(source="report")
        self.assertEqual(self.app.handlers, [])
        self.assertFalse(self.module.is_registered())

        self.assertTrue(self.module.register_passive_detector(source="doc-opening"))
        self.assertEqual(len(self.app.handlers), 1)
        self.assertTrue(self.module.is_registered())

    def test_stale_engine_global_cannot_block_reattachment(self):
        """The bug: this engine's global still held a handler that another
        engine had already detached, so registration returned early."""
        self.module.register_passive_detector(source="startup")
        stale = self.module._HANDLER_REF
        # Another engine detaches and clears only the shared mirror.
        self.app.handlers.remove(stale)
        self.module._set_envvar(self.module.HANDLER_ENVVAR, None)

        # Registration must go through rather than trust the stale global, and
        # must leave exactly one live subscription.
        self.assertTrue(self.module.register_passive_detector(source="doc-opening"))
        self.assertEqual(len(self.app.handlers), 1)
        self.assertTrue(self.module.is_registered())
        self.assertEqual(self.module.load_diagnostics()["register_count"], 2)
        del stale

    def test_repeated_registration_never_stacks_subscriptions(self):
        for _ in range(4):
            self.module.register_passive_detector(source="doc-opening")
        self.assertEqual(len(self.app.handlers), 1)

    def test_registration_is_recorded_in_the_diagnostics(self):
        self.module.reset_diagnostics()
        self.module.register_passive_detector(source="doc-opening")
        diagnostics = self.module.load_diagnostics()

        self.assertTrue(diagnostics["registered"])
        self.assertEqual(diagnostics["registered_by"], "doc-opening")
        self.assertEqual(diagnostics["register_count"], 1)
        self.assertIsNotNone(diagnostics["registered_at"])

        self.module.unregister_passive_detector(source="report")
        diagnostics = self.module.load_diagnostics()
        self.assertFalse(diagnostics["registered"])
        self.assertEqual(diagnostics["unregistered_by"], "report")

    def test_missing_event_source_is_recorded_not_raised(self):
        self.module._revit_application = lambda uiapp=None: None
        self.module.reset_diagnostics()

        self.assertFalse(self.module.register_passive_detector(source="startup"))
        self.assertIn("event source not found", self.module.load_diagnostics()["last_error"])


class DiagnosticsTrailTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()
        self.module.reset_diagnostics()

    def test_matched_failure_records_the_document_key(self):
        doc = FakeDocument(elements={42: FakeLinkElement(42, "ARCH.rvt")})
        failure = FakeFailure("LinkInstanceNeedsReconcile", failing_ids=[42])

        self.module._note_failure_seen(doc, failure, True)
        diagnostics = self.module.load_diagnostics()

        self.assertEqual(diagnostics["failures_seen"], 1)
        self.assertEqual(diagnostics["matched"], 1)
        self.assertEqual(diagnostics["doc_keys_seen"], ["path:c:/models/coordination_model.rvt"])
        self.assertEqual(diagnostics["recent_texts"], [])

    def test_unmatched_failure_keeps_a_bounded_sample_of_texts(self):
        doc = FakeDocument()
        for index in range(module_limit := self.module.RECENT_TEXT_LIMIT + 5):
            failure = FakeFailure("Other", description="warning {}".format(index))
            self.module._note_failure_seen(doc, failure, False)

        diagnostics = self.module.load_diagnostics()
        self.assertEqual(diagnostics["failures_seen"], module_limit)
        self.assertEqual(diagnostics["matched"], 0)
        self.assertEqual(len(diagnostics["recent_texts"]), self.module.RECENT_TEXT_LIMIT)
        self.assertEqual(diagnostics["recent_texts"][-1], "warning {}".format(module_limit - 1))

    def test_duplicate_texts_are_not_repeated(self):
        doc = FakeDocument()
        for _ in range(5):
            self.module._note_failure_seen(doc, FakeFailure("Other", description="same"), False)

        self.assertEqual(self.module.load_diagnostics()["recent_texts"], ["same"])

    def test_diagnostics_survive_a_missing_envvar_store(self):
        self.module._set_envvar = lambda name, value: False
        self.module._get_envvar = lambda name, default=None: default

        self.module._note_failure_seen(FakeDocument(), FakeFailure("Other", description="x"), False)
        self.assertEqual(self.module.load_diagnostics()["failures_seen"], 1)

    def test_debug_pipeline_is_removed(self):
        """The per-event JSON debug pipeline must not return; it wrote a file
        on every transaction warning and every Revit dialog."""
        module = _load_module()
        for removed_name in (
            "append_debug_event",
            "write_debug_file",
            "get_debug_state",
            "passive_debug_file_path",
            "_handle_dialog_box_showing",
        ):
            self.assertFalse(hasattr(module, removed_name), removed_name)


if __name__ == "__main__":
    unittest.main()
