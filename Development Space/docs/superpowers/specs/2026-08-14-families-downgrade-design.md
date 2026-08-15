# Families Downgrade — design

## Problem

A Revit family saved by a newer release cannot be opened by an older one, and
nothing in the API changes that: `SaveAsOptions` has no target version in any
release from 2021 to 2026, and the `.rfa` container is a proprietary,
version-specific format that nothing running inside Revit 2026 can write for
Revit 2021. People still need families "in 2021" - a project on the older
release, a client library, a manufacturer's content - and the answer today is
to rebuild them by hand.

Families Downgrade rebuilds them by tool. It is a **rebuild, not a file
conversion**, and it says so on its first page.

## What the tool is

One button, two modes, the same button on both sides.

- **Export downgrade packages** (run in the newer Revit). Families are chosen
  exactly as in Families Transfer - the project, opened `.rfa` files, Revit
  links - and each becomes a folder `<Family>.downgrade` holding
  `manifest.json` (everything about the family that is data) and one geometry
  file per *attribute group* (its solids as SAT, or DWG with ACIS solids).
- **Rebuild families from downgrade packages** (run in the older Revit, or in
  the same one to see what a round trip keeps). Every package becomes a new
  family document from the best-fitting template and is saved as an `.rfa` of
  *that* Revit into a folder the user picks. Rebuild needs no open document,
  so `bundle.yaml` carries `context: zero-doc` - Revit 2021 straight after
  launch is enough.

A report file (`families_downgrade_report.txt`) is written next to the outputs
in both modes and lists, per family, everything that was not carried.

## What survives, what is lost

Survives: category and family flags (work-plane based, always vertical,
shared, cut with voids, room calculation point, part type, OmniClass), all
family parameters with data type, group, instance/type, shared GUID and
formula, every named type and its values, exact solid geometry (SAT keeps
cylinders round), material per solid by name or by family-parameter
association, subcategories with line colour and weights, detail-level and
plan visibility, `Visible` associations, MEP connectors (system, shape, size,
direction, primary/linked pairs, parameter associations), symbolic and model
lines, named reference planes.

