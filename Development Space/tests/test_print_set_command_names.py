import pathlib
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PRINT_SET_ROOT = (
    REPO_ROOT
    / "EasyBIM.tab"
    / "Print.panel"
    / "Print Set.pulldown"
)


def _read_text(path):
    return path.read_text(encoding="utf-8")


def _find_xaml_element(root, xaml_name):
    name_key = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
    for element in root.iter():
        if element.attrib.get(name_key) == xaml_name:
            return element
    raise AssertionError("Could not find XAML element: {}".format(xaml_name))


class PrintSetCommandNameTests(unittest.TestCase):
    def test_schedule_button_uses_update_print_set_name(self):
        bundle = _read_text(
            PRINT_SET_ROOT
            / "Update Print Set From Schedule.pushbutton"
            / "bundle.yaml"
        )
        xaml = ET.parse(
            str(
                PRINT_SET_ROOT
                / "Update Print Set From Schedule.pushbutton"
                / "UpdatePrintSetFromSchedule.xaml"
            )
        ).getroot()
        script = _read_text(
            PRINT_SET_ROOT
            / "Update Print Set From Schedule.pushbutton"
            / "script.py"
        )

        self.assertIn("Update Print Set", bundle)
        self.assertIn("From Schedule", bundle)
        self.assertEqual(xaml.attrib["Title"], "Update Print Set From Schedule")
        self.assertIn("Update Print Set From Schedule", script)
        self.assertNotIn("Update Print Set By Schedule", script)
        self.assertNotIn("Load Print Set by Schedule", script)
        self.assertFalse(
            (PRINT_SET_ROOT / "Load Print Set by Schedule.pushbutton").exists()
        )
        self.assertFalse(
            (PRINT_SET_ROOT / "Update Print Set By Schedule.pushbutton").exists()
        )

    def test_excel_button_uses_update_print_set_name(self):
        bundle = _read_text(
            PRINT_SET_ROOT
            / "Update Print Set From Excel.pushbutton"
            / "bundle.yaml"
        )
        xaml = ET.parse(
            str(
                PRINT_SET_ROOT
                / "Update Print Set From Excel.pushbutton"
                / "UpdatePrintSetFromExcel.xaml"
            )
        ).getroot()
        script = _read_text(
            PRINT_SET_ROOT
            / "Update Print Set From Excel.pushbutton"
            / "script.py"
        )

        self.assertIn("Update Print Set", bundle)
        self.assertIn("From Excel", bundle)
        self.assertEqual(xaml.attrib["Title"], "Update Print Set From Excel")
        self.assertIn("Update Print Set From Excel", script)
        self.assertNotIn("Load Print Set From Excel", script)
        self.assertFalse(
            (PRINT_SET_ROOT / "Load Print Set From Excel.pushbutton").exists()
        )

    def test_excel_skip_buttons_align_with_discrepancy_tables(self):
        xaml = ET.parse(
            str(
                PRINT_SET_ROOT
                / "Update Print Set From Excel.pushbutton"
                / "UpdatePrintSetFromExcel.xaml"
            )
        ).getroot()

        number_table = _find_xaml_element(xaml, "number_discrepancies_lb")
        name_table = _find_xaml_element(xaml, "name_discrepancies_lb")
        number_skip = _find_xaml_element(xaml, "skipnumbers_b")
        name_skip = _find_xaml_element(xaml, "skipnames_b")

        self.assertEqual(number_table.attrib.get("Grid.Column"), "0")
        self.assertEqual(name_table.attrib.get("Grid.Column"), "2")
        self.assertEqual(number_skip.attrib.get("Grid.Column"), "0")
        self.assertEqual(name_skip.attrib.get("Grid.Column"), "2")


if __name__ == "__main__":
    unittest.main()
