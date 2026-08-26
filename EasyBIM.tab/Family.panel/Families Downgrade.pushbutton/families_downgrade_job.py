# -*- coding: utf-8 -*-
"""Pure helpers for the downgrade bridge: the job a newer Revit hands an older
one, the result it hands back, and the list of versions it may be handed to.

No Revit imports and no process launching here - ``families_downgrade_bridge``
owns both - so every rule below is driven by the test suite without an engine.

The job file matters more than it looks. ``pyrevit run`` passes no arguments to
the script it starts, so a file is the only way to tell the older Revit what to
do; and it reports a failed run poorly enough that its exit code cannot be
trusted, so the result file the child writes - not the process - is the answer.
A run that leaves no result file failed, whatever the exit code said.
"""

import io
import json
import os
import re

from easybim.compat import safe_text as _safe_text

import families_downgrade_state as state


JOB_FORMAT = "easybim-families-downgrade-job"
RESULT_FORMAT = "easybim-families-downgrade-result"
JOB_SCHEMA_VERSION = 1

JOB_FILENAME = "downgrade_job.json"
RESULT_FILENAME = "downgrade_result.json"
STARTED_FILENAME = "downgrade_started.json"
PROGRESS_FILENAME = "downgrade_progress.txt"
CANCEL_FILENAME = "downgrade_cancel.flag"
LOG_FILENAME = "downgrade_log.txt"
PACKAGES_DIRNAME = "packages"

RUN_ROOT_NAME = "EasyBIM"
RUN_FOLDER_NAME = "families_downgrade"

# How the child finds its job. An environment variable set on the child
# process, because two runs may overlap. The variable travels through
# pyrevit.exe into the Revit.exe it starts, and that hop is not something
# this extension controls - so the same job is also written to one fixed
# path, and the child falls back to it. The fallback is the actual plan B,
# not belt and braces, which is why it carries its own age check.
JOB_PATH_ENV = "EASYBIM_DOWNGRADE_JOB"
FALLBACK_JOB_MAX_AGE_SECONDS = 24 * 60 * 60

# Revit takes minutes to start cold, and a family with heavy geometry takes
# minutes to rebuild. Waiting too long is recoverable; killing a Revit that
# was about to finish is not.
LAUNCH_SECONDS = 7 * 60
PER_PACKAGE_SECONDS = 90
TIMEOUT_CAP_SECONDS = 3 * 60 * 60

# Killing the CLI does not kill the Revit it started, so a cancel asks first:
# the flag lets the child stop between families and close Revit properly.
CANCEL_GRACE_SECONDS = 120

# The oldest Revit this tool can rebuild for. Every parameter data type in a
# package is recorded as its ForgeTypeId string, and that only resolves back
# to a legacy ``ParameterType`` because 2021 has ``SpecTypeId`` and
# ``UnitUtils.GetUnitType``. On 2020 and earlier there is nothing to resolve
# it against, so nearly every parameter would land on a storage-type guess -
# a rebuild that silently loses its data types is worse than a refusal.
TARGET_FLOOR = 2021

FLOOR_REASON = ("Revit {} is older than {}, the oldest release this tool can rebuild for: "
                "a package records parameter data types as ForgeTypeId strings, which need "
                "SpecTypeId to resolve, and SpecTypeId arrived in Revit {}.")


def version_number(value):
    """The four-digit release in ``value``, or 0 when there is none."""
    match = re.search(r"(20\d{2})", _safe_text(value))
    return int(match.group(1)) if match else 0


# --------------------------------------------------------------------------
# the job
# --------------------------------------------------------------------------


def run_paths(run_folder):
    """Every file the two Revits pass through, derived from one run folder."""
    run_folder = _safe_text(run_folder)
    return {
        "run_folder": run_folder,
        "package_folder": os.path.join(run_folder, PACKAGES_DIRNAME),
        "job_path": os.path.join(run_folder, JOB_FILENAME),
        "result_path": os.path.join(run_folder, RESULT_FILENAME),
        "started_path": os.path.join(run_folder, STARTED_FILENAME),
        "progress_path": os.path.join(run_folder, PROGRESS_FILENAME),
        "cancel_path": os.path.join(run_folder, CANCEL_FILENAME),
        "log_path": os.path.join(run_folder, LOG_FILENAME),
    }


