# -*- coding: utf-8 -*-
"""What the older Revit runs: rebuild the packages named in a job file.

Started by ``pyrevit run <this file> --revit=<year>`` from the newer Revit, in
a session nobody is watching. Two things follow from that.

Nothing may raise without leaving a trace. There is no dialog to show and no
console to read, so every exit writes a result file; the parent treats a run
that left none as failed, which is the honest reading of a silent Revit.

Nothing above the job may be assumed. The imports below the bootstrap cannot
run until ``sys.path`` carries the folders the job names, because a script
started through the CLI cannot count on ``__file__`` and the target Revit may
have no EasyBIM extension of its own. So the top of this module is stdlib
only, and the two constants it needs are spelled out rather than imported -
a test pins them to the originals.
"""

# pylint: disable=import-error,invalid-name,broad-except
import io
import json
import os
import sys
import tempfile
import time
import traceback


# Duplicated from families_downgrade_job / families_downgrade_bridge on
# purpose: the job has to be found before its module can be imported.
JOB_PATH_ENV = "EASYBIM_DOWNGRADE_JOB"
RUN_ROOT_NAME = "EasyBIM"
RUN_FOLDER_NAME = "families_downgrade"
JOB_FILENAME = "downgrade_job.json"
CRASH_FILENAME = "downgrade_crash.txt"


def _read_json(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.loads(handle.read())


def _write_text(path, text):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(u"" + text)


def fallback_root(environ=None):
    environ = environ if environ is not None else os.environ
    root = (environ.get("LOCALAPPDATA") or environ.get("APPDATA")
            or tempfile.gettempdir())
    return os.path.join(root, RUN_ROOT_NAME, RUN_FOLDER_NAME)


def load_job(environ=None, now=None):
    """``(job, source, error)``.

    The environment variable first; the one fixed path second, because the
    variable has to survive the hop from the CLI into the Revit it starts and
    that is not something the extension controls. A stale fixed copy is
    refused rather than run - rebuilding yesterday's packages into today's
    folder would be a silent wrong answer.
    """
    environ = environ if environ is not None else os.environ
    now = time.time() if now is None else now
    named = environ.get(JOB_PATH_ENV) or ""
    if named:
        try:
            return _read_json(named), named, ""
        except Exception as error:
            return None, named, "the job named by {} could not be read: {}".format(
                JOB_PATH_ENV, error)
    path = os.path.join(fallback_root(environ), JOB_FILENAME)
    try:
        data = _read_json(path)
    except Exception as error:
        return None, path, "no job file was found ({}: {})".format(path, error)
    try:
        created = float((data or {}).get("created") or 0)
    except Exception:
        created = 0
    if not created or not 0 <= (now - created) <= 24 * 60 * 60:
        return None, path, "the job file at {} is stale; it was not run".format(path)
    return data, path, ""


def bootstrap(job):
    """Put the extension's folders on ``sys.path``, nearest first."""
    for key in ("lib_dir", "script_dir"):
        folder = (job or {}).get(key) or ""
        if folder and folder not in sys.path:
            sys.path.insert(0, folder)


def write_started(job):
    """The first mark on disk: it is how the parent tells a Revit that never
    ran the script from one that ran it and died."""
    try:
        _write_text((job or {}).get("started_path") or "",
                    json.dumps({"started": time.time()}))
    except Exception:
        pass


def make_progress(job, write_text=None):
    """A ``rebuild_family_packages`` tick that publishes progress and reads
    the parent's cancel flag - the only way a cancel reaches this session."""
    write_text = write_text or _write_text
    progress_path = (job or {}).get("progress_path") or ""
    cancel_path = (job or {}).get("cancel_path") or ""

    def tick(done, total):
        if progress_path:
            try:
                write_text(progress_path, u"{} {}".format(int(done), int(total)))
            except Exception:
                pass
        if cancel_path:
            try:
                if os.path.isfile(cancel_path):
                    return False
            except Exception:
                pass
        return True

    return tick


def ordered_packages(job, packages):
    """The packages this run exported, in the order its file names were
    planned from. A folder the parent did not write is never picked up."""
    wanted = [(folder or "").lower().rstrip("\\/")
              for folder in list((job or {}).get("package_folders") or [])]
    if not wanted:
        return list(packages or [])
    by_folder = {}
    for package in list(packages or []):
        by_folder[(package.folder or "").lower().rstrip("\\/")] = package
    ordered = []
    for folder in wanted:
        package = by_folder.get(folder)
        if package is not None:
            ordered.append(package)
    return ordered


def get_application():
    """The ``Application`` of this session; no document is needed or opened."""
    try:
        from pyrevit import HOST_APP

        app = getattr(HOST_APP, "app", None)
        if app is not None:
            return app
    except Exception:
        pass
    try:
        return __revit__.Application  # noqa: F821 - pyRevit injects this
    except Exception:
        return None


def rebuild(job):
    """The work, once the path is set up; returns a ``DowngradeSummary``."""
    import families_downgrade_job as fdj
    import families_downgrade_state as state
    from families_downgrade_rebuild import rebuild_family_packages
    from families_downgrade_revit import host_version

    app = get_application()
    if app is None:
        raise RuntimeError("this Revit session offers no Application object")

    running = host_version(app)
    wanted = job.get("target_version") or ""
    if fdj.version_number(running) != fdj.version_number(wanted):
        summary = state.DowngradeSummary(state.MODE_REBUILD)
        summary.add_note(
            "Nothing was rebuilt: this is Revit {}, and the run asked for Revit {}. The "
            "families would have been Revit {} files.".format(running, wanted, running))
        return summary, running

    packages = ordered_packages(job, state.find_packages(job.get("package_folder") or ""))
    options = state.RebuildOptions(job.get("package_folder") or "",
                                   job.get("output_folder") or "",
                                   job.get("template_path") or "")
    summary = rebuild_family_packages(app, packages, options, progress=make_progress(job))
    return summary, running


def report_crash(job, path_hint, error_text, environ=None):
    """Leave something behind even when there is nowhere proper to put it."""
    for path in ((job or {}).get("log_path") or "",
                 os.path.join(fallback_root(environ), CRASH_FILENAME)):
        if not path:
            continue
        try:
            _write_text(path, error_text)
            return path
        except Exception:
            continue
    return path_hint


def main(environ=None):
    """``(result_path, error)``; always tries to leave a result behind."""
    job, source, error = load_job(environ)
    if job is None:
        report_crash(None, source, error or "no job", environ)
        return "", error or "no job"

    write_started(job)
    result_path = job.get("result_path") or ""
    running = job.get("target_version") or ""
    summary = None
    failure = ""
    try:
        bootstrap(job)
        summary, running = rebuild(job)
    except Exception as crash:
        failure = "the rebuild failed inside Revit: {}".format(crash)
        report_crash(job, source, traceback.format_exc(), environ)

    try:
        import families_downgrade_job as fdj

        fdj.write_result(result_path, fdj.build_result(summary, running, failure))
    except Exception as write_error:
        report_crash(job, source, "the result could not be written: {}\n{}".format(
            write_error, traceback.format_exc()), environ)
        return "", "the result could not be written"
    return result_path, failure


if __name__ == "__main__":
    main()
