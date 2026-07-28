# -*- coding: utf-8 -*-
"""Offset grid heads from crop region or selected scope box across views."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines
import math
import re

import clr

clr.AddReference("System.Windows.Forms")
import System.Windows.Forms as WinForms

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

logger = script.get_logger()
get_elementid_value = get_elementid_value_func()

EPS = 1e-8


def _vec2(x, y):
    return (float(x), float(y))


def _v2_sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _v2_add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _v2_mul(a, s):
    return (a[0] * s, a[1] * s)


def _v2_len2(a):
    return a[0] * a[0] + a[1] * a[1]


def _v2_cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _pt_key(pt, tol=1e-6):
    return (round(pt.X / tol), round(pt.Y / tol), round(pt.Z / tol))


def _pt2_key(pt, tol=1e-6):
    return (round(pt[0] / tol), round(pt[1] / tol))


def _is_scope_box(elem):
    try:
        cat = elem.Category
        if not cat:
            return False
        return int(get_elementid_value(cat.Id)) == int(DB.BuiltInCategory.OST_VolumeOfInterest)
    except Exception:
        return False


def _parse_length_to_feet(value_text):
    """Parse offset text into internal feet.
    Supported examples:
      1
      1.25
      1'
      1'-0"
      0'-6"
      6"
    """
    if value_text is None:
        raise ValueError("Offset is empty.")
    txt = str(value_text).strip()
    if not txt:
        raise ValueError("Offset is empty.")

    # feet-inches pattern e.g. 1'-6"
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?\s*$", txt)
    if m:
        ft = float(m.group(1))
        inch = float(m.group(2) or 0.0)
        return ft + inch / 12.0

    # inches pattern e.g. 6"
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*\"\s*$", txt)
    if m:
        return float(m.group(1)) / 12.0

    # feet pattern with marker e.g. 1'
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*'\s*$", txt)
    if m:
        return float(m.group(1))

    # plain numeric as feet
    try:
        return float(txt)
    except Exception:
        raise ValueError("Unable to parse offset. Use formats like 1'-0\", 6\", or decimal feet.")


def _prompt_settings(scopebox_selected):
    dialog = WinForms.Form()
    dialog.Text = "Grid Head Offset"
    dialog.Width = 520
    dialog.Height = 255
    dialog.StartPosition = WinForms.FormStartPosition.CenterScreen
    dialog.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    dialog.MinimizeBox = False
    dialog.MaximizeBox = False

    lbl_source = WinForms.Label()
    lbl_source.Text = "Reference Region"
    lbl_source.Left = 20
    lbl_source.Top = 20
    lbl_source.Width = 160

    source_cb = WinForms.ComboBox()
    source_cb.Left = 190
    source_cb.Top = 16
    source_cb.Width = 285
    source_cb.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    source_cb.Items.Add("Crop View Region (per view)")
    source_cb.Items.Add("Selected Scope Box")
    source_cb.SelectedIndex = 1 if scopebox_selected else 0

    lbl_mode = WinForms.Label()
    lbl_mode.Text = "Offset Direction"
    lbl_mode.Left = 20
    lbl_mode.Top = 60
    lbl_mode.Width = 160

    mode_cb = WinForms.ComboBox()
    mode_cb.Left = 190
    mode_cb.Top = 56
    mode_cb.Width = 285
    mode_cb.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    mode_cb.Items.Add("Out")
    mode_cb.Items.Add("In")
    mode_cb.SelectedIndex = 0

    lbl_offset = WinForms.Label()
    lbl_offset.Text = "Offset (e.g. 1'-0\")"
    lbl_offset.Left = 20
    lbl_offset.Top = 100
    lbl_offset.Width = 160

    offset_tb = WinForms.TextBox()
    offset_tb.Left = 190
    offset_tb.Top = 96
    offset_tb.Width = 285
    offset_tb.Text = "1'-0\""

    note_lbl = WinForms.Label()
    note_lbl.Left = 20
    note_lbl.Top = 132
    note_lbl.Width = 455
    note_lbl.Height = 38
    note_lbl.Text = "Applies to all applicable views and visible line grids.\n" \
                    "If crop/scope boundary is unavailable in a view, that view is skipped."

    run_btn = WinForms.Button()
    run_btn.Text = "Run"
    run_btn.DialogResult = WinForms.DialogResult.OK
    run_btn.Left = 310
    run_btn.Top = 175
    run_btn.Width = 80

    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.DialogResult = WinForms.DialogResult.Cancel
    cancel_btn.Left = 395
    cancel_btn.Top = 175
    cancel_btn.Width = 80

    dialog.Controls.Add(lbl_source)
    dialog.Controls.Add(source_cb)
    dialog.Controls.Add(lbl_mode)
    dialog.Controls.Add(mode_cb)
    dialog.Controls.Add(lbl_offset)
    dialog.Controls.Add(offset_tb)
    dialog.Controls.Add(note_lbl)
    dialog.Controls.Add(run_btn)
    dialog.Controls.Add(cancel_btn)
    dialog.AcceptButton = run_btn
    dialog.CancelButton = cancel_btn

    if dialog.ShowDialog() != WinForms.DialogResult.OK:
        return None

    source = "scopebox" if source_cb.SelectedIndex == 1 else "crop"
    mode = "out" if mode_cb.SelectedIndex == 0 else "in"
    return {
        "source": source,
        "mode": mode,
        "offset_text": offset_tb.Text,
    }


def _view_basis(view):
    try:
        origin = view.Origin
    except Exception:
        origin = DB.XYZ.Zero

    n = view.ViewDirection.Normalize()
    x = None
    y = None
    try:
        x = view.RightDirection.Normalize()
    except Exception:
        x = None
    try:
        y = view.UpDirection.Normalize()
    except Exception:
        y = None

    if x is None or x.GetLength() < EPS:
        fallback = DB.XYZ.BasisZ
        if abs(n.DotProduct(fallback)) > 0.99:
            fallback = DB.XYZ.BasisX
        x = n.CrossProduct(fallback).Normalize()
    if y is None or y.GetLength() < EPS:
        y = n.CrossProduct(x).Normalize()

    # Ensure right-handed basis around view direction
    if x.CrossProduct(y).DotProduct(n) < 0:
        y = y.Negate()

    return origin, x, y, n


def _project_to_view_2d(pt, origin, xaxis, yaxis, normal):
    vec = pt - origin
    return _vec2(vec.DotProduct(xaxis), vec.DotProduct(yaxis)), vec.DotProduct(normal)


def _point_from_view_2d(uv, offset_n, origin, xaxis, yaxis, normal):
    return origin + xaxis.Multiply(uv[0]) + yaxis.Multiply(uv[1]) + normal.Multiply(offset_n)


def _segment_plane_intersection(p0, p1, plane_origin, plane_normal):
    d0 = (p0 - plane_origin).DotProduct(plane_normal)
    d1 = (p1 - plane_origin).DotProduct(plane_normal)

    if abs(d0) < EPS and abs(d1) < EPS:
        return [p0, p1]
    if abs(d0) < EPS:
        return [p0]
    if abs(d1) < EPS:
        return [p1]

    if d0 * d1 > 0:
        return []

    t = d0 / float(d0 - d1)
    return [p0 + (p1 - p0).Multiply(t)]


def _scopebox_boundary_segments_on_view(scopebox_elem, view):
    origin, xaxis, yaxis, normal = _view_basis(view)

    try:
        opts = DB.Options()
        opts.ComputeReferences = False
        geom_elem = scopebox_elem.get_Geometry(opts)
    except Exception:
        geom_elem = None

    points = []
    seen = set()
    if geom_elem:
        for gobj in geom_elem:
            solid = gobj if isinstance(gobj, DB.Solid) else None
            if not solid or solid.Edges is None:
                continue
            for edge in solid.Edges:
                try:
                    crv = edge.AsCurve()
                    tess = list(crv.Tessellate())
                except Exception:
                    tess = []
                if len(tess) < 2:
                    continue
                for i in range(len(tess) - 1):
                    for pint in _segment_plane_intersection(tess[i], tess[i + 1], origin, normal):
                        k = _pt_key(pint)
                        if k not in seen:
                            seen.add(k)
                            points.append(pint)

    if len(points) < 3:
        return []

    pts2d = []
    for pt in points:
        uv, _ = _project_to_view_2d(pt, origin, xaxis, yaxis, normal)
        pts2d.append(uv)

    # unique 2d points
    unique2d = []
    seen2d = set()
    for uv in pts2d:
        k = _pt2_key(uv)
        if k not in seen2d:
            seen2d.add(k)
            unique2d.append(uv)

    if len(unique2d) < 3:
        return []

    cx = sum(p[0] for p in unique2d) / float(len(unique2d))
    cy = sum(p[1] for p in unique2d) / float(len(unique2d))
    ordered = sorted(unique2d, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))

    segments = []
    for i in range(len(ordered)):
        a = ordered[i]
        b = ordered[(i + 1) % len(ordered)]
        if _v2_len2(_v2_sub(a, b)) > EPS:
            segments.append((a, b))
    return segments


def _crop_boundary_segments_on_view(view):
    if not getattr(view, "CropBoxActive", False):
        return []
    try:
        crsm = view.GetCropRegionShapeManager()
        loops = list(crsm.GetCropShape())
    except Exception:
        return []

    origin, xaxis, yaxis, normal = _view_basis(view)

    segments = []
    for loop in loops:
        for curve in loop:
            try:
                tess = list(curve.Tessellate())
            except Exception:
                tess = []
            if len(tess) < 2:
                continue
            for i in range(len(tess) - 1):
                p0 = tess[i]
                p1 = tess[i + 1]
                uv0, _ = _project_to_view_2d(p0, origin, xaxis, yaxis, normal)
                uv1, _ = _project_to_view_2d(p1, origin, xaxis, yaxis, normal)
                if _v2_len2(_v2_sub(uv0, uv1)) > EPS:
                    segments.append((uv0, uv1))
    return segments


def _line_segment_intersection_2d(line_p, line_d, seg_a, seg_b):
    seg_d = _v2_sub(seg_b, seg_a)
    denom = _v2_cross(line_d, seg_d)
    if abs(denom) < EPS:
        return None
    diff = _v2_sub(seg_a, line_p)
    t = _v2_cross(diff, seg_d) / denom
    u = _v2_cross(diff, line_d) / denom
    if u < -EPS or u > 1.0 + EPS:
        return None
    return _v2_add(line_p, _v2_mul(line_d, t))


def _collect_intersections_2d(line_p0, line_p1, boundary_segments):
    d = _v2_sub(line_p1, line_p0)
    if _v2_len2(d) < EPS:
        return []

    points = []
    seen = set()
    for seg_a, seg_b in boundary_segments:
        ip = _line_segment_intersection_2d(line_p0, d, seg_a, seg_b)
        if ip is None:
            continue
        k = _pt2_key(ip, tol=1e-5)
        if k not in seen:
            seen.add(k)
            points.append(ip)
    return points


def _nearest_point(target, points):
    best = None
    best_d2 = None
    for p in points:
        d2 = _v2_len2(_v2_sub(p, target))
        if best is None or d2 < best_d2:
            best = p
            best_d2 = d2
    return best


def _choose_intersections_for_grid_ends(line_p0, line_p1, intersections):
    if len(intersections) < 2:
        return None, None
    i0 = _nearest_point(line_p0, intersections)
    i1 = _nearest_point(line_p1, intersections)
    if i0 is None or i1 is None:
        return None, None
    # avoid using same point for both ends if possible
    if _v2_len2(_v2_sub(i0, i1)) < 1e-7 and len(intersections) > 2:
        # pick farthest for one side
        far = None
        far_d2 = -1.0
        for p in intersections:
            d2 = _v2_len2(_v2_sub(p, i0))
            if d2 > far_d2:
                far = p
                far_d2 = d2
        i1 = far
    return i0, i1


def _get_line_grid_curve_in_view(grid, view):
    for extent_mode in (DB.DatumExtentType.ViewSpecific, DB.DatumExtentType.Model):
        try:
            curves = grid.GetCurvesInView(extent_mode, view)
        except Exception:
            curves = None
        if not curves:
            continue
        for c in curves:
            if isinstance(c, DB.Line):
                return c
    return None


def _collect_target_views(doc):
    views = DB.FilteredElementCollector(doc)\
              .OfClass(DB.View)\
              .WhereElementIsNotElementType()\
              .ToElements()
    filtered = []
    for view in views:
        try:
            if view.IsTemplate:
                continue
            if isinstance(view, DB.ViewSheet):
                continue
            # skip view types that never host datum adjustments in this workflow
            vt = view.ViewType
            if vt in (
                DB.ViewType.Schedule,
                DB.ViewType.DrawingSheet,
                DB.ViewType.Legend,
                DB.ViewType.ProjectBrowser,
                DB.ViewType.SystemBrowser,
                DB.ViewType.Report,
            ):
                continue
            filtered.append(view)
        except Exception:
            continue
    return filtered


def run():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        forms.alert("No active model document.")
        return

    selected_scopebox = None
    try:
        for sid in uidoc.Selection.GetElementIds():
            elem = doc.GetElement(sid)
            if elem and _is_scope_box(elem):
                selected_scopebox = elem
                break
    except Exception:
        selected_scopebox = None

    settings = _prompt_settings(scopebox_selected=(selected_scopebox is not None))
    if not settings:
        return

    try:
        offset_ft = _parse_length_to_feet(settings["offset_text"])
    except Exception as parse_err:
        forms.alert("Invalid offset: {}".format(parse_err))
        return

    if offset_ft < 0:
        forms.alert("Offset must be zero or positive.")
        return

    source = settings["source"]
    mode = settings["mode"]  # out or in

    if source == "scopebox" and not selected_scopebox:
        forms.alert(
            "Selected Scope Box mode was chosen, but no scope box is selected.\n"
            "Please select one scope box and run again."
        )
        return

    views = _collect_target_views(doc)
    if not views:
        forms.alert("No applicable views found.")
        return

    updated = 0
    skipped_no_boundary = 0
    skipped_no_grids = 0
    skipped_non_line_grid = 0
    failed_set_curve = 0
    touched_views = 0

    with revit.Transaction("Grid Head Offset"):
        for view in views:
            origin, xaxis, yaxis, normal = _view_basis(view)

            if source == "crop":
                boundary_segments = _crop_boundary_segments_on_view(view)
            else:
                boundary_segments = _scopebox_boundary_segments_on_view(selected_scopebox, view)

            if not boundary_segments:
                skipped_no_boundary += 1
                continue

            grids = DB.FilteredElementCollector(doc, view.Id)\
                      .OfClass(DB.Grid)\
                      .WhereElementIsNotElementType()\
                      .ToElements()
            if not grids:
                skipped_no_grids += 1
                continue

            view_changed = False
            for grid in grids:
                line_curve = _get_line_grid_curve_in_view(grid, view)
                if not line_curve:
                    skipped_non_line_grid += 1
                    continue

                p0 = line_curve.GetEndPoint(0)
                p1 = line_curve.GetEndPoint(1)

                p0_2d, p0_n = _project_to_view_2d(p0, origin, xaxis, yaxis, normal)
                p1_2d, p1_n = _project_to_view_2d(p1, origin, xaxis, yaxis, normal)
                n_off = (p0_n + p1_n) * 0.5

                intersections = _collect_intersections_2d(p0_2d, p1_2d, boundary_segments)
                i0_2d, i1_2d = _choose_intersections_for_grid_ends(p0_2d, p1_2d, intersections)
                if i0_2d is None or i1_2d is None:
                    continue

                inter0 = _point_from_view_2d(i0_2d, n_off, origin, xaxis, yaxis, normal)
                inter1 = _point_from_view_2d(i1_2d, n_off, origin, xaxis, yaxis, normal)

                dir_vec = p1 - p0
                if dir_vec.GetLength() < EPS:
                    continue
                dir_unit = dir_vec.Normalize()
                out0 = dir_unit.Negate()
                out1 = dir_unit
                sign = 1.0 if mode == "out" else -1.0

                new_p0 = inter0 + out0.Multiply(sign * offset_ft)
                new_p1 = inter1 + out1.Multiply(sign * offset_ft)

                # preserve orientation as original endpoints
                if new_p0.DistanceTo(p0) + new_p1.DistanceTo(p1) > \
                        new_p0.DistanceTo(p1) + new_p1.DistanceTo(p0):
                    new_p0, new_p1 = new_p1, new_p0

                try:
                    grid.SetDatumExtentType(DB.DatumEnds.End0, view, DB.DatumExtentType.ViewSpecific)
                except Exception:
                    pass
                try:
                    grid.SetDatumExtentType(DB.DatumEnds.End1, view, DB.DatumExtentType.ViewSpecific)
                except Exception:
                    pass

                try:
                    new_curve = DB.Line.CreateBound(new_p0, new_p1)
                    grid.SetCurveInView(DB.DatumExtentType.ViewSpecific, view, new_curve)
                    updated += 1
                    view_changed = True
                except Exception as set_err:
                    failed_set_curve += 1
                    logger.debug(
                        "Grid set curve failed | view=%s | grid=%s | %s",
                        view.Name if hasattr(view, "Name") else "<view>",
                        get_elementid_value(grid.Id),
                        set_err
                    )
                    continue

            if view_changed:
                touched_views += 1

    msg = []
    msg.append("Grid Head Offset completed.")
    msg.append("Mode: {} | Offset: {} ft | Source: {}".format(mode.upper(), round(offset_ft, 6), source))
    msg.append("Views processed: {}".format(len(views)))
    msg.append("Views changed: {}".format(touched_views))
    msg.append("Grid heads updated: {}".format(updated))
    msg.append("Views skipped (no boundary): {}".format(skipped_no_boundary))
    msg.append("Views skipped (no grids): {}".format(skipped_no_grids))
    msg.append("Skipped non-line grid curves: {}".format(skipped_non_line_grid))
    msg.append("Failed set-curve attempts: {}".format(failed_set_curve))

    forms.alert("\n".join(msg), title="Grid Head Offset", warn_icon=False)


if __name__ == "__main__":
    run()
