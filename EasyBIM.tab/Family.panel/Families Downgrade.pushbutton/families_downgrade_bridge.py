# -*- coding: utf-8 -*-
"""Starting the older Revit so it can write the ``.rfa`` this one cannot.

A Revit family file is version-specific and ``SaveAsOptions`` has no target
version in any release from 2021 to 2026, so a 2022 family can only be written
by Revit 2022. This module is how Revit 2025 asks it to: the pyRevit CLI's
``pyrevit run <script> --revit=<year>`` starts that Revit, runs the runner
script in it, and closes it again.

It is the repo's only first-party process launch, and it is kept to that one
job so no other module has to grow one. Two rules follow from what the CLI
gives back:

* ``System.Diagnostics.Process``, never Python ``subprocess`` - IronPython's
  is unreliable, and two existing test guards ban it elsewhere in this repo.
* The child's result file is the answer, not the exit code. pyRevit's own
  issue #552 records how little ``pyrevit run`` says when a run fails, so a
  run that left no result file is treated as failed however it exited.

Every process primitive here is a plain function the callers take as a
default argument, so the batch logic above them runs in the test suite with
no .NET at all.
"""

# pylint: disable=import-error,invalid-name,broad-except
import io
import os
import re
import shutil
import tempfile
import time

from easybim.compat import exception_text
from easybim.compat import safe_text

import families_downgrade_job as job


try:  # pragma: no cover - only a real IronPython session has these
    import clr

    clr.AddReference("System")
    from System.Diagnostics import Process as _Process
    from System.Diagnostics import ProcessStartInfo as _ProcessStartInfo
except Exception:  # pragma: no cover - CPython, where the tests inject fakes
    _Process = None
    _ProcessStartInfo = None


CLI_NAME = "pyrevit.exe"

# Revit start-up plus a family rebuild; generous, because the alternative to
# waiting is killing a Revit that was about to finish.
DEFAULT_TIMEOUT_SECONDS = 45 * 60
POLL_MILLISECONDS = 250

CANCELLED = "cancelled"
TIMED_OUT = "timed out"


# --------------------------------------------------------------------------
# finding the tools
# --------------------------------------------------------------------------


def _pyrevit_bin_dirs():
    """Where the running pyRevit says its own binaries live, if it says."""
    found = []
    try:
        import pyrevit
    except Exception:
        return found
    for attribute in ("BIN_DIR", "HOME_DIR"):
        value = safe_text(getattr(pyrevit, attribute, ""))
        if not value:
            continue
        found.append(value)
        found.append(os.path.join(value, "bin"))
    return found


def cli_candidates(environ=None):
    """Every place ``pyrevit.exe`` is worth looking for, best first."""
    environ = environ if environ is not None else os.environ
    folders = list(_pyrevit_bin_dirs())
    for variable, tail in (("APPDATA", "pyRevit-Master"),
                           ("APPDATA", "pyRevit"),
                           ("PROGRAMDATA", "pyRevit-Master"),
                           ("PROGRAMFILES", "pyRevit CLI"),
                           ("PROGRAMFILES", "pyRevit-Master")):
        root = safe_text(environ.get(variable, ""))
        if root:
            folders.append(os.path.join(root, tail, "bin"))
    for entry in safe_text(environ.get("PATH", "")).split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry:
            folders.append(entry)
    candidates = []
    seen = set()
    for folder in folders:
        path = os.path.join(folder, CLI_NAME)
        key = path.lower()
        if key not in seen:
            seen.add(key)
            candidates.append(path)
    return candidates


def find_pyrevit_cli(environ=None, exists=None):
    """The pyRevit CLI's path, or ``""``. Missing is a value, not a failure."""
    exists = exists or os.path.isfile
    for path in cli_candidates(environ):
        try:
            if exists(path):
                return path
        except Exception:
            continue
    return ""


