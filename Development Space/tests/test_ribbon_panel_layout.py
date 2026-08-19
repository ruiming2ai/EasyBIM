"""Where each command sits on the EasyBIM tab.

The per-command ``*_command_names`` tests each check their own bundle from a
hard-coded panel path, so a button moved between panels only shows up there as
a path edit.  These tests hold the shape of the tab itself: which panels exist,
what lives on them, and - just as important - what no longer does.
"""

import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TAB_DIR = REPO_ROOT / "EasyBIM.tab"
FAMILY_PANEL = TAB_DIR / "Family.panel"
MISC_PANEL = TAB_DIR / "Misc Tools.panel"
GENERAL_PANEL = TAB_DIR / "General.panel"

FAMILY_BUTTONS = ("Family Types", "Families Downgrade", "Families Transfer")


def _bundle_names(panel_dir, suffix):
    return sorted(child.name for child in panel_dir.iterdir()
                  if child.is_dir() and child.name.endswith(suffix))


class FamilyPanelTests(unittest.TestCase):
    def test_the_three_family_commands_share_one_panel(self):
        self.assertTrue(FAMILY_PANEL.is_dir())
        for title in FAMILY_BUTTONS:
            self.assertTrue((FAMILY_PANEL / (title + ".pushbutton") / "script.py").is_file(), title)

    def test_the_panel_holds_nothing_but_those_three(self):
        self.assertEqual(_bundle_names(FAMILY_PANEL, ".pushbutton"),
                         sorted(title + ".pushbutton" for title in FAMILY_BUTTONS))

    def test_the_layout_pins_the_order_instead_of_leaving_it_alphabetical(self):
        # Alphabetically the two "Families" buttons would jump ahead of
        # "Family Types", which is the one most reached for.
        layout = (FAMILY_PANEL / "bundle.yaml").read_text(encoding="utf-8")
        self.assertTrue(layout.startswith("layout:"), layout)
        listed = [line.strip("- ").strip() for line in layout.splitlines()[1:] if line.strip()]
        self.assertEqual(listed, list(FAMILY_BUTTONS))

    def test_the_family_commands_left_misc_tools_behind(self):
        for title in FAMILY_BUTTONS:
            self.assertFalse((MISC_PANEL / (title + ".pushbutton")).exists(), title)


class GridOffsetTests(unittest.TestCase):
    def test_the_one_button_grid_panel_is_gone(self):
        self.assertFalse((TAB_DIR / "Grid.panel").exists())

    def test_grid_offset_now_sits_in_misc_tools(self):
        self.assertTrue((MISC_PANEL / "Grid Offset.pushbutton" / "script.py").is_file())


class GeneralPanelTests(unittest.TestCase):
    def test_my_ribbon_sits_on_the_general_panel(self):
        self.assertTrue((GENERAL_PANEL / "My Ribbon.pushbutton" / "script.py").is_file())

    def test_the_panel_it_briefly_had_to_itself_is_gone(self):
        self.assertFalse((TAB_DIR / "My Ribbon.panel").exists())

    def test_my_ribbon_left_misc_tools_behind(self):
        self.assertFalse((MISC_PANEL / "My Ribbon.pushbutton").exists())


class TabTests(unittest.TestCase):
    def test_every_panel_folder_holds_at_least_one_command(self):
        for panel in sorted(TAB_DIR.iterdir()):
            if not (panel.is_dir() and panel.name.endswith(".panel")):
                continue
            bundles = [child for child in panel.iterdir()
                       if child.is_dir() and child.suffix in (".pushbutton", ".pulldown",
                                                              ".splitbutton", ".stack")]
            self.assertTrue(bundles, panel.name)


if __name__ == "__main__":
    unittest.main()
