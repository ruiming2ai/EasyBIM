import importlib.util
import pathlib
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "easybim"
    / "modify_ribbon.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("modify_ribbon", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeItems(list):
    def Add(self, item):
        self.append(item)


class _FakePanels(list):
    def Add(self, panel):
        self.append(panel)


class _FakeRibbon(object):
    def __init__(self, tabs=None):
        self.Tabs = _FakeItems(tabs or [])


class _FakeTab(object):
    def __init__(self, tab_id, title=None, panels=None):
        self.Id = tab_id
        self.Title = title or tab_id
        self.Panels = _FakePanels(panels or [])


class _FakePanelSource(object):
    def __init__(self, panel_id="", title=""):
        self.Id = panel_id
        self.Title = title
        self.AutomationName = title
        self.Items = _FakeItems()


class _FakePanel(object):
    def __init__(self, panel_id="", title=""):
        self.Source = _FakePanelSource(panel_id, title)


class _FakeItem(object):
    def __init__(self, item_id, text):
        self.Id = item_id
        self.Text = text
        self.AutomationName = text


class _FakeAutodeskWindows(object):
    class RibbonPanelSource(_FakePanelSource):
        pass

    class RibbonPanel(_FakePanel):
        pass


def _build_source_panel():
    panel = _FakePanel("CustomCtrl_%EasyBIM%Misc Tools", "Misc Tools")
    panel.Source.Items.extend(
        [
            _FakeItem("slope-command", "Slope"),
            _FakeItem("rotate-command", "3D Rotate\nMultiple"),
            _FakeItem("flip-command", "Flip\nMultiple"),
        ]
    )
    return panel


class ModifyRibbonTests(unittest.TestCase):
    def test_register_modify_shortcuts_adds_existing_easybim_items_once(self):
        module = _load_module()
        ribbon = _FakeRibbon(
            [
                _FakeTab("EasyBIM", panels=[_build_source_panel()]),
                _FakeTab("Modify"),
            ]
        )

        result = module.register_modify_shortcuts(
            ribbon=ribbon,
            autodesk_windows=_FakeAutodeskWindows,
        )
        second_result = module.register_modify_shortcuts(
            ribbon=ribbon,
            autodesk_windows=_FakeAutodeskWindows,
        )

        modify_panel = ribbon.Tabs[1].Panels[0]
        self.assertEqual(modify_panel.Source.Title, "EasyBIM")
        self.assertEqual(
            [item.Id for item in modify_panel.Source.Items],
            ["slope-command", "rotate-command", "flip-command"],
        )
        self.assertEqual(len(ribbon.Tabs[1].Panels), 1)
        self.assertEqual(len(modify_panel.Source.Items), 3)
        self.assertEqual(result["added"], ["Slope", "3D Rotate Multiple", "Flip Multiple"])
        self.assertEqual(second_result["added"], [])
        self.assertEqual(
            second_result["already_present"],
            ["Slope", "3D Rotate Multiple", "Flip Multiple"],
        )

    def test_register_modify_shortcuts_reuses_existing_modify_panel(self):
        module = _load_module()
        existing_panel = _FakePanel(module.MODIFY_PANEL_ID, "EasyBIM")
        ribbon = _FakeRibbon(
            [
                _FakeTab("EasyBIM", panels=[_build_source_panel()]),
                _FakeTab("Modify", panels=[existing_panel]),
            ]
        )

        module.register_modify_shortcuts(
            ribbon=ribbon,
            autodesk_windows=_FakeAutodeskWindows,
        )

        self.assertIs(ribbon.Tabs[1].Panels[0], existing_panel)
        self.assertEqual(len(ribbon.Tabs[1].Panels), 1)
        self.assertEqual(
            [item.Id for item in existing_panel.Source.Items],
            ["slope-command", "rotate-command", "flip-command"],
        )

    def test_register_modify_shortcuts_skips_missing_commands_quietly(self):
        module = _load_module()
        source_panel = _FakePanel("CustomCtrl_%EasyBIM%Misc Tools", "Misc Tools")
        source_panel.Source.Items.Add(_FakeItem("slope-command", "Slope"))
        ribbon = _FakeRibbon(
            [
                _FakeTab("EasyBIM", panels=[source_panel]),
                _FakeTab("Modify"),
            ]
        )

        result = module.register_modify_shortcuts(
            ribbon=ribbon,
            autodesk_windows=_FakeAutodeskWindows,
        )

        self.assertEqual(result["added"], ["Slope"])
        self.assertEqual(result["missing"], ["3D Rotate Multiple", "Flip Multiple"])
        self.assertEqual(
            [item.Id for item in ribbon.Tabs[1].Panels[0].Source.Items],
            ["slope-command"],
        )


if __name__ == "__main__":
    unittest.main()
