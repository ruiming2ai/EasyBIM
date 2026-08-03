"""Governance for Clash Detection Mode.

Two promises are enforced here because they are easy to break silently and
expensive to discover in Revit:

1. The mode never writes to disk.  The user asked for no local file that
   grows, and the repo already removed a debug log for exactly that reason
   (see ``test_coordination_review_passive.test_debug_pipeline_is_removed``).
   Because nothing is written, Stop has nothing to delete.
2. The event handlers are wired at startup and torn down on Stop / document
   close.  A missing detach leaks a live delegate into the next session.
"""

import ast
import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "lib" / "easybim"
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Clash Detection Mode.pushbutton"
)

CLASH_MODULES = [
    LIB_DIR / "clash_detection_state.py",
    LIB_DIR / "clash_detection_engine.py",
    LIB_DIR / "clash_detection_panel.py",
    LIB_DIR / "clash_detection_alert.py",
    LIB_DIR / "clash_detection_setup.py",
    LIB_DIR / "clash_detection_ribbon.py",
    LIB_DIR / "clash_detection_revit.py",
    LIB_DIR / "wpf_notify.py",
    COMMAND_DIR / "script.py",
]

FORBIDDEN_PATTERNS = (
    (r"(?<![\w.])open\s*\(", "builtin open()"),
    (r"(?<![\w.])file\s*\(", "builtin file()"),
    (r"\btempfile\b", "tempfile"),
    (r"\bshutil\b", "shutil"),
    (r"\bos\.makedirs\b", "os.makedirs"),
    (r"\bos\.mkdir\b", "os.mkdir"),
    (r"\bcodecs\.open\b", "codecs.open"),
    (r"\bjson\.dump\b", "json.dump"),
    (r"\bpickle\b", "pickle"),
    (r"\bFileStream\b", "System.IO.FileStream"),
    (r"\bStreamWriter\b", "System.IO.StreamWriter"),
)


def _source(path):
    return path.read_text(encoding="utf-8")


class NoLocalFilesTests(unittest.TestCase):
    def test_every_module_exists(self):
        for path in CLASH_MODULES:
            self.assertTrue(path.exists(), str(path))

    def test_no_module_writes_to_disk(self):
        offenders = []
        for path in CLASH_MODULES:
            source = _source(path)
            for pattern, label in FORBIDDEN_PATTERNS:
                if re.search(pattern, source):
                    offenders.append("%s uses %s" % (path.name, label))
        self.assertFalse(offenders, offenders)

    def test_no_module_imports_a_filesystem_writer(self):
        for path in CLASH_MODULES:
            for node in ast.walk(ast.parse(_source(path))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    root = name.split(".")[0]
                    self.assertNotIn(
                        root,
                        ("tempfile", "shutil", "pickle", "shelve", "sqlite3"),
                        "%s imports %s" % (path.name, name),
                    )

    def test_state_is_held_in_memory_only(self):
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        self.assertIn("EASYBIM_CLASH_DETECTION_HANDLERS", engine)
        # Session state is a module global, not a serialized blob on disk.
        self.assertIn("_SESSION = None", engine)

    def test_stop_clears_everything(self):
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        stop_body = engine.split("def stop(")[1]
        for expected in ("_unsubscribe()", ".clear()", "close_panel()"):
            self.assertIn(expected, stop_body, expected)


class LifecycleTests(unittest.TestCase):
    def test_startup_registers_the_dockable_panel(self):
        # Revit only accepts RegisterDockablePane during application init.
        startup = _source(REPO_ROOT / "startup.py")
        self.assertIn("from easybim import clash_detection_panel", startup)
        self.assertIn("clash_detection_panel.register()", startup)

    def test_doc_closing_hook_stops_the_mode(self):
        hook = _source(REPO_ROOT / "hooks" / "doc-closing.py")
        self.assertIn("from easybim import clash_detection_engine", hook)
        self.assertIn("clash_detection_engine.handle_doc_closing", hook)

    def test_both_events_are_detached(self):
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        self.assertIn("DocumentChanged -=", engine)
        self.assertIn("Idling -=", engine)

    def test_subscription_detaches_before_attaching(self):
        # A pyRevit reload loses sys.modules but not the live delegate, so
        # without the envvar detach every Start would stack a subscription.
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        subscribe_body = engine.split("def _subscribe(")[1].split("def ")[0]
        self.assertLess(
            subscribe_body.index("_detach_stale()"),
            subscribe_body.index("DocumentChanged +="),
        )

    def test_document_changed_handler_does_no_geometry(self):
        # This runs inside Revit's change notification; anything heavier
        # than set bookkeeping here is a per-edit tax on the whole session.
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        body = engine.split("def _on_document_changed(")[1].split("\ndef ")[0]
        for forbidden in (
            "FilteredElementCollector",
            "get_Geometry",
            "IntersectsElementFilter",
            "IntersectsSolidFilter",
            "_run_pass",
        ):
            self.assertNotIn(forbidden, body, forbidden)

    def test_pause_costs_nothing(self):
        # Paused must be as cheap as off: the change handler bails before it
        # touches the queue at all.
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        body = engine.split("def _on_document_changed(sender, args):")[1].split(
            "\ndef "
        )[0]
        first_statement = [
            line.strip() for line in body.splitlines() if line.strip()
        ][1]
        self.assertIn("_PAUSED", first_statement)

    def test_ribbon_badge_restores_on_stop(self):
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        stop_body = engine.split("def stop(reason=None):")[1].split("\ndef ")[0]
        self.assertIn("_update_badge()", stop_body)
        ribbon = _source(LIB_DIR / "clash_detection_ribbon.py")
        self.assertIn("def _restore", ribbon)
        self.assertIn("_ORIGINAL_IMAGES", ribbon)

    def test_resolution_never_runs_on_an_incomplete_query(self):
        # The bug that lost moved and copied clashes: an empty result from a
        # failed query was read as "the user resolved everything".
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        body = engine.split("def _process_work_item(")[1].split("\ndef ")[0]
        self.assertIn("if all_completed:", body)
        self.assertIn("was_recorded_this_pass", body)

    def test_containers_are_expanded(self):
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        self.assertIn("_container_member_ids", engine)
        self.assertIn("GetMemberIds", engine)

    def test_idling_handler_short_circuits_first(self):
        engine = _source(LIB_DIR / "clash_detection_engine.py")
        body = engine.split("def _on_idling(sender, args):")[1].split("\ndef ")[0]
        first_statement = [
            line.strip() for line in body.splitlines() if line.strip()
        ][0]
        self.assertIn("if not _ACTIVE", first_statement)


if __name__ == "__main__":
    unittest.main()
