# -*- coding: utf-8 -*-
"""
RL Tools messages.py

This module centralizes the startup message and helpers.  Both your
`doc-opened.py` hook and the ribbon button call into `show_start_message`
to present the onboarding reminder.  It can also show an RL Tools Active
Workset picker after the alert and print a Coordination Review summary.

Engine notes:
  * In Rocket Mode (CPython), `pyrevit.forms` is unavailable.  We therefore
    build a small WPF dialog ourselves to render true bold.  If WPF fails
    for any reason, we fall back to Revit's `TaskDialog` with plain text.

Usage:
    from rltools.messages import show_start_message
    show_start_message(doc=doc, force=False, open_worksets_after=True)

Parameters:
    title (str): Title for the dialog window (default "RL Tools").
    force (bool): If True, always show the dialog (useful for the ribbon
        button).  Otherwise we filter out families and linked documents.
    doc (Document or None): The active Revit document; the helper uses it
        to decide if the message should display.  If None, the message
        will show when force is True.
    open_worksets_after (bool): When True, an Active Workset picker window
        will open automatically after the user closes the alert.
    run_coord_report_after (bool): When True, a Coordination Review summary
        report is printed after startup actions complete.
"""

import time

# =============================
#  Public API (what others call)
# =============================

START_MESSAGE = (
    "Please Check Your Current **<<Workset>>**.\n\n"
    "For New Users, please read the **<<Starting View>>** (Available in only new projects) "
    "for GB Standards & Best Practices."
)


_UNMAPPED_LINK_KEY = "__UNMAPPED__"
_INSTANCE_LIST_CAP = 200

# Timeout while waiting for the target document to become active/ready for
# the Active Workset picker.
_WORKSETS_POST_TIMEOUT_SEC = 45.0
_STARTUP_JOB_MAX_AGE_SEC = 300.0
_STARTUP_STATE_ENVVAR = "RLTOOLS_STARTUP_QUEUE_STATE"
_FILE_OPEN_TRIGGER_ENVVAR = "RLTOOLS_FILE_OPEN_TRIGGER"
_FILE_OPEN_TRIGGER_MAX_AGE_SEC = 90.0



def show_start_message(
    title="RL Tools",
    force=False,
    doc=None,
    open_worksets_after=False,
    run_coord_report_after=False
):
    """Show onboarding dialog and optionally run startup follow-up actions.

    We keep this function name and signature stable so your hook and button do
    not need to change other than toggling optional flags.
    """
    # 1) Decide if we should show (unless the caller explicitly forces it)
    if not (force or _should_show_for_doc(doc)):
        return

    # 2) Try the rich WPF dialog first (it renders true bold); track success
    shown = _alert_wpf_with_bold(title, START_MESSAGE)

    # 3) If WPF failed for any reason, fall back to Revit TaskDialog (plain text)
    if not shown:
        try:
            from Autodesk.Revit.UI import TaskDialog
            TaskDialog.Show(title, START_MESSAGE.replace("**", ""))
            shown = True
        except Exception:
            # Last resort: write to console so at least something is visible in logs
            print("[RL Tools] {}: {}".format(title, START_MESSAGE.replace("**", "")))

    # 4) Optionally run/queue startup follow-up actions after user closes dialog.
    if shown and (open_worksets_after or run_coord_report_after):
        run_doc = doc if _is_doc_valid(doc) else None
        if not _is_doc_valid(run_doc):
            run_doc = _get_active_doc_from_uiapp(_get_uiapp())

        if _is_doc_valid(run_doc):
            _run_startup_actions_now(
                doc=run_doc,
                open_worksets_after=open_worksets_after,
                run_coord_report_after=run_coord_report_after,
            )
        else:
            _enqueue_startup_actions(
                doc=doc,
                open_worksets_after=open_worksets_after,
                run_coord_report_after=run_coord_report_after,
            )


def run_start_message_workflow(doc=None, force=False):
    """Run the default RL Tools start-message workflow."""
    return show_start_message(
        doc=doc,
        force=force,
        open_worksets_after=True,
        run_coord_report_after=True,
    )


def run_start_message_on_file_open(doc=None):
    """Run Start Message for a qualifying opened file with context fallback."""
    if _is_doc_eligible_for_file_open(doc):
        _clear_file_open_trigger_pending()
        return run_start_message_workflow(doc=doc, force=False)

    # Only defer when hook timing did not provide a usable document context.
    if not _is_doc_valid(doc):
        _mark_file_open_trigger_pending()
    else:
        _clear_file_open_trigger_pending()
    return None


# =============================
#  Internals (helpers)
# =============================

