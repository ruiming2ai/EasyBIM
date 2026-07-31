# -*- coding: utf-8 -*-
"""Temp Phase manual command runtime.

The button is intentionally self-contained Python.  It records the original
view state in the EasyBIM pyRevit environment so the Python close hooks can
restore it later; no controller DLL is required for the normal pyRevit path.
"""

from __future__ import print_function

import os
import time

try:
    from pyrevit import script
except Exception:
    script = None


TITLE = "Temp Phase"
STATE_ENVVAR = "EASYBIM_TEMP_PHASE_VIEW_STATE"


class _NullLogger(object):
    def debug(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


try:
    LOGGER = script.get_logger() if script is not None else _NullLogger()
except Exception:
    LOGGER = _NullLogger()


def run_pushbutton(command_script_path=""):
    """Run the manual Temp Phase workflow from the pyRevit button."""
    _log("PythonCommandBegin scriptPath={0}".format(_safe_text(command_script_path)))

    uiapp = _get_uiapp()
    if uiapp is None:
        _show_alert(TITLE, "Revit UI context is not available.")
        _log("PythonCommandNoUiApp")
        return

    uidoc = getattr(uiapp, "ActiveUIDocument", None)
    doc = getattr(uidoc, "Document", None) if uidoc else None
    view = getattr(uidoc, "ActiveView", None) if uidoc else None

    if not _is_doc_supported(doc):
        _show_alert(TITLE, "Open a project document to use this tool.")
        _log("PythonCommandNoProjectDocument")
        return

    if not _is_view_valid(view):
        _show_alert(TITLE, "Open a valid project view to use this tool.")
        _log("PythonCommandInvalidView")
        return

    doc_key = _doc_key(doc)
    view_id_int = _eid_to_int(getattr(view, "Id", None))
    if view_id_int is None:
        _show_alert(TITLE, "Could not read active view id.")
        _log("PythonCommandMissingViewId")
        return

    view_key = _view_key(doc_key, view_id_int)
    state = _get_state()
    existing = state["view_sessions"].get(view_key)
    original_phase_id = _phase_from_session_or_view(existing, view)
    if original_phase_id is None:
        _show_alert(TITLE, "Active view does not support editable Phase.")
        _log("PythonCommandMissingOriginalPhase view={0}".format(_view_display_name(view)))
        return

    _log(
        "PythonPhasePickerOpening view={0} originalPhase={1}".format(
            _view_display_name(view),
            original_phase_id,
        )
    )
    selected_phase_id = _show_phase_picker(doc, view, original_phase_id)
    _log(
        "PythonPhasePickerClosed selectedPhase={0}".format(
            "" if selected_phase_id is None else selected_phase_id
        )
    )
    if selected_phase_id is None:
        return

    template_id = _template_from_session_or_view(existing, view)
    ok = _apply_selected_phase_transaction(
        doc=doc,
        view=view,
        selected_phase_id=selected_phase_id,
        template_id=template_id,
    )
    if not ok:
        _show_alert(
            TITLE,
            "Could not apply the selected temporary phase while preserving Temporary View Properties.",
        )
        _log("PythonApplyFailed view={0}".format(_view_display_name(view)))
        return

    doc_runtime_id = _get_doc_runtime_id(doc)
    state["view_sessions"][view_key] = {
        "doc_key": doc_key,
        "doc_runtime_id": int(doc_runtime_id) if doc_runtime_id is not None else None,
        "view_id": int(view_id_int),
        "view_name": _view_display_name(view),
        "original_phase_id": int(original_phase_id),
        "selected_phase_id": int(selected_phase_id),
        "temp_template_id": int(template_id) if template_id is not None else None,
        "updated_at": time.time(),
    }
    state["last_seen_tvp"][view_key] = bool(_is_tvp_active(view))
    _save_state(state)

    _log(
        "PythonApplySuccess view={0} selectedPhase={1} sessionRecorded={2}".format(
            _view_display_name(view),
            selected_phase_id,
            True,
        )
    )


def log_command_context(command_script_path=""):
    """Record the physical pyRevit script path that Revit executed."""
    extension_root = _find_extension_root(command_script_path) or _find_extension_root(__file__)
    _log(
        "PythonCommandContext scriptPath={0} modulePath={1} extensionRoot={2}".format(
            _safe_text(command_script_path),
            _safe_text(__file__),
            _safe_text(extension_root),
        )
    )


def _phase_from_session_or_view(existing, view):
    if isinstance(existing, dict) and existing.get("original_phase_id") is not None:
        try:
            return int(existing.get("original_phase_id"))
        except Exception:
            pass
    return _get_view_phase_id(view)


def _template_from_session_or_view(existing, view):
    if isinstance(existing, dict) and existing.get("temp_template_id") is not None:
        try:
            return int(existing.get("temp_template_id"))
        except Exception:
            pass

    template_id = _get_temp_template_reference_id(view)
    if template_id is not None:
        return int(template_id)

    return _eid_to_int(getattr(view, "Id", None))


def _apply_selected_phase_transaction(doc, view, selected_phase_id, template_id):
    DB = _get_db()
    if DB is None:
        return False

    tx = DB.Transaction(doc, "Temp Phase & View: Apply Phase")
    started = False
    try:
        tx.Start()
        started = True

        if _is_tvp_active(view):
            _disable_tvp(view)

        if not _set_view_phase_id(view, selected_phase_id):
            raise Exception("Failed setting VIEW_PHASE")

        if not _enable_tvp(view, template_id):
            raise Exception("Failed re-enabling Temporary View Properties")

        tx.Commit()
        _log(
            "PythonApplyTransactionCommitted view={0} phase={1}".format(
                _view_display_name(view),
                selected_phase_id,
            )
        )
        return True
    except Exception as ex:
        LOGGER.debug("Temp Phase apply failed: %s", ex)
        _log("PythonApplyTransactionException {0}".format(_exception_text(ex)))
        if started:
            _rollback_transaction(tx)
        return False


def _show_phase_picker(doc, view, current_phase_id):
    phases = _collect_document_phases(doc)
    if not phases:
        _show_alert(TITLE, "No phases are available in this document.")
        return None

    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System.Windows.Forms as WinForms
    except Exception as ex:
        _log("PythonPhasePickerLoadException {0}".format(_exception_text(ex)))
        _show_alert(TITLE, "Could not load Windows Forms UI. Phase selection cancelled.")
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
    info.Text = "Select Temporary Phase for view:\n{0}".format(_view_display_name(view))
    form.Controls.Add(info)

    combo = WinForms.ComboBox()
    combo.Left = 14
    combo.Top = 72
    combo.Width = 430
    combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList

    phase_ids = []
    selected_index = 0
    for index, phase_data in enumerate(phases):
        combo.Items.Add("{0} ({1})".format(phase_data["name"], phase_data["id"]))
        phase_ids.append(int(phase_data["id"]))
        if int(phase_data["id"]) == int(current_phase_id):
            selected_index = index

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


def _collect_document_phases(doc):
    result = []
    if not _is_doc_valid(doc):
        return result

    try:
        for phase in doc.Phases:
            phase_id = _eid_to_int(getattr(phase, "Id", None))
            if phase_id is None:
                continue

            result.append(
                {
                    "id": int(phase_id),
                    "name": _safe_text(getattr(phase, "Name", "")) or "Phase {0}".format(phase_id),
                }
            )
    except Exception:
        return []

    return result


def _get_view_phase_param(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return None

    try:
        return view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
    except Exception:
        return None


def _get_view_phase_id(view):
    param = _get_view_phase_param(view)
    if param is None:
        return None

    try:
        return _eid_to_int(param.AsElementId())
    except Exception:
        return None


def _set_view_phase_id(view, phase_id_int):
    if phase_id_int is None:
        return False

    param = _get_view_phase_param(view)
    if param is None:
        return False

    try:
        return bool(param.Set(_int_to_eid(phase_id_int)))
    except Exception:
        return False


def _get_temp_template_reference_id(view):
    if not _is_view_valid(view):
        return None

    getter_names = (
        "GetTemporaryViewPropertiesId",
        "GetTemporaryViewPropertiesViewId",
    )
    prop_names = (
        "TemporaryViewPropertiesId",
        "TemporaryViewPropertiesViewId",
    )

    for getter_name in getter_names:
        getter = getattr(view, getter_name, None)
        if callable(getter):
            try:
                value_int = _eid_to_int(getter())
                if _is_valid_id(value_int):
                    return int(value_int)
            except Exception:
                continue

    for prop_name in prop_names:
        try:
            value = getattr(view, prop_name)
        except Exception:
            value = None
        value_int = _eid_to_int(value)
        if _is_valid_id(value_int):
            return int(value_int)

    return None


def _is_tvp_active(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return False

    try:
        if hasattr(view, "IsTemporaryViewModeEnabled"):
            return bool(view.IsTemporaryViewModeEnabled(DB.TemporaryViewMode.TemporaryViewProperties))
    except Exception:
        pass

    try:
        legacy = getattr(view, "IsTemporaryViewPropertiesModeEnabled", None)
        if callable(legacy):
            return bool(legacy())
    except Exception:
        pass

    return False


def _disable_tvp(view):
    DB = _get_db()
    if DB is None or not _is_view_valid(view):
        return False

    try:
        view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryViewProperties)
        return True
    except Exception:
        return False


def _enable_tvp(view, template_id):
    if not _is_view_valid(view):
        return False

    target_ids = []
    if template_id is not None:
        target_ids.append(_int_to_eid(template_id))
    target_ids.append(getattr(view, "Id", None))

    for target_id in target_ids:
        if target_id is None:
            continue
        try:
            view.EnableTemporaryViewPropertiesMode(target_id)
            return True
        except Exception:
            continue

    return False


def _show_alert(title, message):
    UI = _get_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(title, message)
            return
        except Exception:
            pass

    print("[{0}] {1}".format(title, message))


def _view_key(doc_key, view_id_int):
    return "{0}|{1}".format(doc_key, int(view_id_int))


def _doc_key(doc):
    if not _is_doc_valid(doc):
        return ""

    try:
        path = _safe_text(doc.PathName).strip()
    except Exception:
        path = ""

    if path:
        return "path|{0}".format(path.lower())

    try:
        hash_code = _safe_text(doc.GetHashCode())
    except Exception:
        hash_code = _safe_text(id(doc))

    title = _safe_text(getattr(doc, "Title", "")).strip().lower()
    return "mem|{0}|{1}".format(title, hash_code)


def _get_doc_runtime_id(doc):
    if not _is_doc_valid(doc):
        return None

    for attr_name in ("DocumentId", "Id"):
        try:
            value = getattr(doc, attr_name)
        except Exception:
            value = None
        value_int = _to_int(value)
        if value_int is not None:
            return value_int

    try:
        return _to_int(doc.GetHashCode())
    except Exception:
        return None


def _is_doc_supported(doc):
    if not _is_doc_valid(doc):
        return False

    try:
        if bool(doc.IsFamilyDocument):
            return False
    except Exception:
        pass

    try:
        if bool(doc.IsLinked):
            return False
    except Exception:
        pass

    return True


def _is_doc_valid(doc):
    if doc is None:
        return False

    try:
        return bool(doc.IsValidObject)
    except Exception:
        return True


def _is_view_valid(view):
    if view is None:
        return False

    try:
        if not bool(view.IsValidObject):
            return False
    except Exception:
        pass

    try:
        if bool(view.IsTemplate):
            return False
    except Exception:
        pass

    return True


def _view_display_name(view):
    return _safe_text(getattr(view, "Name", "")).strip()


def _rollback_transaction(tx):
    try:
        tx.RollBack()
        return
    except Exception:
        pass

    try:
        tx.Rollback()
    except Exception:
        pass


def _get_state():
    if script is not None:
        try:
            state = script.get_envvar(STATE_ENVVAR)
        except Exception:
            state = None
    else:
        state = None

    if not isinstance(state, dict):
        state = {}

    state.setdefault("last_seen_tvp", {})
    state.setdefault("view_sessions", {})

    if not isinstance(state.get("last_seen_tvp"), dict):
        state["last_seen_tvp"] = {}
    if not isinstance(state.get("view_sessions"), dict):
        state["view_sessions"] = {}

    return state


def _save_state(state):
    if script is None:
        return

    try:
        script.set_envvar(STATE_ENVVAR, state)
    except Exception as ex:
        _log("PythonStateSaveException {0}".format(_exception_text(ex)))


def _get_uiapp(uiapp=None):
    if uiapp is not None:
        return uiapp

    try:
        return __revit__
    except Exception:
        return None


def _get_db():
    try:
        from Autodesk.Revit import DB

        return DB
    except Exception:
        return None


def _get_ui():
    try:
        from Autodesk.Revit import UI

        return UI
    except Exception:
        return None


def _get_revit_version(uiapp):
    try:
        return _safe_text(uiapp.Application.VersionNumber).strip()
    except Exception:
        return "unknown"


def _eid_to_int(element_id):
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


def _to_int(value):
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def _int_to_eid(value):
    DB = _get_db()
    if DB is None:
        return None

    try:
        return DB.ElementId(int(value))
    except Exception:
        return None


def _is_valid_id(value):
    if value is None:
        return False

    try:
        return int(value) > 0
    except Exception:
        return False


def _find_extension_root(start_path):
    path = _safe_text(start_path)
    if not path:
        return ""

    try:
        directory = path if os.path.isdir(path) else os.path.dirname(path)
        while directory:
            if os.path.isfile(os.path.join(directory, "Extension.yaml")):
                return directory
            if directory.lower().endswith(".extension"):
                return directory
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
    except Exception:
        return ""

    return ""


def _log(message):
    text = "{0} {1}".format(_timestamp(), _safe_text(message))

    try:
        LOGGER.debug(_safe_text(message))
    except Exception:
        pass

    try:
        log_path = _get_log_path()
        log_dir = os.path.dirname(log_path)
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        with open(log_path, "a") as stream:
            stream.write(text + os.linesep)
    except Exception:
        pass


def _get_log_path():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "EasyBIM", "Temp Phase", "logs", "events.log")


def _timestamp():
    try:
        import datetime

        return datetime.datetime.now().isoformat()
    except Exception:
        return _safe_text(time.time())


def _exception_text(exception):
    if exception is None:
        return ""

    text = "{0}: {1}".format(exception.__class__.__name__, _safe_text(exception))
    inner = getattr(exception, "InnerException", None)
    if inner is not None:
        text += " | Inner: {0}".format(_exception_text(inner))
    return text


def _safe_text(value):
    if value is None:
        return ""

    try:
        return str(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return ""