Lost, by design and always reported: parametric and constrained geometry
(rebuilt solids are static `FreeFormElement`s), dimensions, labels and text,
nested families (flattened into geometry), voids as forms (their cuts are
already baked into the exported solids), hosted openings (a door rebuilt on a
door template keeps the template's opening, not its own), type catalogs,
images, reporting parameters (rebuilt as plain), 2D families (annotations,
tags, detail items, profiles, title blocks), adaptive, mass and in-place
families.

## Rules the tool enforces

- **The source is never saved.** Project and link families are read through
  `EditFamily` and closed without saving; an opened `.rfa` is read inside a
  `TransactionGroup` that is rolled back, so scratch views, hidden elements
  and type switches all disappear.
- **Nothing is dropped silently.** Every fallback in the mapping layer, every
  refused stage of the rebuild, every solid that would not become native is a
  note on that family, in the dialog's counts and in the report in full.
- **The ForgeTypeId string is the cross-version key.** Revit 2021 already has
  `SpecTypeId` for every measurable spec and `UnitUtils.GetUnitType`, so a
  data type recorded as its ForgeTypeId string resolves on 2021 to a
  `UnitType`, whose normalised name *is* the `ParameterType` name. Groups go
  the same way (`GroupTypeId` property name ↔ `PG_*`), with the seven spelling
  quirks Autodesk left (`CurtainGridn1` → `PG_CURTAIN_GRID_1`, `Termination` →
  `PG_TERMINTATION`, …) in a table. Ten groups and a handful of specs newer
  than 2021 have no legacy equivalent and land in Other / by storage type with
  a note.
- **Every version-dependent choice is made at runtime by asking the API what
  it has**, never by comparing a version number. The rebuild's
  `HostCapabilities` reads `Definition.GetDataType` (2022+ ⇒ the ForgeTypeId
  `AddParameter` overload), `GroupTypeId` (2024+), `ParameterType` /
  `BuiltInParameterGroup` (gone in 2023 / 2025) and picks the rung.
- **One geometry file per attribute group.** Two solids travel together
  exactly when the rebuild would treat them identically: same material source
  (family parameter, named material, or by category), same subcategory, same
  `FamilyElementVisibility`, same `Visible` (literal or association). Groups
  are exported through a scratch 3D view each, with everything else hidden,
  and the view is deleted afterwards.
- **An empty export is retried at the other detail levels.** A form shown only
  in Coarse is invisible in a Fine view. Each group starts at the finest level
  its visibility allows and retries at the other two when the SAT carries no
  body records.
- **The exported file is found by folder diff.** Revit's single-view export
  may suffix the view name; the new file is located and renamed to `gNN`.
- **Import is self-checked.** `Placement = Origin`, `Unit = Default`, then the
  imported bounding box is compared to the exported one: a size mismatch
  re-imports through the unit candidates (Foot first) until it matches, a
  residual offset is corrected with `MoveElement`, and both are reported.
- **Bodies become native solids or the import stays whole.** Each body goes
  through `FreeFormElement.Create`, then `SolidUtils.SplitVolumes` on refusal;
  if any body still refuses, the group's FreeForms are deleted and the import
  instance is kept - visual completeness beats a hole - and the note says
  that group has no per-solid attributes.
- **Connectors go on faces, and say how far they landed.** The recorded
  origin and normal pick an exact face (origin inside), else a coplanar one;
  the API places a connector at the face centre, so the miss distance is
  reported. Rectangular and oval connectors get their orientation back through
  `CONNECTOR_ANGLE`. Work-plane connectors cannot be placed by the API and are
  reported.
- **Warnings are swallowed, errors roll back and are reported.** Every
  transaction on both sides carries an `IFailuresPreprocessor`; a commit that
  did not land is a note, never a silent success.
- **Categories the target lacks are mapped, not guessed.** Revit 2022 added
  ten model categories 2021 does not have (verified against the 2021
  assembly); each lands where people used to put those families (Plumbing
  Equipment → Plumbing Fixtures, Medical Equipment → Specialty Equipment, …)
  and the swap is named. Anything else unknown becomes Generic Models.
- **Templates: hosting outranks category.** A family's category can be
  reassigned after creation, its hosting cannot, so a face-based lighting
  fixture is built on `Generic Model face based` and given its category,
  while a wall-hosted door is built on the door template. Names are matched
  singular/plural blind (`Doors` ↔ `Metric Door.rft`); when the template
  folder is empty the run asks once for an `.rft`.
- **Overwrites are asked about once, before anything is replaced**, in both
  modes; a package overwrite touches only the tool's own files
  (`manifest.json`, `gNN.sat|dwg`), never the user's other files in that
  folder, and a half-written package is cleaned up the same way.
- **The DWG option is a hedge.** Revit's SAT writer version is not selectable
  and an older reader might refuse a newer file. `Geometry format: DWG` sends
  the same solids as ACIS inside an AutoCAD 2007 file; the first 2026 → 2021
  session decides which stays the default.

## Deliberate exclusions

- No `pyrevit run` bridge that launches the older Revit itself: it would be
  the repo's first external process launch and needs its own trial.
- No per-type geometry inside one family via visibility switches; the
  **One family per type** export option covers MEP fittings instead.
- No recursion into nested families, no type catalogs, no 2D families, no
  per-family template override, no `DialogBoxShowing` auto-dismiss (added
  only if a session ever meets a modal dialog).
- Reference planes carry `Is Reference` but not `Defines Origin`: the
  template's origin planes already define it and a second pair would fight.

## Files

```
EasyBIM.tab/Misc Tools.panel/Families Downgrade.pushbutton/
  bundle.yaml                       # title, tooltip, author, min_revit_version: 2021, context: zero-doc
  script.py                         # mode page -> export step machine | rebuild page; progress, report, summary
  families_downgrade_state.py       # pure: manifest, mapping tables, groups, bbox checks, templates ranking, report text
  families_downgrade_revit.py       # shared Revit helpers: reflection, transactions, sessions, templates, scratch shared file
  families_downgrade_export.py      # FamilyReader + per-group export + the export batch
  families_downgrade_rebuild.py     # HostCapabilities, RebuildContext, the stages, the rebuild batch
  families_downgrade_ui.py          # ModeWindow, ExportOptionsWindow, RebuildWindow
  ModeWindow.xaml, ExportOptionsWindow.xaml, RebuildWindow.xaml
  icon.png, icon.dark.png
lib/easybim/family_selection_{state,ui,revit}.py + lib/easybim/ui/family_selection_*.xaml
                                    # the selection pages shared with Families Transfer
Development Space/tests/test_families_downgrade_{state,revit,command_names}.py
```

## Still to verify in Revit

Round trip in one release first (export a mechanical-equipment family with
two duct connectors and symbolic lines, rebuild in the same Revit, load both
side by side); then 2026 → 2021 with SAT and, if 2021 refuses the files, with
DWG; a `Visible`-associated form and a coarse-only form; an opened `.rfa`
left unchanged; a link family; a Plumbing Equipment family landing as
Plumbing Fixtures; cancel mid-run in each mode; a rebuild into a folder that
already holds a name; Rebuild with no project open in Revit 2021.