def autodesk_roots(environ=None):
    environ = environ if environ is not None else os.environ
    roots = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
        value = safe_text(environ.get(variable, ""))
        if value:
            roots.append(os.path.join(value, "Autodesk"))
    seen = set()
    unique = []
    for root in roots:
        key = root.lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def scan_installed_revits(roots=None, environ=None):
    """``[RevitInstall]`` from the Autodesk folders on this machine.

    The deterministic half of the search: a folder named for a release with a
    ``Revit.exe`` in it is a Revit, whatever the CLI does or does not report.
    """
    installs = []
    for root in (roots if roots is not None else autodesk_roots(environ)):
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            continue
        for name in entries:
            match = re.match(r"^Revit\s+(20\d{2})$", safe_text(name).strip())
            if not match:
                continue
            folder = os.path.join(root, name)
            if not os.path.isfile(os.path.join(folder, "Revit.exe")):
                continue
            installs.append(job.RevitInstall(match.group(1), folder, "scan"))
    return installs


def installed_revits(cli_path="", roots=None, environ=None, capture=None):
    """Every Revit on this machine: the folder scan first, the CLI second."""
    scanned = scan_installed_revits(roots, environ)
    from_cli = []
    cli_path = safe_text(cli_path)
    if cli_path:
        capture = capture or capture_output
        ok, text, _error = capture(cli_path, ["revits", "--installed"])
        if ok:
            from_cli = job.parse_installed_revits(text)
    return job.merge_installs(scanned, from_cli)


# --------------------------------------------------------------------------
# the run folder
# --------------------------------------------------------------------------


def run_root(temp_dir=None):
    root = safe_text(temp_dir) or tempfile.gettempdir()
    return os.path.join(root, job.RUN_ROOT_NAME, job.RUN_FOLDER_NAME)


def create_run_folder(temp_dir=None, now=None, unique=None):
    """A private folder for one run: the packages live here, not in the
    user's output folder, and the whole thing is removed afterwards."""
    now = time.time() if now is None else now
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    unique = safe_text(unique) or "{:x}".format(os.getpid())
    folder = os.path.join(run_root(temp_dir), job.run_folder_name(stamp, unique))
    index = 2
    while os.path.exists(folder):
        folder = os.path.join(run_root(temp_dir),
                              job.run_folder_name(stamp, "{}_{}".format(unique, index)))
        index += 1
    paths = job.run_paths(folder)
    os.makedirs(paths["package_folder"])
    return paths


def fallback_job_path(environ=None):
    """The one fixed path the child falls back to when the environment
    variable did not survive the hop into the target Revit."""
    environ = environ if environ is not None else os.environ
    root = (safe_text(environ.get("LOCALAPPDATA", "")) or safe_text(environ.get("APPDATA", ""))
            or tempfile.gettempdir())
    return os.path.join(root, job.RUN_ROOT_NAME, job.RUN_FOLDER_NAME, job.JOB_FILENAME)


def write_fallback_job(data, environ=None):
    """Best effort: a failure here only costs the fallback, not the run."""
    path = fallback_job_path(environ)
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        job.write_job(path, data)
        return path
    except Exception:
        return ""


def request_cancel(paths):
    """Ask the child to stop between families.

    Killing the CLI would leave the Revit it started running, so the child is
    asked first and killed only if it will not go - a force-killed Revit
    leaves journals, lock files and possibly a half-written family behind.
    """
    try:
        with io.open(paths.get("cancel_path", ""), "w", encoding="utf-8") as handle:
            handle.write(u"cancel")
        return True
    except Exception:
        return False


def read_progress(paths):
    """``(done, total)`` from the child's progress file, zeros if unreadable."""
    try:
        with io.open(paths.get("progress_path", ""), "r", encoding="utf-8") as handle:
            return job.parse_progress_line(handle.read())
    except Exception:
        return 0, 0


def discard_run_folder(paths):
    """Remove the run folder; a note when something was left behind."""
    folder = safe_text((paths or {}).get("run_folder"))
    if not folder or not os.path.isdir(folder):
        return ""
    try:
        shutil.rmtree(folder)
        return ""
    except Exception as error:
        return ("The temporary downgrade packages could not be removed from {}: {}".format(
            folder, exception_text(error)))


