# -*- coding: utf-8 -*-
"""Space Boundary - room and space boundaries as filled regions, tracked in the model."""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import datetime
import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

try:
    from pyrevit import HOST_APP
except Exception:
    HOST_APP = None

__title__ = "Space Boundary"

# The difference report outlives this run and its row buttons reach Revit
# through an ExternalEvent whose handler is Python. A recycled engine would
# kill both, so the button keeps its engine (the Sheet Manager rule).
__persistentengine__ = True

# Must equal space_boundary_ui.ACTIVE_ENVVAR (test-pinned).
ACTIVE_ENVVAR = "EASYBIM_SPACE_BOUNDARY_ACTIVE"

#: Which list in the model's shared record belongs to this tool: the regions
#: a reviewer accepted as deliberate differences. Kept apart from the
#: room-to-region record on each region because a region another user owns
#: cannot be written, yet its difference still has to be acceptable.
TOOL_KEY = "space_boundary"

STALE_MODULES = (
    "space_boundary_ui",
    "space_boundary_revit",
    "space_boundary_storage",
    "space_boundary_state",
    "space_boundary_settings",
    "easybim.type_checklist",
    "easybim.local_settings",
    "easybim.check_windows",
    "easybim.model_store",
    "easybim.external_events",
)

logger = script.get_logger()


def _drop_stale_modules():
    """Let an extension update take effect without restarting Revit.

    ``__persistentengine__`` keeps this engine's ``sys.modules`` alive across
    a pyRevit reload, so updated modules would never be re-imported.  Dropping
    them is only safe while no report is open, so the flag is read from the
    pyRevit envvar store rather than from the module we may be replacing.
    """
    try:
        if script.get_envvar(ACTIVE_ENVVAR):
            return False
    except Exception:
        return False
    dropped = 0
    for name in list(sys.modules):
        if name in STALE_MODULES:
            sys.modules.pop(name, None)
            dropped += 1
    if dropped:
        logger.debug("Reloaded %s Space Boundary module(s).", dropped)
    return bool(dropped)


def _uiapp():
    if HOST_APP is not None:
        try:
            uiapp = HOST_APP.uiapp
            if uiapp is not None:
                return uiapp
        except Exception:
            pass
    try:
        return __revit__  # noqa: F821 - pyRevit runtime global
    except Exception:
        return None


def _uidoc(uiapp):
    uidoc = getattr(uiapp, "ActiveUIDocument", None) if uiapp is not None else None
    return uidoc if uidoc is not None else revit.uidoc


