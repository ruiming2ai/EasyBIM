# -*- coding: utf-8 -*-
"""Align placed views on sheets using model coordinates only."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines
import math
from collections import defaultdict

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func


logger = script.get_logger()
get_elementid_value = get_elementid_value_func()

EPS = 1e-9
MODEL_ANCHOR_HOST = DB.XYZ.Zero
SCOPE_BOX_BIP = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST_CROP", None)

try:
    INVALID_EID = DB.ElementId.InvalidElementId
except Exception:
    INVALID_EID = DB.ElementId(-1)


def _eid_int(element_id):
    if not element_id:
        return None
    try:
        return int(get_elementid_value(element_id))
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return None


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


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


def _rotation_angle_from_viewport(viewport):
    try:
        rotation = viewport.Rotation
    except Exception:
        rotation = None

    rotation_text = _safe_text(rotation).lower()
    if "clockwise" in rotation_text and "counter" not in rotation_text:
        return -math.pi * 0.5
    if "counter" in rotation_text:
        return math.pi * 0.5
    if "upside" in rotation_text or "180" in rotation_text:
        return math.pi
    return 0.0


def _rotate_xy(x_val, y_val, angle):
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    return x_val * cos_a - y_val * sin_a, x_val * sin_a + y_val * cos_a


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


def _try_compute_model_anchor_on_sheet(doc, viewport, model_point):
    if not viewport:
        return None, "Viewport is missing."

    try:
        view = doc.GetElement(viewport.ViewId)
    except Exception:
        view = None

    ok, reason = _is_supported_view_type(view)
    if not ok:
        return None, reason

    if hasattr(viewport, "HasViewTransforms"):
        try:
            if not viewport.HasViewTransforms():
                return None, "Viewport has no model transforms."
        except Exception:
            pass

    if hasattr(viewport, "GetProjectionToSheetTransform"):
        try:
            trf = viewport.GetProjectionToSheetTransform()
            if trf:
                return trf.OfPoint(model_point), ""
        except Exception:
            pass

    try:
        origin = view.Origin
        right_dir = view.RightDirection
        up_dir = view.UpDirection
    except Exception:
        return None, "View does not expose orientation vectors for anchor solve."

    try:
        if right_dir.GetLength() < EPS or up_dir.GetLength() < EPS:
            return None, "View orientation vectors are invalid."
    except Exception:
        return None, "View orientation vectors are invalid."

    scale = 1.0
    try:
        scale = float(view.Scale)
        if abs(scale) < EPS:
            scale = 1.0
    except Exception:
        scale = 1.0

    try:
        model_vec = _xyz_sub(model_point, origin)
        u_proj = model_vec.DotProduct(right_dir) / scale
        v_proj = model_vec.DotProduct(up_dir) / scale
    except Exception:
        return None, "Unable to project model point to view plane."

    try:
        outline = view.Outline
        center_u = (outline.Min.U + outline.Max.U) * 0.5
        center_v = (outline.Min.V + outline.Max.V) * 0.5
    except Exception:
        return None, "View outline is unavailable."

    local_dx = u_proj - center_u
    local_dy = v_proj - center_v

    angle = _rotation_angle_from_viewport(viewport)
    rot_dx, rot_dy = _rotate_xy(local_dx, local_dy, angle)

    try:
        center = viewport.GetBoxCenter()
    except Exception:
        return None, "Viewport box center is unavailable."

    return _xyz(center.X + rot_dx, center.Y + rot_dy, center.Z), ""


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

    target_eid = scope_box_id if scope_box_id else INVALID_EID
    try:
        param.Set(target_eid)
        return True, ""
    except Exception as ex:
        return False, "Failed setting scope box: {}".format(ex)


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


class AlignmentOptions(object):
    def __init__(self, match_title_position, match_title_line_length, assign_scope_box, assign_crop_region, match_viewport_type):
        self.match_title_position = bool(match_title_position)
        self.match_title_line_length = bool(match_title_line_length)
        self.assign_scope_box = bool(assign_scope_box)
        self.assign_crop_region = bool(assign_crop_region)
        self.match_viewport_type = bool(match_viewport_type)


class ReferenceSelection(object):
    def __init__(self, doc_option, viewport_row, model_anchor_in_ref_doc):
        self.doc_option = doc_option
        self.viewport_row = viewport_row
        self.model_anchor_in_ref_doc = model_anchor_in_ref_doc


class SkipRecord(object):
    def __init__(self, element_id, sheet_label, view_label, reason):
        self.element_id = element_id
        self.sheet_label = sheet_label
        self.view_label = view_label
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
        self.pinned_unpinned = 0
        self.pinned_restored = 0
        self.skip_records = []

    def add_skip(self, element_id, sheet_label, view_label, reason):
        self.skip_records.append(SkipRecord(element_id, sheet_label, view_label, reason))


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
            model_point = self._reference_model_anchor_point(doc_option)
            cached = self._collect_candidate_viewport_rows(doc_option.doc, model_point=model_point)
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
            self.ref_sheet_cb.SelectedIndex = 0
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

    def _reference_model_anchor_point(self, ref_doc_option):
        if not ref_doc_option:
            return MODEL_ANCHOR_HOST

        trf = ref_doc_option.doc_to_host_transform
        if trf:
            try:
                return trf.Inverse.OfPoint(MODEL_ANCHOR_HOST)
            except Exception:
                return MODEL_ANCHOR_HOST
        return MODEL_ANCHOR_HOST

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

        model_anchor_in_ref_doc = self._reference_model_anchor_point(doc_option)
        return ReferenceSelection(doc_option, view_option.row, model_anchor_in_ref_doc)

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

    def _build_summary_text(self, stats):
        lines = [
            "View Align completed.",
            "Targets selected: {}".format(stats.targets_selected),
            "Targets processed: {}".format(stats.targets_processed),
            "Aligned by model coordinates: {}".format(stats.aligned),
            "Title position matched: {}".format(stats.title_pos_matched),
            "Title line length matched: {}".format(stats.title_line_matched),
            "Scope box assigned: {}".format(stats.scope_assigned),
            "Crop region assigned: {}".format(stats.crop_assigned),
            "Viewport type matched: {}".format(stats.viewport_type_matched),
            "Pinned unpinned: {}".format(stats.pinned_unpinned),
            "Pinned restored: {}".format(stats.pinned_restored),
            "Skipped / warnings: {}".format(len(stats.skip_records)),
        ]

        if stats.skip_records:
            lines.append("")
            lines.append("Skipped / warning details (up to 160 rows):")
            for row in stats.skip_records[:160]:
                lines.append(
                    "- id {} | {} | {} | {}".format(
                        row.element_id,
                        row.sheet_label,
                        row.view_label,
                        row.reason,
                    )
                )
            if len(stats.skip_records) > 160:
                lines.append("... {} additional rows omitted.".format(len(stats.skip_records) - 160))

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

        ref_anchor, ref_anchor_reason = _try_compute_model_anchor_on_sheet(
            reference.doc_option.doc,
            ref_viewport,
            reference.model_anchor_in_ref_doc,
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

        stats = RunStats()
        stats.targets_selected = len(target_ids)

        with revit.TransactionGroup("View Align", doc=self.active_doc):
            with revit.Transaction("Apply View Align", doc=self.active_doc, log_errors=False):
                for viewport_id_int in target_ids:
                    row = self._target_rows_by_id.get(viewport_id_int)
                    if not row:
                        stats.add_skip(viewport_id_int, "<unknown>", "<unknown>", "Viewport is not in target pool.")
                        continue

                    viewport = getattr(row, "viewport", None)
                    if not _is_valid_api_object(viewport):
                        stats.add_skip(
                            viewport_id_int,
                            row.sheet_label,
                            row.view_name,
                            "Viewport no longer exists.",
                        )
                        continue

                    view = self.active_doc.GetElement(viewport.ViewId)
                    if not _is_valid_api_object(view):
                        stats.add_skip(
                            viewport_id_int,
                            row.sheet_label,
                            row.view_name,
                            "Target view no longer exists.",
                        )
                        continue

                    stats.targets_processed += 1
                    was_pinned = False

                    try:
                        if getattr(viewport, "Pinned", False):
                            try:
                                viewport.Pinned = False
                                was_pinned = True
                                stats.pinned_unpinned += 1
                            except Exception as unpin_ex:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    "Failed to unpin target viewport: {}".format(unpin_ex),
                                )
                                continue

                        if options.assign_scope_box:
                            ok_scope, scope_reason = _set_scope_box(view, ref_scope_box_id)
                            if ok_scope:
                                stats.scope_assigned += 1
                            else:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    scope_reason,
                                )

                        if options.match_viewport_type:
                            ok_type, type_reason = _match_viewport_type(viewport, ref_viewport)
                            if ok_type:
                                stats.viewport_type_matched += 1
                            else:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    type_reason,
                                )

                        if options.assign_crop_region:
                            clear_ok, clear_reason = _clear_scope_box(view)
                            if not clear_ok:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    "Scope clear before crop copy: {}".format(clear_reason),
                                )

                            crop_ok, crop_reason = _copy_crop_settings(view, ref_crop_settings)
                            if crop_ok:
                                stats.crop_assigned += 1
                            else:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    crop_reason,
                                )

                        target_anchor, target_reason = _try_compute_model_anchor_on_sheet(
                            self.active_doc,
                            viewport,
                            MODEL_ANCHOR_HOST,
                        )
                        if target_anchor is None:
                            stats.add_skip(
                                viewport_id_int,
                                row.sheet_label,
                                row.view_name,
                                "Model-coordinate solve failed: {}".format(target_reason),
                            )
                            continue

                        delta = _xyz_sub(ref_anchor, target_anchor)
                        current_center = viewport.GetBoxCenter()
                        viewport.SetBoxCenter(_xyz_add(current_center, delta))
                        stats.aligned += 1

                        if options.match_title_position:
                            ok_pos, pos_reason = _set_label_offset(viewport, ref_label_offset)
                            if ok_pos:
                                stats.title_pos_matched += 1
                            else:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    pos_reason,
                                )

                        if options.match_title_line_length:
                            ok_len, len_reason = _set_label_line_length(viewport, ref_label_line_length)
                            if ok_len:
                                stats.title_line_matched += 1
                            else:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    len_reason,
                                )

                    except Exception as ex:
                        stats.add_skip(
                            viewport_id_int,
                            row.sheet_label,
                            row.view_name,
                            "Unexpected failure: {}".format(ex),
                        )
                    finally:
                        if was_pinned:
                            try:
                                viewport.Pinned = True
                                stats.pinned_restored += 1
                            except Exception as repin_ex:
                                stats.add_skip(
                                    viewport_id_int,
                                    row.sheet_label,
                                    row.view_name,
                                    "Failed to restore pinned state: {}".format(repin_ex),
                                )

        summary = self._build_summary_text(stats)
        self._set_status("Done. Aligned {} of {} processed target viewport(s).".format(stats.aligned, stats.targets_processed))
        forms.alert(summary, title="View Align", warn_icon=False)


def main():
    if not revit.doc:
        forms.alert("No active document found.", title="View Align")
        return

    window = ViewAlignWindow("Script.xaml")
    window.ShowDialog()


if __name__ == "__main__":
    main()