def build_job(paths, output_folder, target_version, script_dir="", lib_dir="",
              package_folders=None, template_path="", source_version="", created=0):
    """The instruction file the older Revit reads when it starts.

    ``script_dir`` and ``lib_dir`` are carried rather than worked out by the
    child: a script run through the CLI cannot count on ``__file__``, and the
    target Revit may have no EasyBIM extension of its own to import from.

    ``package_folders`` is explicit and ordered so the child rebuilds exactly
    what this run exported, in the order the ``.rfa`` names were planned from.
    """
    paths = dict(paths or {})
    job = {
        "format": JOB_FORMAT,
        "schema_version": JOB_SCHEMA_VERSION,
        "created": float(created or 0),
        "output_folder": _safe_text(output_folder),
        "target_version": _safe_text(target_version),
        "template_path": _safe_text(template_path),
        "source_version": _safe_text(source_version),
        "script_dir": _safe_text(script_dir),
        "lib_dir": _safe_text(lib_dir),
        "package_folders": [_safe_text(folder) for folder in list(package_folders or [])],
    }
    for key in ("run_folder", "package_folder", "result_path", "started_path",
                "progress_path", "cancel_path", "log_path"):
        job[key] = _safe_text(paths.get(key))
    return job


def validate_job(data):
    """``(ok, reason)`` for a parsed job."""
    if not isinstance(data, dict):
        return False, "the job file is not a JSON object"
    if _safe_text(data.get("format")) != JOB_FORMAT:
        return False, "the job file is not a Families Downgrade job"
    try:
        version = int(data.get("schema_version"))
    except Exception:
        return False, "the job file has no schema version"
    if version > JOB_SCHEMA_VERSION:
        return False, ("the job file was written by a newer Families Downgrade "
                       "(schema {}); update the extension".format(version))
    if not _safe_text(data.get("package_folder")):
        return False, "the job file names no package folder"
    if not _safe_text(data.get("output_folder")):
        return False, "the job file names no output folder"
    return True, ""


def fallback_job_is_fresh(data, now):
    """Whether the one fixed-path job copy is this run's and not last week's."""
    try:
        created = float((data or {}).get("created") or 0)
    except Exception:
        return False
    if not created:
        return False
    return 0 <= (float(now) - created) <= FALLBACK_JOB_MAX_AGE_SECONDS


def estimate_timeout_seconds(package_count):
    """Long enough for a cold Revit plus the families, capped."""
    total = LAUNCH_SECONDS + PER_PACKAGE_SECONDS * max(int(package_count or 0), 1)
    return min(total, TIMEOUT_CAP_SECONDS)


def run_folder_name(stamp, unique):
    """``<stamp>-<unique>`` - the per-run folder under the temp root."""
    return "{}-{}".format(_safe_text(stamp) or "run", _safe_text(unique) or "0")


def format_progress_line(done, total):
    return "{} {}".format(int(done or 0), int(total or 0))


def parse_progress_line(text):
    """``(done, total)`` from the child's progress file; zeros for anything else."""
    parts = _safe_text(text).strip().split()
    if len(parts) < 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


def write_job(path, job):
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(u"" + json.dumps(job, indent=2, sort_keys=True, ensure_ascii=True))
    return path