def rescue_packages(paths, output_folder):
    """Move a failed run's packages next to the output; ``(folder, note)``.

    A run that did not produce families must not also destroy the packages -
    they are the whole export, and rebuilding them by hand in the older Revit
    is the way out every failure message points at.
    """
    source = safe_text((paths or {}).get("package_folder"))
    if not source or not os.path.isdir(source):
        return "", ""
    target = os.path.join(safe_text(output_folder), "Families Downgrade packages")
    try:
        if os.path.isdir(target):
            target = "{}-{}".format(target, time.strftime("%H%M%S"))
        shutil.move(source, target)
        return target, ""
    except Exception as error:
        return source, ("The packages could not be moved next to the output ({}), so they "
                        "were left at:\n{}".format(exception_text(error), source))


# --------------------------------------------------------------------------
# the process primitives
# --------------------------------------------------------------------------


def quote_argument(value):
    value = safe_text(value)
    return '"{}"'.format(value) if " " in value and not value.startswith('"') else value


def build_arguments(parts):
    return " ".join(quote_argument(part) for part in parts if safe_text(part))


def _start_info(executable, arguments, environment=None, capture=False):
    info = _ProcessStartInfo(executable, arguments)
    info.UseShellExecute = False
    info.CreateNoWindow = True
    if capture:
        info.RedirectStandardOutput = True
        info.RedirectStandardError = True
    for key, value in (environment or {}).items():
        info.EnvironmentVariables[key] = safe_text(value)
    return info


def capture_output(executable, parts, timeout_seconds=120):  # pragma: no cover - needs .NET
    """Run a short command and collect stdout; ``(ok, text, error)``."""
    if _Process is None:
        return False, "", "no .NET process support in this session"
    try:
        process = _Process()
        process.StartInfo = _start_info(executable, build_arguments(parts), capture=True)
        process.Start()
        text = process.StandardOutput.ReadToEnd()
        process.WaitForExit(int(max(timeout_seconds, 1) * 1000))
        return True, safe_text(text), ""
    except Exception as error:
        return False, "", exception_text(error)


def run_watched(executable, parts, environment=None, on_tick=None,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS, on_stop=None,
                grace_seconds=job.CANCEL_GRACE_SECONDS):  # pragma: no cover - needs .NET
    """Run a long command, ticking while it works; ``(exit_code, error)``.

    ``on_tick(seconds)`` returning False means the user pressed Cancel.

    Stopping is a request before it is a kill. ``Process.Kill`` here would end
    ``pyrevit.exe`` and leave the Revit it started running, so ``on_stop()``
    asks the child to stop between families and close Revit properly; only a
    child that will not go within the grace is killed. A child that exits
    during the grace is a normal exit - it will have written its own result
    saying it was cancelled.
    """
    if _Process is None:
        return -1, "no .NET process support in this session"
    try:
        process = _Process()
        process.StartInfo = _start_info(executable, build_arguments(parts), environment)
        process.Start()
    except Exception as error:
        return -1, exception_text(error)

    waited = 0.0
    step = POLL_MILLISECONDS / 1000.0
    stopping = ""
    asked_at = 0.0
    while True:
        try:
            if process.WaitForExit(POLL_MILLISECONDS):
                break
        except Exception as error:
            return -1, exception_text(error)
        waited += step
        if stopping:
            if waited - asked_at > grace_seconds:
                _kill(process)
                return -1, stopping
            continue
        if on_tick is not None and not on_tick(waited):
            stopping = CANCELLED
        elif timeout_seconds and waited > timeout_seconds:
            stopping = TIMED_OUT
        if stopping:
            asked_at = waited
            if on_stop is not None:
                try:
                    on_stop()
                except Exception:
                    pass
    try:
        return int(process.ExitCode), ""
    except Exception:
        return 0, ""


def _kill(process):  # pragma: no cover - needs .NET
    try:
        process.Kill()
    except Exception:
        pass


# --------------------------------------------------------------------------
# the round trip
# --------------------------------------------------------------------------


