"""Checks for the script the older Revit runs.

Nobody watches this script: it starts in a Revit opened by the command line
tool, works, and the session closes. So the rules pinned here are the ones
that decide whether the run can be reported at all - that a job is found by
either route, that a stale one is refused, that the cancel flag reaches a
session with no UI, and above all that every ending leaves a result file
behind. A silent Revit and a crashed Revit look identical from outside.
"""

import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
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
    runner = _load("families_downgrade_runner", COMMAND_DIR / "families_downgrade_runner.py")
    return state, job, bridge, runner


state, job, bridge, runner = _load_modules()


class FakePackage(object):
    def __init__(self, folder, family_name):
        self.folder = folder
        self.family_name = family_name


class DuplicatedConstantTests(unittest.TestCase):
    """The runner spells these out because it must find the job before it can
    import the module that owns them. They must not drift apart."""

    def test_the_job_environment_variable_matches(self):
        self.assertEqual(job.JOB_PATH_ENV, runner.JOB_PATH_ENV)

    def test_the_fallback_folder_and_file_names_match(self):
        self.assertEqual(job.RUN_ROOT_NAME, runner.RUN_ROOT_NAME)
        self.assertEqual(job.RUN_FOLDER_NAME, runner.RUN_FOLDER_NAME)
        self.assertEqual(job.JOB_FILENAME, runner.JOB_FILENAME)

    def test_the_runner_and_the_parent_agree_on_the_fallback_path(self):
        environ = {"LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local"}
        self.assertEqual(bridge.fallback_job_path(environ),
                         os.path.join(runner.fallback_root(environ), runner.JOB_FILENAME))

    def test_the_runner_only_needs_the_standard_library_before_bootstrapping(self):
        # Anything imported at module level runs before sys.path is set up.
        source = (COMMAND_DIR / "families_downgrade_runner.py").read_text(encoding="utf-8")
        imported = [line.strip() for line in source.splitlines()
                    if line[:1] not in (" ", "\t")
                    and (line.startswith("import ") or line.startswith("from "))]
        self.assertEqual(
            ["import io", "import json", "import os", "import sys",
             "import tempfile", "import time", "import traceback"],
            sorted(imported))


class LoadJobTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-runner-")
        self.paths = job.run_paths(self.root)
        self.data = job.build_job(self.paths, "C:\\out", "2022", created=time.time())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _fallback_environ(self):
        return {"LOCALAPPDATA": os.path.join(self.root, "local")}

    def test_the_environment_variable_is_used_when_it_points_at_a_job(self):
        job.write_job(self.paths["job_path"], self.data)
        loaded, source, error = runner.load_job({runner.JOB_PATH_ENV: self.paths["job_path"]})
        self.assertEqual("", error)
        self.assertEqual(self.paths["job_path"], source)
        self.assertEqual("2022", loaded["target_version"])

    def test_a_named_job_that_cannot_be_read_is_reported_not_silently_skipped(self):
        loaded, _source, error = runner.load_job(
            {runner.JOB_PATH_ENV: os.path.join(self.root, "gone.json")})
        self.assertIsNone(loaded)
        self.assertIn("could not be read", error)

    def test_the_fixed_path_is_the_fallback_when_the_variable_did_not_arrive(self):
        environ = self._fallback_environ()
        fallback = bridge.write_fallback_job(self.data, environ)
        self.assertTrue(fallback)
        loaded, source, error = runner.load_job(environ)
        self.assertEqual("", error)
        self.assertEqual(fallback, source)
        self.assertEqual("2022", loaded["target_version"])

    def test_a_stale_fixed_path_job_is_refused_rather_than_run(self):
        environ = self._fallback_environ()
        old = job.build_job(self.paths, "C:\\out", "2022", created=time.time() - 48 * 3600)
        bridge.write_fallback_job(old, environ)
        loaded, _source, error = runner.load_job(environ)
        self.assertIsNone(loaded)
        self.assertIn("stale", error)

    def test_a_job_with_no_timestamp_is_refused_through_the_fallback(self):
        environ = self._fallback_environ()
        bridge.write_fallback_job(job.build_job(self.paths, "C:\\out", "2022"), environ)
        self.assertIsNone(runner.load_job(environ)[0])

    def test_no_job_anywhere_is_an_error_not_a_crash(self):
        loaded, _source, error = runner.load_job(self._fallback_environ())
        self.assertIsNone(loaded)
        self.assertIn("no job file was found", error)


class ProgressAndCancelTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-tick-")
        self.paths = job.run_paths(self.root)
        os.makedirs(self.paths["package_folder"])
        self.data = job.build_job(self.paths, "C:\\out", "2022")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_each_tick_publishes_where_the_run_has_got_to(self):
        tick = runner.make_progress(self.data)
        self.assertTrue(tick(3, 12))
        self.assertEqual((3, 12), bridge.read_progress(self.paths))

    def test_the_parents_cancel_flag_stops_the_batch(self):
        tick = runner.make_progress(self.data)
        self.assertTrue(tick(0, 4))
        bridge.request_cancel(self.paths)
        self.assertFalse(tick(1, 4))

    def test_a_progress_file_that_cannot_be_written_never_stops_the_run(self):
        def angry(_path, _text):
            raise IOError("read only")

        tick = runner.make_progress(self.data, angry)
        self.assertTrue(tick(1, 2))

    def test_an_unreadable_progress_file_reads_as_no_progress(self):
        with io.open(self.paths["progress_path"], "w", encoding="utf-8") as handle:
            handle.write(u"nonsense")
        self.assertEqual((0, 0), bridge.read_progress(self.paths))


class OrderedPackagesTests(unittest.TestCase):
    def test_the_run_rebuilds_what_it_exported_in_the_planned_order(self):
        data = {"package_folders": ["C:\\run\\packages\\B.downgrade",
                                    "C:\\run\\packages\\A.downgrade"]}
        found = [FakePackage("C:\\run\\packages\\A.downgrade", "A"),
                 FakePackage("C:\\run\\packages\\B.downgrade", "B")]
        self.assertEqual(["B", "A"], [p.family_name for p in runner.ordered_packages(data, found)])

    def test_a_folder_this_run_did_not_export_is_not_picked_up(self):
        data = {"package_folders": ["C:\\run\\packages\\A.downgrade"]}
        found = [FakePackage("C:\\run\\packages\\A.downgrade", "A"),
                 FakePackage("C:\\run\\packages\\Stray.downgrade", "Stray")]
        self.assertEqual(["A"], [p.family_name for p in runner.ordered_packages(data, found)])

    def test_paths_match_case_and_trailing_slash_blind(self):
        data = {"package_folders": ["c:\\RUN\\packages\\a.downgrade\\"]}
        found = [FakePackage("C:\\run\\packages\\A.downgrade", "A")]
        self.assertEqual(["A"], [p.family_name for p in runner.ordered_packages(data, found)])

    def test_a_job_that_names_no_folders_falls_back_to_everything_found(self):
        found = [FakePackage("C:\\a.downgrade", "A")]
        self.assertEqual(found, runner.ordered_packages({}, found))


class MainTests(unittest.TestCase):
    """``main`` must leave a result behind on every path out."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fdg-main-")
        self.paths = job.run_paths(self.root)
        os.makedirs(self.paths["package_folder"])
        job.write_job(self.paths["job_path"],
                      job.build_job(self.paths, os.path.join(self.root, "out"), "2022",
                                    created=time.time()))
        self.environ = {runner.JOB_PATH_ENV: self.paths["job_path"]}
        self.original = runner.rebuild

    def tearDown(self):
        runner.rebuild = self.original
        shutil.rmtree(self.root, ignore_errors=True)

    def _result(self):
        return job.read_result(self.paths["result_path"])

    def test_a_good_run_writes_a_result_the_parent_can_validate(self):
        summary = state.DowngradeSummary(state.MODE_REBUILD)
        summary.written.append(state.DowngradeResult("Fan Coil", "out\\Fan Coil.rfa", "rebuilt"))
        runner.rebuild = lambda _job: (summary, "2022")

        path, error = runner.main(self.environ)

        self.assertEqual(self.paths["result_path"], path)
        self.assertEqual("", error)
        data = self._result()
        self.assertEqual((True, ""), job.validate_result(data))
        self.assertEqual("2022", data["host_version"])
        self.assertEqual(1, len(job.result_to_summary(data).written))

    def test_the_started_mark_is_written_before_any_work(self):
        seen = {}

        def rebuild(_job):
            seen["started"] = os.path.isfile(self.paths["started_path"])
            return state.DowngradeSummary(state.MODE_REBUILD), "2022"

        runner.rebuild = rebuild
        runner.main(self.environ)
        self.assertTrue(seen["started"])

    def test_a_rebuild_that_blows_up_still_leaves_a_result_and_a_log(self):
        def rebuild(_job):
            raise RuntimeError("Revit fell over")

        runner.rebuild = rebuild
        path, error = runner.main(self.environ)

        self.assertEqual(self.paths["result_path"], path)
        self.assertIn("Revit fell over", error)
        data = self._result()
        self.assertEqual((True, ""), job.validate_result(data))
        self.assertIn("Revit fell over", data["error"])
        self.assertIn("Revit fell over", job.result_to_summary(data).notes[0])
        self.assertIn("Traceback", io.open(self.paths["log_path"], encoding="utf-8").read())

    def test_no_job_at_all_writes_a_crash_note_and_no_result(self):
        path, error = runner.main({"LOCALAPPDATA": os.path.join(self.root, "local")})
        self.assertEqual("", path)
        self.assertIn("no job file was found", error)
        self.assertFalse(os.path.isfile(self.paths["result_path"]))
        crash = os.path.join(runner.fallback_root({"LOCALAPPDATA": os.path.join(
            self.root, "local")}), runner.CRASH_FILENAME)
        self.assertTrue(os.path.isfile(crash))


if __name__ == "__main__":
    unittest.main()
