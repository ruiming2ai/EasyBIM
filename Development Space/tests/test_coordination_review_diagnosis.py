import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "lib"
    / "easybim"
    / "coordination_review_diagnosis.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("coordination_review_diagnosis", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evidence(**overrides):
    """A model where everything worked: listener attached, links monitored."""
    base = {
        "registered": True,
        "registered_now": True,
        "registered_at": 100.0,
        "registered_by": "doc-opening",
        "register_count": 1,
        "unregistered_at": None,
        "unregistered_by": "",
        "events_seen": 3,
        "failures_seen": 7,
        "matched": 0,
        "recent_texts": ["Room is not in a properly enclosed region"],
        "doc_key": "title:tower.rvt",
        "doc_aliases": ["title:tower.rvt", "model:tower"],
        "stored_doc_keys": [],
        "link_count": 4,
        "monitoring_count": 12,
        "warning_list_matches": 0,
    }
    base.update(overrides)
    return base


class NothingToReviewTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_model_without_links(self):
        verdict = self.module.diagnose(_evidence(link_count=0, monitoring_count=0))
        self.assertEqual(verdict["code"], self.module.VERDICT_NO_LINKS)
        self.assertTrue(verdict["benign"])
        self.assertIn("no Revit links", verdict["headline"])

    def test_links_but_no_copy_monitor(self):
        verdict = self.module.diagnose(_evidence(monitoring_count=0))
        self.assertEqual(verdict["code"], self.module.VERDICT_NOT_APPLICABLE)
        self.assertTrue(verdict["benign"])
        self.assertIn("Copy/Monitor", verdict["headline"])

    def test_monitoring_present_and_listener_saw_traffic_means_clear(self):
        verdict = self.module.diagnose(_evidence())
        self.assertEqual(verdict["code"], self.module.VERDICT_CLEAR)
        self.assertTrue(verdict["benign"])
        self.assertIn("No link reported changes", verdict["headline"])
        self.assertIn("7 warning messages", verdict["details"][0])

    def test_unknown_counts_do_not_claim_not_applicable(self):
        verdict = self.module.diagnose(_evidence(link_count=None, monitoring_count=None))
        self.assertEqual(verdict["code"], self.module.VERDICT_CLEAR)


class ListenerStateTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_never_attached(self):
        verdict = self.module.diagnose(
            _evidence(register_count=0, registered=False, registered_now=False, registered_at=None)
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_LISTENER_OFF)
        self.assertFalse(verdict["benign"])
        self.assertIn("not attached", verdict["headline"])

    def test_detached_after_the_last_attach_is_off(self):
        """The old bug: the report detached the listener in one engine and the
        hook's own engine still believed it was attached."""
        verdict = self.module.diagnose(
            _evidence(
                registered=False,
                registered_now=False,
                registered_at=100.0,
                unregistered_at=140.0,
            )
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_LISTENER_OFF)

    def test_reattached_after_a_detach_is_covered(self):
        verdict = self.module.diagnose(
            _evidence(
                registered=False,
                registered_now=False,
                registered_at=200.0,
                unregistered_at=140.0,
                register_count=2,
            )
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_CLEAR)

    def test_registered_now_wins_over_timestamps(self):
        verdict = self.module.diagnose(
            _evidence(registered=False, registered_now=True, unregistered_at=999.0)
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_CLEAR)

    def test_listener_off_outranks_a_document_mismatch(self):
        verdict = self.module.diagnose(
            _evidence(
                register_count=0,
                registered=False,
                registered_now=False,
                stored_doc_keys=["title:other.rvt"],
            )
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_LISTENER_OFF)


class DocumentMismatchTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_records_under_another_key(self):
        verdict = self.module.diagnose(
            _evidence(stored_doc_keys=["title:(unknown)", "path:c:/x/tower.rvt"])
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_DOC_MISMATCH)
        self.assertFalse(verdict["benign"])
        self.assertIn("title:tower.rvt", verdict["details"][0])
        self.assertIn("title:(unknown)", verdict["details"][1])

    def test_own_aliases_are_not_a_mismatch(self):
        verdict = self.module.diagnose(
            _evidence(stored_doc_keys=["title:tower.rvt", "model:tower"])
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_CLEAR)

    def test_mismatch_list_is_bounded(self):
        verdict = self.module.diagnose(
            _evidence(stored_doc_keys=["k{}".format(i) for i in range(9)])
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_DOC_MISMATCH)
        self.assertEqual(verdict["details"][1].count(","), 4)


class WarningListTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_warning_list_hit_outranks_everything(self):
        verdict = self.module.diagnose(
            _evidence(
                warning_list_matches=2,
                register_count=0,
                registered=False,
                registered_now=False,
                monitoring_count=0,
                link_count=0,
            )
        )
        self.assertEqual(verdict["code"], self.module.VERDICT_WARNING_LIST)
        self.assertFalse(verdict["benign"])
        self.assertIn("2 Coordination Review warnings", verdict["headline"])

    def test_single_match_is_singular(self):
        verdict = self.module.diagnose(_evidence(warning_list_matches=1))
        self.assertIn("1 Coordination Review warning.", verdict["headline"])


class NoTrafficTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_attached_but_revit_raised_nothing(self):
        verdict = self.module.diagnose(_evidence(events_seen=0, failures_seen=0))
        self.assertEqual(verdict["code"], self.module.VERDICT_UNKNOWN)
        self.assertFalse(verdict["benign"])
        self.assertIn("Reload a link", verdict["action"])


class SummaryTextTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_module()

    def test_headline_details_and_action_are_joined(self):
        text = self.module.summary_text(self.module.diagnose(_evidence(monitoring_count=0)))
        self.assertTrue(text.startswith("Nothing in this model uses Copy/Monitor."))
        self.assertIn("Coordination Review only reports changes", text)

    def test_empty_verdict_is_safe(self):
        self.assertEqual(self.module.summary_text(None), "")

    def test_empty_evidence_never_raises(self):
        verdict = self.module.diagnose({})
        self.assertEqual(verdict["code"], self.module.VERDICT_LISTENER_OFF)
        self.assertTrue(self.module.summary_text(verdict))


if __name__ == "__main__":
    unittest.main()