def _should_show_for_doc(doc):
    """Filter out contexts where showing the popup would be noisy.
    - Skip families and linked docs by default.
    - We no longer require worksharing; both non-workshared and workshared projects will see it.
    """
    if doc is None:
        # If the hook couldn't fetch a Document at this instant, we still show.
        return True
    if getattr(doc, "IsFamilyDocument", False):
        return False
    if hasattr(doc, "IsLinked") and doc.IsLinked:
        return False
    return True


def _enqueue_startup_actions(doc, open_worksets_after, run_coord_report_after):
    """Queue post-dialog actions to be processed by hooks/app-idling.py."""
    if not (open_worksets_after or run_coord_report_after):
        return

    job_id = _enqueue_startup_job(
        doc=doc,
        open_worksets_after=open_worksets_after,
        run_coord_report_after=run_coord_report_after,
    )
    if job_id is not None:
        return

    # Fallback path only when env-var state cannot be used.
    if open_worksets_after:
        _show_workset_picker_for_doc(doc)
    if run_coord_report_after:
        _print_coordination_review_report(doc)


def _run_startup_actions_now(doc, open_worksets_after, run_coord_report_after):
    """Run post-alert actions immediately for current valid document context."""
    logger = _get_logger()

    if open_worksets_after:
        try:
            _show_workset_picker_for_doc(doc)
        except Exception as ex:
            if logger:
                logger.warning("Workset picker failed after start message: %s", ex)

    if run_coord_report_after:
        try:
            _print_coordination_review_report(doc)
        except Exception as ex:
            if logger:
                logger.warning("Coordination report failed after start message: %s", ex)


def process_startup_jobs(uiapp=None):
    """Process queued startup jobs. Called from hooks/app-idling.py."""
    uiapp = uiapp or _get_uiapp()
    if not uiapp:
        return

    _process_file_open_trigger_pending(uiapp=uiapp)

    state = _load_startup_state()
    jobs = state.get("jobs", [])
    if not jobs:
        return

    now = time.time()
    remaining = []
    changed = False

    for job in jobs:
        if not isinstance(job, dict):
            changed = True
            continue

        before = (
            job.get("stage"),
            job.get("posted_at"),
            job.get("post_deadline"),
            job.get("doc_is_workshared"),
        )
        try:
            done = _process_startup_job(uiapp, job, now)
        except Exception:
            done = True

        if done:
            changed = True
            continue

        remaining.append(job)
        after = (
            job.get("stage"),
            job.get("posted_at"),
            job.get("post_deadline"),
            job.get("doc_is_workshared"),
        )
        if after != before:
            changed = True

    if changed or len(remaining) != len(jobs):
        state["jobs"] = remaining
        _save_startup_state(state)


def _process_startup_job(uiapp, job, now):
    stage = job.get("stage")
    target_doc = _resolve_doc_from_job(uiapp, job)
    active_doc = _get_active_doc_from_uiapp(uiapp)
    has_identity = _job_has_doc_identity(job)
    job_age = now - float(job.get("created_at", now))
    if job_age > _STARTUP_JOB_MAX_AGE_SEC:
        job["stage"] = "run_report"
        stage = "run_report"

    if stage == "show_workset_picker":
        if not job.get("open_worksets_after", False):
            job["stage"] = "run_report"
            return False

        candidate_doc = target_doc
        if not _is_doc_valid(candidate_doc) and not has_identity:
            candidate_doc = active_doc

        doc_is_workshared = job.get("doc_is_workshared")
        if doc_is_workshared is None and _is_doc_valid(candidate_doc):
            doc_is_workshared = bool(getattr(candidate_doc, "IsWorkshared", False))
            job["doc_is_workshared"] = doc_is_workshared

        if doc_is_workshared is False:
            job["stage"] = "run_report"
            return False

        if _is_doc_valid(target_doc) and not _is_same_doc(target_doc, active_doc):
            # Keep waiting for this document to become active before showing picker.
            _refresh_worksets_deadline(job, now)
            return False

        if not _is_doc_valid(candidate_doc):
            if now >= float(job.get("post_deadline", now)):
                job["stage"] = "run_report"
            return False

        if now >= float(job.get("post_deadline", now)):
            job["stage"] = "run_report"
            return False

        try:
            _show_workset_picker_for_doc(candidate_doc)
        except Exception as ex:
            logger = _get_logger()
            if logger:
                logger.warning("Queued workset picker failed: %s", ex)
        job["stage"] = "run_report"
        return False

    if stage == "run_report":
        if job.get("run_coord_report_after", False):
            report_doc = target_doc
            if not _is_doc_valid(report_doc) and not has_identity:
                report_doc = active_doc
            _print_coordination_review_report(report_doc)
        job["stage"] = "done"
        return True

    return True