def preflight(target_version, cli_path="", installs=None):
    """``(ok, reason)`` before any process is started."""
    version = safe_text(target_version)
    if not version:
        return False, "No target Revit version was chosen."
    if not safe_text(cli_path):
        return False, (
            "The pyRevit command line tool (pyrevit.exe) was not found, so Revit {0} cannot "
            "be started for you. Install the pyRevit CLI, or use 'Export downgrade packages' "
            "here and run Families Downgrade in Revit {0} yourself.".format(version))
    if installs is not None:
        numbers = [install.number for install in installs]
        if job.version_number(version) not in numbers:
            return False, "Revit {} was not found on this computer.".format(version)
    return True, ""


def _no_result_reason(version, package_folder, exit_code, started):
    if started:
        opening = ("Revit {0} started the rebuild but closed without writing a result, so "
                   "what happened there cannot be reported.".format(version))
        cause = ("Any families it managed to write are in the output folder. ")
    else:
        opening = ("Revit {0} never got as far as running the rebuild, so nothing was "
                   "written.".format(version))
        cause = ("The usual cause is that pyRevit is not attached to Revit {0}: open a "
                 "command prompt and run\n\n    pyrevit attach master --installed\n\nthen "
                 "try again. It can also mean Revit {0} could not start. ".format(version))
    return ("{0}\n\n{1}The downgrade packages were kept, so you can open Revit {2} yourself "
            "and use 'Rebuild families from downgrade packages' on:\n{3}\n\n(The command "
            "line tool exited with code {4}.)".format(
                opening, cause, version, package_folder, exit_code))


def run_downgrade(cli_path, target_version, runner_script, paths, on_tick=None,
                  timeout_seconds=DEFAULT_TIMEOUT_SECONDS, run=None, read_result=None,
                  exists=None, stop=None):
    """Drive the older Revit through one package folder.

    Returns ``(summary, error)``: a ``state.DowngradeSummary`` when the child
    reported one, otherwise ``None`` and a sentence saying what happened and
    where the packages were left.
    """
    run = run or run_watched
    read_result = read_result or job.read_result
    exists = exists or os.path.isfile
    stop = stop or (lambda: request_cancel(paths))
    version = safe_text(target_version)
    package_folder = safe_text(paths.get("package_folder"))
    result_path = safe_text(paths.get("result_path"))

    exit_code, error = run(
        cli_path,
        ["run", runner_script, "--revit={}".format(version)],
        {job.JOB_PATH_ENV: safe_text(paths.get("job_path"))},
        on_tick,
        timeout_seconds,
        stop,
    )

    if error == CANCELLED:
        return None, ("Cancelled. Revit {} was asked to stop and then closed; it may take a "
                      "moment to disappear. The downgrade packages were kept at:\n{}".format(
                          version, package_folder))
    if error == TIMED_OUT:
        minutes = int(max(timeout_seconds, 60) / 60)
        return None, ("Revit {} was still working after {} minutes, so it was asked to stop. "
                      "It may still be closing in the background. The downgrade packages were "
                      "kept at:\n{}".format(version, minutes, package_folder))
    if error:
        return None, ("Revit {} could not be started: {}\n\nThe downgrade packages were kept "
                      "at:\n{}".format(version, error, package_folder))

    if not exists(result_path):
        started = bool(exists(safe_text(paths.get("started_path"))))
        return None, _no_result_reason(version, package_folder, exit_code, started)
    try:
        data = read_result(result_path)
    except Exception as read_error:
        return None, ("Revit {} wrote a result that could not be read: {}\n\nThe downgrade "
                      "packages were kept at:\n{}".format(
                          version, exception_text(read_error), package_folder))
    ok, reason = job.validate_result(data)
    if not ok:
        return None, ("Revit {} wrote a result that is not a Families Downgrade result ({}).\n\n"
                      "The downgrade packages were kept at:\n{}".format(
                          version, reason, package_folder))

    ran_in = safe_text(data.get("host_version"))
    if ran_in and job.version_number(ran_in) != job.version_number(version):
        return None, ("The rebuild ran in Revit {} instead of Revit {}, so nothing was kept: "
                      "the families would have been Revit {} files. The downgrade packages "
                      "were kept at:\n{}".format(ran_in, version, ran_in, package_folder))
    return job.result_to_summary(data), ""