def _now_utc():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class Run(object):
    """One command run: the catalog, the plan, the report, and the writes.

    Everything the windows call back into goes through here so there is one
    place that knows how the sources, the views and the boundary location
    were resolved - the setup, the dry run and the difference report all have
    to agree about that or a region would be judged by a rule it was never
    drawn with.
    """

    def __init__(self, doc, brevit, bstate, bstorage, model_store):
        self.doc = doc
        self.revit = brevit
        self.state = bstate
        self.storage = bstorage
        self.model_store = model_store
        self.settings = {}
        self.picked = []
        self.sources = []
        self.views = []
        self.config = {}
        self._catalog = {}

    # -- reading -----------------------------------------------------------

    def collect_catalog(self, settings):
        brevit, bstate = self.revit, self.state
        self.sources = brevit.collect_sources(self.doc)
        self.views = brevit.collect_views(self.doc)
        kind = settings.get("kind") or bstate.KIND_ROOM
        brevit.apply_kind(self.sources, kind)
        name, source, note = brevit.resolved_boundary_location(self.doc, kind)
        tracked = len(bstorage_rows(self.storage, self.doc))
        self._catalog = {
            "sources": self.sources,
            "views": self.views,
            "active_view": brevit.active_view_row(self.doc, self.views),
            "region_types": brevit.collect_region_types(self.doc),
            "line_styles": brevit.collect_line_styles(self.doc),
            "boundary": {"name": name, "source": source, "note": note},
            "tracked_sentence": _tracked_sentence(tracked),
        }
        return self._catalog

    def refresh_boundary(self, settings):
        """The document's setting is per spatial type, so it is re-read when
        the kind changes rather than assumed to be the same for both."""
        name, source, note = self.revit.resolved_boundary_location(
            self.doc, settings.get("kind") or self.state.KIND_ROOM)
        self._catalog["boundary"] = {"name": name, "source": source, "note": note}
        return self._catalog

    def catalog(self):
        return self._catalog

    def _location(self, settings):
        resolved = self._catalog.get("boundary") or {}
        name, source, _sentence = self.state.effective_location(
            settings, resolved.get("name"), resolved.get("source"), resolved.get("note"))
        return name, source

    def _region_type(self, settings):
        wanted = self.state.safe_text(settings.get("region_type_name"))
        rows = self._catalog.get("region_types") or []
        for row in rows:
            if self.state.safe_text(row.get("name")) == wanted:
                return row
        return rows[0] if rows else {}

    def _line_style(self, settings):
        """The chosen boundary line style, else the invisible one, else none."""
        wanted = self.state.safe_text(settings.get("line_style_name"))
        rows = self._catalog.get("line_styles") or []
        if wanted:
            for row in rows:
                if self.state.safe_text(row.get("name")) == wanted:
                    return row
        for row in rows:
            if row.get("is_invisible"):
                return row
        return rows[0] if rows else {}

    def _chosen_views(self, settings):
        if (settings.get("scope") or self.state.SCOPE_ACTIVE) == self.state.SCOPE_ACTIVE:
            active = self._catalog.get("active_view")
            return [active] if active else []
        wanted = set(self.state.safe_text(name) for name in settings.get("view_names") or [])
        return [row for row in self.views if self.state.safe_text(row.get("name")) in wanted]

    def _chosen_sources(self, settings):
        wanted = set(self.state.safe_text(key) for key in settings.get("source_keys") or [])
        rows = []
        for row in self.sources:
            row = dict(row)
            row["is_checked"] = self.state.safe_text(row.get("key")) in wanted
            rows.append(row)
        return rows

    def build_plan(self, settings, progress=None, replace=None, room_uids=None):
        """The one plan object the preview and the writer both read.

        ``replace`` overrides the setup's own choice, because an Update in the
        report is a replacement by definition: without it a pair that already
        has a region would be planned as "already drawn" and the update would
        find nothing to do.  ``room_uids`` narrows the read to the rooms an
        update actually needs, so one row costs one boundary and not the job.
        """
        settings = dict(settings or {})
        if replace is not None:
            settings["replace_existing"] = bool(replace)
        brevit, bstate = self.revit, self.state
        location, source = self._location(settings)
        self.settings = dict(settings)
        self.config = bstate.config_from_settings(settings, location,
                                                  self._region_type(settings),
                                                  self._line_style(settings))
        self.config["boundary_source"] = source
        source_rows = self._chosen_sources(settings)
        collected = brevit.rooms_from_sources(source_rows, self.config["kind"])
        rooms = collected["rooms"]
        wanted = None
        if room_uids is not None:
            wanted = set(bstate.safe_text(uid) for uid in room_uids)
        elif (settings.get("subject") or bstate.SUBJECT_ALL) == bstate.SUBJECT_ONE and self.picked:
            wanted = set(bstate.safe_text(entry.get("uid")) for entry in self.picked)
        if wanted is not None:
            rooms = [room for room in rooms
                     if any(bstate.safe_text(room.get("uid")).endswith(uid) for uid in wanted)]
        views = self._chosen_views(settings)
        visibility = brevit.visibility_map(
            self.doc, views, rooms, source_rows, self.config["kind"],
            include_design_options=self.config["include_design_options"])
        read = brevit.read_boundary_map(rooms, location, grid_mm=self.config["grid_mm"],
                                        progress=progress)
        existing = self._existing()
        plan = bstate.build_plan(self.config, views, rooms, read["boundaries"], existing,
                                 visibility=visibility)
        plan["boundaries"] = read["boundaries"]
        for skip in collected["skips"]:
            plan["skips"].append({"scope": u"source", "key": u"", "title": skip["title"],
                                  "code": skip["code"], "reason": skip["reason"],
                                  "view_uid": u"", "quiet": False})
        if read["cancelled"]:
            plan["notes"].append(u"The scan stopped at its time budget; the rooms it never "
                                 u"reached are listed as skipped.")
        return plan

    def _existing(self):
        """``{pair key: {region_id}}`` - what is already drawn and tracked."""
        found = {}
        for row in self.revit.read_relationships(self.doc):
            record = row.get("record") or {}
            key = self.state.pair_key(record.get("view_uid"), record.get("room_uid"),
                                      record.get("link_uid"))
            found[key] = {"region_id": row.get("region_id")}
        return found

    def draw(self, plan, checked_keys, progress=None):
        return self.revit.create_regions(self.doc, plan, checked_keys=checked_keys,
                                         progress=progress, created_utc=_now_utc())

    # -- the difference report ---------------------------------------------

    def check(self, settings, progress=None):
        brevit, bstate = self.revit, self.state
        location, source = self._location(settings)
        self.settings = dict(settings)
        self.config = bstate.config_from_settings(settings, location,
                                                  self._region_type(settings),
                                                  self._line_style(settings))
        self.config["boundary_source"] = source
        live = brevit.read_relationships(self.doc)
        source_rows = self._chosen_sources(settings)
        collected = brevit.rooms_from_sources(source_rows, self.config["kind"])
        # Every location any region was drawn at, so each is judged by its own
        # rule rather than by whatever the model is set to today.
        locations = set([location])
        for row in live:
            name = bstate.safe_text((row.get("record") or {}).get("boundary_location"))
            if name:
                locations.add(name)
        rooms_now = brevit.rooms_now_map(collected["rooms"], sorted(locations),
                                         grid_mm=self.config["grid_mm"], progress=progress)
        # Whether each region's own view still shows its room: read once per
        # view that holds a tracked region, never per region.
        owner_uids = set(bstate.safe_text(row.get("owner_view_uid")) for row in live)
        owner_views = [view for view in self.views
                       if bstate.safe_text(view.get("uid")) in owner_uids]
        visibility = brevit.visibility_map(
            self.doc, owner_views, collected["rooms"], source_rows, self.config["kind"],
            include_design_options=self.config["include_design_options"])
        for row in live:
            shown = visibility.get(bstate.safe_text(row.get("owner_view_uid"))) or {}
            visible = shown.get("visible")
            record = row.get("record") or {}
            if visible is None:
                row["room_visible"] = None
            else:
                row["room_visible"] = bstate.safe_text(record.get("room_uid")) in visible
        loaded = [bstate.safe_text(row.get("uid")) for row in self.sources
                  if row.get("loaded") and row.get("uid")]
        run_sources = [bstate.safe_text(row.get("uid")) or bstate.HOST_KEY
                       for row in source_rows if row.get("is_checked")]
        report = bstate.classify_drift(
            live, rooms_now, brevit.views_now_map(self.views),
            accepted=self.model_store.read(self.doc, TOOL_KEY),
            loaded_links=loaded, run_sources=run_sources)
        for skip in collected["skips"]:
            report["notes"].append(u"{0}: {1}".format(skip["title"], skip["reason"]))
        for shown in visibility.values():
            if shown.get("note"):
                report["notes"].append(shown["note"])
        return report