def _load_startup_state():
    default_state = {"next_id": 1, "jobs": []}

    try:
        from pyrevit import script
        raw_state = script.get_envvar(_STARTUP_STATE_ENVVAR)
    except Exception:
        return default_state

    if not isinstance(raw_state, dict):
        return default_state

    state = dict(default_state)
    jobs = raw_state.get("jobs", [])
    if isinstance(jobs, list):
        state["jobs"] = [job for job in jobs if isinstance(job, dict)]

    try:
        next_id = int(raw_state.get("next_id", 1))
    except Exception:
        next_id = 1
    state["next_id"] = max(next_id, 1)

    return state


def _save_startup_state(state):
    try:
        from pyrevit import script
        script.set_envvar(_STARTUP_STATE_ENVVAR, state)
        return True
    except Exception:
        return False


def _load_file_open_trigger_state():
    default_state = {"pending": False, "created_at": 0.0}

    try:
        from pyrevit import script
        raw_state = script.get_envvar(_FILE_OPEN_TRIGGER_ENVVAR)
    except Exception:
        return default_state

    if not isinstance(raw_state, dict):
        return default_state

    state = dict(default_state)
    state["pending"] = bool(raw_state.get("pending", False))
    try:
        state["created_at"] = float(raw_state.get("created_at", 0.0))
    except Exception:
        state["created_at"] = 0.0
    if state["created_at"] < 0.0:
        state["created_at"] = 0.0
    return state


def _save_file_open_trigger_state(state):
    try:
        from pyrevit import script
        script.set_envvar(_FILE_OPEN_TRIGGER_ENVVAR, state)
        return True
    except Exception:
        return False


def _mark_file_open_trigger_pending():
    state = _load_file_open_trigger_state()
    state["pending"] = True
    state["created_at"] = time.time()
    _save_file_open_trigger_state(state)


def _clear_file_open_trigger_pending():
    state = {"pending": False, "created_at": 0.0}
    _save_file_open_trigger_state(state)


def _process_file_open_trigger_pending(uiapp=None):
    state = _load_file_open_trigger_state()
    if not state.get("pending", False):
        return

    now = time.time()
    try:
        created_at = float(state.get("created_at", 0.0))
    except Exception:
        created_at = 0.0
    if created_at <= 0.0:
        created_at = now
        state["created_at"] = created_at
        _save_file_open_trigger_state(state)

    if (now - created_at) > _FILE_OPEN_TRIGGER_MAX_AGE_SEC:
        _clear_file_open_trigger_pending()
        return

    uiapp = uiapp or _get_uiapp()
    if not uiapp:
        return

    active_doc = _get_active_doc_from_uiapp(uiapp)
    if not _is_doc_eligible_for_file_open(active_doc):
        return

    try:
        run_start_message_workflow(doc=active_doc, force=False)
        _clear_file_open_trigger_pending()
    except Exception as ex:
        logger = _get_logger()
        if logger:
            logger.warning("Deferred start-message run failed on file-open fallback: %s", ex)


def _next_startup_job_id(state):
    try:
        next_id = int(state.get("next_id", 1))
    except Exception:
        next_id = 1

    next_id = max(next_id, 1)
    state["next_id"] = next_id + 1
    return next_id


def _enqueue_startup_job(doc, open_worksets_after, run_coord_report_after):
    state = _load_startup_state()
    now = time.time()
    job_id = _next_startup_job_id(state)

    stage = "show_workset_picker" if open_worksets_after else "run_report"
    job = {
        "id": job_id,
        "doc_path": _get_doc_path(doc),
        "doc_title": _get_doc_title_for_key(doc),
        "doc_is_workshared": _get_doc_is_workshared(doc),
        "open_worksets_after": bool(open_worksets_after),
        "run_coord_report_after": bool(run_coord_report_after),
        "stage": stage,
        "created_at": now,
        "post_deadline": now + _WORKSETS_POST_TIMEOUT_SEC,
        "posted_at": None,
    }

    try:
        state_jobs = list(state.get("jobs", []))
    except Exception:
        state_jobs = []
    state_jobs.append(job)
    state["jobs"] = state_jobs

    try:
        saved = _save_startup_state(state)
        if not saved:
            return None
        return job_id
    except Exception:
        return None


