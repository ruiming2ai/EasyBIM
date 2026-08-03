import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[2] / "lib" / "easybim" / "view_template_ribbon.py"
STARTUP_PATH = pathlib.Path(__file__).resolve().parents[2] / "startup.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("view_template_ribbon", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeItems(list):
    def Add(self, item):
        self.append(item)


class _FakeRibbon(object):
    def __init__(self, tabs=None):
        self.Tabs = _FakeItems(tabs or [])


class _FakeTab(object):
    def __init__(self, tab_id, title=None, panels=None):
        self.Id = tab_id
        self.Title = title or tab_id
        self.Panels = _FakeItems(panels or [])


class _FakePanelSource(object):
    def __init__(self, title="", items=None):
        self.Title = title
        self.Items = _FakeItems(items or [])


class _FakePanel(object):
    def __init__(self, title="", items=None):
        self.Source = _FakePanelSource(title, items)


class _FakeItem(object):
    def __init__(self, text, image=None, large_image=None, children=None):
        self.Text = text
        self.AutomationName = text
        self.Image = image
        self.LargeImage = large_image
        self.Items = _FakeItems(children or [])


class ViewTemplateRibbonTests(unittest.TestCase):
    def test_target_aliases_match_view_settings_transfer_pushbutton_title(self):
        module = _load_module()

        self.assertEqual(
            module.TARGET_ITEM_ALIASES,
            ("View Settings Transfer", "View Settings\nTransfer"),
        )

    def test_apply_native_view_template_icon_copies_native_images_to_easybim_button(self):
        module = _load_module()
        native_item = _FakeItem("View Templates", image="native-small", large_image="native-large")
        target_item = _FakeItem("View Settings\nTransfer")
        ribbon = _FakeRibbon(
            [
                _FakeTab("View", panels=[_FakePanel("Graphics", [native_item])]),
                _FakeTab("EasyBIM", panels=[_FakePanel("Views", [target_item])]),
            ]
        )

        result = module.apply_native_view_template_icon(ribbon=ribbon)

        self.assertTrue(result["updated"])
        self.assertEqual(target_item.Image, "native-small")
        self.assertEqual(target_item.LargeImage, "native-large")

    def test_apply_native_view_template_icon_reports_missing_source_or_target(self):
        module = _load_module()
        ribbon = _FakeRibbon([_FakeTab("EasyBIM", panels=[_FakePanel("Views", [])])])

        result = module.apply_native_view_template_icon(ribbon=ribbon)

        self.assertFalse(result["updated"])
        self.assertIn("source", result["missing"])

    def test_startup_registers_view_template_icon_helper(self):
        source = STARTUP_PATH.read_text()

        self.assertIn("view_template_ribbon", source)
        self.assertIn("apply_native_view_template_icon", source)


if __name__ == "__main__":
    unittest.main()
