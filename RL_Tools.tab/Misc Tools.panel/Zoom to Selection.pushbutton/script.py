# -*- coding: utf-8 -*-
"""Zoom selected elements using best model view or sheet viewport context."""

# pylint: disable=import-error,invalid-name,broad-except,too-many-lines
import math
import re
import time

import clr

clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
try:
    clr.AddReference("RevitAPIUI")
except Exception:
    pass

import System.Windows.Forms as WinForms
from System.Collections.Generic import List

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

try:
    from Autodesk.Revit.UI import RevitCommandId, PostableCommand
except Exception:
    RevitCommandId = None
    PostableCommand = None

try:
    from Autodesk.Revit.UI.Selection import ObjectType
except Exception:
    ObjectType = None

try:
    from Autodesk.Revit.Exceptions import OperationCanceledException
except Exception:
    class OperationCanceledException(Exception):
        pass


__title__ = "Zoom to Selection"
logger = script.get_logger()
get_elementid_value = get_elementid_value_func()
EPS = 1e-9
MIN_RECT = 1e-6


def _safe(v):
    try:
        return str(v)
    except Exception:
        return ""


def _eid_int(eid):
    if eid is None:
        return None
    for getter in (
        lambda x: int(get_elementid_value(x)),
        lambda x: int(x.IntegerValue),
        lambda x: int(x.Value),
        lambda x: int(x),
    ):
        try:
            return getter(eid)
        except Exception:
            pass
    return None


def _valid_eid(eid):
    return _eid_int(eid) not in (None, -1)


def _xyz(x, y, z):
    return DB.XYZ(float(x), float(y), float(z))


def _vsub(a, b):
    return _xyz(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def _cat_name(elem):
    try:
        c = elem.Category
        if c and c.Name:
            return str(c.Name)
    except Exception:
        pass
    return "(No Category)"


def _parse_len_ft(txt):
    txt = _safe(txt).strip()
    if not txt:
        raise ValueError("Zoom extend is empty.")
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*'\s*(\d+(?:\.\d+)?)?\s*\"?\s*$", txt)
    if m:
        return float(m.group(1)) + float(m.group(2) or 0.0) / 12.0
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*\"\s*$", txt)
    if m:
        return float(m.group(1)) / 12.0
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*'\s*$", txt)
    if m:
        return float(m.group(1))
    return float(txt)


def _prompt():
    f = WinForms.Form()
    f.Text = __title__
    f.Width = 480
    f.Height = 205
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.MinimizeBox = False
    f.MaximizeBox = False
    l = WinForms.Label()
    l.Left, l.Top, l.Width, l.Height = 16, 16, 430, 38
    l.Text = "Auto-open/activate best view or sheet viewport.\nLinked picks supported via point-pick fallback."
    t = WinForms.TextBox()
    t.Left, t.Top, t.Width = 150, 66, 290
    t.Text = '6"'
    l2 = WinForms.Label()
    l2.Left, l2.Top, l2.Width = 16, 70, 120
    l2.Text = "Zoom Extend"
    run = WinForms.Button()
    run.Left, run.Top, run.Width = 278, 110, 75
    run.Text = "Run"
    run.DialogResult = WinForms.DialogResult.OK
    cancel = WinForms.Button()
    cancel.Left, cancel.Top, cancel.Width = 360, 110, 80
    cancel.Text = "Cancel"
    cancel.DialogResult = WinForms.DialogResult.Cancel
    for c in (l, l2, t, run, cancel):
        f.Controls.Add(c)
    f.AcceptButton, f.CancelButton = run, cancel
    if f.ShowDialog() != WinForms.DialogResult.OK:
        return None
    return t.Text


def _dedupe(ts):
    seen = set()
    out = []
    for t in ts:
        if t["uid"] in seen:
            continue
        seen.add(t["uid"])
        out.append(t)
    return out