def bstorage_rows(bstorage, doc):
    try:
        return bstorage.read_all(doc)
    except Exception:
        return []


def _tracked_sentence(count):
    if not count:
        return (u"No filled region in this model carries a room relationship yet. Draw some on "
                u"the Create tab and they are tracked from then on.")
    return u"{0:,} filled region(s) in this model carry a room relationship.".format(count)


def main():
    forms.check_modeldoc(exitscript=True)
    if getattr(revit.doc, "IsFamilyDocument", False):
        forms.alert("Space Boundary requires an open project document.",
                    title=__title__, exitscript=True)
    _drop_stale_modules()

    import space_boundary_revit as brevit
    import space_boundary_settings as bsettings
    import space_boundary_state as bstate
    import space_boundary_storage as bstorage
    import space_boundary_ui as bui
    from easybim import external_events
    from easybim import model_store
    from easybim.progress import ProgressSession

    doc = revit.doc
    available, reason = bstorage.availability(doc)
    if not available:
        forms.alert(u"Space Boundary cannot keep the room relationship in this model:\n\n"
                    u"{0}".format(reason), title=__title__, exitscript=True)

    # A new run replaces the previous report rather than stacking windows.
    bui.close_open_window()

    settings, note = bsettings.load()
    run = Run(doc, brevit, bstate, bstorage, model_store)
    with forms.ProgressBar(title="Listing sources, views and region types...",
                           indeterminate=True):
        run.collect_catalog(settings)

    while True:
        result, settings, picked = bui.show_setup(run.catalog(), settings, note=note,
                                                  picked=run.picked)
        note = u""
        if not result:
            return
        run.picked = picked
        ok, error = bsettings.save(settings)
        if not ok:
            logger.warning("Space Boundary settings not saved: %s", error)
        brevit.apply_kind(run.sources, settings.get("kind") or bstate.KIND_ROOM)
        run.refresh_boundary(settings)

        if result == "pick":
            picked = brevit.pick_spatial(revit.uidoc, settings.get("kind") or bstate.KIND_ROOM)
            if picked:
                run.picked = picked
            continue

        if result == "check":
            _open_report(run, settings, bui, brevit, bstate, external_events, model_store,
                         ProgressSession)
            return

        with ProgressSession("Reading rooms and their boundaries...",
                             cancellable=True) as progress:
            def _tick(done, total):
                progress.update(done, total)
                return not progress.cancelled

            plan = run.build_plan(settings, progress=_tick)

        plan_result, checked_keys = bui.show_plan(plan, run.config)
        if plan_result == "back":
            continue
        if plan_result != "draw":
            return

        with ProgressSession("Drawing filled regions...", cancellable=True) as progress:
            def _draw_tick(done, total):
                progress.update(done, total)
                return not progress.cancelled

            outcome = run.draw(plan, checked_keys, progress=_draw_tick)

        notes = _report_write(outcome)
        _open_report(run, settings, bui, brevit, bstate, external_events, model_store,
                     ProgressSession, extra_notes=notes)
        return


