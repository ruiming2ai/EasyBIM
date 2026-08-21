# -*- coding: utf-8 -*-
"""Align placed views on sheets using model coordinates only."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines
from collections import defaultdict

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func


logger = script.get_logger()
get_elementid_value = get_elementid_value_func()

MODEL_ANCHOR_HOST = DB.XYZ.Zero
SCOPE_BOX_BIP = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST_CROP", None)

try:
    INVALID_EID = DB.ElementId.InvalidElementId
except Exception:
    INVALID_EID = DB.ElementId(-1)


from easybim import sheet_geometry
from easybim import sheet_titleblocks
from easybim.compat import eid_to_int as _eid_int
from easybim.compat import safe_text as _safe_text


def _xyz(x, y, z):
    return DB.XYZ(float(x), float(y), float(z))


def _is_valid_api_object(obj):
    return bool(obj) and bool(getattr(obj, "IsValidObject", True))


def _xyz_add(a, b):
    return _xyz(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def _xyz_sub(a, b):
    return _xyz(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def _doc_path_key(doc):
    try:
        path = _safe_text(doc.PathName).strip()
        if path:
            return path.lower()
    except Exception:
        pass
    return "<memory>|{}".format(_safe_text(getattr(doc, "Title", "")).lower())


def _is_supported_view_type(view):
    if not view:
        return False, "View is missing."

    try:
        if view.IsTemplate:
            return False, "View template is not supported."
    except Exception:
        pass

    view_type = getattr(view, "ViewType", None)
    excluded_types = set(
        [
            DB.ViewType.Legend,
            DB.ViewType.DraftingView,
            DB.ViewType.Schedule,
            DB.ViewType.DrawingSheet,
            DB.ViewType.ProjectBrowser,
            DB.ViewType.SystemBrowser,
            DB.ViewType.Report,
        ]
    )

    if hasattr(DB.ViewType, "PanelSchedule"):
        excluded_types.add(DB.ViewType.PanelSchedule)

    if view_type in excluded_types:
        return False, "Unsupported view type: {}".format(_safe_text(view_type))

    try:
        if isinstance(view, DB.View3D) and view.IsPerspective:
            return False, "Perspective 3D views are not supported."
    except Exception:
        pass

    return True, ""


def _normalized_view_type_label(view):
    vt = _safe_text(getattr(view, "ViewType", "Unknown"))
    normalize = {
        "FloorPlan": "Floor Plan",
        "CeilingPlan": "Ceiling Plan",
        "EngineeringPlan": "Engineering Plan",
        "AreaPlan": "Area Plan",
        "Section": "Section",
        "Elevation": "Elevation",
        "Detail": "Detail",
        "ThreeD": "3D",
    }
    return normalize.get(vt, vt)


def _get_view_cropbox_center_in_model(view):
    if not view:
        return None, "View is missing."

    try:
        crop_box = view.CropBox
    except Exception:
        crop_box = None

    if not crop_box:
        return None, "Reference view has no crop box."

    try:
        local_center = _xyz(
            (crop_box.Min.X + crop_box.Max.X) * 0.5,
            (crop_box.Min.Y + crop_box.Max.Y) * 0.5,
            (crop_box.Min.Z + crop_box.Max.Z) * 0.5,
        )
    except Exception:
        return None, "Reference crop box bounds are unavailable."

    try:
        trf = crop_box.Transform
    except Exception:
        trf = None

    if trf:
        try:
            return trf.OfPoint(local_center), ""
        except Exception as ex:
            return None, "Failed transforming crop box center to model coordinates: {}".format(ex)

    return local_center, ""


def _map_reference_point_to_host(model_point_in_ref_doc, doc_option):
    if not model_point_in_ref_doc:
        return None, "Reference model point is missing."
    if not doc_option:
        return model_point_in_ref_doc, ""

    trf = getattr(doc_option, "doc_to_host_transform", None)
    if not trf:
        return model_point_in_ref_doc, ""

    try:
        return trf.OfPoint(model_point_in_ref_doc), ""
    except Exception as ex:
        return None, "Failed converting linked reference model point to host coordinates: {}".format(ex)


def _try_build_projection(doc, viewport):
    """A ``SheetProjection`` for a placed view, or ``(None, reason)``.

    Build this *after* every configuration write and after a regeneration.
    `sheet_geometry`'s own docstring is explicit about why: `View.Outline` moves
    whenever the crop, the annotation crop or the scale changes, so "a
    SheetProjection is only valid for as long as the view is untouched".
    """
    if not viewport:
        return None, "Viewport is missing."

    try:
        view = doc.GetElement(viewport.ViewId)
    except Exception:
        view = None

    ok, reason = _is_supported_view_type(view)
    if not ok:
        return None, reason

    # One shared implementation with Linked Sheets Transfer, in
    # lib/easybim/sheet_geometry.py - two copies of this maths would drift.
    return sheet_geometry.build_projection(view, viewport)


def _try_compute_model_anchor_on_sheet(doc, viewport, model_point):
    projection, projection_reason = _try_build_projection(doc, viewport)
    if projection is None:
        return None, projection_reason

    point = sheet_geometry.to_xyz(model_point)
    if point is None:
        return None, "Unable to project model point to view plane."

    sheet_point = projection.project(point)
    return _xyz(sheet_point[0], sheet_point[1], sheet_point[2]), ""


def _clone_curve_loop(curve_loop):
    loop_copy = DB.CurveLoop()
    for crv in curve_loop:
        cloned = None
        if hasattr(crv, "Clone"):
            try:
                cloned = crv.Clone()
            except Exception:
                cloned = None
        if cloned is None and hasattr(crv, "CreateTransformed"):
            try:
                cloned = crv.CreateTransformed(DB.Transform.Identity)
            except Exception:
                cloned = None
        if cloned is None:
            cloned = crv
        loop_copy.Append(cloned)
    return loop_copy


def _extract_crop_settings(source_view):
    settings = {
        "crop_active": None,
        "crop_visible": None,
        "annotation_crop_active": None,
        "annotation_offsets": {},
        "crop_loops": [],
    }

    if not source_view:
        return None, "Reference view is missing."

    try:
        settings["crop_active"] = bool(source_view.CropBoxActive)
    except Exception:
        settings["crop_active"] = None

    try:
        settings["crop_visible"] = bool(source_view.CropBoxVisible)
    except Exception:
        settings["crop_visible"] = None

    try:
        shape_mgr = source_view.GetCropRegionShapeManager()
    except Exception:
        return None, "Reference view has no crop region manager."

    if shape_mgr:
        try:
            loops = list(shape_mgr.GetCropShape())
        except Exception:
            loops = []

        copied_loops = []
        for loop in loops:
            try:
                copied_loops.append(_clone_curve_loop(loop))
            except Exception:
                continue
        settings["crop_loops"] = copied_loops

        can_have_anno = False
        if hasattr(shape_mgr, "CanHaveAnnotationCrop"):
            try:
                can_have_anno = bool(shape_mgr.CanHaveAnnotationCrop)
            except Exception:
                can_have_anno = False

        if can_have_anno and hasattr(shape_mgr, "AnnotationCropActive"):
            try:
                settings["annotation_crop_active"] = bool(shape_mgr.AnnotationCropActive)
            except Exception:
                settings["annotation_crop_active"] = None

            for pname in (
                "TopAnnotationCropOffset",
                "BottomAnnotationCropOffset",
                "LeftAnnotationCropOffset",
                "RightAnnotationCropOffset",
            ):
                if hasattr(shape_mgr, pname):
                    try:
                        settings["annotation_offsets"][pname] = float(getattr(shape_mgr, pname))
                    except Exception:
                        continue

    return settings, ""


def _set_scope_box(view, scope_box_id):
    ok, reason = _can_set_scope_box(view)
    if not ok:
        return False, reason

    target_eid = scope_box_id if scope_box_id else INVALID_EID
    try:
        view.get_Parameter(SCOPE_BOX_BIP).Set(target_eid)
        return True, ""
    except Exception as ex:
        return False, "Failed setting scope box: {}".format(ex)


def _can_set_scope_box(view):
    if SCOPE_BOX_BIP is None:
        return False, "Scope box parameter is not available in this Revit version."

    try:
        param = view.get_Parameter(SCOPE_BOX_BIP)
    except Exception:
        param = None

    if not param:
        return False, "Target view does not support scope box."

    if param.IsReadOnly:
        return False, "Target scope box parameter is read-only."
    return True, ""


def _clear_scope_box(view):
    return _set_scope_box(view, INVALID_EID)


def _copy_crop_settings(target_view, crop_settings):
    if not target_view:
        return False, "Target view is missing."

    try:
        shape_mgr = target_view.GetCropRegionShapeManager()
    except Exception:
        shape_mgr = None

    if not shape_mgr:
        return False, "Target view has no crop region manager."

    try:
        if crop_settings.get("crop_active") is not None:
            target_view.CropBoxActive = bool(crop_settings.get("crop_active"))
    except Exception:
        pass

    try:
        if crop_settings.get("crop_visible") is not None:
            target_view.CropBoxVisible = bool(crop_settings.get("crop_visible"))
    except Exception:
        pass

    loops = crop_settings.get("crop_loops") or []
    if loops:
        try:
            shape_mgr.SetCropShape(_clone_curve_loop(loops[0]))
        except Exception as ex:
            return False, "Failed applying crop region shape: {}".format(ex)

    annotation_active = crop_settings.get("annotation_crop_active")
    if annotation_active is not None:
        if hasattr(shape_mgr, "CanHaveAnnotationCrop") and hasattr(shape_mgr, "AnnotationCropActive"):
            try:
                if shape_mgr.CanHaveAnnotationCrop:
                    shape_mgr.AnnotationCropActive = bool(annotation_active)
            except Exception:
                pass

    for pname, value in (crop_settings.get("annotation_offsets") or {}).items():
        if hasattr(shape_mgr, pname):
            try:
                setattr(shape_mgr, pname, float(value))
            except Exception:
                continue

    return True, ""


def _can_copy_crop_settings(target_view):
    if not target_view:
        return False, "Target view is missing."

    try:
        shape_mgr = target_view.GetCropRegionShapeManager()
    except Exception:
        shape_mgr = None
    if not shape_mgr:
        return False, "Target view has no crop region manager."
    return True, ""


def _get_scope_box_id(view):
    if SCOPE_BOX_BIP is None:
        return None
    try:
        param = view.get_Parameter(SCOPE_BOX_BIP)
    except Exception:
        param = None
    if not param:
        return None
    try:
        eid = param.AsElementId()
        if _eid_int(eid) in (None, -1):
            return INVALID_EID
        return eid
    except Exception:
        return INVALID_EID


def _get_label_offset(viewport):
    if hasattr(viewport, "LabelOffset"):
        try:
            return viewport.LabelOffset
        except Exception:
            return None
    return None


def _set_label_offset(viewport, value):
    if value is None:
        return False, "Reference label offset is unavailable."
    if not hasattr(viewport, "LabelOffset"):
        return False, "LabelOffset API is unavailable for this viewport."
    try:
        viewport.LabelOffset = _xyz(value.X, value.Y, value.Z)
        return True, ""
    except Exception as ex:
        return False, "Failed setting title position: {}".format(ex)


def _can_set_label_offset(viewport, value):
    if value is None:
        return False, "Reference label offset is unavailable."
    if not hasattr(viewport, "LabelOffset"):
        return False, "LabelOffset API is unavailable for this viewport."
    return True, ""


def _get_label_line_length(viewport):
    if hasattr(viewport, "LabelLineLength"):
        try:
            return float(viewport.LabelLineLength)
        except Exception:
            return None
    return None


def _set_label_line_length(viewport, value):
    if value is None:
        return False, "Reference label line length is unavailable."
    if not hasattr(viewport, "LabelLineLength"):
        return False, "LabelLineLength API is unavailable for this viewport."
    try:
        viewport.LabelLineLength = float(value)
        return True, ""
    except Exception as ex:
        return False, "Failed setting title line length: {}".format(ex)


def _can_set_label_line_length(viewport, value):
    if value is None:
        return False, "Reference label line length is unavailable."
    if not hasattr(viewport, "LabelLineLength"):
        return False, "LabelLineLength API is unavailable for this viewport."
    return True, ""


def _match_viewport_type(target_viewport, reference_viewport):
    if not _is_valid_api_object(target_viewport):
        return False, "Target viewport is invalid."
    if not _is_valid_api_object(reference_viewport):
        return False, "Reference viewport is invalid."

    try:
        ref_type_id = reference_viewport.GetTypeId()
    except Exception as ex:
        return False, "Could not read reference viewport type: {}".format(ex)

    if _eid_int(ref_type_id) in (None, -1):
        return False, "Reference viewport type is unavailable."

    try:
        current_type_id = target_viewport.GetTypeId()
    except Exception:
        current_type_id = None

    if _eid_int(current_type_id) == _eid_int(ref_type_id):
        return True, ""

    try:
        target_viewport.ChangeTypeId(ref_type_id)
        return True, ""
    except Exception as ex:
        return False, "Failed matching viewport type: {}".format(ex)


def _can_match_viewport_type(target_viewport, ref_type_id):
    if not _is_valid_api_object(target_viewport):
        return False, "Target viewport is invalid."
    if _eid_int(ref_type_id) in (None, -1):
        return False, "Reference viewport type is unavailable."
    try:
        current_type_id = target_viewport.GetTypeId()
    except Exception:
        current_type_id = None

    if _eid_int(current_type_id) == _eid_int(ref_type_id):
        return True, ""

    if hasattr(target_viewport, "IsValidType"):
        try:
            if not target_viewport.IsValidType(ref_type_id):
                return False, "Target viewport cannot use the reference viewport type."
        except Exception:
            pass
    return True, ""


def _builtin_category_int(builtin_category):
    try:
        return int(builtin_category)
    except Exception:
        return None


TITLE_BLOCK_CATEGORY_INT = _builtin_category_int(
    getattr(DB.BuiltInCategory, "OST_TitleBlocks", None))


def _element_category(element):
    """``(category_id_int, category_name)``; either half can be None/blank."""
    category = getattr(element, "Category", None)
    if category is None:
        return None, ""
    try:
        return _eid_int(category.Id), _safe_text(category.Name)
    except Exception:
        return None, ""


def _active_sheet_hint(doc):
    """(sheet_id_int, view_id_int) for whatever the user currently has open.

    Either half can be None. ``uidoc.ActiveGraphicalView`` is tried before
    ``doc.ActiveView`` - the guarded cascade Tag Align's ``_active_view`` uses,
    because the first is absent outside a UI context and the second can raise.

    A sheet answers directly; any other view answers with its own id, which the
    caller resolves to a sheet through the viewport rows it already holds.
    """
    view = None
    try:
        uidoc = revit.uidoc
        view = uidoc.ActiveGraphicalView if uidoc else None
    except Exception:
        view = None

    if view is None:
        try:
            view = doc.ActiveView
        except Exception:
            view = None

    if view is None:
        return None, None

    try:
        if isinstance(view, DB.ViewSheet):
            return _eid_int(view.Id), None
    except Exception:
        return None, None

    return None, _eid_int(getattr(view, "Id", None))


def _title_block_for_sheet(doc, sheet):
    """(title_block, extra_count) for one sheet; (None, 0) when it has none."""
    return sheet_titleblocks.first_title_block(DB, doc, sheet)


def _sheet_owned_elements(doc, sheet, keep_title_block_id=None):
    """{int id: element} for the things the sheet itself owns.

    ``FilteredElementCollector(doc, sheet.Id)`` is scoped by *visibility*, not
    by ownership: it also returns the model elements seen through the placed
    viewports.  This repo names that same unfiltered call `_visible_element_ids`
    in Families Downgrade, and Grid Offset depends on the behaviour to reach
    project-wide `DB.Grid` datums through a view.

    Handing one of those to `_move_sheet_element` would translate real model
    geometry by a paper-space vector, which is why the ownership test below is
    not optional.  Sheet Manager's `copy_sheet_detailing` and Linked Sheets
    Transfer's `sheet_detailing_ids` guard the same collector the same way.
    """
    found = {}
    try:
        elements = (
            DB.FilteredElementCollector(doc, sheet.Id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return found

    sheet_id_int = _eid_int(sheet.Id)
    by_id = {}
    candidates = []

    for element in elements:
        element_id = _eid_int(getattr(element, "Id", None))
        if element_id in (None, -1):
            continue

        is_viewport = isinstance(element, DB.Viewport)

        owner_id = None
        if not is_viewport:
            try:
                owner_id = _eid_int(element.OwnerViewId)
            except Exception:
                owner_id = None

        try:
            is_revision_schedule = bool(
                getattr(element, "IsTitleblockRevisionSchedule", False)
            )
        except Exception:
            is_revision_schedule = False

        category_id, _ = _element_category(element)
        is_title_block = (
            TITLE_BLOCK_CATEGORY_INT is not None
            and category_id == TITLE_BLOCK_CATEGORY_INT
        )

        by_id[element_id] = element
        candidates.append(
            (element_id, owner_id, is_viewport, is_revision_schedule, is_title_block)
        )

    owned_ids = sheet_titleblocks.sheet_owned_ids(
        candidates,
        sheet_id_int,
        keep_title_block_id=keep_title_block_id,
    )
    for element_id in owned_ids:
        found[element_id] = by_id[element_id]
    return found


def _unpin_if_pinned(element):
    try:
        if bool(getattr(element, "Pinned", False)):
            element.Pinned = False
            return True
    except Exception:
        pass
    return False


def _move_sheet_element(element, shift):
    """Move one sheet-owned element by a (dx, dy, dz) sheet-space shift.

    Issued one element at a time on purpose. The Revit API has no
    ``CanMoveElement`` to pre-test with - the ``Can*`` pair is
    ``CanMirrorElement``/``CanMirrorElements`` - so a grouped, workset-locked
    or otherwise immovable element can only be discovered by trying. One at a
    time makes that cost a single reported note instead of rolling back the
    whole run.

    A viewport is repositioned through ``SetBoxCenter``, this tool's own idiom
    for moving one, rather than through ``ElementTransformUtils``.
    """
    if not _is_valid_api_object(element):
        return False, "Element is no longer valid."

    try:
        if isinstance(element, DB.Viewport):
            center = element.GetBoxCenter()
            element.SetBoxCenter(
                _xyz(center.X + shift[0], center.Y + shift[1], center.Z + shift[2])
            )
        else:
            DB.ElementTransformUtils.MoveElement(
                element.Document,
                element.Id,
                _xyz(shift[0], shift[1], shift[2]),
            )
        return True, ""
    except Exception as ex:
        return False, "Failed moving element: {}".format(ex)


class AlignmentOptions(object):
    def __init__(self, match_title_position, match_title_line_length, assign_scope_box, assign_crop_region, match_viewport_type, align_title_block=False, move_sheet_content=False):
        self.match_title_position = bool(match_title_position)
        self.match_title_line_length = bool(match_title_line_length)
        self.assign_scope_box = bool(assign_scope_box)
        self.assign_crop_region = bool(assign_crop_region)
        self.match_viewport_type = bool(match_viewport_type)
        self.align_title_block = bool(align_title_block)
        # Only meaningful under align_title_block; the sub-option cannot act alone.
        self.move_sheet_content = bool(align_title_block) and bool(move_sheet_content)


class ReferenceSelection(object):
    def __init__(self, doc_option, viewport_row):
        self.doc_option = doc_option
        self.viewport_row = viewport_row


class IssueRecord(object):
    def __init__(self, viewport_id, view_id, sheet_label, view_label, stage, reason):
        self.viewport_id = viewport_id
        self.view_id = view_id
        self.sheet_label = sheet_label
        self.view_label = view_label
        self.stage = stage
        self.reason = reason


class RunStats(object):
    def __init__(self):
        self.targets_selected = 0
        self.targets_processed = 0
        self.aligned = 0
        self.title_pos_matched = 0
        self.title_line_matched = 0
        self.scope_assigned = 0
        self.crop_assigned = 0
        self.viewport_type_matched = 0
        self.title_blocks_aligned = 0
        self.sheet_elements_moved = 0
        self.pinned_unpinned = 0
        self.pinned_restored = 0
        self.issues = []
        # Notes are deliberately NOT issues: anything in self.issues aborts the
        # whole run before apply, and a sheet without a title block must not
        # cost every other sheet its alignment.
        self.notes = []

    def add_issue(self, viewport_id, view_id, sheet_label, view_label, reason, stage):
        self.issues.append(IssueRecord(viewport_id, view_id, sheet_label, view_label, stage, reason))

    def add_note(self, text):
        self.notes.append(_safe_text(text))


class ReferenceDocOption(object):
    def __init__(self, doc, display, key, doc_to_host_transform=None):
        self.doc = doc
        self.display = display
        self.key = key
        self.doc_to_host_transform = doc_to_host_transform


class ViewportRow(object):
    def __init__(self, doc, sheet, viewport, view):
        self.doc = doc
        self.sheet = sheet
        self.viewport = viewport
        self.view = view

        self.sheet_id_int = _eid_int(sheet.Id)
        self.viewport_id_int = _eid_int(viewport.Id)
        self.view_id_int = _eid_int(view.Id)

        self.sheet_number = _safe_text(getattr(sheet, "SheetNumber", ""))
        self.sheet_name = _safe_text(getattr(sheet, "Name", ""))
        self.view_name = _safe_text(getattr(view, "Name", ""))
        self.view_type_badge_text = _normalized_view_type_label(view)

        self.sheet_label = "{} - {}".format(self.sheet_number, self.sheet_name)
        self.display = "{} ({})".format(self.view_name, self.sheet_label)
        self.search_blob = "{} {} {} {}".format(
            self.sheet_number.lower(),
            self.sheet_name.lower(),
            self.view_name.lower(),
            self.view_type_badge_text.lower(),
        )


class ReferenceSheetOption(object):
    def __init__(self, sheet, rows):
        self.sheet = sheet
        self.rows = rows
        self.display = "{} - {}".format(_safe_text(sheet.SheetNumber), _safe_text(sheet.Name))


class ReferenceViewOption(object):
    def __init__(self, row):
        self.row = row
        self.display = "{} [{}]".format(row.view_name, row.view_type_badge_text)


class TargetViewportNode(object):
    def __init__(self, is_sheet, key, display_name, view_type_badge_text="", row=None):
        self.is_sheet = bool(is_sheet)
        self.key = key
        self.display_name = display_name
        self.view_type_badge_text = view_type_badge_text
        self.row = row
        self.children = []
        self.is_checked = False


class ViewAlignWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)

        self.active_doc = revit.doc
        # Read before any combo is populated; _refresh_reference_doc_combo
        # cascades straight into the sheet combo, which consumes it.
        self._active_sheet_hint = _active_sheet_hint(self.active_doc)
        self._reference_doc_options = []
        self._reference_rows_by_doc_key = {}

        self._target_rows = []
        self._target_rows_by_id = {}
        self._target_sheet_records = []
        self._checked_viewport_ids = set()

        self._setup_defaults()
        self._load_reference_docs()
        self._load_target_rows()

        self._refresh_reference_doc_combo()
        self._refresh_target_tree()

    def _setup_defaults(self):
        self.match_title_pos_cb.IsChecked = True
        self.match_title_line_cb.IsChecked = True
        self.assign_scope_cb.IsChecked = False
        self.assign_crop_cb.IsChecked = False
        self.match_viewport_type_cb.IsChecked = False
        self.align_title_block_cb.IsChecked = False
        self.move_sheet_content_cb.IsChecked = False

    def _set_status(self, text):
        self.status_tb.Text = _safe_text(text)

    def _load_reference_docs(self):
        self._reference_doc_options = self._collect_reference_doc_options(self.active_doc)

    def _load_target_rows(self):
        rows = self._collect_candidate_viewport_rows(self.active_doc, model_point=MODEL_ANCHOR_HOST)
        self._target_rows = rows
        self._target_rows_by_id = {row.viewport_id_int: row for row in rows}

        sheet_map = defaultdict(list)
        for row in rows:
            sheet_map[row.sheet_id_int].append(row)

        records = []
        for sheet_id_int, sheet_rows in sheet_map.items():
            if not sheet_rows:
                continue
            sheet_rows_sorted = sorted(sheet_rows, key=lambda x: (x.view_name.lower(), x.viewport_id_int))
            sheet = sheet_rows_sorted[0].sheet
            records.append(
                {
                    "sheet_id_int": sheet_id_int,
                    "sheet_number": _safe_text(sheet.SheetNumber),
                    "sheet_name": _safe_text(sheet.Name),
                    "sheet_search": "{} {}".format(
                        _safe_text(sheet.SheetNumber).lower(),
                        _safe_text(sheet.Name).lower(),
                    ),
                    "rows": sheet_rows_sorted,
                }
            )

        self._target_sheet_records = sorted(
            records,
            key=lambda x: (x["sheet_number"].lower(), x["sheet_name"].lower(), x["sheet_id_int"]),
        )

    def _collect_reference_doc_options(self, active_doc):
        sources = {}

        def _upsert_doc(doc, tag, doc_to_host_transform=None):
            if not doc:
                return
            try:
                if doc.IsFamilyDocument:
                    return
            except Exception:
                pass

            key = _doc_path_key(doc)
            row = sources.get(key)
            if row is None:
                row = {
                    "doc": doc,
                    "tags": set(),
                    "doc_to_host_transform": None,
                    "sort_weight": 100,
                }
                sources[key] = row

            row["tags"].add(tag)

            if tag == "Active":
                row["sort_weight"] = 0
            elif tag == "Linked" and row["sort_weight"] > 1:
                row["sort_weight"] = 1
            elif tag == "Open" and row["sort_weight"] > 2:
                row["sort_weight"] = 2

            if doc_to_host_transform is not None and row["doc_to_host_transform"] is None:
                row["doc_to_host_transform"] = doc_to_host_transform

        _upsert_doc(active_doc, "Active", DB.Transform.Identity)

        uiapp = None
        app = None
        try:
            uiapp = revit.uidoc.Application if revit.uidoc else None
            app = uiapp.Application if uiapp else None
        except Exception:
            app = None

        if app:
            try:
                for open_doc in app.Documents:
                    _upsert_doc(open_doc, "Open")
            except Exception:
                pass

        try:
            link_instances = (
                DB.FilteredElementCollector(active_doc)
                .OfClass(DB.RevitLinkInstance)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception:
            link_instances = []

        for link_inst in link_instances:
            try:
                link_doc = link_inst.GetLinkDocument()
            except Exception:
                link_doc = None
            if not link_doc:
                continue

            trf = None
            if hasattr(link_inst, "GetTotalTransform"):
                try:
                    trf = link_inst.GetTotalTransform()
                except Exception:
                    trf = None
            if trf is None and hasattr(link_inst, "GetTransform"):
                try:
                    trf = link_inst.GetTransform()
                except Exception:
                    trf = None

            _upsert_doc(link_doc, "Linked", trf)

        options = []
        for key, row in sources.items():
            tags = row["tags"]
            ordered_tags = [tag for tag in ("Active", "Linked", "Open") if tag in tags]
            label = "{} [{}]".format(_safe_text(row["doc"].Title), "/".join(ordered_tags))
            options.append(
                ReferenceDocOption(
                    doc=row["doc"],
                    display=label,
                    key=key,
                    doc_to_host_transform=row["doc_to_host_transform"],
                )
            )

        options.sort(
            key=lambda x: (
                0 if "[Active" in x.display else (1 if "[Linked" in x.display else 2),
                x.display.lower(),
            )
        )
        return options

    def _collect_candidate_viewport_rows(self, doc, model_point):
        rows = []
        try:
            sheets = (
                DB.FilteredElementCollector(doc)
                .OfClass(DB.ViewSheet)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception:
            sheets = []

        sorted_sheets = sorted(
            sheets,
            key=lambda x: (_safe_text(getattr(x, "SheetNumber", "")).lower(), _safe_text(getattr(x, "Name", "")).lower()),
        )

        for sheet in sorted_sheets:
            try:
                viewport_ids = list(sheet.GetAllViewports())
            except Exception:
                viewport_ids = []

            for viewport_id in viewport_ids:
                viewport = doc.GetElement(viewport_id)
                if not viewport:
                    continue

                view = None
                try:
                    view = doc.GetElement(viewport.ViewId)
                except Exception:
                    view = None

                ok, _ = _is_supported_view_type(view)
                if not ok:
                    continue

                anchor, reason = _try_compute_model_anchor_on_sheet(doc, viewport, model_point)
                if anchor is None:
                    logger.debug(
                        "Skipping viewport id %s from selection. Reason: %s",
                        _eid_int(viewport.Id),
                        reason,
                    )
                    continue

                rows.append(ViewportRow(doc, sheet, viewport, view))

        return rows

    def _refresh_reference_doc_combo(self):
        self.ref_doc_cb.ItemsSource = self._reference_doc_options

        if self._reference_doc_options:
            self.ref_doc_cb.SelectedIndex = 0
            self._refresh_reference_sheet_combo()
        else:
            self.ref_sheet_cb.ItemsSource = []
            self.ref_view_cb.ItemsSource = []
            self._set_status("No reference documents were found.")

    def _refresh_reference_sheet_combo(self):
        doc_option = self.ref_doc_cb.SelectedItem
        if not doc_option:
            self.ref_sheet_cb.ItemsSource = []
            self.ref_view_cb.ItemsSource = []
            return

        cached = self._reference_rows_by_doc_key.get(doc_option.key)
        if cached is None:
            cached = self._collect_candidate_viewport_rows(doc_option.doc, model_point=MODEL_ANCHOR_HOST)
            self._reference_rows_by_doc_key[doc_option.key] = cached

        sheet_map = defaultdict(list)
        for row in cached:
            sheet_map[row.sheet_id_int].append(row)

        sheet_options = []
        for _, rows in sheet_map.items():
            sheet_options.append(ReferenceSheetOption(rows[0].sheet, sorted(rows, key=lambda x: x.view_name.lower())))

        sheet_options.sort(key=lambda x: x.display.lower())
        self.ref_sheet_cb.ItemsSource = sheet_options

        if sheet_options:
            self.ref_sheet_cb.SelectedIndex = self._preferred_sheet_index(
                doc_option,
                sheet_options,
                cached,
            )
            self._refresh_reference_view_combo()
            self._set_status(
                "Loaded {} reference viewport(s) from {}.".format(
                    len(cached),
                    _safe_text(doc_option.doc.Title),
                )
            )
        else:
            self.ref_view_cb.ItemsSource = []
            self._set_status("No model-coordinate-compatible reference viewports found in selected document.")

    def _preferred_sheet_index(self, doc_option, sheet_options, rows):
        """The sheet row to open on: the one the user is looking at, else 0.

        Only meaningful for the active document - a sheet open in the host says
        nothing about which sheet a linked model should offer. Falls back to 0
        both when nothing is active and when the scan finds no match, the rule
        `view_template_transfer_ui._active_view_index` follows.
        """
        if not sheet_options:
            return 0
        if not doc_option or doc_option.doc != self.active_doc:
            return 0

        sheet_id_int, view_id_int = self._active_sheet_hint

        # Standing in a view rather than on a sheet: the rows already carry the
        # view -> sheet mapping, so no reverse index has to be built.
        if sheet_id_int is None and view_id_int is not None:
            for row in rows or []:
                if row.view_id_int == view_id_int:
                    sheet_id_int = row.sheet_id_int
                    break

        if sheet_id_int is None:
            return 0

        for index, option in enumerate(sheet_options):
            if option.rows and option.rows[0].sheet_id_int == sheet_id_int:
                return index
        return 0

    def _refresh_reference_view_combo(self):
        sheet_option = self.ref_sheet_cb.SelectedItem
        if not sheet_option:
            self.ref_view_cb.ItemsSource = []
            return

        view_options = [ReferenceViewOption(x) for x in sheet_option.rows]
        self.ref_view_cb.ItemsSource = view_options
        if view_options:
            self.ref_view_cb.SelectedIndex = 0

    def _refresh_target_tree(self):
        search_token = _safe_text(self.target_search_tb.Text).strip().lower()

        root_nodes = []
        visible_targets = 0
        checked_visible = 0

        for record in self._target_sheet_records:
            candidate_rows = []
            if search_token:
                for row in record["rows"]:
                    if search_token in row.search_blob:
                        candidate_rows.append(row)
            else:
                candidate_rows = list(record["rows"])

            if not candidate_rows:
                continue

            sheet_node = TargetViewportNode(
                is_sheet=True,
                key=record["sheet_id_int"],
                display_name="{} - {}".format(record["sheet_number"], record["sheet_name"]),
            )

            child_checked_count = 0
            for row in candidate_rows:
                is_checked = row.viewport_id_int in self._checked_viewport_ids
                child = TargetViewportNode(
                    is_sheet=False,
                    key=row.viewport_id_int,
                    display_name=row.view_name,
                    view_type_badge_text=row.view_type_badge_text,
                    row=row,
                )
                child.is_checked = is_checked
                sheet_node.children.append(child)
                visible_targets += 1
                if is_checked:
                    child_checked_count += 1
                    checked_visible += 1

            if child_checked_count == 0:
                sheet_node.is_checked = False
            elif child_checked_count == len(sheet_node.children):
                sheet_node.is_checked = True
            else:
                sheet_node.is_checked = None

            root_nodes.append(sheet_node)

        self.target_tv.ItemsSource = root_nodes
        self.target_count_tb.Text = "Visible targets: {} | Checked (visible): {} | Checked (all): {}".format(
            visible_targets,
            checked_visible,
            len(self._checked_viewport_ids),
        )

        self._set_status(
            "Target pool loaded: {} model-compatible viewport(s) on {} sheet(s).".format(
                len(self._target_rows),
                len(self._target_sheet_records),
            )
        )

    def _iter_visible_child_nodes(self):
        roots = self.target_tv.ItemsSource or []
        for sheet_node in roots:
            for child in sheet_node.children:
                yield sheet_node, child

    def _set_tree_expanded(self, expand):
        try:
            self.target_tv.UpdateLayout()
        except Exception:
            pass

        for item in self.target_tv.Items:
            root_container = self.target_tv.ItemContainerGenerator.ContainerFromItem(item)
            if root_container:
                self._set_container_expanded_recursive(root_container, expand)

    def _set_container_expanded_recursive(self, container, expand):
        try:
            container.IsExpanded = bool(expand)
            container.UpdateLayout()
        except Exception:
            pass

        for child_item in container.Items:
            child_container = container.ItemContainerGenerator.ContainerFromItem(child_item)
            if child_container:
                self._set_container_expanded_recursive(child_container, expand)

    def _get_selected_reference(self):
        doc_option = self.ref_doc_cb.SelectedItem
        view_option = self.ref_view_cb.SelectedItem
        if not doc_option or not view_option:
            return None

        return ReferenceSelection(doc_option, view_option.row)

    def ref_doc_changed(self, sender, args):
        del sender, args
        self._refresh_reference_sheet_combo()

    def ref_sheet_changed(self, sender, args):
        del sender, args
        self._refresh_reference_view_combo()

    def target_search_changed(self, sender, args):
        del sender, args
        self._refresh_target_tree()

    def target_checkbox_click(self, sender, args):
        del args
        node = getattr(sender, "DataContext", None)
        if not node:
            return

        desired = sender.IsChecked
        if node.is_sheet:
            checked_state = bool(desired)
            for child in node.children:
                if checked_state:
                    self._checked_viewport_ids.add(child.key)
                else:
                    self._checked_viewport_ids.discard(child.key)
        else:
            if bool(desired):
                self._checked_viewport_ids.add(node.key)
            else:
                self._checked_viewport_ids.discard(node.key)

        self._refresh_target_tree()

    def check_all_visible_click(self, sender, args):
        del sender, args
        for _, child in self._iter_visible_child_nodes():
            self._checked_viewport_ids.add(child.key)
        self._refresh_target_tree()

    def clear_targets_click(self, sender, args):
        del sender, args
        self._checked_viewport_ids.clear()
        self._refresh_target_tree()

    def expand_all_click(self, sender, args):
        del sender, args
        self._set_tree_expanded(True)

    def collapse_all_click(self, sender, args):
        del sender, args
        self._set_tree_expanded(False)

    def assign_scope_clicked(self, sender, args):
        del sender, args
        if self.assign_scope_cb.IsChecked:
            self.assign_crop_cb.IsChecked = False

    def assign_crop_clicked(self, sender, args):
        del sender, args
        if self.assign_crop_cb.IsChecked:
            self.assign_scope_cb.IsChecked = False

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()

    def _add_issue(self, stats, row, reason, stage, viewport_id_int=None, view_id_int=None):
        if row:
            viewport_id = row.viewport_id_int if viewport_id_int is None else viewport_id_int
            view_id = row.view_id_int if view_id_int is None else view_id_int
            sheet_label = row.sheet_label
            view_label = row.view_name
        else:
            viewport_id = viewport_id_int
            view_id = view_id_int
            sheet_label = "<unknown>"
            view_label = "<unknown>"

        stats.add_issue(
            viewport_id=viewport_id,
            view_id=view_id,
            sheet_label=sheet_label,
            view_label=view_label,
            reason=reason,
            stage=stage,
        )

    def _plan_title_block_moves(self, ref_point, rows_by_sheet, options, stats):
        """One move plan per target sheet: the elements to shift, and by how much.

        Planned per *sheet*, not per viewport - a sheet carrying three selected
        viewports must move its frame once, not three times.

        A sheet with no title block is recorded as a note and dropped from the
        plan. Its viewports still align. It must never reach ``stats.issues``,
        which aborts the entire run before anything is written.
        """
        plans = []

        for sheet_id_int in sorted(rows_by_sheet.keys()):
            rows = rows_by_sheet[sheet_id_int]
            sheet = rows[0].sheet
            sheet_label = rows[0].sheet_label

            title_block, extra_count = _title_block_for_sheet(self.active_doc, sheet)
            if title_block is None:
                stats.add_note(
                    "{}: sheet has no title block; title block alignment skipped.".format(sheet_label)
                )
                continue

            if extra_count:
                stats.add_note(
                    "{}: sheet has {} extra title block(s); the first one was used.".format(
                        sheet_label,
                        extra_count,
                    )
                )

            shift = sheet_titleblocks.title_block_shift(
                ref_point,
                sheet_titleblocks.location_point(title_block),
            )
            if shift is None:
                stats.add_note(
                    "{}: title block is already at the reference position.".format(sheet_label)
                )
                continue

            title_block_id = _eid_int(title_block.Id)
            owned = _sheet_owned_elements(
                self.active_doc,
                sheet,
                keep_title_block_id=title_block_id,
            )
            owned[title_block_id] = title_block

            move_ids = sheet_titleblocks.plan_sheet_move(
                sorted(owned.keys()),
                [row.viewport_id_int for row in rows],
                options.move_sheet_content,
                title_block_id=title_block_id,
            )

            plans.append(
                {
                    "sheet_label": sheet_label,
                    "shift": shift,
                    "elements": [owned[x] for x in move_ids if x in owned],
                }
            )

        return plans

    def _apply_title_block_moves(self, plans, stats):
        """Shift each planned sheet. Runs inside the caller's transaction."""
        for plan in plans:
            shift = plan["shift"]
            sheet_label = plan["sheet_label"]
            elements = plan["elements"]
            pinned_elements = []

            for index, element in enumerate(elements):
                if _unpin_if_pinned(element):
                    pinned_elements.append(element)
                    stats.pinned_unpinned += 1

                ok_move, move_reason = _move_sheet_element(element, shift)
                if not ok_move:
                    stats.add_note("{}: {}".format(sheet_label, move_reason))
                    continue

                # plan_sheet_move always puts the title block first.
                if index == 0:
                    stats.title_blocks_aligned += 1
                else:
                    stats.sheet_elements_moved += 1

            for element in pinned_elements:
                try:
                    element.Pinned = True
                    stats.pinned_restored += 1
                except Exception:
                    pass

    def _build_summary_text(self, stats, headline):
        lines = [
            headline,
            "Targets selected: {}".format(stats.targets_selected),
            "Targets processed: {}".format(stats.targets_processed),
            "Aligned by model coordinates: {}".format(stats.aligned),
            "Title position matched: {}".format(stats.title_pos_matched),
            "Title line length matched: {}".format(stats.title_line_matched),
            "Scope box assigned: {}".format(stats.scope_assigned),
            "Crop region assigned: {}".format(stats.crop_assigned),
            "Viewport type matched: {}".format(stats.viewport_type_matched),
            "Title blocks aligned: {}".format(stats.title_blocks_aligned),
            "Other sheet elements moved: {}".format(stats.sheet_elements_moved),
            "Pinned unpinned: {}".format(stats.pinned_unpinned),
            "Pinned restored: {}".format(stats.pinned_restored),
            "Notes: {}".format(len(stats.notes)),
            "Issues: {}".format(len(stats.issues)),
        ]

        if stats.notes:
            lines.append("")
            lines.append("Notes (up to 200 rows):")
            for note in stats.notes[:200]:
                lines.append("- {}".format(note))
            if len(stats.notes) > 200:
                lines.append("... {} additional note(s) omitted.".format(len(stats.notes) - 200))

        if stats.issues:
            lines.append("")
            lines.append("Issue details (up to 200 rows):")
            for row in stats.issues[:200]:
                lines.append(
                    "- stage {} | viewport id {} | view id {} | {} | {} | {}".format(
                        _safe_text(row.stage) or "<unknown>",
                        _safe_text(row.viewport_id) or "<unknown>",
                        _safe_text(row.view_id) or "<unknown>",
                        row.sheet_label,
                        row.view_label,
                        row.reason,
                    )
                )
            if len(stats.issues) > 200:
                lines.append("... {} additional rows omitted.".format(len(stats.issues) - 200))

        return "\n".join(lines)

    def run_click(self, sender, args):
        del sender, args

        if not self.active_doc:
            forms.alert("No active document found.", title="View Align")
            return

        reference = self._get_selected_reference()
        if not reference:
            forms.alert("Select a valid reference document/sheet/view.", title="View Align")
            return

        target_ids = sorted(self._checked_viewport_ids)
        if not target_ids:
            forms.alert("Select at least one target viewport.", title="View Align")
            return

        options = AlignmentOptions(
            match_title_position=self.match_title_pos_cb.IsChecked,
            match_title_line_length=self.match_title_line_cb.IsChecked,
            assign_scope_box=self.assign_scope_cb.IsChecked,
            assign_crop_region=self.assign_crop_cb.IsChecked,
            match_viewport_type=self.match_viewport_type_cb.IsChecked,
            align_title_block=self.align_title_block_cb.IsChecked,
            move_sheet_content=self.move_sheet_content_cb.IsChecked,
        )

        if options.assign_scope_box and options.assign_crop_region:
            forms.alert(
                "Assign Scope Box and Assign Crop View Region are mutually exclusive.",
                title="View Align",
            )
            return

        ref_row = reference.viewport_row
        ref_viewport = ref_row.viewport
        ref_view = ref_row.view

        if not _is_valid_api_object(ref_viewport) or not _is_valid_api_object(ref_view):
            forms.alert("Reference viewport/view is no longer valid.", title="View Align")
            return

        ref_model_anchor_in_ref_doc, ref_center_reason = _get_view_cropbox_center_in_model(ref_view)
        if ref_model_anchor_in_ref_doc is None:
            forms.alert(
                "Could not compute reference model anchor from reference view center.\nReason: {}".format(ref_center_reason),
                title="View Align",
            )
            return

        global_model_anchor_host, host_anchor_reason = _map_reference_point_to_host(
            ref_model_anchor_in_ref_doc,
            reference.doc_option,
        )
        if global_model_anchor_host is None:
            forms.alert(
                "Could not convert reference anchor to host coordinates.\nReason: {}".format(host_anchor_reason),
                title="View Align",
            )
            return

        ref_anchor, ref_anchor_reason = _try_compute_model_anchor_on_sheet(
            reference.doc_option.doc,
            ref_viewport,
            ref_model_anchor_in_ref_doc,
        )
        if ref_anchor is None:
            forms.alert(
                "Reference viewport is not model-coordinate compatible.\nReason: {}".format(ref_anchor_reason),
                title="View Align",
            )
            return

        ref_scope_box_id = None
        if options.assign_scope_box:
            ref_scope_box_id = _get_scope_box_id(ref_view)

        ref_crop_settings = None
        if options.assign_crop_region:
            ref_crop_settings, crop_reason = _extract_crop_settings(ref_view)
            if ref_crop_settings is None:
                forms.alert(
                    "Could not read crop settings from reference view.\nReason: {}".format(crop_reason),
                    title="View Align",
                )
                return

        ref_label_offset = _get_label_offset(ref_viewport) if options.match_title_position else None
        ref_label_line_length = _get_label_line_length(ref_viewport) if options.match_title_line_length else None
        ref_title_block_point = None
        ref_title_block_extra = 0
        if options.align_title_block:
            # Sheet points are paper space. A reference sheet in a linked or
            # other open document needs no conversion, and must NOT go through
            # doc_to_host_transform - that maps model coordinates, and a sheet
            # is not part of the model's geometry.
            ref_title_block, ref_title_block_extra = _title_block_for_sheet(
                reference.doc_option.doc,
                ref_row.sheet,
            )
            if ref_title_block is None:
                forms.alert(
                    "The reference sheet has no title block, so Align Title Block has nothing to match.",
                    title="View Align",
                )
                return

            ref_title_block_point = sheet_titleblocks.location_point(ref_title_block)
            if ref_title_block_point is None:
                forms.alert(
                    "The reference title block has no location point to align to.",
                    title="View Align",
                )
                return

        ref_viewport_type_id = None
        ref_type_reason = ""
        if options.match_viewport_type:
            try:
                ref_viewport_type_id = ref_viewport.GetTypeId()
            except Exception as ex:
                ref_type_reason = "Could not read reference viewport type: {}".format(ex)

        stats = RunStats()
        stats.targets_selected = len(target_ids)

        if ref_title_block_extra:
            stats.add_note(
                "Reference sheet {} has {} extra title block(s); the first one was used.".format(
                    ref_row.sheet_label,
                    ref_title_block_extra,
                )
            )

        if options.assign_scope_box and ref_scope_box_id is None:
            self._add_issue(
                stats,
                ref_row,
                "Reference view does not expose scope box parameter.",
                "precheck",
            )

        if options.match_viewport_type:
            if ref_type_reason:
                self._add_issue(stats, ref_row, ref_type_reason, "precheck")
            elif _eid_int(ref_viewport_type_id) in (None, -1):
                self._add_issue(stats, ref_row, "Reference viewport type is unavailable.", "precheck")

        apply_context_rows = []
        for viewport_id_int in target_ids:
            row = self._target_rows_by_id.get(viewport_id_int)
            if not row:
                self._add_issue(
                    stats,
                    row=None,
                    reason="Viewport is not in target pool.",
                    stage="precheck",
                    viewport_id_int=viewport_id_int,
                    view_id_int=None,
                )
                continue

            viewport = getattr(row, "viewport", None)
            if not _is_valid_api_object(viewport):
                self._add_issue(stats, row, "Viewport no longer exists.", "precheck")
                continue

            view = self.active_doc.GetElement(viewport.ViewId)
            if not _is_valid_api_object(view):
                self._add_issue(stats, row, "Target view no longer exists.", "precheck")
                continue

            has_row_issue = False

            target_anchor, target_reason = _try_compute_model_anchor_on_sheet(
                self.active_doc,
                viewport,
                global_model_anchor_host,
            )
            if target_anchor is None:
                has_row_issue = True
                self._add_issue(stats, row, "Model-coordinate solve failed: {}".format(target_reason), "precheck")

            if options.assign_scope_box:
                if _eid_int(ref_scope_box_id) not in (None, -1) and reference.doc_option.doc != self.active_doc:
                    has_row_issue = True
                    self._add_issue(
                        stats,
                        row,
                        "Assign Scope Box requires a reference scope box from the active document.",
                        "precheck",
                    )
                ok_scope, scope_reason = _can_set_scope_box(view)
                if not ok_scope:
                    has_row_issue = True
                    self._add_issue(stats, row, scope_reason, "precheck")

            if options.assign_crop_region:
                ok_crop, crop_reason = _can_copy_crop_settings(view)
                if not ok_crop:
                    has_row_issue = True
                    self._add_issue(stats, row, crop_reason, "precheck")

            if options.match_viewport_type:
                ok_type, type_reason = _can_match_viewport_type(viewport, ref_viewport_type_id)
                if not ok_type:
                    has_row_issue = True
                    self._add_issue(stats, row, type_reason, "precheck")

            if options.match_title_position:
                ok_pos, pos_reason = _can_set_label_offset(viewport, ref_label_offset)
                if not ok_pos:
                    has_row_issue = True
                    self._add_issue(stats, row, pos_reason, "precheck")

            if options.match_title_line_length:
                ok_len, len_reason = _can_set_label_line_length(viewport, ref_label_line_length)
                if not ok_len:
                    has_row_issue = True
                    self._add_issue(stats, row, len_reason, "precheck")

            if has_row_issue:
                continue

            # target_anchor is deliberately NOT carried forward: it was
            # measured before any write, and the placement re-measures after
            # the regeneration instead. It exists here only to prove the
            # viewport can be measured at all.
            apply_context_rows.append(
                {
                    "row": row,
                    "viewport": viewport,
                    "view": view,
                }
            )

        if stats.issues:
            summary = self._build_summary_text(stats, "View Align aborted before apply. No viewports were moved.")
            self._set_status("Precheck failed. Fix reported issues and rerun.")
            forms.alert(summary, title="View Align", warn_icon=True)
            return

        title_block_plans = []
        if options.align_title_block:
            rows_by_sheet = defaultdict(list)
            for context in apply_context_rows:
                rows_by_sheet[context["row"].sheet_id_int].append(context["row"])
            title_block_plans = self._plan_title_block_moves(
                ref_title_block_point,
                rows_by_sheet,
                options,
                stats,
            )

        tx_group = DB.TransactionGroup(self.active_doc, "View Align")
        tx = None
        current_row = None
        current_viewport = None
        pinned_rows = []

        try:
            tx_group.Start()
            tx = DB.Transaction(self.active_doc, "Apply View Align")
            tx.Start()

            # ---- pass 1: every write that can reshape a viewport box -------
            # Nothing is measured yet. View.Outline is derived from the crop,
            # the annotation crop, the scale and the view template, and stays
            # stale until the document regenerates, so a measurement taken
            # before these writes describes a box that no longer exists.
            self._apply_title_block_moves(title_block_plans, stats)

            for context in apply_context_rows:
                current_row = context["row"]
                current_viewport = context["viewport"]
                current_view = context["view"]

                stats.targets_processed += 1

                if getattr(current_viewport, "Pinned", False):
                    current_viewport.Pinned = False
                    pinned_rows.append((current_row, current_viewport))
                    stats.pinned_unpinned += 1

                if options.assign_scope_box:
                    ok_scope, scope_reason = _set_scope_box(current_view, ref_scope_box_id)
                    if not ok_scope:
                        raise Exception(scope_reason)
                    stats.scope_assigned += 1

                if options.match_viewport_type:
                    ok_type, type_reason = _match_viewport_type(current_viewport, ref_viewport)
                    if not ok_type:
                        raise Exception(type_reason)
                    stats.viewport_type_matched += 1

                if options.assign_crop_region:
                    clear_ok, clear_reason = _clear_scope_box(current_view)
                    if not clear_ok:
                        raise Exception("Scope clear before crop copy: {}".format(clear_reason))

                    crop_ok, crop_reason = _copy_crop_settings(current_view, ref_crop_settings)
                    if not crop_ok:
                        raise Exception(crop_reason)
                    stats.crop_assigned += 1

            # ---- the regeneration that makes the next reads describe reality
            # `Linked Sheets Transfer.place_and_align` does exactly this, and
            # says why: "Measuring a stale outline is exactly how a viewport
            # ends up confidently misplaced."
            current_row = None
            current_viewport = None
            try:
                self.active_doc.Regenerate()
            except Exception as regen_error:
                raise Exception(
                    "Could not regenerate before measuring: {}".format(regen_error)
                )

            # The reference is re-measured in that same regenerated state, so
            # both sides of the comparison come from one moment in time.
            ref_sheet_anchor, ref_sheet_reason = _try_compute_model_anchor_on_sheet(
                reference.doc_option.doc,
                ref_viewport,
                ref_model_anchor_in_ref_doc,
            )
            if ref_sheet_anchor is None:
                raise Exception(
                    "Reference viewport could not be measured after the writes: {}".format(
                        ref_sheet_reason
                    )
                )

            ref_sheet_point = sheet_geometry.to_xyz(ref_sheet_anchor)
            anchor_point = sheet_geometry.to_xyz(global_model_anchor_host)
            if ref_sheet_point is None or anchor_point is None:
                raise Exception("Reference anchor could not be read as a point.")

            # ---- pass 2: measure last, then place --------------------------
            for context in apply_context_rows:
                current_row = context["row"]
                current_viewport = context["viewport"]

                projection, projection_reason = _try_build_projection(
                    self.active_doc,
                    current_viewport,
                )
                if projection is None:
                    raise Exception(
                        "Measurement after the writes failed: {}".format(projection_reason)
                    )

                new_center = projection.box_center_for(anchor_point, ref_sheet_point)
                current_viewport.SetBoxCenter(
                    _xyz(new_center[0], new_center[1], new_center[2])
                )
                stats.aligned += 1

                if options.match_title_position:
                    ok_pos, pos_reason = _set_label_offset(current_viewport, ref_label_offset)
                    if not ok_pos:
                        raise Exception(pos_reason)
                    stats.title_pos_matched += 1

                if options.match_title_line_length:
                    ok_len, len_reason = _set_label_line_length(current_viewport, ref_label_line_length)
                    if not ok_len:
                        raise Exception(len_reason)
                    stats.title_line_matched += 1

            for row, viewport in pinned_rows:
                current_row = row
                current_viewport = viewport
                viewport.Pinned = True
                stats.pinned_restored += 1

            tx.Commit()
            tx_group.Assimilate()
        except Exception as ex:
            self._add_issue(
                stats,
                row=current_row,
                reason="Apply failed: {}".format(ex),
                stage="apply",
                viewport_id_int=_eid_int(getattr(current_viewport, "Id", None)),
            )
            if tx:
                try:
                    tx.RollBack()
                except Exception:
                    pass
            try:
                tx_group.RollBack()
            except Exception:
                pass

            summary = self._build_summary_text(stats, "View Align aborted during apply. Transaction rolled back.")
            self._set_status("Apply failed and was rolled back.")
            forms.alert(summary, title="View Align", warn_icon=True)
            return

        summary = self._build_summary_text(stats, "View Align completed.")
        self._set_status("Done. Aligned {} of {} selected target viewport(s).".format(stats.aligned, stats.targets_selected))
        forms.alert(summary, title="View Align", warn_icon=False)


def main():
    if not revit.doc:
        forms.alert("No active document found.", title="View Align")
        return

    window = ViewAlignWindow("Script.xaml")
    window.ShowDialog()


if __name__ == "__main__":
    main()
