# -*- coding: utf-8 -*-
"""
RL Tools messages.py

This module centralizes the startup message and helpers.  Both your
`doc-opened.py` hook and the ribbon button call into `show_start_message`
to present the onboarding reminder.  It also optionally opens the native
Revit Worksets dialog after the alert and can print a Coordination Review
summary report.

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
    open_worksets_after (bool): When True, the native Worksets dialog will
        open automatically after the user closes the alert.
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

# Timeout while waiting for Worksets command to become postable.
_WORKSETS_POST_TIMEOUT_SEC = 10.0
# Idling resumes only after modal Worksets closes. A short delay prevents
# running report on the same cycle as command posting.
_WORKSETS_POST_SETTLE_SEC = 0.15

_STARTUP_JOBS = {}
_STARTUP_JOB_SEQ = 0
_IDLING_HANDLER_ATTACHED = False


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

    # 4) Optionally queue startup follow-up actions after user closes dialog.
    if shown and (open_worksets_after or run_coord_report_after):
        _enqueue_startup_actions(
            doc=doc,
            open_worksets_after=open_worksets_after,
            run_coord_report_after=run_coord_report_after,
        )


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
    """Queue post-dialog actions and process them in UIApplication.Idling.

    This avoids command-race timing right after document open.
    """
    global _STARTUP_JOB_SEQ

    _STARTUP_JOB_SEQ += 1
    job_id = _STARTUP_JOB_SEQ

    stage = "post_worksets" if open_worksets_after else "run_report"
    now = time.time()
    _STARTUP_JOBS[job_id] = {
        "id": job_id,
        "doc": doc,
        "open_worksets_after": bool(open_worksets_after),
        "run_coord_report_after": bool(run_coord_report_after),
        "stage": stage,
        "created_at": now,
        "post_deadline": now + _WORKSETS_POST_TIMEOUT_SEC,
        "posted_at": None,
    }

    if _ensure_idling_handler():
        return

    # Fallback path if idling could not be hooked in current environment.
    if open_worksets_after:
        _open_worksets_dialog_safely()
    if run_coord_report_after:
        _print_coordination_review_report(doc)
    _STARTUP_JOBS.pop(job_id, None)


def _ensure_idling_handler():
    global _IDLING_HANDLER_ATTACHED

    if _IDLING_HANDLER_ATTACHED:
        return True

    uiapp = _get_uiapp()
    if not uiapp:
        return False

    try:
        uiapp.Idling += _on_uiapp_idling
        _IDLING_HANDLER_ATTACHED = True
        return True
    except Exception:
        return False


def _detach_idling_handler():
    global _IDLING_HANDLER_ATTACHED

    if not _IDLING_HANDLER_ATTACHED:
        return

    uiapp = _get_uiapp()
    if not uiapp:
        _IDLING_HANDLER_ATTACHED = False
        return

    try:
        uiapp.Idling -= _on_uiapp_idling
    except Exception:
        pass
    _IDLING_HANDLER_ATTACHED = False


def _on_uiapp_idling(sender, args):
    now = time.time()
    done_job_ids = []
    for job_id, job in list(_STARTUP_JOBS.items()):
        try:
            if _process_startup_job(sender, job, now):
                done_job_ids.append(job_id)
        except Exception:
            done_job_ids.append(job_id)

    for job_id in done_job_ids:
        _STARTUP_JOBS.pop(job_id, None)

    if not _STARTUP_JOBS:
        _detach_idling_handler()


def _process_startup_job(uiapp, job, now):
    stage = job.get("stage")
    doc = job.get("doc")

    if stage == "post_worksets":
        if _try_post_worksets_command(uiapp):
            job["stage"] = "wait_after_worksets"
            job["posted_at"] = now
            return False

        if now >= job.get("post_deadline", now):
            job["stage"] = "run_report"
        return False

    if stage == "wait_after_worksets":
        posted_at = job.get("posted_at", now)
        if (now - posted_at) >= _WORKSETS_POST_SETTLE_SEC:
            job["stage"] = "run_report"
        return False

    if stage == "run_report":
        if job.get("run_coord_report_after", False):
            _print_coordination_review_report(doc)
        job["stage"] = "done"
        return True

    return True


def _try_post_worksets_command(uiapp):
    """Try posting Worksets command; returns True only if command was posted."""
    try:
        from Autodesk.Revit.UI import RevitCommandId, PostableCommand

        if uiapp is None:
            return False

        cmd_id = RevitCommandId.LookupPostableCommandId(PostableCommand.Worksets)
        if not cmd_id:
            return False

        can_post = True
        if hasattr(uiapp, "CanPostCommand"):
            try:
                can_post = bool(uiapp.CanPostCommand(cmd_id))
            except Exception:
                can_post = True

        if not can_post:
            return False

        uiapp.PostCommand(cmd_id)
        return True
    except Exception:
        return False


def _print_coordination_review_report(doc):
    report = _build_coordination_report(doc)
    output = _get_output_window()

    if output:
        try:
            _render_report_html(output, report)
            return
        except Exception:
            pass

    _render_report_text(report)


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


def _get_output_window():
    try:
        from pyrevit import script
        return script.get_output()
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


def _open_worksets_dialog_safely():
    """Open the native Revit Worksets dialog by posting the built-in command.
    We guard in a try/except so a missing command never crashes your script.
    """
    try:
        from Autodesk.Revit.UI import RevitCommandId, PostableCommand
        app = _get_uiapp()
        if app is None:
            return
        cmd_id = RevitCommandId.LookupPostableCommandId(PostableCommand.Worksets)
        if cmd_id:
            app.PostCommand(cmd_id)
    except Exception:
        # If this ever fails (older Revit, custom environment), we simply skip.
        pass

