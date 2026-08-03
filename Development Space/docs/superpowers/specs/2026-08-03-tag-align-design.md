# Tag Align - design

Date: 2026-08-03
Command: `EasyBIM.tab/Misc Tools.panel/Tag Align.pushbutton`

## Problem

Placing tags is fast in Revit; making them look consistent is not. Users nudge
hundreds of tags by hand so that every door tag sits the same distance above its
door and every pipe tag sits the same distance off its run. Revit has no
"copy this tag's placement to those elements" command.

Tag Align captures a reference tag's placement - its head offset from the host
element, measured both in the host's own frame and in the view's frame - and
replays it onto other elements, optionally creating the tag as well.

## Rules the tool enforces

These came out of the planning conversation and are not negotiable defaults;
they are what makes the offset unambiguous:

1. **One tag family type per reference set.** Mixed tag types are a hard stop.
2. **One element category per reference set.** Cross-category is never matched.
3. **Two scopes only:** exact family + type, or same family / any type
   (the default). There is no "all families" scope.
4. A reference set that would put two different tags in the same place is
   **blocked** until the user picks a winner per conflict group.

## Two ways in

`Select One Reference Tag` is followed by a page asking whether the tag can be
aligned to any orientation:

- **Yes** - the offset is expressed in the element's own frame and rotates with
  it. A tag two feet above a horizontal pipe sits two feet off the side of a
  vertical pipe. This is the answer for pipes, ducts, walls and beams.
- **No** - the offset stays in the view's Right/Up axes and only elements at
  the reference's orientation are touched. Everything else is skipped and named
  in the report, so the user knows to add a second reference for them.

`Select Multiple Reference Tags` always matches per orientation: each reference
covers the element types and orientation it was measured at. An orientation with
no reference is skipped unless `Rotate the nearest reference for unmatched
orientations` is ticked.

## Geometry

Everything is reduced to numbers in `tag_align_revit` and computed in
`tag_align_state`, which is why the maths is unit tested without Revit.

- **View frame**: `view.RightDirection` / `UpDirection` / `ViewDirection`. Only
  differences are ever decomposed (`head - anchor`), so the view origin cancels
  and the same code serves plans, sections, elevations and 3D views.
- **Anchor**, same rule for reference and target: location point, else location
  curve midpoint (which keeps a tag centred on walls of differing length), else
  the view-space bounding box centre.
- **Direction**: a location curve's tangent wins over a family instance's hand
  orientation, because the tangent is what the eye reads as "the direction of
  the wall" *and* because it is an undirected axis. That is recorded as
  `axis_symmetric`, and it is what lets a wall drawn right-to-left match a wall
  drawn left-to-right (the `Treat 180° flipped elements as the same orientation`
  option, on by default, and only ever applied when both sides are undirected).
- **Offsets are stored twice**, both in model feet: `offset_view` along the
  view axes and `offset_local` along the element's own in-plane frame. Storing
  both means the same reference can be replayed either way without re-reading
  the model.
- **View scale** is stored rather than baked in, so `Scale offset with view
  scale` can be toggled after the fact. A reference measured at 1:100 and
  applied at 1:50 reproduces the same printed distance.
- **Degenerate frames**: an element whose axis runs into the screen (a riser in
  plan) projects to a point. That is reported as "no direction" so the offset
  falls back to the view axes rather than dividing by a vanishing length.

## Conflict model

`validate_references(references, scope, scale_with_view, collapse_flip)` groups
by scope key, then clusters by orientation (greedily, so two directions either
side of a bucket edge are not split), then compares offsets.

| Code | Meaning | Handling |
|---|---|---|
| `TAG_TYPE_MIXED` | more than one tag family type | fatal, whole set discarded |
| `CATEGORY_MIXED` | hosts span categories | fatal, whole set discarded |
| `DUPLICATE` | same key, same offset | merged silently, reported as a note |
| `POSITION_CONFLICT` | same type + orientation, different offset | resolvable |
| `SCOPE_AMBIGUITY` | two types of one family claim the same orientation | resolvable |

Multi-reference tags, orphaned tags and tags on linked elements are dropped
individually with a reason, not treated as fatal.

Validation reruns on **every** scope and option change, because scope and the
view-scale option both change whether two references agree. The main window
runs it inline (it is pure Python) and shows the count in the hint line; the
resolution window only opens when the user presses Align.

## Processing

`build_plan` is a complete dry run - it resolves every target, records every
skip with its reason and element id, and returns the items to write. That is
what the confirmation dialog reports, so the preview cannot drift from the
write. `execute_plan` then applies the items.

Batch writes go through a raw `DB.Transaction` rather than `revit.Transaction`
so a cancelled progress bar can roll the whole batch back without raising; the
counters are zeroed on rollback so the report never claims work that is gone.
Click-one-at-a-time uses one transaction per click, so undo walks back click by
click.

Which tag gets moved: the host's tags of the **reference tag's family**, exact
type first. A "Door Tag - Large" on a door whose reference is "Door Tag - Small"
is the same annotation and gets aligned; a "Fire Rating Tag" on the same door is
somebody else's tag and is left alone.

## Deliberate exclusions

`RoomTag` / `AreaTag` / `SpaceTag` derive from `SpatialElementTag`, not
`IndependentTag`, and expose position through `Location` rather than
`TagHeadPosition`. They are rejected at reference-pick with a clear message
rather than half-supported.

## Files

```
Tag Align.pushbutton/
  script.py                  step loop, Revit picking, transactions
  tag_align_state.py         plain Python: rules, conflicts, scope, summaries
  tag_align_revit.py         Revit API: measure, plan, execute, filters
  tag_align_ui.py            WPF windows (no Revit API)
  *.xaml                     eight windows
```

Tests: `test_tag_align_state.py` (maths and conflicts),
`test_tag_align_command_names.py` (bundle, XAML parse, handler and control
resolution per window class, IronPython 2.7 safety).
