# Linked Sheets Copy - design

## Problem

Setting up a sheet set is the expensive part of starting a discipline model.
When a consultant's model is already linked in and already has its sheets laid
out - title block placed, plans cropped, view titles positioned - that layout
is free to reuse.

The tools that already do something like this place the views by eye. The
grids therefore land a few millimetres off, and the two sheets do not overlay
when printed - which is the one thing the exercise is for. `Linked Sheets
Copy` places every viewport by **model coordinates** instead, reusing the
solver `View Align` has been aligning viewports with since 2026-03.

## Rules the tool enforces

- **Revit 2024 or newer, with no fallback.** `RevitLinkGraphicsSettings` and
  `View.SetLinkOverrides` do not exist before 2024, and without them there is
  no way to say "show this link as it appears in that linked view" - which is
  the entire mechanism. `min_revit_version: 2024`, re-checked at runtime with
  `hasattr(DB, "RevitLinkGraphicsSettings")`.
- **Plan views only, in this version.** Floor, ceiling, structural and area
  plans are rebuilt. Sections, elevations, 3D views, drafting views, legends
  and schedules are listed on their sheet and named as skipped, with the
  reason, rather than silently dropped.
- **Nothing is written before the dry run is shown.** `build_plan` produces
  the complete sheet x view matrix with a status and a reason per cell, and
  the confirmation window and the executor read the same object - so preview
  and write cannot drift.
- **The host model is never mutated by a name clash.** Every cross-document
  copy runs through one `CopyPasteOptions` factory whose
  `IDuplicateTypeNamesHandler` answers `UseDestinationTypes`.
- **Measure last.** See below; this is the part worth understanding before
  changing anything.

## The alignment guarantee

### One solver, in `lib/easybim/sheet_geometry.py`

`View Align`'s anchor solver moved into `lib/easybim/sheet_geometry.py` and
both tools now import it. Two copies of this maths would let "align" and
"copy and align" drift apart, which is exactly the failure `AGENTS.md` warns
about - and the shared module is the first test coverage `View Align` has ever
had. It is written against duck-typed vectors (anything with `.X/.Y/.Z`), so
the whole thing runs off Revit:

```
(pu, pv)    = ((P - View.Origin)*Right / Scale, (P - View.Origin)*Up / Scale)
(cu, cv)    = centre of View.Outline
(dx, dy)    = R(viewport rotation) * (pu - cu, pv - cv)
sheet_point = Viewport.GetBoxCenter() + (dx, dy)
```

Two of Revit's own behaviours are load-bearing: `View.Outline` is expressed as
scaled offsets from `View.Origin` along Right/Up, and `Viewport.GetBoxCenter()`
is the sheet position of that outline's centre.

### Placing one viewport

1. `P_link` = centre of the linked view's crop box, in link coordinates.
2. `S_ref` = where `P_link` prints on the **linked** sheet.
3. Build and fully configure the host view - level, template, scale, crop,
   rotation, annotation crop, view range, link override.
4. `doc.Regenerate()`, create the viewport, set its rotation and type,
   `doc.Regenerate()` again.
5. `S_new` = where `transform.OfPoint(P_link)` prints in the new viewport.
6. `SetBoxCenter(current + (S_ref - S_new))`.

**Measure last, and regenerate first.** `View.Outline` is derived: it moves
when the crop, the annotation crop, the scale or a view template changes, and
it is stale until the document regenerates. `View Align` solves its anchor in
a precheck pass and *then* writes the crop, which is why its Assign Scope Box
and Assign Crop Region options can misplace a viewport. Here there is exactly
one measurement, it is last, and it follows a regeneration - so an imperfect
crop reproduction moves the viewport *border* and never the drawing.

### Orientation

A link rotated about Z has to produce a rotated crop region in the host, or
the drawing diverges from the linked sheet everywhere except at the anchor.
That falls out of composing the transforms:

```
host_crop.Transform = link_transform * linked_crop.Transform
```

with the extents unchanged, because a link transform is rigid. What a plan
view *cannot* represent is caught once, before anything is built, and the link
is refused with the reason: `HasReflection` (mirrored), `BasisZ` not vertical
(tilted), `Scale != 1`.

### The second point

One matched point proves the translation and nothing else. A corner of the
crop box is as far from the anchor as the drawing goes, so a wrong scale or a
wrong rotation shows up there largest. Every placement reports the miss in
printed millimetres, and anything over `ALIGNMENT_TOLERANCE_MM` (0.2 mm) is
flagged in the report. The placed viewport's box size is compared against the
linked one for the same reason.

### The title block

Sheet coordinates are absolute, and the viewports are aligned to the *linked
sheet's* absolute coordinates. If the host title block does not sit where the
linked one sits, every viewport lands at the right sheet coordinate and the
wrong title-block coordinate - precisely the failure this tool exists to
avoid. So the host title block is moved to the linked instance's location
before anything else is read back.