def _resolve_doc_from_job(uiapp, job):
    if not uiapp or not isinstance(job, dict):
        return None

    app = getattr(uiapp, "Application", None)
    docs = getattr(app, "Documents", None)
    if docs is None:
        return None

    doc_path = _normalize_path(job.get("doc_path"))
    doc_title = _normalize_title(job.get("doc_title"))

    docs_list = []
    try:
        for doc in docs:
            docs_list.append(doc)
    except Exception:
        return None

    if doc_path:
        for doc in docs_list:
            if _normalize_path(_get_doc_path(doc)) == doc_path:
                return doc

    if doc_title:
        for doc in docs_list:
            if _normalize_title(_get_doc_title_for_key(doc)) == doc_title:
                return doc

    return None


def _job_has_doc_identity(job):
    if not isinstance(job, dict):
        return False
    if _normalize_path(job.get("doc_path")):
        return True
    if _normalize_title(job.get("doc_title")):
        return True
    return False


def _get_active_doc_from_uiapp(uiapp):
    if not uiapp:
        return None
    try:
        uidoc = uiapp.ActiveUIDocument
    except Exception:
        uidoc = None
    if uidoc is None:
        return None
    try:
        return uidoc.Document
    except Exception:
        return None


def _is_same_doc(doc_a, doc_b):
    if not _is_doc_valid(doc_a) or not _is_doc_valid(doc_b):
        return False
    try:
        if doc_a.Equals(doc_b):
            return True
    except Exception:
        pass
    path_a = _normalize_path(_get_doc_path(doc_a))
    path_b = _normalize_path(_get_doc_path(doc_b))
    if path_a and path_b:
        return path_a == path_b
    title_a = _normalize_title(_get_doc_title_for_key(doc_a))
    title_b = _normalize_title(_get_doc_title_for_key(doc_b))
    return bool(title_a and title_b and title_a == title_b)


def _get_doc_path(doc):
    if not _is_doc_valid(doc):
        return ""
    try:
        path = getattr(doc, "PathName", "") or ""
    except Exception:
        path = ""
    return _safe_text(path).strip()


def _get_doc_title_for_key(doc):
    if not _is_doc_valid(doc):
        return ""
    try:
        title = getattr(doc, "Title", "") or ""
    except Exception:
        title = ""
    return _safe_text(title).strip()


def _get_doc_is_workshared(doc):
    if not _is_doc_valid(doc):
        return None
    try:
        return bool(getattr(doc, "IsWorkshared", False))
    except Exception:
        return None


def _normalize_path(path_text):
    text = _safe_text(path_text).strip()
    if not text:
        return ""
    return text.lower()


def _normalize_title(title_text):
    text = _safe_text(title_text).strip()
    if not text:
        return ""
    return text.lower()


def _refresh_worksets_deadline(job, now):
    try:
        current = float(job.get("post_deadline", 0.0))
    except Exception:
        current = 0.0
    proposed = now + _WORKSETS_POST_TIMEOUT_SEC
    job["post_deadline"] = max(current, proposed)


def _show_workset_picker_for_doc(doc):
    """Show Active Workset picker and apply selected workset when possible."""
    context = _resolve_workset_picker_context(doc)
    shown, selected_workset_id = _show_workset_picker_dialog(context)
    if not shown:
        return False

    if selected_workset_id is None:
        return False

    if not context.get("can_apply", False):
        return False

    target_doc = context.get("doc")
    if not _is_doc_valid(target_doc):
        return False

    return _set_active_workset_id(target_doc, selected_workset_id)


def _resolve_workset_picker_context(doc):
    """Resolve picker context so empty states are explicit instead of silent."""
    context = {
        "doc": None,
        "is_doc_valid": False,
        "is_workshared": False,
        "worksets": [],
        "active_workset_id": None,
        "can_apply": False,
        "empty_reason": "",
    }

    if not _is_doc_valid(doc):
        context["empty_reason"] = "No valid document context."
        return context

    context["doc"] = doc
    context["is_doc_valid"] = True

    is_workshared = bool(_get_doc_is_workshared(doc))
    context["is_workshared"] = is_workshared
    if not is_workshared:
        context["empty_reason"] = "Document is not workshared."
        return context

    worksets = _collect_user_worksets(doc)
    context["worksets"] = worksets
    context["active_workset_id"] = _get_active_workset_id(doc)

    if not worksets:
        context["empty_reason"] = "No user worksets in this project."
        return context

    context["can_apply"] = True
    return context