def _collect_targets(doc, uidoc):
    issues = []
    targets = []
    try:
        sids = list(uidoc.Selection.GetElementIds())
    except Exception:
        sids = []
    for sid in sids:
        e = doc.GetElement(sid)
        if not e or isinstance(e, DB.Viewport):
            continue
        if isinstance(e, DB.ElementType) or isinstance(e, DB.View):
            issues.append((_cat_name(e), "Selection type is not zoomable."))
            continue
        targets.append({"uid": "H:{}".format(_eid_int(sid)), "kind": "host", "eid": sid})
    if targets:
        return _dedupe(targets), issues
    if ObjectType is None:
        issues.append(("(Unknown)", "Point pick API unavailable."))
        return [], issues
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.PointOnElement, "Select elements to zoom")
    except OperationCanceledException:
        return [], issues
    except Exception as ex:
        issues.append(("(Unknown)", "Picking failed: {}".format(ex)))
        return [], issues
    for r in refs:
        leid = getattr(r, "LinkedElementId", None)
        eid = getattr(r, "ElementId", None)
        if _valid_eid(leid) and _valid_eid(eid):
            li, le = _eid_int(eid), _eid_int(leid)
            targets.append({"uid": "L:{}:{}".format(li, le), "kind": "linked", "lid": eid, "leid": leid})
        elif _valid_eid(eid):
            e = doc.GetElement(eid)
            if e and not isinstance(e, DB.Viewport):
                targets.append({"uid": "H:{}".format(_eid_int(eid)), "kind": "host", "eid": eid})
    return _dedupe(targets), issues


def _is_model_view(v):
    if not v:
        return False
    try:
        if v.IsTemplate:
            return False
    except Exception:
        pass
    if isinstance(v, DB.ViewSheet):
        return False
    vt = getattr(v, "ViewType", None)
    bad = {
        DB.ViewType.Legend, DB.ViewType.Schedule, DB.ViewType.DrawingSheet,
        DB.ViewType.ProjectBrowser, DB.ViewType.SystemBrowser, DB.ViewType.Report,
        DB.ViewType.DraftingView,
    }
    if hasattr(DB.ViewType, "PanelSchedule"):
        bad.add(DB.ViewType.PanelSchedule)
    if vt in bad:
        return False
    try:
        if isinstance(v, DB.View3D) and v.IsPerspective:
            return False
    except Exception:
        pass
    return True


def _open_view_ids(uidoc):
    out = set()
    try:
        for uv in uidoc.GetOpenUIViews():
            out.add(_eid_int(uv.ViewId))
    except Exception:
        pass
    return out


def _collect_candidates(doc, uidoc):
    open_ids = _open_view_ids(uidoc)
    cs = []
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View).WhereElementIsNotElementType().ToElements():
        if not _is_model_view(v):
            continue
        vid = _eid_int(v.Id)
        cs.append({"kind": "model", "id": "M:{}".format(vid), "view_id": v.Id, "is_open": vid in open_ids,
                   "display": "View: {} ({})".format(_safe(getattr(v, "Name", "")), _safe(getattr(v, "ViewType", "")) )})
    sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).WhereElementIsNotElementType().ToElements()
    sheets = sorted(sheets, key=lambda s: (_safe(getattr(s, "SheetNumber", "")).lower(), _safe(getattr(s, "Name", "")).lower()))
    for s in sheets:
        try:
            vpids = list(s.GetAllViewports())
        except Exception:
            vpids = []
        for vpid in vpids:
            vp = doc.GetElement(vpid)
            if not vp:
                continue
            pv = doc.GetElement(vp.ViewId)
            if not _is_model_view(pv):
                continue
            sid_i, vpid_i = _eid_int(s.Id), _eid_int(vpid)
            cs.append({"kind": "sheet", "id": "S:{}:{}".format(sid_i, vpid_i), "sheet_id": s.Id, "viewport_id": vpid,
                       "view_id": pv.Id, "is_open": sid_i in open_ids,
                       "display": "Sheet {} - {} | {}".format(_safe(getattr(s, "SheetNumber", "")), _safe(getattr(s, "Name", "")), _safe(getattr(pv, "Name", "")))})
    return cs


def _bbox(elem, view):
    if elem is None:
        return None, False
    if view is not None:
        try:
            b = elem.get_BoundingBox(view)
            if b and b.Min and b.Max:
                return b, True
        except Exception:
            pass
    try:
        b = elem.get_BoundingBox(None)
        if b and b.Min and b.Max:
            return b, False
    except Exception:
        pass
    return None, False


def _link_trf(li):
    for name in ("GetTotalTransform", "GetTransform"):
        m = getattr(li, name, None)
        if m:
            try:
                trf = m()
                if trf:
                    return trf
            except Exception:
                pass
    return DB.Transform.Identity


def _bbox_pts(b, ext, trf):
    if not b:
        return []
    mnx, mny, mnz = b.Min.X - ext, b.Min.Y - ext, b.Min.Z - ext
    mxx, mxy, mxz = b.Max.X + ext, b.Max.Y + ext, b.Max.Z + ext
    pts = [_xyz(x, y, z) for x in (mnx, mxx) for y in (mny, mxy) for z in (mnz, mxz)]
    if trf is None:
        return pts
    out = []
    for p in pts:
        try:
            out.append(trf.OfPoint(p))
        except Exception:
            out.append(p)
    return out


