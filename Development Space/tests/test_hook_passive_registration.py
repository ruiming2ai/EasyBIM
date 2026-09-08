"""The Coordination Review listener needs a live application handed to it.

A lib module cannot see pyRevit's ``__revit__``, and ``HOST_APP.uiapp`` is
None during application init, so ``register_passive_detector()`` called with
no argument finds no event source and silently attaches nothing - which is
how a session ended up with no listener at all.  The same lesson is already
recorded for the Idling delegate, which every entry point hands ``__revit__``
explicitly.
"""

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _source(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


class PassiveRegistrationCallSiteTests(unittest.TestCase):
    def test_startup_passes_its_application(self):
        text = _source("startup.py")
        self.assertIn("register_passive_detector(_passive_app, source=\"startup\")", text)
        self.assertIn("_passive_app = __revit__", text)

    def test_doc_opening_hook_passes_its_application(self):
        text = _source("hooks/doc-opening.py")
        self.assertIn(
            "register_passive_detector(__revit__, source=\"doc-opening\")", text
        )

    def test_doc_opened_hook_is_a_guarded_safety_net(self):
        text = _source("hooks/doc-opened.py")
        self.assertIn("if not coordination_review_passive.is_registered():", text)
        self.assertIn(
            "register_passive_detector(__revit__, source=\"doc-opened\")", text
        )

    def test_no_call_site_relies_on_the_implicit_application(self):
        for relative_path in ("startup.py", "hooks/doc-opening.py", "hooks/doc-opened.py"):
            text = _source(relative_path)
            self.assertNotIn(
                "register_passive_detector(source=", text, relative_path
            )
            self.assertNotIn("register_passive_detector()", text, relative_path)

    def test_every_registration_names_its_source(self):
        """The source is what makes the diagnostics trail readable."""
        for relative_path in ("startup.py", "hooks/doc-opening.py", "hooks/doc-opened.py"):
            text = _source(relative_path)
            for line in text.splitlines():
                if "register_passive_detector(" in line and "def " not in line:
                    self.assertIn("source=", line, relative_path)


if __name__ == "__main__":
    unittest.main()