#: How many refused rooms the dialog after Draw lists before it says "and N more".
REFUSAL_LINES = 25


def _report_write(outcome):
    """Say what was written - and, above all, what was not.

    A room Revit refused used to reach a person only as a debug line, which
    is a silent drop with extra steps: the preview listed the room, Draw ran
    without complaint, and the region simply was not there.  Now every
    refusal is named, with Revit's own words, before the report opens, and
    the lines are carried into the report's notes as well.
    """
    outcome = outcome or {}
    if outcome.get("error"):
        text = u"Nothing was drawn - the whole run was rolled back:\n\n{0}".format(
            outcome["error"])
        forms.alert(text, title=__title__)
        return [text]
    summary = [u"{0:,} region(s) drawn".format(outcome.get("created", 0))]
    if outcome.get("replaced"):
        summary.append(u"{0:,} updated".format(outcome["replaced"]))
    problems = []
    for entry in outcome.get("failed") or []:
        problems.append(u"Refused - {0}: {1}".format(entry.get("title") or u"(view)",
                                                     entry.get("reason")))
    for entry in outcome.get("skipped") or []:
        problems.append(u"Skipped - {0}: {1}".format(entry.get("title") or u"(view)",
                                                     entry.get("reason")))
    for note in outcome.get("notes") or []:
        problems.append(note)
    if outcome.get("cancelled"):
        problems.append(u"The run stopped early, so the rest were not drawn.")
    lines = [u", ".join(summary) + u"."] + problems
    logger.debug("Space Boundary: %s", u" | ".join(lines))
    if problems:
        shown = problems[:REFUSAL_LINES]
        if len(problems) > REFUSAL_LINES:
            shown.append(u"… and {0:,} more; the report's notes carry them all.".format(
                len(problems) - REFUSAL_LINES))
        forms.alert(u"{0}\n\n{1}".format(lines[0], u"\n".join(u"• " + line for line in shown)),
                    title=__title__)
    return lines


