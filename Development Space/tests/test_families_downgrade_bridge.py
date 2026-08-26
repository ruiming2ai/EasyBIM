"""Checks for the module that starts the older Revit.

The launch itself cannot be tested - it needs .NET and two Revits - so the
process primitives are the seam: every function that decides something takes
its runner as a default argument, and these tests pass fakes. What is pinned
here is the decisions, which is where a bad run turns into a user staring at
an empty folder: where the CLI is looked for, what counts as an installed
Revit, and the rule that the child's result file, not its exit code, says
whether anything was rebuilt.
"""

import importlib.util
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
    bridge = _load("families_downgrade_bridge", COMMAND_DIR / "families_downgrade_bridge.py")
    return state, job, bridge


state, job, bridge = _load_modules()


class FakeRun(object):
    """Stands in for ``run_watched``: records the launch, replays an outcome."""

    def __init__(self, exit_code=0, error="", on_start=None):
        self.exit_code = exit_code
        self.error = error
        self.on_start = on_start
        self.calls = []

    def __call__(self, executable, parts, environment=None, on_tick=None, timeout_seconds=0,
                 on_stop=None):
        self.calls.append({"executable": executable, "parts": list(parts),
                           "environment": dict(environment or {}),
                           "timeout_seconds": timeout_seconds, "on_stop": on_stop})
        if self.on_start is not None:
            self.on_start()
        return self.exit_code, self.error


class CliDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-cli-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _environ(self, **overrides):
        environ = {"APPDATA": os.path.join(self.root, "appdata"),
                   "PROGRAMDATA": os.path.join(self.root, "programdata"),
                   "PROGRAMFILES": os.path.join(self.root, "pf"),
                   "PATH": os.path.join(self.root, "onpath")}
        environ.update(overrides)
        return environ

    def _plant(self, *parts):
        folder = os.path.join(self.root, *parts)
        os.makedirs(folder)
        path = os.path.join(folder, bridge.CLI_NAME)
        with open(path, "w") as handle:
            handle.write("")
        return path

    def test_the_cli_is_found_in_the_pyrevit_appdata_install(self):
        wanted = self._plant("appdata", "pyRevit-Master", "bin")
        self.assertEqual(wanted, bridge.find_pyrevit_cli(self._environ()))

    def test_the_cli_is_found_on_the_path_when_nowhere_else_has_it(self):
        wanted = self._plant("onpath")
        self.assertEqual(wanted, bridge.find_pyrevit_cli(self._environ()))

    def test_the_appdata_install_wins_over_the_path(self):
        wanted = self._plant("appdata", "pyRevit-Master", "bin")
        self._plant("onpath")
        self.assertEqual(wanted, bridge.find_pyrevit_cli(self._environ()))

    def test_no_cli_anywhere_is_an_empty_string_not_a_crash(self):
        self.assertEqual("", bridge.find_pyrevit_cli(self._environ()))

    def test_a_candidate_that_cannot_be_probed_is_stepped_over(self):
        wanted = self._plant("onpath")

        def angry_exists(path):
            if "pyRevit-Master" in path:
                raise OSError("permission denied")
            return os.path.isfile(path)

        self.assertEqual(wanted, bridge.find_pyrevit_cli(self._environ(), angry_exists))

    def test_the_candidate_list_has_no_duplicates(self):
        candidates = bridge.cli_candidates(self._environ(PATH=os.pathsep.join(
            [os.path.join(self.root, "onpath"), os.path.join(self.root, "onpath")])))
        self.assertEqual(len(candidates), len(set(path.lower() for path in candidates)))


class InstallScanTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-scan-")
        self.autodesk = os.path.join(self.root, "Autodesk")
        os.makedirs(self.autodesk)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _plant(self, name, with_exe=True):
        folder = os.path.join(self.autodesk, name)
        os.makedirs(folder)
        if with_exe:
            with open(os.path.join(folder, "Revit.exe"), "w") as handle:
                handle.write("")
        return folder

    def test_a_release_folder_holding_revit_exe_is_an_install(self):
        self._plant("Revit 2025")
        self._plant("Revit 2022")
        installs = bridge.scan_installed_revits([self.autodesk])
        self.assertEqual(["2022", "2025"], sorted(i.version for i in installs))
        self.assertTrue(all(i.source == "scan" for i in installs))

    def test_a_folder_without_revit_exe_is_not_an_install(self):
        self._plant("Revit 2020", with_exe=False)
        self.assertEqual([], bridge.scan_installed_revits([self.autodesk]))

    def test_other_autodesk_products_are_ignored(self):
        self._plant("AutoCAD 2025")
        self._plant("Revit Server 2022")
        self.assertEqual([], bridge.scan_installed_revits([self.autodesk]))

    def test_a_root_that_does_not_exist_is_skipped_silently(self):
        self.assertEqual([], bridge.scan_installed_revits([os.path.join(self.root, "nope")]))

    def test_the_scan_and_the_cli_listing_are_merged_scan_first(self):
        self._plant("Revit 2025")

        def capture(_executable, _parts, timeout_seconds=0):
            return True, ('Autodesk Revit 2022 | Path: "C:\\Autodesk\\Revit 2022"\n'
                          'Autodesk Revit 2025 | Path: "C:\\elsewhere\\Revit 2025"\n'), ""

        installs = bridge.installed_revits("pyrevit.exe", [self.autodesk], {}, capture)
        self.assertEqual(["2025", "2022"], [i.version for i in installs])
        self.assertEqual(os.path.join(self.autodesk, "Revit 2025"), installs[0].path)

    def test_a_cli_that_fails_still_leaves_the_scan_results(self):
        self._plant("Revit 2025")

        def capture(_executable, _parts, timeout_seconds=0):
            return False, "", "the CLI is not there"

        installs = bridge.installed_revits("pyrevit.exe", [self.autodesk], {}, capture)
        self.assertEqual(["2025"], [i.version for i in installs])

    def test_with_no_cli_path_the_listing_is_never_asked_for(self):
        self._plant("Revit 2025")
        calls = []

        def capture(*args, **kwargs):
            calls.append(args)
            return True, "", ""

        bridge.installed_revits("", [self.autodesk], {}, capture)
        self.assertEqual([], calls)


class ArgumentTests(unittest.TestCase):
    def test_a_path_with_spaces_is_quoted_and_a_plain_one_is_not(self):
        self.assertEqual('"C:\\Program Files\\a.py"',
                         bridge.quote_argument("C:\\Program Files\\a.py"))
        self.assertEqual("--revit=2022", bridge.quote_argument("--revit=2022"))

    def test_empty_parts_are_dropped_from_the_command_line(self):
        self.assertEqual('run "C:\\a b\\r.py" --revit=2022',
                         bridge.build_arguments(["run", "C:\\a b\\r.py", "", "--revit=2022"]))


class PreflightTests(unittest.TestCase):
    def test_a_missing_cli_names_the_manual_way_out(self):
        ok, reason = bridge.preflight("2022", "")
        self.assertFalse(ok)
        self.assertIn("pyrevit.exe", reason)
        self.assertIn("Export downgrade packages", reason)
        self.assertIn("Revit 2022", reason)

    def test_a_target_that_is_not_installed_is_refused(self):
        installs = [job.RevitInstall("2025", "C:\\x")]
        ok, reason = bridge.preflight("2022", "pyrevit.exe", installs)
        self.assertFalse(ok)
        self.assertIn("Revit 2022 was not found on this computer", reason)

    def test_no_target_at_all_is_refused(self):
        self.assertFalse(bridge.preflight("", "pyrevit.exe")[0])

    def test_an_installed_target_with_a_cli_passes(self):
        installs = [job.RevitInstall("2022", "C:\\x")]
        self.assertEqual((True, ""), bridge.preflight("2022", "pyrevit.exe", installs))


class RunDowngradeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-run-")
        self.paths = job.run_paths(self.root)
        os.makedirs(self.paths["package_folder"])
        self.result_path = self.paths["result_path"]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_result(self, summary=None, error="", host_version="2022"):
        job.write_result(self.result_path, job.build_result(summary, host_version, error))

    def _touch_started(self):
        with open(self.paths["started_path"], "w") as handle:
            handle.write("{}")

    def _summary(self):
        summary = state.DowngradeSummary(state.MODE_REBUILD)
        summary.written.append(state.DowngradeResult("Fan Coil", "out\\Fan Coil.rfa", "rebuilt"))
        return summary

    def _run(self, run, **kwargs):
        return bridge.run_downgrade(
            "pyrevit.exe", "2022", "C:\\ext\\runner.py", self.paths, run=run, **kwargs)

    def test_the_child_is_launched_with_the_target_version_and_the_job_path(self):
        run = FakeRun(on_start=lambda: self._write_result(self._summary()))
        summary, error = self._run(run)

        self.assertEqual("", error)
        self.assertEqual(1, len(summary.written))
        call = run.calls[0]
        self.assertEqual(["run", "C:\\ext\\runner.py", "--revit=2022"], call["parts"])
        self.assertEqual({job.JOB_PATH_ENV: self.paths["job_path"]}, call["environment"])
        # A stop hook is always handed over: killing the CLI would leave Revit running.
        self.assertTrue(callable(call["on_stop"]))

    def test_a_result_file_wins_even_when_the_exit_code_is_angry(self):
        # pyRevit run reports failure poorly; the file is the answer.
        run = FakeRun(exit_code=1, on_start=lambda: self._write_result(self._summary()))
        summary, error = self._run(run)
        self.assertEqual("", error)
        self.assertEqual(1, len(summary.written))

    def test_a_child_that_never_started_points_at_the_attach_command(self):
        summary, error = self._run(FakeRun(exit_code=0))
        self.assertIsNone(summary)
        self.assertIn("never got as far as running the rebuild", error)
        self.assertIn("pyrevit attach", error)
        self.assertIn(self.paths["package_folder"], error)

    def test_a_child_that_started_and_died_is_told_apart_from_one_that_never_ran(self):
        summary, error = self._run(FakeRun(exit_code=1, on_start=self._touch_started))
        self.assertIsNone(summary)
        self.assertIn("started the rebuild but closed without writing a result", error)
        # Not an attachment problem: it clearly ran, so do not send them chasing that.
        self.assertNotIn("pyrevit attach", error)

    def test_a_rebuild_that_landed_in_the_wrong_revit_is_refused(self):
        run = FakeRun(on_start=lambda: self._write_result(self._summary(), host_version="2024"))
        summary, error = self._run(run)
        self.assertIsNone(summary)
        self.assertIn("ran in Revit 2024 instead of Revit 2022", error)

    def test_cancelling_says_the_packages_were_kept(self):
        summary, error = self._run(FakeRun(exit_code=-1, error=bridge.CANCELLED))
        self.assertIsNone(summary)
        self.assertIn("Cancelled", error)
        self.assertIn("asked to stop", error)
        self.assertIn(self.paths["package_folder"], error)

    def test_a_child_that_stopped_on_its_own_reports_its_cancelled_summary(self):
        # The cooperative stop worked: Revit closed itself and left a result.
        cancelled = self._summary()
        cancelled.cancelled = True
        run = FakeRun(on_start=lambda: self._write_result(cancelled))
        summary, error = self._run(run)
        self.assertEqual("", error)
        self.assertTrue(summary.cancelled)
        self.assertEqual(1, len(summary.written))

    def test_a_timeout_names_the_limit_in_minutes(self):
        summary, error = self._run(FakeRun(exit_code=-1, error=bridge.TIMED_OUT),
                                   timeout_seconds=600)
        self.assertIsNone(summary)
        self.assertIn("after 10 minutes", error)

    def test_a_launch_that_failed_outright_carries_the_reason(self):
        summary, error = self._run(FakeRun(exit_code=-1, error="file not found"))
        self.assertIsNone(summary)
        self.assertIn("could not be started", error)
        self.assertIn("file not found", error)

    def test_an_unreadable_result_is_reported_not_raised(self):
        def write_junk():
            with open(self.result_path, "w") as handle:
                handle.write("{not json")

        summary, error = self._run(FakeRun(on_start=write_junk))
        self.assertIsNone(summary)
        self.assertIn("could not be read", error)

    def test_a_result_from_something_else_is_refused(self):
        def write_foreign():
            with open(self.result_path, "w") as handle:
                handle.write('{"format": "something-else"}')

        summary, error = self._run(FakeRun(on_start=write_foreign))
        self.assertIsNone(summary)
        self.assertIn("not a Families Downgrade result", error)

    def test_a_child_that_crashed_comes_back_as_a_summary_carrying_the_error(self):
        run = FakeRun(exit_code=1,
                      on_start=lambda: self._write_result(None, "the runner crashed: boom"))
        summary, error = self._run(run)
        self.assertEqual("", error)
        self.assertEqual([], summary.written)
        self.assertIn("the runner crashed: boom", summary.notes)


if __name__ == "__main__":
    unittest.main()
