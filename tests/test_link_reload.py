import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "easybim"
    / "link_reload.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("link_reload", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeLink(object):
    def __init__(self, name, linked=True, unloaded=False, reload_ok=True):
        self.name = name
        self.linked = linked
        self.unloaded = unloaded
        self.reload_ok = reload_ok


class LinkReloadTests(unittest.TestCase):
    def test_reload_link_elements_skips_imported_unloaded_and_counts_failures(self):
        module = _load_module()
        links = [
            _FakeLink("revit"),
            _FakeLink("imported", linked=False),
            _FakeLink("unloaded", unloaded=True),
            _FakeLink("failed", reload_ok=False),
        ]
        reloaded_names = []

        summary = module.reload_link_elements(
            doc=object(),
            link_elements=links,
            linked_cad_type_ids=set(),
            is_linked_func=lambda doc, link, linked_ids: link.linked,
            is_unloaded_func=lambda doc, link: link.unloaded,
            reload_func=lambda doc, link: (
                reloaded_names.append(link.name) or link.reload_ok
            ),
        )

        self.assertEqual(reloaded_names, ["revit", "failed"])
        self.assertEqual(summary["reloaded"], 1)
        self.assertEqual(summary["skipped_imported"], 1)
        self.assertEqual(summary["skipped_unloaded"], 1)
        self.assertEqual(summary["skipped_unsupported"], 1)

    def test_ask_and_reload_loaded_links_reloads_only_when_user_confirms(self):
        module = _load_module()
        calls = []

        module.ask_and_reload_loaded_links(
            object(),
            confirm_func=lambda title: False,
            reload_func=lambda doc: calls.append(doc),
        )
        self.assertEqual(calls, [])

        doc = object()
        module.ask_and_reload_loaded_links(
            doc,
            confirm_func=lambda title: True,
            reload_func=lambda doc: calls.append(doc),
        )

        self.assertEqual(calls, [doc])


if __name__ == "__main__":
    unittest.main()