def _open_report(run, settings, bui, brevit, bstate, external_events, model_store,
                 ProgressSession, extra_notes=None):
    """Compare both sides, then hand the report the callables it needs."""
    with ProgressSession("Comparing regions with their rooms...", cancellable=True) as progress:
        def _tick(done, total):
            progress.update(done, total)
            return not progress.cancelled

        report = run.check(settings, progress=_tick)
    if extra_notes:
        report["notes"] = list(extra_notes) + list(report.get("notes") or [])

    # Created here, inside the command run, while an API context still exists.
    bridge = external_events.ExternalEventBridge("EasyBIM Space Boundary")
    ready = bridge.create()

    def _alive():
        if not getattr(run.doc, "IsValidObject", True):
            raise bui.DocumentGone()

    def recheck(uiapp):
        del uiapp
        _alive()
        return run.check(settings)

    def show(uiapp, item):
        _alive()
        return brevit.show(_uidoc(uiapp), item)

    def _replace_plan(items):
        """One plan with every wanted pair marked as a replacement."""
        wanted = {}
        for item in items:
            key = bstate.pair_key(item.get("view_uid"), item.get("room_uid"),
                                  item.get("link_uid"))
            wanted[key] = item
        plan = run.build_plan(settings, replace=True,
                              room_uids=[item.get("room_uid") for item in items])
        keys = []
        for entry in plan["items"]:
            item = wanted.get(entry["key"])
            if item is None:
                continue
            entry["action"] = "replace"
            entry["replaces_region_id"] = item.get("region_id")
            keys.append(entry["key"])
        return plan, keys, [key for key in wanted if key not in keys]

    def update(uiapp, item):
        """Delete the region and draw it again from the room as it is now."""
        del uiapp
        _alive()
        plan, keys, missing = _replace_plan([item])
        if missing:
            return {"ok": False, "report": run.check(settings),
                    "message": u"That room is no longer in a view or source this run reads."}
        outcome = run.draw(plan, keys)
        ok = bool(outcome.get("created") or outcome.get("replaced"))
        return {"ok": ok, "report": run.check(settings),
                "message": u"Updated from the room as it is now." if ok else
                _first_reason(outcome)}

    def update_all(uiapp, items):
        """Every drifted row at once, in one undo step."""
        del uiapp
        _alive()
        if not items:
            return {"ok": False, "report": None, "message": u"Nothing to update."}
        plan, keys, missing = _replace_plan(items)
        outcome = run.draw(plan, keys) if keys else {}
        done = int(outcome.get("created", 0)) + int(outcome.get("replaced", 0))
        refused = len(outcome.get("failed") or []) + len(outcome.get("skipped") or []) + len(missing)
        parts = [u"{0:,} updated".format(done)]
        if refused:
            parts.append(u"{0:,} could not be".format(refused))
        if outcome.get("error"):
            parts.append(u"the run was rolled back: {0}".format(outcome["error"]))
        return {"ok": done > 0, "report": run.check(settings), "message": u", ".join(parts) + u"."}

    def delete(uiapp, item):
        del uiapp
        _alive()
        outcome = brevit.delete_regions(run.doc, [item.get("region_id")])
        ok = bool(outcome.get("deleted"))
        return {"ok": ok, "report": run.check(settings),
                "message": u"Deleted." if ok else _first_failed(outcome)}

    def accept_difference(uiapp, key, on):
        """Accept one region's difference as deliberate, or restore it.

        Permanent, and kept in the model beside the relationship: the row
        stays accepted whatever the room or the region do next, survives a
        Sync to Central, and comes back only when somebody restores it.
        """
        del uiapp
        _alive()
        ok, _keys, reason = model_store.set_ignored(run.doc, TOOL_KEY, key, on)
        return {"ok": ok, "report": run.check(settings), "message": reason}

    bui.show_report(report, run.config, bridge=bridge if ready else None, uiapp=_uiapp(),
                    recheck=recheck, show=show, update=update, update_all=update_all,
                    accept_difference=accept_difference, delete=delete)


def _first_reason(outcome):
    for entry in (outcome or {}).get("failed") or []:
        return entry.get("reason") or u"Revit refused it."
    for entry in (outcome or {}).get("skipped") or []:
        return entry.get("reason") or u"It was skipped."
    return (outcome or {}).get("error") or u"Nothing was drawn."


def _first_failed(outcome):
    for entry in (outcome or {}).get("failed") or []:
        return entry.get("reason") or u"Revit refused it."
    return u"Nothing changed."


if __name__ == "__main__":
    try:
        main()
    except Exception as run_error:
        logger.exception("Space Boundary failed.")
        forms.alert("Space Boundary failed:\n{0}".format(run_error), title=__title__)
