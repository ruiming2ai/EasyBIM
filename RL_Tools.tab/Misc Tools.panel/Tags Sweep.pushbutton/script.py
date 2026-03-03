# -*- coding: utf-8 -*-
"""Find orphaned tags (missing hosts) and optionally delete them."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines
import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

__title__ = "Tags Sweep"

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()


def _eid_int(eid):
    if eid is None:
        return None
    try:
        return int(get_elementid_value(eid))
    except Exception:
        try:
            return int(eid.IntegerValue)
        except Exception:
            return None


def _is_invalid_eid(eid):
    if eid is None:
        return True
    try:
        return eid == DB.ElementId.InvalidElementId
    except Exception:
        return True


def _safe_doc_get(doc, eid):
    if _is_invalid_eid(eid):
        return None
    try:
        return doc.GetElement(eid)
    except Exception:
        return None


def _safe_call(obj, method_name, default=None):
    try:
        method = getattr(obj, method_name, None)
        if method is None:
            return default
        return method()
    except Exception:
        return default


def _best_effort_type_name(tag, doc):
    try:
        tid = tag.GetTypeId()
        t = _safe_doc_get(doc, tid)
        if t is not None:
            n = getattr(t, "Name", None)
            if n:
                return str(n)
    except Exception:
        pass

    try:
        n = getattr(tag, "Name", None)
        if n:
            return str(n)
    except Exception:
        pass

    return "<Unknown Type>"


def _best_effort_view_name(tag, doc):
    try:
        vid = getattr(tag, "OwnerViewId", None)
        v = _safe_doc_get(doc, vid)
        if v is not None:
            return "{} ({})".format(v.Name, _eid_int(vid))
    except Exception:
        pass
    return "<No Owner View>"


def _best_effort_category(tag):
    try:
        cat = getattr(tag, "Category", None)
        if cat is not None and getattr(cat, "Name", None):
            return str(cat.Name)
    except Exception:
        pass
    return "<No Category>"


def _tagged_element_missing_independent(tag, doc):
    # 1) Direct tagged element id (many cases)
    try:
        host_id = _safe_call(tag, "GetTaggedLocalElementId", default=None)
        if host_id is not None:
            return _safe_doc_get(doc, host_id) is None
    except Exception:
        pass

    # 2) Multiple references
    refs = _safe_call(tag, "GetTaggedReferences", default=None)
    if refs:
        found_valid = False
        for ref in refs:
            if ref is None:
                continue
            try:
                linked_id = getattr(ref, "LinkedElementId", None)
                if linked_id and not _is_invalid_eid(linked_id):
                    found_valid = True
                    continue
            except Exception:
                pass

            try:
                host_id = getattr(ref, "ElementId", None)
                if host_id and not _is_invalid_eid(host_id):
                    if _safe_doc_get(doc, host_id) is not None:
                        found_valid = True
                        continue
            except Exception:
                pass

        return not found_valid

    # 3) Fallback property checks used on older APIs
    fallback_eids = []
    for pname in ("TaggedElementId", "TaggedLocalElementId", "HostElementId"):
        try:
            val = getattr(tag, pname, None)
            if isinstance(val, DB.ElementId):
                fallback_eids.append(val)
            elif val is not None:
                eid = getattr(val, "HostElementId", None)
                if isinstance(eid, DB.ElementId):
                    fallback_eids.append(eid)
        except Exception:
            continue

    if fallback_eids:
        for eid in fallback_eids:
            if _safe_doc_get(doc, eid) is not None:
                return False
        return True

    # If API gives us no host data, keep conservative (not orphan) to avoid false delete.
    return False


def _tagged_element_missing_spatial(tag, doc):
    # Spatial tags usually expose one host-ish property.
    candidates = []
    for pname in ("Room", "Space", "Area", "TaggedElementId", "HostElementId"):
        try:
            val = getattr(tag, pname, None)
            if val is None:
                continue
            if isinstance(val, DB.ElementId):
                candidates.append(val)
            else:
                try:
                    # Some APIs return element directly
                    elem_id = getattr(val, "Id", None)
                    if isinstance(elem_id, DB.ElementId):
                        candidates.append(elem_id)
                except Exception:
                    pass
        except Exception:
            continue

    if candidates:
        for eid in candidates:
            if _safe_doc_get(doc, eid) is not None:
                return False
        return True

    # Some spatial tags have IsOrphaned flag
    try:
        val = getattr(tag, "IsOrphaned", None)
        if isinstance(val, bool):
            return val
    except Exception:
        pass

    return False


def _collect_orphan_tags(doc):
    orphans = []
    orphan_ids = set()

    # IndependentTag
    try:
        indep_tags = DB.FilteredElementCollector(doc) \
            .OfClass(DB.IndependentTag) \
            .WhereElementIsNotElementType() \
            .ToElements()
    except Exception:
        indep_tags = []

    for tag in indep_tags:
        try:
            if _tagged_element_missing_independent(tag, doc):
                eid_int = _eid_int(tag.Id)
                if eid_int is not None and eid_int not in orphan_ids:
                    orphan_ids.add(eid_int)
                    orphans.append(tag)
        except Exception as ex:
            logger.debug("IndependentTag host-check failed | id=%s | %s", _eid_int(tag.Id), ex)

    # SpatialElementTag descendants (room/space/area tags)
    try:
        spatial_tags = DB.FilteredElementCollector(doc) \
            .OfClass(DB.SpatialElementTag) \
            .WhereElementIsNotElementType() \
            .ToElements()
    except Exception:
        spatial_tags = []

    for tag in spatial_tags:
        try:
            if _tagged_element_missing_spatial(tag, doc):
                eid_int = _eid_int(tag.Id)
                if eid_int is not None and eid_int not in orphan_ids:
                    orphan_ids.add(eid_int)
                    orphans.append(tag)
        except Exception as ex:
            logger.debug("SpatialElementTag host-check failed | id=%s | %s", _eid_int(tag.Id), ex)

    return orphans


def _build_display_rows(tags, doc):
    rows = []
    for tag in tags:
        eid = _eid_int(tag.Id)
        row = "{0:<28} | {1:<30} | ID {2:<10} | {3}".format(
            _best_effort_category(tag),
            _best_effort_type_name(tag, doc),
            eid,
            _best_effort_view_name(tag, doc),
        )
        rows.append(row)
    return rows


def _show_results_dialog(rows, count):
    dialog = WinForms.Form()
    dialog.Text = __title__
    dialog.Width = 980
    dialog.Height = 560
    dialog.StartPosition = WinForms.FormStartPosition.CenterScreen
    dialog.FormBorderStyle = WinForms.FormBorderStyle.Sizable
    dialog.MinimizeBox = False
    dialog.MaximizeBox = True

    title_lbl = WinForms.Label()
    title_lbl.Left = 16
    title_lbl.Top = 14
    title_lbl.Width = 930
    title_lbl.Height = 22
    title_lbl.Text = "Found {} orphaned tags. Review list below.".format(count)

    list_box = WinForms.ListBox()
    list_box.Left = 16
    list_box.Top = 40
    list_box.Width = 930
    list_box.Height = 440
    list_box.HorizontalScrollbar = True
    list_box.SelectionMode = WinForms.SelectionMode.One

    for row in rows:
        list_box.Items.Add(row)

    delete_btn = WinForms.Button()
    delete_btn.Text = "Delete"
    delete_btn.DialogResult = WinForms.DialogResult.OK
    delete_btn.Left = 770
    delete_btn.Top = 492
    delete_btn.Width = 84

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 862
    cancel_btn.Top = 492
    cancel_btn.Width = 84

    dialog.Controls.Add(title_lbl)
    dialog.Controls.Add(list_box)
    dialog.Controls.Add(delete_btn)
    dialog.Controls.Add(cancel_btn)

    dialog.AcceptButton = delete_btn
    dialog.CancelButton = cancel_btn

    return dialog.ShowDialog() == WinForms.DialogResult.OK


def _delete_orphans(doc, tags):
    deleted = 0
    failed = 0
    samples = []

    with revit.Transaction("Tags Sweep - Delete Orphan Tags"):
        for tag in tags:
            eid = tag.Id
            eid_int = _eid_int(eid)
            try:
                doc.Delete(eid)
                deleted += 1
            except Exception as ex:
                failed += 1
                logger.debug("Delete orphan tag failed | id=%s | %s", eid_int, ex)
                if len(samples) < 20:
                    samples.append("id {} -> {}".format(eid_int, ex))

    return deleted, failed, samples


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if not doc or not uidoc:
        forms.alert("No active project document.", title=__title__)
        return

    orphans = _collect_orphan_tags(doc)
    if not orphans:
        forms.alert("No orphan tags detected.", title=__title__)
        return

    rows = _build_display_rows(orphans, doc)

    do_delete = _show_results_dialog(rows, len(orphans))
    if not do_delete:
        return

    deleted, failed, samples = _delete_orphans(doc, orphans)

    lines = [
        "Tags Sweep completed.",
        "Orphan tags found: {}".format(len(orphans)),
        "Deleted: {}".format(deleted),
        "Failed: {}".format(failed),
    ]

    if samples:
        lines.append("")
        lines.append("Failure samples:")
        lines.extend(samples)

    forms.alert("\n".join(lines), title=__title__, warn_icon=False)


if __name__ == "__main__":
    run()