def _collect_user_worksets(doc):
    if not _is_doc_valid(doc):
        return []

    try:
        from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind

        collector = FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)
        if hasattr(collector, "ToWorksets"):
            worksets = collector.ToWorksets()
        else:
            worksets = list(collector)
    except Exception:
        worksets = []

    rows = []
    for workset in worksets or []:
        ws_int = _to_int_elementid(getattr(workset, "Id", None))
        if ws_int is None:
            continue
        ws_name = _safe_text(getattr(workset, "Name", "")).strip()
        if not ws_name:
            ws_name = "Workset {}".format(ws_int)
        rows.append({"id": ws_int, "name": ws_name})

    rows.sort(key=lambda x: x.get("name", "").lower())
    return rows


def _get_active_workset_id(doc):
    if not _is_doc_valid(doc):
        return None

    try:
        table = doc.GetWorksetTable()
    except Exception:
        table = None

    if table is not None and hasattr(table, "GetActiveWorksetId"):
        try:
            return _to_int_elementid(table.GetActiveWorksetId())
        except Exception:
            pass

    try:
        from Autodesk.Revit.DB import WorksetTable
        return _to_int_elementid(WorksetTable.GetActiveWorksetId(doc))
    except Exception:
        return None


def _set_active_workset_id(doc, workset_id_int):
    if not _is_doc_valid(doc):
        return False

    try:
        from Autodesk.Revit.DB import WorksetId
        target_id = WorksetId(int(workset_id_int))
    except Exception:
        return False

    try:
        table = doc.GetWorksetTable()
    except Exception:
        table = None

    if table is not None and hasattr(table, "SetActiveWorksetId"):
        try:
            table.SetActiveWorksetId(target_id)
            return True
        except Exception:
            pass

    try:
        from Autodesk.Revit.DB import WorksetTable
        WorksetTable.SetActiveWorksetId(doc, target_id)
        return True
    except Exception:
        return False


def _show_workset_picker_dialog(context):
    """Show one modal picker and return (shown, selected_workset_id)."""
    return _show_workset_picker_dialog_wpfwindow(context)


def _show_workset_picker_dialog_wpfwindow(context):
    """Single pyRevit WPFWindow picker. Returns (shown, selected_id)."""
    worksets = list((context or {}).get("worksets") or [])
    active_ws_id = (context or {}).get("active_workset_id")
    can_apply = bool((context or {}).get("can_apply", False)) and bool(worksets)

    selected_index = -1
    if worksets:
        selected_index = 0
        for idx, row in enumerate(worksets):
            if row.get("id") == active_ws_id:
                selected_index = idx
                break

    try:
        import os
        from pyrevit import forms
    except Exception:
        logger = _get_logger()
        if logger:
            logger.warning("Workset picker window failed to load pyRevit WPF components.")
        return False, None

    xaml_path = os.path.join(os.path.dirname(__file__), "ui", "workset_picker.xaml")
    result = {"confirmed": False, "selected_id": None}

    class _WorksetPickerWindow(forms.WPFWindow):
        def __init__(self):
            forms.WPFWindow.__init__(self, xaml_path)
            self.Topmost = True
            self._can_apply = can_apply
            self._rows = worksets

            for row in self._rows:
                self.workset_cb.Items.Add(row.get("name", ""))

            if selected_index >= 0 and self.workset_cb.Items.Count > selected_index:
                self.workset_cb.SelectedIndex = selected_index
            else:
                self.workset_cb.SelectedIndex = -1

            self._sync_ok_state()
            self.workset_cb.SelectionChanged += self._on_selection_changed
            self.ok_btn.Click += self._on_ok_click
            self.cancel_btn.Click += self._on_cancel_click

        def _selected_index(self):
            try:
                return int(self.workset_cb.SelectedIndex)
            except Exception:
                return -1

        def _sync_ok_state(self):
            self.ok_btn.IsEnabled = bool(self._can_apply and self._selected_index() >= 0)

        def _on_selection_changed(self, sender, args):
            del sender, args
            self._sync_ok_state()

        def _on_ok_click(self, sender, args):
            del sender, args
            idx = self._selected_index()
            if idx < 0 or idx >= len(self._rows):
                return
            result["selected_id"] = self._rows[idx].get("id")
            result["confirmed"] = True
            self.Close()

        def _on_cancel_click(self, sender, args):
            del sender, args
            self.Close()

    try:
        window = _WorksetPickerWindow()
        window.ShowDialog()
    except Exception as ex:
        logger = _get_logger()
        if logger:
            logger.warning("Workset picker window failed to render: %s", ex)
        return False, None

    if result.get("confirmed"):
        return True, result.get("selected_id")
    return True, None


def _print_coordination_review_report(doc):
    report = _build_coordination_report(doc)
    if _show_coordination_review_dialog(report, doc=doc):
        return

    output = _get_output_window()

    if output:
        try:
            _set_output_always_on_top(output)
        except Exception:
            pass
        try:
            _render_report_html(output, report)
            return
        except Exception:
            pass

    _render_report_text(report)