## Levels

Copy/Monitor is the source of truth, because it is the only one that records a
human decision. `Level.IsMonitoringLinkElement()` /
`GetMonitoredLinkElementIds()`, filtered to the **selected link instance** - a
host level monitoring the same level in another instance of the same link says
nothing about this one.

The ladder below it is a guess, and the window says which rung was used:

1. `Monitored` - Copy/Monitor
2. `Name` - an exact, unique name match
3. `Elevation` - within `LEVEL_TOLERANCE_FT` (1/8"), compared in *host*
   coordinates so the link transform is already accounted for
4. `Manual` - the user's dropdown, which always wins
5. `Unmapped` - views on that level are skipped, and the rest of their sheet
   is still built

Two host levels monitoring one linked level resolve to the first and are
flagged `(two match)`, rather than blocking the run.

The same map remaps `ViewPlan.GetViewRange()`: each plane's level id is
translated and its offset moved by the host/linked elevation difference, so
the plane stays at the same absolute height. `Unlimited`, `LevelAbove`,
`LevelBelow` and `Current` pass through untouched.

## Options, and why the defaults are what they are

Ticked: **Copy title block**, **Copy title block parameters**, **Match
viewport type**, **Match view title position and line length**.

Everything else is off. The rule is that an option which *adds elements or
types to the host model* has to be asked for; the two title-block options are
the exception because a sheet without its title block is not a reused sheet,
and the two matching options are the exception because "view, view title, view
title length should be exactly matching the linked sheet" is the requirement
the tool was asked for.

**Copy the linked view's annotations** is off and carries a warning in its
tooltip, in the confirmation, and behind an acknowledgement tick: with the
link shown By Linked View, the linked view's text, detail lines and filled
regions **already draw through the link**, so copying them draws everything
twice until that link is switched off in the new view. Sheet-level content is
the opposite case - a sheet is not part of the linked model's geometry and can
never arrive through the link, so copying is the only way to get it.

## Deliberate exclusions

- **Tags, dimensions, spot dimensions and view references** are never copied
  out of a linked view. Each is a reference to a model element that does not
  exist in this document.
- **Legends and schedules** cannot be copied between documents, and a copied
  schedule would read *this* model's data rather than the link's - which is
  worse than not having it. They are listed as skipped.
- **Revision clouds** need an explicit opt-in: a cloud drags its revision onto
  the target sheet, and this model's revisions are not the link's. Same
  reasoning as `sheet_manager_revit.copy_sheet_detailing`.
- **`ElementId` parameters** are never copied. An id means nothing in another
  document, and writing one would point at whatever shares that number here.
- **Sheets inside a nested link** are not reachable through
  `GetLinkDocument()` and are not listed.
- **Scope boxes** are matched by name only, because a scope box cannot cross
  documents.
- **Guide grids** are not copied.
- **Area plans** whose `AreaScheme` has no host counterpart are skipped rather
  than creating an area scheme - that is too large a change to make on the
  user's behalf.
- **Shape-edited crops with curved edges.** A polyline crop is rebuilt in the
  host view's plane; an arc would have to be reconstructed there too, and the
  failure mode is a silently wrong boundary, so it falls back to the rectangle
  and says so.

## Transactions

One `TransactionGroup`, assimilated - one Ctrl+Z for the whole run. Inside it,
one nested group **per sheet**, rolled back on failure, so a sheet whose title
block will not copy does not take twenty good sheets with it. The report is
snapshotted and restored alongside that rollback (`RunSummary.snapshot` /
`restore`), because a report claiming work that is no longer in the model is
worse than no report. A cancelled progress bar rolls the outer group back and
`clear_applied()` zeroes everything.

Warnings are swallowed by an `IFailuresPreprocessor` so a batch is not
interrupted; errors still surface and fail their own sheet.

## Files

```
EasyBIM.tab/Sheet.panel/Linked Sheets Copy.pushbutton/
    script.py                       launcher, entry guards, Context (Revit callables)
    linked_sheets_copy_state.py     pure: rows, level map, plan, collisions, report text
    linked_sheets_copy_revit.py     every Revit API call
    linked_sheets_copy_ui.py        the three WPFWindow classes
    LinkedSheetsCopyWindow.xaml     link picker + sheet grid + options + level map
    ConfirmWindow.xaml              the dry run, with its acknowledgements
    ReportWindow.xaml               what happened, incl. the alignment deviation
    bundle.yaml, icon.png, icon.dark.png
EasyBIM.tab/Sheet.panel/bundle.yaml     new: panel button order
lib/easybim/sheet_geometry.py           shared with View Align
Development Space/tests/test_sheet_geometry.py
Development Space/tests/test_linked_sheets_copy_state.py
Development Space/tests/test_linked_sheets_copy_command_names.py
```
