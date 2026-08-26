"""Pure-Python checks for the Families Downgrade state module.

The mapping tables are the part of this tool that a Revit session cannot
easily verify (a wrong group name lands a parameter in Other without a
sound), so they are pinned here: the quirk lists, the ladders, and - when the
Revit API reference assemblies happen to be on this machine - the full
enum-name coverage the tables claim.
"""

import importlib.util
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Family.panel" / "Families Downgrade.pushbutton"
)
STATE_MODULE_PATH = COMMAND_DIR / "families_downgrade_state.py"

# The Revit API NuGet packages, when present, let the coverage checks run
# against the real enum member names; elsewhere those tests skip.
API_PACKAGES = pathlib.Path(
    "/mnt/c/Users/RML/.nuget/packages/revit_all_main_versions_api_x64")
API_2021_XML = API_PACKAGES / "2021.0.0" / "lib" / "net48" / "RevitAPI.xml"
API_2021_DLL = API_PACKAGES / "2021.0.0" / "lib" / "net48" / "RevitAPI.dll"
API_2026_XML = API_PACKAGES / "2026.0.0" / "lib" / "net8.0" / "RevitAPI.xml"


def _load_state_module():
    lib_root = str(REPO_ROOT / "lib")
    if lib_root not in sys.path:
        sys.path.insert(0, lib_root)
    spec = importlib.util.spec_from_file_location(
        "families_downgrade_state", str(STATE_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = _load_state_module()


class FakeOption(object):
    def __init__(self, name):
        self.name = name


class NormalisationTests(unittest.TestCase):
    def test_prefixes_underscores_and_case_are_dropped(self):
        self.assertEqual("identitydata", state.normalize_enum_name("PG_IDENTITY_DATA"))
        self.assertEqual("identitydata", state.normalize_enum_name("IdentityData"))
        self.assertEqual("hvacairflow", state.normalize_enum_name("UT_HVAC_Airflow"))
        self.assertEqual("hvacairflow", state.normalize_enum_name("HVACAirflow"))

    def test_only_one_prefix_is_stripped(self):
        # "UT_" inside a name that does not start with it must survive.
        self.assertEqual("outdoorut", state.normalize_enum_name("PG_OUTDOOR_UT"))


class GroupMappingTests(unittest.TestCase):
    GROUPS_2021 = ["PG_GEOMETRY", "PG_IDENTITY_DATA", "PG_CURTAIN_GRID_1",
                   "PG_PROFILE_2", "PG_TERMINTATION", "PG_TEXT", "INVALID"]

    def test_recorded_legacy_name_wins(self):
        name, note = state.builtin_group_for(
            {"name": "Width", "builtin_group": "PG_TEXT", "group_property": "Geometry"},
            self.GROUPS_2021)
        self.assertEqual(("PG_TEXT", None), (name, note))

    def test_property_names_normalise_onto_legacy_names(self):
        name, note = state.builtin_group_for(
            {"name": "Width", "group_property": "IdentityData"}, self.GROUPS_2021)
        self.assertEqual(("PG_IDENTITY_DATA", None), (name, note))

    def test_the_spelling_quirks_are_in_the_table(self):
        for prop, legacy in (("CurtainGridn1", "PG_CURTAIN_GRID_1"),
                             ("Profilen2", "PG_PROFILE_2"),
                             ("Termination", "PG_TERMINTATION")):
            name, note = state.builtin_group_for(
                {"name": "p", "group_property": prop}, self.GROUPS_2021)
            self.assertEqual(legacy, name, prop)
            self.assertIsNone(note)

    def test_a_group_newer_than_the_host_lands_in_other_with_a_note(self):
        name, note = state.builtin_group_for(
            {"name": "Zone", "group_property": "ElectricalAnalysis",
             "group_type_id": "autodesk.parameter.group:electrical-analysis-1.0.0"},
            self.GROUPS_2021)
        self.assertEqual("INVALID", name)
        self.assertIn("ElectricalAnalysis", note)
        self.assertIn("Zone", note)

    def test_an_empty_group_is_other_without_a_note(self):
        self.assertEqual(("INVALID", None), state.builtin_group_for({"name": "p"}, self.GROUPS_2021))


class ParameterTypeMappingTests(unittest.TestCase):
    TYPES_2021 = ["Text", "URL", "MultilineText", "Integer", "NumberOfPoles",
                  "YesNo", "Material", "Image", "LoadClassification",
                  "FamilyType", "Number", "Length", "Area", "HVACAirflow",
                  "ElectricalApparentPower", "WireSize"]

    def test_recorded_legacy_name_wins(self):
        name, note = state.parameter_type_for(
            {"name": "W", "parameter_type": "Length", "storage_type": "Double"},
            self.TYPES_2021)
        self.assertEqual(("Length", None), (name, note))

    def test_measurable_specs_resolve_through_the_unit_type(self):
        name, note = state.parameter_type_for(
            {"name": "Flow", "spec_type_id": "autodesk.spec.aec.hvac:air-flow-2.0.0",
             "spec_property": "AirFlow", "storage_type": "Double"},
            self.TYPES_2021, unit_type_name="UT_HVAC_Airflow")
        self.assertEqual(("HVACAirflow", None), (name, note))
        name, note = state.parameter_type_for(
            {"name": "VA", "spec_property": "ApparentPower", "storage_type": "Double"},
            self.TYPES_2021, unit_type_name="UT_Electrical_Apparent_Power")
        self.assertEqual("ElectricalApparentPower", name)

    def test_non_measurable_specs_use_the_table(self):
        for prop, legacy in (("String.Text", "Text"), ("String.Url", "URL"),
                             ("String.MultilineText", "MultilineText"),
                             ("Int.Integer", "Integer"), ("Int.NumberOfPoles", "NumberOfPoles"),
                             ("Boolean.YesNo", "YesNo"), ("Reference.Material", "Material"),
                             ("Reference.Image", "Image"),
                             ("Reference.LoadClassification", "LoadClassification"),
                             ("Reference.FamilyType", "FamilyType")):
            name, note = state.parameter_type_for(
                {"name": "p", "spec_property": prop}, self.TYPES_2021)
            self.assertEqual(legacy, name, prop)
            self.assertIsNone(note, prop)

    def test_a_spec_property_that_names_the_type_directly_still_matches(self):
        name, note = state.parameter_type_for(
            {"name": "L", "spec_property": "Length", "storage_type": "Double"}, self.TYPES_2021)
        self.assertEqual(("Length", None), (name, note))

    def test_an_unknown_spec_falls_back_to_the_storage_type_with_a_note(self):
        name, note = state.parameter_type_for(
            {"name": "Spin", "spec_property": "AngularSpeed",
             "spec_type_id": "autodesk.spec.aec:angular-speed-2.0.0",
             "storage_type": "Double"},
            self.TYPES_2021)
        self.assertEqual("Number", name)
        self.assertIn("AngularSpeed", note)
        self.assertIn("unitless", note)

    def test_a_fill_pattern_reference_is_never_matched_by_name(self):
        name, note = state.parameter_type_for(
            {"name": "Hatch", "spec_property": "Reference.FillPattern",
             "storage_type": "ElementId"},
            self.TYPES_2021 + ["FillPattern"])
        self.assertIsNone(name)
        self.assertIn("skipped", note)


class CategoryMappingTests(unittest.TestCase):
    CATEGORIES_2021 = ["OST_MechanicalEquipment", "OST_SpecialityEquipment",
                       "OST_PlumbingFixtures", "OST_GenericModel", "OST_Site",
                       "OST_CommunicationDevices"]

    def test_a_category_the_host_has_is_kept(self):
        self.assertEqual(("OST_MechanicalEquipment", None),
                         state.category_for("OST_MechanicalEquipment", self.CATEGORIES_2021))

    def test_the_2022_categories_land_where_people_used_to_put_them(self):
        for new, old in (("OST_PlumbingEquipment", "OST_PlumbingFixtures"),
                         ("OST_MechanicalControlDevices", "OST_MechanicalEquipment"),
                         ("OST_MedicalEquipment", "OST_SpecialityEquipment"),
                         ("OST_Hardscape", "OST_Site"),
                         ("OST_AudioVisualDevices", "OST_CommunicationDevices")):
            name, note = state.category_for(new, self.CATEGORIES_2021)
            self.assertEqual(old, name, new)
            self.assertIn(new, note)
            self.assertIn(old, note)

    def test_anything_else_unknown_becomes_generic_models(self):
        name, note = state.category_for("OST_SomethingFromTheFuture", self.CATEGORIES_2021)
        self.assertEqual("OST_GenericModel", name)
        self.assertIn("Generic Models", note)

    def test_the_fallback_table_only_names_categories_the_target_would_have(self):
        for target in state.CATEGORY_FALLBACKS.values():
            self.assertIn(target, self.CATEGORIES_2021 + ["OST_MechanicalEquipment"])


class SupportTests(unittest.TestCase):
    def test_model_families_are_supported(self):
        self.assertIsNone(state.family_support_reason(
            "OST_MechanicalEquipment", "Model", "OneLevelBased"))

    def test_annotation_detail_profile_and_view_based_families_are_refused(self):
        self.assertIn("2D", state.family_support_reason("OST_GenericAnnotation", "Annotation", "ViewBased"))
        self.assertIn("DetailComponents", state.family_support_reason("OST_DetailComponents", "Model", "ViewBased"))
        self.assertIn("ProfileFamilies", state.family_support_reason("OST_ProfileFamilies", "Model", "ViewBased"))
        self.assertIn("Adaptive", state.family_support_reason("OST_GenericModel", "Model", "Adaptive"))
        self.assertIn("in-place", state.family_support_reason("OST_Walls", "Model", "Invalid", is_in_place=True))


class SystemTypeTests(unittest.TestCase):
    def test_names_shared_between_the_enums_pass_straight_through(self):
        self.assertEqual(("SupplyAir", None),
                         state.system_type_name_for("SupplyAir", ["SupplyAir", "ReturnAir"]))

    def test_the_electrical_data_quirk(self):
        self.assertEqual(("Data", None),
                         state.system_type_name_for("DataCircuit", ["PowerCircuit", "Data"]))

    def test_unknown_systems_fall_back_with_a_note(self):
        name, note = state.system_type_name_for("Recirculation", ["SupplyAir", "Global", "Fitting"])
        self.assertEqual("Global", name)
        self.assertIn("Recirculation", note)


class GeometryGroupTests(unittest.TestCase):
    def test_solids_with_one_treatment_share_one_group(self):
        records = [
            {"element_id": 1, "material": {"kind": "parameter", "name": "Body Material"},
             "subcategory": "", "visibility": state.visibility_record(),
             "visible": {"kind": "literal", "value": True}},
            {"element_id": 2, "material": {"kind": "parameter", "name": "Body Material"},
             "subcategory": "", "visibility": state.visibility_record(),
             "visible": {"kind": "literal", "value": True}},
            {"element_id": 3, "material": {"kind": "material", "name": "Steel"},
             "subcategory": "Frame", "visibility": state.visibility_record(coarse=False),
             "visible": {"kind": "parameter", "name": "Show Frame"}},
        ]
        groups = state.group_elements(records)
        self.assertEqual(2, len(groups))
        self.assertEqual([1, 2], groups[0]["element_ids"])
        self.assertEqual([3], groups[1]["element_ids"])
        self.assertEqual(1, groups[0]["index"])
        self.assertEqual("Frame", groups[1]["subcategory"])

    def test_the_detail_level_order_starts_where_the_form_is_shown(self):
        self.assertEqual(["Fine", "Medium", "Coarse"],
                         state.detail_levels_to_try(state.visibility_record()))
        self.assertEqual(["Coarse", "Fine", "Medium"],
                         state.detail_levels_to_try(state.visibility_record(fine=False, medium=False)))
        self.assertEqual(["Medium", "Coarse", "Fine"],
                         state.detail_levels_to_try(state.visibility_record(fine=False)))

    def test_geometry_files_are_numbered_per_group_and_carry_the_format(self):
        self.assertEqual("g01.sat", state.geometry_file_name(1))
        self.assertEqual("g12.dwg", state.geometry_file_name(12, state.GEOMETRY_DWG))


class BoundingBoxTests(unittest.TestCase):
    def test_a_twelve_times_smaller_import_is_caught(self):
        recorded = [[0, 0, 0], [12.0, 6.0, 3.0]]
        imported = [[0, 0, 0], [1.0, 0.5, 0.25]]
        self.assertFalse(state.size_matches(recorded, imported))
        self.assertAlmostEqual(1.0 / 12.0, state.size_ratio(recorded, imported))

    def test_a_matching_import_passes_and_its_offset_is_the_centre_delta(self):
        recorded = [[0, 0, 0], [2.0, 2.0, 2.0]]
        imported = [[1, 1, 1], [3.0, 3.0, 3.0]]
        self.assertTrue(state.size_matches(recorded, imported))
        self.assertEqual((-1.0, -1.0, -1.0), state.bbox_offset(recorded, imported))
        self.assertTrue(state.offset_is_significant(state.bbox_offset(recorded, imported)))
        self.assertFalse(state.offset_is_significant((0.0, 1e-6, 0.0)))

    def test_a_degenerate_recorded_box_never_blocks(self):
        self.assertTrue(state.size_matches([[0, 0, 0], [0, 0, 0]], [[0, 0, 0], [5, 5, 5]]))

    def test_the_unit_candidates_start_with_feet(self):
        self.assertEqual("Foot", state.IMPORT_UNIT_CANDIDATES[0])


class ConnectorGeometryTests(unittest.TestCase):
    def test_an_exact_face_scores_zero_and_a_coplanar_one_scores_one(self):
        self.assertEqual(0.0, state.face_match_score(
            (1, 1, 2), (0, 0, 1), (0, 0, 2), (0, 0, 1), True))
        self.assertEqual(1.0, state.face_match_score(
            (9, 9, 2), (0, 0, 1), (0, 0, 2), (0, 0, 1), False))

    def test_other_planes_and_flipped_normals_are_not_candidates(self):
        self.assertIsNone(state.face_match_score((1, 1, 2), (0, 0, 1), (0, 0, 3), (0, 0, 1), True))
        self.assertIsNone(state.face_match_score((1, 1, 2), (0, 0, 1), (0, 0, 2), (0, 0, -1), True))

    def test_the_connector_angle_is_the_signed_rotation_about_the_normal(self):
        import math
        self.assertAlmostEqual(math.pi / 2, state.connector_angle((0, 1, 0), (1, 0, 0), (0, 0, 1)))
        self.assertAlmostEqual(-math.pi / 2, state.connector_angle((0, -1, 0), (1, 0, 0), (0, 0, 1)))
        self.assertAlmostEqual(0.0, state.connector_angle((1, 0, 0), (1, 0, 0), (0, 0, 1)))


class TemplateRankingTests(unittest.TestCase):
    TEMPLATES = [
        "Metric Generic Model.rft", "Metric Generic Model face based.rft",
        "Metric Generic Model wall based.rft", "Metric Door.rft",
        "Metric Door - Curtain Wall.rft", "Metric Mechanical Equipment.rft",
        "Metric Column.rft", "Metric Generic Model line based.rft", "notes.txt",
    ]

    def test_a_category_template_beats_generic_model(self):
        ranked = state.rank_templates(self.TEMPLATES, {
            "category_label": "Mechanical Equipment", "placement_type": "OneLevelBased"})
        self.assertEqual("Metric Mechanical Equipment.rft", ranked[0])
        self.assertEqual("Metric Generic Model.rft", ranked[1])

    def test_hosting_picks_the_hosted_generic_template(self):
        ranked = state.rank_templates(self.TEMPLATES, {
            "category_label": "Lighting Fixtures", "hosting_behavior_label": "Face"})
        self.assertEqual("Metric Generic Model face based.rft", ranked[0])
        ranked = state.rank_templates(self.TEMPLATES, {
            "category_label": "Lighting Fixtures", "hosting_behavior": 1})
        self.assertEqual("Metric Generic Model wall based.rft", ranked[0])

    def test_hosting_outranks_an_unhosted_category_template(self):
        # The category can be reassigned afterwards; the hosting cannot.
        templates = self.TEMPLATES + ["Metric Lighting Fixture.rft",
                                      "Metric Lighting Fixture wall based.rft"]
        ranked = state.rank_templates(templates, {
            "category_label": "Lighting Fixtures", "hosting_behavior_label": "Face"})
        self.assertEqual("Metric Generic Model face based.rft", ranked[0])
        ranked = state.rank_templates(templates, {
            "category_label": "Lighting Fixtures", "hosting_behavior_label": "Wall"})
        self.assertEqual("Metric Lighting Fixture wall based.rft", ranked[0])
        ranked = state.rank_templates(templates, {"category_label": "Lighting Fixtures"})
        self.assertEqual("Metric Lighting Fixture.rft", ranked[0])

    def test_plural_category_labels_find_singular_template_names(self):
        self.assertEqual(["pipe accessories", "pipe accessory"],
                         state.category_label_tokens("Pipe Accessories"))
        self.assertEqual(["doors", "door"], state.category_label_tokens("Doors"))
        self.assertEqual(["casework"], state.category_label_tokens("Casework"))

    def test_line_based_families_get_the_line_based_template(self):
        ranked = state.rank_templates(self.TEMPLATES, {
            "category_label": "Generic Models", "placement_type": "CurveBased"})
        self.assertEqual("Metric Generic Model line based.rft", ranked[0])

    def test_shorter_category_names_win_ties_and_non_templates_are_dropped(self):
        ranked = state.rank_templates(self.TEMPLATES, {"category_label": "Doors",
                                                       "hosting_behavior_label": "Wall"})
        self.assertNotIn("notes.txt", ranked)
        self.assertEqual("Metric Door.rft", ranked[0])


class ManifestAndPackageTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_manifest_round_trips_and_validates(self):
        manifest = state.new_manifest("Fan Coil", {"revit_version": "2026"})
        manifest["parameters"].append({"name": "Width", "storage_type": "Double"})
        state.add_note(manifest, "nested family flattened")
        state.add_note(manifest, "nested family flattened")
        folder = os.path.join(self.root, "Fan Coil.downgrade")
        os.makedirs(folder)
        state.write_manifest(folder, manifest)
        loaded = state.read_manifest(os.path.join(folder, state.MANIFEST_NAME))
        self.assertEqual(manifest, loaded)
        self.assertEqual(["nested family flattened"], loaded["notes"])
        self.assertEqual((True, ""), state.validate_manifest(loaded))

    def test_foreign_and_future_manifests_are_refused_with_a_reason(self):
        self.assertIn("not a Families Downgrade", state.validate_manifest({"format": "x"})[1])
        newer = state.new_manifest("F", {})
        newer["schema_version"] = state.SCHEMA_VERSION + 1
        ok, reason = state.validate_manifest(newer)
        self.assertFalse(ok)
        self.assertIn("newer", reason)
        self.assertFalse(state.validate_manifest([])[0])

    def test_find_packages_lists_usable_and_broken_folders(self):
        good = os.path.join(self.root, "Fan Coil.downgrade")
        os.makedirs(good)
        manifest = state.new_manifest("Fan Coil", {"revit_version": "2026"})
        manifest["family"]["category_label"] = "Mechanical Equipment"
        manifest["types"] = [{"name": "A"}, {"name": "B"}]
        state.write_manifest(good, manifest)
        broken = os.path.join(self.root, "Broken.downgrade")
        os.makedirs(broken)
        with open(os.path.join(broken, state.MANIFEST_NAME), "w") as handle:
            handle.write("{not json")
        os.makedirs(os.path.join(self.root, "Unrelated"))

        packages = state.find_packages(self.root)
        self.assertEqual(["Broken", "Fan Coil"], [p.family_name for p in packages])
        fan_coil = packages[1]
        self.assertTrue(fan_coil.is_usable)
        self.assertTrue(fan_coil.is_selected)
        self.assertEqual("2026", fan_coil.source_version)
        self.assertEqual(2, fan_coil.type_count)
        self.assertEqual("Mechanical Equipment", fan_coil.category_label)
        self.assertFalse(packages[0].is_usable)
        self.assertFalse(packages[0].is_selected)
        self.assertIn("cannot be read", packages[0].reason)

    def test_packages_a_level_further_down_are_found_too(self):
        # People point the picker at the folder above the one the export
        # wrote into just as often as at the folder itself.
        parent = os.path.join(self.root, "Downgrades", "2026 run")
        os.makedirs(parent)
        for name in ("Fan Coil", "Pump"):
            folder = os.path.join(parent, "{}.downgrade".format(name))
            os.makedirs(folder)
            state.write_manifest(folder, state.new_manifest(name, {}))
        os.makedirs(os.path.join(self.root, "Downgrades", "notes"))

        packages = state.find_packages(os.path.join(self.root, "Downgrades"))
        self.assertEqual(["Fan Coil", "Pump"], [p.family_name for p in packages])
        self.assertTrue(all(p.is_usable for p in packages))
        # Three levels down is still too far.
        self.assertEqual([], state.find_packages(self.root))

    def test_a_package_folder_picked_directly_is_found(self):
        folder = os.path.join(self.root, "Only.downgrade")
        os.makedirs(folder)
        state.write_manifest(folder, state.new_manifest("Only", {}))
        self.assertEqual(1, len(state.find_packages(folder)))
        self.assertEqual([], state.find_packages(os.path.join(self.root, "missing")))

    def test_owned_files_are_the_manifest_and_geometry_only(self):
        folder = os.path.join(self.root, "P.downgrade")
        os.makedirs(folder)
        for name in ("manifest.json", "g01.sat", "g02.dwg", "notes.txt", "g1x.sat"):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write("x")
        owned = sorted(os.path.basename(p) for p in state.owned_package_files(folder))
        self.assertEqual(["g01.sat", "g02.dwg", "manifest.json"], owned)


class PlannedNamesTests(unittest.TestCase):
    def test_package_folders_are_sanitised_and_deduplicated(self):
        options = [FakeOption("Duct: Round"), FakeOption("Duct_ Round"), FakeOption("")]
        names = state.planned_package_folder_names(options)
        self.assertEqual(["Duct_ Round.downgrade", "Duct_ Round_2.downgrade",
                          "Family.downgrade"], names)

    def test_split_types_name_one_package_per_type(self):
        names = state.planned_package_folder_names(
            [FakeOption("Elbow")], split_types=True,
            type_names_for=lambda option: ["100 mm", "150 mm"])
        self.assertEqual(["Elbow - 100 mm.downgrade", "Elbow - 150 mm.downgrade"], names)

    def test_rfa_names_follow_the_shared_export_sanitiser(self):
        packages = [state.PackageInfo("a", {"family": {"name": "Fan?"}}),
                    state.PackageInfo("b", {"family": {"name": "Fan_"}})]
        self.assertEqual(["Fan.rfa", "Fan_2.rfa"], state.planned_rfa_filenames(packages))

    def test_the_overwrite_warning_caps_its_list(self):
        text = state.build_overwrite_text(["a"] * 25, 30, noun="package(s)")
        self.assertIn("25 of 30 package(s)", text)
        self.assertIn("Plus 5 more.", text)


class ReportTests(unittest.TestCase):
    def test_summary_and_report_carry_counts_notes_and_per_family_losses(self):
        summary = state.DowngradeSummary(state.MODE_REBUILD)
        summary.written.append(state.DowngradeResult(
            "Fan Coil", "C:/out/Fan Coil.rfa", "rebuilt",
            notes=["2 nested families flattened", "formula on 'Depth' refused"]))
        summary.skipped.append(state.DowngradeResult("Tag", "", "annotation families are 2D"))
        summary.failed.append(state.DowngradeResult("Pump", "", "SaveAs failed: locked"))
        summary.add_note("Template folder: C:/templates")
        summary.report_path = "C:/out/families_downgrade_report.txt"

        text = state.build_summary_text(summary)
        self.assertIn("rebuild completed", text)
        self.assertIn("Families rebuilt: 1", text)
        self.assertIn("Skipped: 1", text)
        self.assertIn("Failed: 1", text)
        self.assertIn("Things not carried (see the report): 2", text)
        self.assertIn("Report: C:/out/families_downgrade_report.txt", text)
        self.assertIn("Template folder", text)
        self.assertIn("Fan Coil -> C:/out/Fan Coil.rfa: rebuilt", text)

        report = state.build_report_text(summary, "2021", "C:/out")
        self.assertIn("Revit 2021", report)
        self.assertIn("not a file conversion", report)
        self.assertIn("    . 2 nested families flattened", report)
        self.assertIn("    . formula on 'Depth' refused", report)
        self.assertIn("Pump: SaveAs failed: locked", report)

    def test_a_downgrade_reports_its_two_halves_as_one_run(self):
        export = state.DowngradeSummary(state.MODE_EXPORT)
        export.skipped.append(state.DowngradeResult("Tag", "", "2D families are not carried"))
        export.add_note("A link refused two families.")
        rebuild = state.DowngradeSummary(state.MODE_REBUILD)
        rebuild.written.append(state.DowngradeResult("Fan Coil", "out\\Fan Coil.rfa", "rebuilt"))
        rebuild.failed.append(state.DowngradeResult("Pump", "", "no family template"))
        rebuild.add_note("A link refused two families.")

        summary = state.combine_downgrade_summaries(export, rebuild)

        self.assertEqual(state.MODE_DOWNGRADE, summary.mode)
        self.assertEqual(["Fan Coil"], [r.family_name for r in summary.written])
        self.assertEqual(["Tag"], [r.family_name for r in summary.skipped])
        self.assertEqual(["Pump"], [r.family_name for r in summary.failed])
        # The same note from both halves is said once.
        self.assertEqual(["A link refused two families."], summary.notes)
        self.assertIn("Families Downgrade downgrade completed.",
                      state.build_summary_text(summary))

    def test_a_family_the_other_revit_never_mentioned_becomes_a_failure(self):
        # Otherwise it is simply absent from every list, which reads as success.
        rebuild = state.DowngradeSummary(state.MODE_REBUILD)
        rebuild.written.append(state.DowngradeResult("Fan Coil", "out\\Fan Coil.rfa", "rebuilt"))
        summary = state.combine_downgrade_summaries(
            None, rebuild, expected_names=["Fan Coil", "Pump"])
        self.assertEqual(["Pump"], [r.family_name for r in summary.failed])
        self.assertIn("did not report this family", summary.failed[0].status)

    def test_a_cancel_in_either_half_cancels_the_run(self):
        export = state.DowngradeSummary(state.MODE_EXPORT)
        export.cancelled = True
        self.assertTrue(state.combine_downgrade_summaries(export, None).cancelled)
        rebuild = state.DowngradeSummary(state.MODE_REBUILD)
        rebuild.cancelled = True
        self.assertTrue(state.combine_downgrade_summaries(None, rebuild).cancelled)

    def test_no_rebuild_half_at_all_still_reports_what_was_packaged(self):
        export = state.DowngradeSummary(state.MODE_EXPORT)
        export.failed.append(state.DowngradeResult("Pump", "", "export failed"))
        summary = state.combine_downgrade_summaries(export, None)
        self.assertEqual([], summary.written)
        self.assertEqual(["Pump"], [r.family_name for r in summary.failed])

    def test_cancelled_runs_say_so(self):
        summary = state.DowngradeSummary(state.MODE_EXPORT)
        summary.cancelled = True
        self.assertIn("export cancelled", state.build_summary_text(summary))
        self.assertIn("The run was cancelled.", state.build_report_text(summary))

    def test_the_report_is_written_next_to_the_outputs(self):
        root = tempfile.mkdtemp(prefix="fdg-")
        try:
            summary = state.DowngradeSummary()
            path = state.write_report(root, summary, "2026")
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(path, summary.report_path)
            with open(path) as handle:
                self.assertIn("Revit 2026", handle.read())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_report_that_cannot_be_written_becomes_a_note(self):
        summary = state.DowngradeSummary()
        path = state.write_report(os.path.join(tempfile.gettempdir(), "no", "such", "dir"), summary)
        self.assertEqual("", path)
        self.assertTrue(any("report file could not be written" in note for note in summary.notes))


@unittest.skipUnless(API_2021_XML.is_file() and API_2021_DLL.is_file() and API_2026_XML.is_file(),
                     "Revit API reference assemblies are not on this machine")
class EnumCoverageTests(unittest.TestCase):
    """The claims the mapping tables make, checked against the real names."""

    @classmethod
    def setUpClass(cls):
        xml_2021 = API_2021_XML.read_text(encoding="utf-8", errors="ignore")
        xml_2026 = API_2026_XML.read_text(encoding="utf-8", errors="ignore")
        dll_2021 = API_2021_DLL.read_bytes()
        cls.parameter_types = set(re.findall(
            r'name="F:Autodesk\.Revit\.DB\.ParameterType\.([A-Za-z0-9_]+)"', xml_2021))
        cls.unit_types = set(re.findall(
            r'name="F:Autodesk\.Revit\.DB\.UnitType\.([A-Za-z0-9_]+)"', xml_2021))
        cls.group_properties = set(re.findall(
            r'name="P:Autodesk\.Revit\.DB\.GroupTypeId\.([A-Za-z0-9]+)"', xml_2026))
        cls.builtin_groups = set(
            m.decode() for m in re.findall(rb"PG_[A-Z0-9_]{2,}", dll_2021))
        cls.categories_2021 = set(
            m.decode() for m in re.findall(rb"OST_[A-Za-z0-9_]+", dll_2021))

    def test_every_measurable_unit_type_maps_onto_a_parameter_type(self):
        # The ten that do not are scales, sheet lengths, site angle, custom
        # and undefined - none of them a family parameter data type.
        unmatched = []
        for unit_type in sorted(self.unit_types):
            name, note = state.parameter_type_for(
                {"name": "p", "storage_type": "Double"}, self.parameter_types,
                unit_type_name=unit_type)
            if note is not None:
                unmatched.append(unit_type)
        allowed = {"UT_AreaForceScale", "UT_Custom", "UT_DecSheetLength",
                   "UT_ForceScale", "UT_LinearForceScale", "UT_LinearMomentScale",
                   "UT_MomentScale", "UT_SheetLength", "UT_SiteAngle", "UT_Undefined"}
        self.assertEqual(sorted(allowed), unmatched)

    def test_every_2026_group_maps_or_is_a_known_newcomer(self):
        newcomers = {"AlternateUnits", "ElectricalAnalysis", "ElectricalEngineering",
                     "LifeSafety", "PrimaryUnits", "StructuralSectionDimensions",
                     "ToposolidSubdivision", "ViewPositioning", "Visualization",
                     "WallCrossSectionDefinition"}
        fell_to_other = []
        for prop in sorted(self.group_properties):
            name, note = state.builtin_group_for({"name": "p", "group_property": prop},
                                                 self.builtin_groups)
            if note is not None:
                fell_to_other.append(prop)
        self.assertEqual(sorted(newcomers), fell_to_other)
        for quirk, legacy in state.GROUP_NAME_QUIRKS.items():
            self.assertIn(quirk, self.group_properties)
            self.assertIn(legacy, self.builtin_groups)

    def test_the_category_fallbacks_are_real_2021_categories(self):
        for new, old in state.CATEGORY_FALLBACKS.items():
            self.assertNotIn(new, self.categories_2021, new)
            self.assertIn(old, self.categories_2021, old)
        self.assertIn(state.GENERIC_MODEL_CATEGORY, self.categories_2021)

    def test_the_non_measurable_table_names_real_parameter_types(self):
        for legacy in state.NON_MEASURABLE_PARAMETER_TYPES.values():
            self.assertIn(legacy, self.parameter_types, legacy)


if __name__ == "__main__":
    unittest.main()