def _show_coordination_review_dialog(report, doc=None):
    try:
        from rltools.coordination_review_window import show_coordination_review_dialog
    except Exception:
        return False

    try:
        return bool(show_coordination_review_dialog(report, doc=doc, uiapp=_get_uiapp()))
    except Exception:
        return False


def _set_output_always_on_top(output):
    if output is None:
        return

    try:
        output.show()
    except Exception:
        pass

    win = None
    try:
        win = output.window
    except Exception:
        win = None

    if win is None:
        return

    try:
        win.Topmost = True
    except Exception:
        pass

    try:
        win.Activate()
    except Exception:
        pass


def _build_coordination_report(doc):
    title = _get_doc_title(doc)
    link_map = _collect_link_instances(doc)

    grouped = {}
    total_matching_warnings = 0
    total_link_assignments = 0

    warnings = _get_document_warnings(doc)
    for warning in warnings:
        description = _clean_warning_text(_get_warning_description(warning))
        if "coordination review" not in description.lower():
            continue

        total_matching_warnings += 1

        failing_ids = _get_warning_failing_element_ids(warning)
        mapped_links, host_instance_ids = _resolve_warning_link_mapping(
            doc=doc,
            failing_ids=failing_ids,
            link_map=link_map,
        )

        target_links = mapped_links if mapped_links else set([_UNMAPPED_LINK_KEY])
        for link_key in target_links:
            warning_bucket = grouped.setdefault(link_key, {})
            row = warning_bucket.setdefault(
                description,
                {"count": 0, "instance_ids": set()},
            )
            row["count"] += 1
            row["instance_ids"].update(host_instance_ids)
            total_link_assignments += 1

    link_totals = {}
    for link_id in link_map.keys():
        link_totals[link_id] = _sum_link_warning_counts(grouped.get(link_id, {}))
    if _UNMAPPED_LINK_KEY in grouped:
        link_totals[_UNMAPPED_LINK_KEY] = _sum_link_warning_counts(grouped.get(_UNMAPPED_LINK_KEY, {}))

    return {
        "doc_title": title,
        "link_map": link_map,
        "grouped": grouped,
        "link_totals": link_totals,
        "total_matching_warnings": total_matching_warnings,
        "total_link_assignments": total_link_assignments,
    }