def _target_pts_host(doc, t, view, ext):
    if t["kind"] == "host":
        e = doc.GetElement(t["eid"])
        c = _cat_name(e)
        b, strong = _bbox(e, view)
        if not b:
            return [], False, c, "No bounding box."
        return _bbox_pts(b, ext, None), strong, c, ""
    li = doc.GetElement(t["lid"])
    if not isinstance(li, DB.RevitLinkInstance):
        return [], False, "(Linked)", "Link instance missing."
    ld = li.GetLinkDocument()
    if ld is None:
        return [], False, "(Linked)", "Linked document not loaded."
    le = ld.GetElement(t["leid"])
    c = _cat_name(le)
    try:
        b = le.get_BoundingBox(None)
    except Exception:
        b = None
    if not b:
        return [], False, c, "Linked element has no bounding box."
    return _bbox_pts(b, ext, _link_trf(li)), False, c, ""


def _rot_ang(vp):
    txt = _safe(getattr(vp, "Rotation", "")).lower()
    if "clockwise" in txt and "counter" not in txt:
        return -math.pi * 0.5
    if "counter" in txt:
        return math.pi * 0.5
    if "upside" in txt or "180" in txt:
        return math.pi
    return 0.0


def _proj_sheet(vp, v, p):
    try:
        if hasattr(vp, "GetProjectionToSheetTransform"):
            trf = vp.GetProjectionToSheetTransform()
            if trf:
                return trf.OfPoint(p), ""
    except Exception:
        pass
    try:
        o, r, u = v.Origin, v.RightDirection, v.UpDirection
        sc = float(v.Scale or 1.0)
        vec = _vsub(p, o)
        uu = vec.DotProduct(r) / sc
        vv = vec.DotProduct(u) / sc
        ol = v.Outline
        cu = (ol.Min.U + ol.Max.U) * 0.5
        cv = (ol.Min.V + ol.Max.V) * 0.5
        dx, dy = uu - cu, vv - cv
        a = _rot_ang(vp)
        rx = dx * math.cos(a) - dy * math.sin(a)
        ry = dx * math.sin(a) + dy * math.cos(a)
        c = vp.GetBoxCenter()
        return _xyz(c.X + rx, c.Y + ry, c.Z), ""
    except Exception:
        return None, "Projection to sheet failed."


def _rect_from_pts(pts):
    if not pts:
        return None
    return (min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts),
            max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))


