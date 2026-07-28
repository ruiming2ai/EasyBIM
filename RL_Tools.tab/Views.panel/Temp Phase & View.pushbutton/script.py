# -*- coding: utf-8 -*-
"""Temp Phase command entrypoint."""

from __future__ import print_function

from rltools import temp_phase_view


TITLE = "Temp Phase"


def _show_alert(message):
    temp_phase_view._show_alert(TITLE, message)


def _show_phase_picker(doc, view, current_phase_id):
    phases = temp_phase_view._collect_document_phases(doc)
    if not phases:
        _show_alert("No phases are available in this document.")
        return None

    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as WinForms
    except Exception:
        _show_alert("Could not load Windows Forms UI. Phase selection cancelled.")
        return None

    form = WinForms.Form()
    form.Text = TITLE
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.Width = 470
    form.Height = 230
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.MinimizeBox = False

    info = WinForms.Label()
    info.Left = 14
    info.Top = 14
    info.Width = 430
    info.Height = 44
    info.Text = "Select Temporary Phase for view:\n{}".format(
        temp_phase_view._safe_text(getattr(view, "Name", ""))
    )
    form.Controls.Add(info)

    combo = WinForms.ComboBox()
    combo.Left = 14
    combo.Top = 72
    combo.Width = 430
    combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList

    phase_ids = []
    selected_index = 0
    for idx, phase_data in enumerate(phases):
        combo.Items.Add("{} ({})".format(phase_data["name"], phase_data["id"]))
        phase_ids.append(int(phase_data["id"]))
        if int(phase_data["id"]) == int(current_phase_id):
            selected_index = idx

    if phase_ids:
        combo.SelectedIndex = selected_index
    form.Controls.Add(combo)

    ok_btn = WinForms.Button()
    ok_btn.Text = "OK"
    ok_btn.DialogResult = WinForms.DialogResult.OK
    ok_btn.Left = 270
    ok_btn.Top = 132
    ok_btn.Width = 84
    form.Controls.Add(ok_btn)

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 360
    cancel_btn.Top = 132
    cancel_btn.Width = 84
    form.Controls.Add(cancel_btn)

    form.AcceptButton = ok_btn
    form.CancelButton = cancel_btn

    result = form.ShowDialog()
    if result != WinForms.DialogResult.OK:
        return None

    index = int(combo.SelectedIndex)
    if index < 0 or index >= len(phase_ids):
        return None
    return int(phase_ids[index])


def main():
    uiapp = temp_phase_view._get_uiapp()
    if uiapp is None:
        _show_alert("Revit UI context is not available.")
        return

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    view = getattr(uidoc, "ActiveView", None) if uidoc else None

    if not temp_phase_view._is_doc_supported(doc):
        _show_alert("Open a project document to use this tool.")
        return
    if not temp_phase_view._is_view_valid(view):
        _show_alert("Open a valid project view to use this tool.")
        return

    doc_key = temp_phase_view._doc_key(doc)
    view_id_int = temp_phase_view._eid_to_int(getattr(view, "Id", None))
    if view_id_int is None:
        _show_alert("Could not read active view id.")
        return

    view_key = temp_phase_view._view_key(doc_key, view_id_int)
    state = temp_phase_view._get_state()
    existing = state["view_sessions"].get(view_key)
    original_phase_id = temp_phase_view._phase_from_session_or_view(existing, view)
    if original_phase_id is None:
        _show_alert("Active view does not support editable Phase.")
        return

    selected_phase_id = _show_phase_picker(doc, view, original_phase_id)
    if selected_phase_id is None:
        return

    template_id = temp_phase_view._template_from_session_or_view(existing, view)
    ok = temp_phase_view._apply_selected_phase_transaction(
        doc=doc,
        view=view,
        selected_phase_id=selected_phase_id,
        template_id=template_id,
    )
    if not ok:
        _show_alert(
            "Could not apply the selected temporary phase while preserving Temporary View Properties."
        )
        return

    doc_runtime_id = temp_phase_view._get_doc_runtime_id(doc)
    state["view_sessions"][view_key] = {
        "doc_key": doc_key,
        "doc_runtime_id": int(doc_runtime_id) if doc_runtime_id is not None else None,
        "view_id": int(view_id_int),
        "view_name": temp_phase_view._safe_text(getattr(view, "Name", "")),
        "original_phase_id": int(original_phase_id),
        "selected_phase_id": int(selected_phase_id),
        "temp_template_id": int(template_id) if template_id is not None else None,
        "updated_at": temp_phase_view.time.time(),
    }
    state["last_seen_tvp"][view_key] = bool(temp_phase_view._is_tvp_active(view))
    temp_phase_view._save_state(state)


main()