def _collect_link_instances(doc):
    link_map = {}
    if not _is_doc_valid(doc):
        return link_map

    try:
        from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
        instances = (
            FilteredElementCollector(doc)
            .OfClass(RevitLinkInstance)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        instances = []

    for inst in instances:
        try:
            link_id_int = _to_int_elementid(inst.Id)
        except Exception:
            link_id_int = None
        if link_id_int is None:
            continue

        name = _safe_text(getattr(inst, "Name", ""))
        if not name:
            name = "Link {}".format(link_id_int)

        link_map[link_id_int] = {
            "name": name,
            "element_id": inst.Id,
        }

    return link_map


def _get_document_warnings(doc):
    if not _is_doc_valid(doc):
        return []
    try:
        return list(doc.GetWarnings() or [])
    except Exception:
        return []


def _get_warning_description(warning):
    try:
        return warning.GetDescriptionText() or ""
    except Exception:
        return ""


def _get_warning_failing_element_ids(warning):
    try:
        failing = warning.GetFailingElements()
        return list(failing or [])
    except Exception:
        return []


def _resolve_warning_link_mapping(doc, failing_ids, link_map):
    """Map warning failing elements to link instance ids + host failing ids."""
    mapped_link_ids = set()
    host_instance_ids = set()

    if not _is_doc_valid(doc):
        return mapped_link_ids, host_instance_ids

    for failing_id in failing_ids:
        failing_id_int = _to_int_elementid(failing_id)
        if failing_id_int is not None:
            host_instance_ids.add(failing_id_int)

        elem = None
        try:
            elem = doc.GetElement(failing_id)
        except Exception:
            elem = None

        if elem is None:
            continue

        # Direct hit: the failing element itself is a RevitLinkInstance.
        if failing_id_int in link_map:
            mapped_link_ids.add(failing_id_int)

        # Copy/Monitor relation: host element can expose monitored link ids.
        if hasattr(elem, "GetMonitoredLinkElementIds"):
            try:
                monitored_link_ids = elem.GetMonitoredLinkElementIds()
            except Exception:
                monitored_link_ids = []
            for monitored_id in monitored_link_ids or []:
                monitored_id_int = _to_int_elementid(monitored_id)
                if monitored_id_int in link_map:
                    mapped_link_ids.add(monitored_id_int)

    return mapped_link_ids, host_instance_ids


def _sum_link_warning_counts(link_bucket):
    total = 0
    for row in link_bucket.values():
        total += int(row.get("count", 0))
    return total


def _render_report_html(output, report):
    if output is None or not hasattr(output, "print_html"):
        raise RuntimeError("pyRevit output html not available")

    link_map = report.get("link_map", {})
    grouped = report.get("grouped", {})
    link_totals = report.get("link_totals", {})
    total_matching = int(report.get("total_matching_warnings", 0))
    total_assignments = int(report.get("total_link_assignments", 0))

    html = []
    html.append("<h3>Coordination Review Summary</h3>")
    html.append("<p><b>Document:</b> {}</p>".format(_escape_html(report.get("doc_title", "(Unknown)"))))
    html.append("<p><b>Matching warnings:</b> {}<br/><b>Link assignments:</b> {}</p>".format(
        total_matching, total_assignments
    ))

    ordered_link_keys = sorted(link_map.keys(), key=lambda x: link_map[x]["name"].lower())
    if _UNMAPPED_LINK_KEY in grouped:
        ordered_link_keys.append(_UNMAPPED_LINK_KEY)

    if not ordered_link_keys:
        html.append("<p>No Revit links found in this document.</p>")

    for link_key in ordered_link_keys:
        is_unmapped = (link_key == _UNMAPPED_LINK_KEY)
        link_name = "Unmapped" if is_unmapped else _safe_text(link_map.get(link_key, {}).get("name", "Unknown Link"))
        link_total = int(link_totals.get(link_key, 0))
        html.append("<h4>{} ({})</h4>".format(_escape_html(link_name), link_total))

        warning_bucket = grouped.get(link_key, {})
        if not warning_bucket:
            html.append("<p>0 issue(s)</p>")
            continue

        html.append("<ul>")
        ordered_warning_keys = sorted(warning_bucket.keys(), key=lambda x: x.lower())
        for warning_text in ordered_warning_keys:
            row = warning_bucket.get(warning_text, {})
            count = int(row.get("count", 0))
            instance_ids = sorted(list(row.get("instance_ids", set())))
            total_instances = len(instance_ids)

            html.append("<li><b>{}</b>: {} issue(s)".format(_escape_html(warning_text), count))

            if total_instances:
                visible_ids = instance_ids[:_INSTANCE_LIST_CAP]
                hidden_count = total_instances - len(visible_ids)
                html.append("<details><summary>Element instances ({})</summary>".format(total_instances))
                html.append("<ul>")
                for instance_id in visible_ids:
                    html.append("<li>{}</li>".format(_format_instance_link_html(output, instance_id)))
                html.append("</ul>")
                if hidden_count > 0:
                    html.append("<p>(+{} more)</p>".format(hidden_count))
                html.append("</details>")
            html.append("</li>")
        html.append("</ul>")

    output.print_html("\n".join(html))


def _render_report_text(report):
    link_map = report.get("link_map", {})
    grouped = report.get("grouped", {})
    link_totals = report.get("link_totals", {})
    total_matching = int(report.get("total_matching_warnings", 0))
    total_assignments = int(report.get("total_link_assignments", 0))

    print("Coordination Review Summary")
    print("Document: {}".format(report.get("doc_title", "(Unknown)")))
    print("Matching warnings: {}".format(total_matching))
    print("Link assignments: {}".format(total_assignments))

    ordered_link_keys = sorted(link_map.keys(), key=lambda x: link_map[x]["name"].lower())
    if _UNMAPPED_LINK_KEY in grouped:
        ordered_link_keys.append(_UNMAPPED_LINK_KEY)

    if not ordered_link_keys:
        print("No Revit links found in this document.")
        return

    for link_key in ordered_link_keys:
        is_unmapped = (link_key == _UNMAPPED_LINK_KEY)
        link_name = "Unmapped" if is_unmapped else _safe_text(link_map.get(link_key, {}).get("name", "Unknown Link"))
        link_total = int(link_totals.get(link_key, 0))
        print("")
        print("{} ({})".format(link_name, link_total))

        warning_bucket = grouped.get(link_key, {})
        if not warning_bucket:
            print("  0 issue(s)")
            continue

        ordered_warning_keys = sorted(warning_bucket.keys(), key=lambda x: x.lower())
        for warning_text in ordered_warning_keys:
            row = warning_bucket.get(warning_text, {})
            count = int(row.get("count", 0))
            instance_ids = sorted(list(row.get("instance_ids", set())))
            total_instances = len(instance_ids)
            print("  - {}: {} issue(s)".format(warning_text, count))

            if total_instances:
                visible_ids = instance_ids[:_INSTANCE_LIST_CAP]
                hidden_count = total_instances - len(visible_ids)
                print("    Instances [{} shown / {} total]: {}".format(
                    len(visible_ids),
                    total_instances,
                    ", ".join(str(x) for x in visible_ids),
                ))
                if hidden_count > 0:
                    print("    (+{} more)".format(hidden_count))


def _format_instance_link_html(output, instance_id):
    if output is None:
        return _escape_html(str(instance_id))
    try:
        from Autodesk.Revit.DB import ElementId
        linked = output.linkify(ElementId(int(instance_id)))
        if linked:
            return linked
    except Exception:
        pass
    return _escape_html(str(instance_id))


def _escape_html(text):
    text = _safe_text(text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _clean_warning_text(text):
    cleaned = _safe_text(text).strip()
    return cleaned if cleaned else "(No description)"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _to_int_elementid(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id.IntegerValue)
    except Exception:
        pass
    try:
        return int(element_id.Value)
    except Exception:
        pass
    try:
        return int(element_id)
    except Exception:
        return None


def _get_doc_title(doc):
    if not _is_doc_valid(doc):
        return "(No active document)"
    try:
        title = getattr(doc, "Title", None)
        if title:
            return str(title)
    except Exception:
        pass
    return "(Unnamed document)"


def _is_doc_valid(doc):
    if doc is None:
        return False
    if hasattr(doc, "IsValidObject"):
        try:
            if not doc.IsValidObject:
                return False
        except Exception:
            return False
    return True


def _is_doc_eligible_for_file_open(doc):
    if not _is_doc_valid(doc):
        return False
    if getattr(doc, "IsFamilyDocument", False):
        return False
    if hasattr(doc, "IsLinked") and doc.IsLinked:
        return False
    return True


def _get_output_window():
    try:
        from pyrevit import script
        return script.get_output()
    except Exception:
        return None


def _get_logger():
    try:
        from pyrevit import script
        return script.get_logger()
    except Exception:
        return None


def _get_uiapp():
    try:
        return __revit__
    except Exception:
        pass

    try:
        from pyrevit import HOST_APP
        return HOST_APP.uiapp
    except Exception:
        return None


def _alert_wpf_with_bold(title, message):
    """Render a simple modal WPF dialog and interpret **bold** markers visually.
    Returns True if the dialog was shown successfully, False if any error occurs.
    """
    try:
        # (1) Ensure WPF assemblies are loaded for CPython/IronPython
        import clr
        clr.AddReference("PresentationFramework")
        clr.AddReference("PresentationCore")
        clr.AddReference("WindowsBase")

        # (2) Import the WPF types we need
        from System.Windows import Window, WindowStartupLocation, SizeToContent, HorizontalAlignment, Thickness
        from System.Windows.Controls import StackPanel, TextBlock, Button
        from System.Windows.Documents import Run, Bold, LineBreak

        # (3) Helper to add a text chunk that may contain single \n line breaks
        def _add_text_chunk(tb, text, make_bold=False):
            parts = text.split("\n")
            for idx, chunk in enumerate(parts):
                run = Run(chunk)
                tb.Inlines.Add(Bold(run) if make_bold else run)
                if idx < len(parts) - 1:
                    tb.Inlines.Add(LineBreak())

        # (4) Build a small window
        win = Window()
        win.Title = title
        win.SizeToContent = SizeToContent.WidthAndHeight
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        win.MinWidth = 440
        win.Topmost = True

        root = StackPanel()
        root.Margin = Thickness(20)

        # (5) Convert our mini-markdown ("**bold**") into WPF inlines
        for para in message.split("\n\n"):
            tb = TextBlock()
            tb.TextWrapping = True
            segments = para.split("**")
            for i, seg in enumerate(segments):
                _add_text_chunk(tb, seg, make_bold=(i % 2 == 1))
            if root.Children.Count > 0:
                tb.Margin = Thickness(0, 8, 0, 0)
            root.Children.Add(tb)

        # (6) OK button to close the dialog
        ok = Button()
        ok.Content = "OK"
        ok.MinWidth = 90
        ok.Margin = Thickness(0, 16, 0, 0)
        ok.HorizontalAlignment = HorizontalAlignment.Right

        def _close(sender, args):
            try:
                win.DialogResult = True
            except Exception:
                pass
            win.Close()

        ok.Click += _close

        root.Children.Add(ok)
        win.Content = root

        # (7) Show dialog modally; returns after user clicks OK
        win.ShowDialog()
        return True

    except Exception:
        return False

