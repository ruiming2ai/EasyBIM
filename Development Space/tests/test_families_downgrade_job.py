"""Pure-Python checks for the Families Downgrade bridge contract.

Everything the two Revits say to each other goes through the job and result
files pinned here. Neither side can be watched while it runs - the older Revit
starts, works and closes on its own - so the file format, the version floor
and the parsing of the CLI's listing are the only things a test can hold, and
they are the things that decide whether a run produces a file or a shrug.
"""

import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = (
    REPO_ROOT / "EasyBIM.tab" / "Family.panel" / "Families Downgrade.pushbutton"
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_modules():
    for extra in (str(REPO_ROOT / "lib"), str(COMMAND_DIR)):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    state = _load("families_downgrade_state", COMMAND_DIR / "families_downgrade_state.py")
    job = _load("families_downgrade_job", COMMAND_DIR / "families_downgrade_job.py")
    return state, job


state, job = _load_modules()


class VersionNumberTests(unittest.TestCase):
    def test_a_release_year_is_found_wherever_it_sits(self):
        self.assertEqual(2022, job.version_number("2022"))
        self.assertEqual(2022, job.version_number("Revit 2022"))
        self.assertEqual(2026, job.version_number(u"Autodesk Revit 2026.1"))

    def test_anything_without_a_year_is_zero(self):
        self.assertEqual(0, job.version_number(""))
        self.assertEqual(0, job.version_number(None))
        self.assertEqual(0, job.version_number("Revit LT"))


class JobFileTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-job-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_every_file_the_two_revits_share_comes_from_one_run_folder(self):
        paths = job.run_paths(self.root)
        self.assertEqual(os.path.join(self.root, job.PACKAGES_DIRNAME), paths["package_folder"])
        self.assertEqual(os.path.join(self.root, job.RESULT_FILENAME), paths["result_path"])
        self.assertEqual(os.path.join(self.root, job.CANCEL_FILENAME), paths["cancel_path"])

    def test_a_job_round_trips_and_validates(self):
        paths = job.run_paths(self.root)
        built = job.build_job(paths, os.path.join(self.root, "out"), "2022",
                              script_dir="C:\\ext", lib_dir="C:\\lib",
                              package_folders=["A.downgrade"], source_version="2025")
        self.assertEqual((True, ""), job.validate_job(built))
        self.assertEqual(paths["result_path"], built["result_path"])
        self.assertEqual(["A.downgrade"], built["package_folders"])
        written = job.write_job(paths["job_path"], built)
        self.assertEqual(built, job.read_job(written))

    def test_foreign_future_and_incomplete_jobs_are_refused_with_a_reason(self):
        paths = job.run_paths(self.root)
        self.assertIn("not a Families Downgrade job", job.validate_job({"format": "x"})[1])
        self.assertFalse(job.validate_job([])[0])
        newer = job.build_job(paths, "out", "2022")
        newer["schema_version"] = job.JOB_SCHEMA_VERSION + 1
        self.assertIn("newer", job.validate_job(newer)[1])
        self.assertIn("no output folder", job.validate_job(job.build_job(paths, "", "2022"))[1])
        self.assertIn("no package folder",
                      job.validate_job(job.build_job({}, "out", "2022"))[1])

    def test_a_picked_template_travels_to_the_other_revit(self):
        # The target Revit is the one that has to open it, so the path is
        # carried rather than looked up again on the other side.
        paths = job.run_paths(self.root)
        built = job.build_job(paths, "out", "2022",
                              template_path="C:\\rft\\Metric Generic Model.rft")
        self.assertEqual("C:\\rft\\Metric Generic Model.rft", built["template_path"])
        self.assertEqual((True, ""), job.validate_job(built))

    def test_a_fixed_path_job_is_only_used_while_it_is_fresh(self):
        paths = job.run_paths(self.root)
        fresh = job.build_job(paths, "out", "2022", created=1000.0)
        self.assertTrue(job.fallback_job_is_fresh(fresh, 1000.0 + 60))
        self.assertFalse(job.fallback_job_is_fresh(fresh, 1000.0 + 48 * 3600))
        self.assertFalse(job.fallback_job_is_fresh(job.build_job(paths, "out", "2022"), 1000.0))
        self.assertFalse(job.fallback_job_is_fresh({"created": "soon"}, 1000.0))

    def test_the_wait_scales_with_the_families_and_is_capped(self):
        self.assertEqual(job.LAUNCH_SECONDS + job.PER_PACKAGE_SECONDS,
                         job.estimate_timeout_seconds(0))
        self.assertEqual(job.LAUNCH_SECONDS + 10 * job.PER_PACKAGE_SECONDS,
                         job.estimate_timeout_seconds(10))
        self.assertEqual(job.TIMEOUT_CAP_SECONDS, job.estimate_timeout_seconds(10000))

    def test_progress_survives_a_half_written_line(self):
        self.assertEqual((3, 12), job.parse_progress_line(job.format_progress_line(3, 12)))
        self.assertEqual((0, 0), job.parse_progress_line("3"))
        self.assertEqual((0, 0), job.parse_progress_line(""))
        self.assertEqual((0, 0), job.parse_progress_line("three twelve"))


class ResultFileTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-result-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _summary(self):
        summary = state.DowngradeSummary(state.MODE_REBUILD)
        summary.written.append(state.DowngradeResult(
            "Fan Coil", "C:\\out\\Fan Coil.rfa", "rebuilt",
            notes=["the geometry stage failed: boom"]))
        summary.failed.append(state.DowngradeResult("Pump", "", "no family template"))
        summary.add_note("Shared parameters are rebuilt as family parameters")
        return summary

    def test_a_summary_survives_the_trip_through_json(self):
        path = job.write_result(os.path.join(self.root, job.RESULT_FILENAME),
                                job.build_result(self._summary(), host_version="2022"))
        loaded = job.read_result(path)
        self.assertEqual((True, ""), job.validate_result(loaded))
        summary = job.result_to_summary(loaded)

        self.assertEqual(state.MODE_REBUILD, summary.mode)
        self.assertEqual(1, len(summary.written))
        self.assertEqual("Fan Coil", summary.written[0].family_name)
        self.assertEqual("C:\\out\\Fan Coil.rfa", summary.written[0].target)
        self.assertEqual(["the geometry stage failed: boom"], summary.written[0].notes)
        self.assertEqual(1, len(summary.failed))
        self.assertIn("Shared parameters are rebuilt as family parameters", summary.notes)
        # The existing report text renders it exactly like a local run.
        self.assertIn("Families rebuilt: 1", state.build_summary_text(summary))

    def test_a_runner_that_died_carries_its_error_into_the_notes(self):
        result = job.build_result(None, host_version="2022", error="the runner crashed: boom")
        summary = job.result_to_summary(result)
        self.assertEqual([], summary.written)
        self.assertIn("the runner crashed: boom", summary.notes)

    def test_a_foreign_result_is_refused(self):
        self.assertIn("not a Families Downgrade result", job.validate_result({"format": "x"})[1])
        self.assertFalse(job.validate_result("nonsense")[0])

    def test_a_result_missing_its_lists_still_becomes_an_empty_summary(self):
        summary = job.result_to_summary({"format": job.RESULT_FORMAT})
        self.assertEqual([], summary.written)
        self.assertEqual([], summary.failed)
        self.assertFalse(summary.cancelled)


class InstalledRevitParsingTests(unittest.TestCase):
    # Captured shapes of `pyrevit revits --installed`; the CLI has reshaped
    # this listing between releases, so both are accepted.
    NEW_STYLE = (
        'Autodesk Revit 2025 | Version: 25.0.0.0 | Build: 20240314_1515(x64) | '
        'Language: 1033 | Path: "C:\\Program Files\\Autodesk\\Revit 2025\\"\n'
        'Autodesk Revit 2022 | Version: 22.0.2.392 | Build: 20210224_1530(x64) | '
        'Language: 1033 | Path: "C:\\Program Files\\Autodesk\\Revit 2022\\"\n'
    )
    OLD_STYLE = (
        "\n"
        "Revit 2020 | Build: 20190412_1224(x64) | Path: C:\\Program Files\\Autodesk\\Revit 2020\n"
        "junk line with no year at all\n"
    )

    def test_the_current_listing_yields_versions_and_paths(self):
        installs = job.parse_installed_revits(self.NEW_STYLE)
        self.assertEqual(["2025", "2022"], [i.version for i in installs])
        self.assertEqual("C:\\Program Files\\Autodesk\\Revit 2025\\", installs[0].path)
        self.assertEqual([2025, 2022], [i.number for i in installs])

    def test_an_older_listing_and_junk_lines_are_survived(self):
        installs = job.parse_installed_revits(self.OLD_STYLE)
        self.assertEqual(["2020"], [i.version for i in installs])
        self.assertEqual("C:\\Program Files\\Autodesk\\Revit 2020", installs[0].path)

    def test_nothing_at_all_is_an_empty_list_not_a_crash(self):
        self.assertEqual([], job.parse_installed_revits(""))
        self.assertEqual([], job.parse_installed_revits(None))

    def test_merging_sources_keeps_one_row_per_version_newest_first(self):
        scanned = [job.RevitInstall("2022", "C:\\scan\\2022", "scan"),
                   job.RevitInstall("2025", "C:\\scan\\2025", "scan")]
        from_cli = [job.RevitInstall("2022", "C:\\cli\\2022", "cli"),
                    job.RevitInstall("2021", "C:\\cli\\2021", "cli")]
        merged = job.merge_installs(scanned, from_cli)
        self.assertEqual(["2025", "2022", "2021"], [i.version for i in merged])
        # The first source wins, so a scanned path is not overwritten by the CLI's.
        self.assertEqual("C:\\scan\\2022", merged[1].path)

    def test_a_version_that_is_not_a_release_year_is_dropped_by_the_merge(self):
        self.assertEqual([], job.merge_installs([job.RevitInstall("LT", "C:\\x")]))


class TargetChoiceTests(unittest.TestCase):
    def _installs(self, *versions):
        return [job.RevitInstall(v, "C:\\Program Files\\Autodesk\\Revit {}".format(v))
                for v in versions]

    def test_every_installed_release_from_the_floor_up_is_selectable(self):
        rows = job.target_choices(self._installs("2025", "2022", "2021"), "2025")
        self.assertEqual(["2025", "2022", "2021"], [r.version for r in rows])
        self.assertTrue(all(r.is_enabled for r in rows))

    def test_the_running_revit_says_no_second_revit_is_started(self):
        rows = job.target_choices(self._installs("2025", "2022"), "2025")
        self.assertTrue(rows[0].is_host)
        self.assertIn("no second Revit is started", rows[0].label)
        self.assertFalse(rows[1].is_host)

    def test_a_release_below_the_floor_is_listed_but_disabled_with_the_reason(self):
        rows = job.target_choices(self._installs("2022", "2020", "2019"), "2025")
        self.assertEqual(["2022", "2020", "2019"], [r.version for r in rows])
        self.assertTrue(rows[0].is_enabled)
        for row in rows[1:]:
            self.assertFalse(row.is_enabled)
            self.assertIn("not supported", row.label)
            self.assertIn("SpecTypeId", row.reason)
            self.assertIn("2021", row.reason)

    def test_the_floor_is_2021_because_that_is_where_spectypeid_arrived(self):
        self.assertEqual(2021, job.TARGET_FLOOR)

    def test_a_newer_installed_release_is_offered_and_labelled_as_newer(self):
        rows = job.target_choices(self._installs("2026", "2022"), "2022")
        self.assertIn("newer than this Revit", rows[0].label)
        self.assertTrue(rows[0].is_enabled)

    def test_the_default_is_the_newest_release_below_this_revit(self):
        rows = job.target_choices(self._installs("2026", "2025", "2022", "2021"), "2025")
        self.assertEqual("2022", job.default_target(rows, "2025").version)

    def test_with_nothing_below_it_the_default_is_the_newest_usable_row(self):
        rows = job.target_choices(self._installs("2026", "2025"), "2025")
        self.assertEqual("2026", job.default_target(rows, "2025").version)

    def test_a_disabled_row_is_never_the_default_and_no_rows_is_none(self):
        rows = job.target_choices(self._installs("2020"), "2025")
        self.assertIsNone(job.default_target(rows, "2025"))
        self.assertIsNone(job.default_target([], "2025"))


if __name__ == "__main__":
    unittest.main()