def _merge_rect(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def _rect_pts(r):
    if r is None:
        return None, None
    mnx, mny, mnz, mxx, mxy, mxz = r
    if (mxx - mnx) < MIN_RECT:
        mnx, mxx = mnx - MIN_RECT * 0.5, mxx + MIN_RECT * 0.5
    if (mxy - mny) < MIN_RECT:
        mny, mxy = mny - MIN_RECT * 0.5, mxy + MIN_RECT * 0.5
    if (mxz - mnz) < MIN_RECT:
        mnz, mxz = mnz - MIN_RECT * 0.5, mxz + MIN_RECT * 0.5
    return _xyz(mnx, mny, mnz), _xyz(mxx, mxy, mxz)


def _eval_candidate(doc, c, targets, ext):
    rect, cov, strong, miss = None, 0, 0, {}
    if c["kind"] == "model":
        v = doc.GetElement(c["view_id"])
        if not _is_model_view(v):
            return None
        for t in targets:
            pts, s, cat, rsn = _target_pts_host(doc, t, v, ext)
            if not pts:
                miss[t["uid"]] = (cat, rsn)
                continue
            cov += 1
            strong += 1 if s else 0
            rect = _merge_rect(rect, _rect_from_pts(pts))
    else:
        vp, pv = doc.GetElement(c["viewport_id"]), doc.GetElement(c["view_id"])
        if not vp or not _is_model_view(pv):
            return None
        for t in targets:
            pts, s, cat, rsn = _target_pts_host(doc, t, pv, ext)
            if not pts:
                miss[t["uid"]] = (cat, rsn)
                continue
            sp = []
            for p in pts:
                p2, why = _proj_sheet(vp, pv, p)
                if p2:
                    sp.append(p2)
                elif t["uid"] not in miss:
                    miss[t["uid"]] = (cat, why)
            if not sp:
                miss[t["uid"]] = (cat, "Cannot project to sheet viewport.")
                continue
            cov += 1
            strong += 1 if s else 0
            rect = _merge_rect(rect, _rect_from_pts(sp))
    if cov <= 0 or rect is None:
        return None
    pmin, pmax = _rect_pts(rect)
    area = max(pmax.X - pmin.X, MIN_RECT) * max(pmax.Y - pmin.Y, MIN_RECT)
    return {"candidate": c, "coverage": cov, "strong": strong, "missing": miss, "min": pmin, "max": pmax, "area": area}


def _rank(evals, total):
    def k(e):
        c = e["candidate"]
        return (
            0 if e["coverage"] >= total else 1,
            -e["coverage"],
            0 if c["kind"] == "sheet" else 1,
            float(e["area"]),
            0 if c.get("is_open") else 1,
            -e["strong"],
        )
    return sorted(evals, key=k)


def _pump(n=6, sl=0.03):
    for _ in range(n):
        try:
            WinForms.Application.DoEvents()
        except Exception:
            pass
        try:
            time.sleep(sl)
        except Exception:
            pass


def _uiapp():
    try:
        return __revit__
    except Exception:
        return None


def _post(cmd):
    if RevitCommandId is None or PostableCommand is None:
        return False
    app = _uiapp()
    if not app:
        return False
    try:
        cid = RevitCommandId.LookupPostableCommandId(cmd)
        if not cid:
            return False
        if hasattr(app, "CanPostCommand") and not app.CanPostCommand(cid):
            return False
        app.PostCommand(cid)
        return True
    except Exception:
        return False


def _req_view(uidoc, v):
    try:
        uidoc.RequestViewChange(v)
        return True
    except Exception:
        pass
    try:
        uidoc.ActiveView = v
        return True
    except Exception:
        return False


def _set_sel(uidoc, eids):
    try:
        ids = List[DB.ElementId]()
        for eid in eids:
            ids.Add(eid)
        uidoc.Selection.SetElementIds(ids)
        return True
    except Exception:
        return False


def _uiview(uidoc, vid):
    vi = _eid_int(vid)
    try:
        for uv in uidoc.GetOpenUIViews():
            if _eid_int(uv.ViewId) == vi:
                return uv
    except Exception:
        pass
    return None


def _zoom(uidoc, vid, pmin, pmax):
    uv = _uiview(uidoc, vid)
    if not uv:
        try:
            uv = _uiview(uidoc, uidoc.ActiveView.Id)
        except Exception:
            uv = None
    if not uv:
        return False, "No open UI view for zoom."
    try:
        uv.ZoomAndCenterRectangle(pmin, pmax)
        return True, ""
    except Exception as ex:
        return False, str(ex)


def _apply(doc, uidoc, ev):
    c = ev["candidate"]
    if c["kind"] == "model":
        v = doc.GetElement(c["view_id"])
        if not v or not _req_view(uidoc, v):
            return False, "Could not open model view."
        _pump()
        ok, msg = _zoom(uidoc, v.Id, ev["min"], ev["max"])
        return ok, ("Zoomed: " + c["display"]) if ok else msg

    s = doc.GetElement(c["sheet_id"])
    vp = doc.GetElement(c["viewport_id"])
    if not s or not vp or not _req_view(uidoc, s):
        return False, "Could not open sheet viewport context."
    _pump()
    _set_sel(uidoc, [vp.Id])
    act = False
    if PostableCommand is not None and hasattr(PostableCommand, "ActivateView"):
        act = _post(PostableCommand.ActivateView)
    _pump(8, 0.04)
    ok, msg = _zoom(uidoc, s.Id, ev["min"], ev["max"])
    if not ok:
        return False, msg
    return True, ("Zoomed sheet viewport ({})".format("activated" if act else "fallback")) + ": " + c["display"]


def _fallback_3d(doc, uidoc):
    v3 = None
    views = DB.FilteredElementCollector(doc).OfClass(DB.View3D).WhereElementIsNotElementType().ToElements()
    good = []
    for v in views:
        try:
            if v.IsTemplate or v.IsPerspective:
                continue
        except Exception:
            continue
        good.append(v)
    if not good:
        return False, "No fallback 3D view available."
    for v in good:
        if _safe(getattr(v, "Name", "")) == "{3D}":
            v3 = v
            break
    if v3 is None:
        good.sort(key=lambda x: _safe(getattr(x, "Name", "")).lower())
        v3 = good[0]
    if not _req_view(uidoc, v3):
        return False, "Could not open fallback 3D view."
    _pump()
    posted = False
    if PostableCommand is not None and hasattr(PostableCommand, "SectionBox"):
        posted = _post(PostableCommand.SectionBox)
        _pump(10, 0.04)
    return True, "Opened fallback 3D view{}.".format(" and posted Section Box" if posted else "")


def _issue_report(rows):
    if not rows:
        return
    m = {}
    for c, r in rows:
        key = (_safe(c) or "(Unknown Category)", _safe(r) or "(No reason)")
        m[key] = m.get(key, 0) + 1
    lines = ["Some selected categories are not available for zoom.", "", "Category | Reason | Count", "-" * 42]
    for (c, r), n in sorted(m.items(), key=lambda x: (x[0][0].lower(), x[0][1].lower())):
        lines.append("{} | {} | {}".format(c, r, n))
    forms.alert("\n".join(lines), title=__title__, warn_icon=False)


def _uncovered_issues(doc, targets, evals):
    cov = {t["uid"]: False for t in targets}
    rsn = {}
    for e in evals:
        miss = e.get("missing", {})
        for t in targets:
            uid = t["uid"]
            if uid not in miss:
                cov[uid] = True
            elif uid not in rsn:
                rsn[uid] = miss[uid]
    out = []
    for t in targets:
        if cov.get(t["uid"]):
            continue
        c, r = rsn.get(t["uid"], (_cat_name(doc.GetElement(t.get("eid")) if t["kind"] == "host" else None), "No suitable view/viewport."))
        out.append((c, r))
    return out


def _next_window(doc, uidoc, evals, total, note):
    f = WinForms.Form()
    f.Text = "Next View - " + __title__
    f.Width = 610
    f.Height = 225
    f.StartPosition = WinForms.FormStartPosition.CenterScreen
    f.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    f.MinimizeBox = False
    f.MaximizeBox = False
    f.TopMost = True
    s1 = WinForms.Label()
    s1.Left, s1.Top, s1.Width, s1.Height = 18, 16, 565, 52
    s2 = WinForms.Label()
    s2.Left, s2.Top, s2.Width, s2.Height = 18, 72, 565, 58
    b1 = WinForms.Button()
    b1.Left, b1.Top, b1.Width = 392, 136, 90
    b1.Text = "Next View"
    b2 = WinForms.Button()
    b2.Left, b2.Top, b2.Width = 490, 136, 90
    b2.Text = "Close"
    b2.DialogResult = WinForms.DialogResult.OK
    for c in (s1, s2, b1, b2):
        f.Controls.Add(c)
    f.CancelButton = b2
    state = {"i": 0}

    def refresh():
        if not evals:
            s1.Text = "No additional appropriate views were found."
            s2.Text = note
            b1.Enabled = False
            return
        i = state["i"]
        e = evals[i]
        c = e["candidate"]
        s1.Text = "Context {} of {} | {} | Coverage {}/{}".format(i + 1, len(evals), "Sheet viewport" if c["kind"] == "sheet" else "Model view", e["coverage"], total)
        s2.Text = c["display"]
        b1.Enabled = i < len(evals) - 1

    def next_click(sender, args):
        del sender, args
        if not evals or state["i"] >= len(evals) - 1:
            refresh()
            return
        state["i"] += 1
        ok, msg = _apply(doc, uidoc, evals[state["i"]])
        if not ok:
            s2.Text = "Failed: " + msg
        refresh()

    b1.Click += next_click
    refresh()
    f.ShowDialog()


def run():
    doc, uidoc = revit.doc, revit.uidoc
    if not doc or not uidoc:
        forms.alert("No active project document.", title=__title__)
        return
    txt = _prompt()
    if txt is None:
        return
    try:
        ext = _parse_len_ft(txt)
    except Exception as ex:
        forms.alert("Invalid zoom extend: {}".format(ex), title=__title__)
        return
    if ext < 0:
        forms.alert("Zoom extend must be zero or positive.", title=__title__)
        return

    targets, issues = _collect_targets(doc, uidoc)
    if not targets:
        forms.alert("No valid elements selected for zoom.", title=__title__)
        return

    evals = []
    for c in _collect_candidates(doc, uidoc):
        try:
            e = _eval_candidate(doc, c, targets, ext)
        except Exception as ex:
            logger.debug("Eval failed %s: %s", c.get("id"), ex)
            e = None
        if e:
            evals.append(e)
    ranked = _rank(evals, len(targets))
    issues.extend(_uncovered_issues(doc, targets, ranked))

    note = ""
    if not ranked:
        ok, msg = _fallback_3d(doc, uidoc)
        note = msg
        if not ok:
            forms.alert(msg, title=__title__)
        _issue_report(issues)
        _next_window(doc, uidoc, [], len(targets), note)
        return

    ok, msg = _apply(doc, uidoc, ranked[0])
    if not ok:
        forms.alert("Could not zoom first context: {}".format(msg), title=__title__)

    _issue_report(issues)
    _next_window(doc, uidoc, ranked, len(targets), note)


if __name__ == "__main__":
    run()