def read_job(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.loads(handle.read())


# --------------------------------------------------------------------------
# the result
# --------------------------------------------------------------------------


def _result_row(result):
    return {
        "family_name": _safe_text(getattr(result, "family_name", "")),
        "target": _safe_text(getattr(result, "target", "")),
        "status": _safe_text(getattr(result, "status", "")),
        "notes": [_safe_text(note) for note in list(getattr(result, "notes", []) or [])],
    }


def build_result(summary, host_version="", error=""):
    """A ``DowngradeSummary`` as the JSON the parent Revit reads back."""
    summary = summary or state.DowngradeSummary(state.MODE_REBUILD)
    return {
        "format": RESULT_FORMAT,
        "schema_version": JOB_SCHEMA_VERSION,
        "mode": _safe_text(summary.mode) or state.MODE_REBUILD,
        "cancelled": bool(summary.cancelled),
        "host_version": _safe_text(host_version),
        "error": _safe_text(error),
        "notes": [_safe_text(note) for note in list(summary.notes or [])],
        "written": [_result_row(row) for row in list(summary.written or [])],
        "skipped": [_result_row(row) for row in list(summary.skipped or [])],
        "failed": [_result_row(row) for row in list(summary.failed or [])],
    }


def validate_result(data):
    """``(ok, reason)`` for a parsed result."""
    if not isinstance(data, dict):
        return False, "the result file is not a JSON object"
    if _safe_text(data.get("format")) != RESULT_FORMAT:
        return False, "the result file is not a Families Downgrade result"
    return True, ""


def write_result(path, result):
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(u"" + json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return path


def read_result(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.loads(handle.read())


def result_to_summary(data):
    """Rebuild a ``DowngradeSummary`` so the existing report and dialog text
    render the older Revit's run exactly like a local one."""
    data = data if isinstance(data, dict) else {}
    summary = state.DowngradeSummary(_safe_text(data.get("mode")) or state.MODE_REBUILD)
    summary.cancelled = bool(data.get("cancelled"))
    for note in list(data.get("notes") or []):
        summary.add_note(note)
    for key in ("written", "skipped", "failed"):
        rows = getattr(summary, key)
        for row in list(data.get(key) or []):
            row = row if isinstance(row, dict) else {}
            rows.append(state.DowngradeResult(
                row.get("family_name"), row.get("target"), row.get("status"),
                notes=row.get("notes")))
    error = _safe_text(data.get("error"))
    if error:
        summary.add_note(error)
    return summary


# --------------------------------------------------------------------------
# which Revit a package may be handed to
# --------------------------------------------------------------------------


class RevitInstall(object):
    """One Revit found on this machine."""

    def __init__(self, version, path="", source=""):
        self.version = _safe_text(version)
        self.path = _safe_text(path)
        self.source = _safe_text(source)

    @property
    def number(self):
        return version_number(self.version)

    def __str__(self):
        return "Revit {}".format(self.version)


class TargetChoice(object):
    """One row of the target-version dropdown."""

    def __init__(self, version, label, is_enabled=True, reason="", is_host=False):
        self.version = _safe_text(version)
        self.label = _safe_text(label)
        self.is_enabled = bool(is_enabled)
        self.reason = _safe_text(reason)
        self.is_host = bool(is_host)

    @property
    def number(self):
        return version_number(self.version)

    def __str__(self):
        return self.label


def parse_installed_revits(text):
    """``[RevitInstall]`` from ``pyrevit revits --installed`` output.

    Deliberately forgiving: the CLI has reshaped this listing between
    releases, so anything with a release year on it counts and the path is
    taken when the line offers one.
    """
    installs = []
    seen = set()
    for line in _safe_text(text).splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r"Revit[^\d]{0,4}(20\d{2})", line) or re.search(r"\b(20\d{2})\b", line)
        if not match:
            continue
        version = match.group(1)
        if version in seen:
            continue
        path_match = re.search(r'Path:\s*"([^"]+)"', line) or re.search(r"Path:\s*(\S.*)$", line)
        path = path_match.group(1).strip().strip('"') if path_match else ""
        seen.add(version)
        installs.append(RevitInstall(version, path, "cli"))
    return installs


def merge_installs(*groups):
    """One list per version, first source wins, newest first."""
    merged = {}
    for group in groups:
        for install in list(group or []):
            if not install.number:
                continue
            merged.setdefault(install.version, install)
    return sorted(merged.values(), key=lambda install: -install.number)


def target_choices(installs, host_version=""):
    """The dropdown rows for the Revits found on this machine.

    Anything older than the floor is listed rather than hidden: a missing
    2020 reads as a broken scan, while a greyed one with the reason on it
    reads as an answer.
    """
    host = version_number(host_version)
    rows = []
    for install in merge_installs(installs):
        number = install.number
        is_host = bool(host) and number == host
        label = "Revit {}".format(install.version)
        if is_host:
            label = "{} (this Revit - rebuilt here, no second Revit is started)".format(label)
        elif host and number > host:
            label = "{} (newer than this Revit)".format(label)
        if number < TARGET_FLOOR:
            rows.append(TargetChoice(
                install.version, "{} - not supported".format(label), False,
                FLOOR_REASON.format(install.version, TARGET_FLOOR, TARGET_FLOOR), is_host))
            continue
        rows.append(TargetChoice(install.version, label, True, "", is_host))
    return rows


def default_target(choices, host_version=""):
    """The row to pre-select: the newest usable one below this Revit, else the
    newest usable one at all. A downgrade normally goes down by one release."""
    usable = [row for row in list(choices or []) if row.is_enabled]
    if not usable:
        return None
    host = version_number(host_version)
    if host:
        below = [row for row in usable if row.number < host]
        if below:
            return below[0]
    return usable[0]
