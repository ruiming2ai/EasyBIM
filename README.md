# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase & View (Revit 2015-2027)

Temp Phase & View is a Python-only pyRevit workflow driven by one ribbon
button. Clicking it opens a window that carries every option:

- **Apply** — apply the phase selected in the list to the active view and
  record its original state in the EasyBIM session.
- **Restore** — undo the temporary phase and view settings for the active view
  immediately, without waiting for the file to close.
- **Restore All Views** — do the same for every view in the document, covering
  both tracked views and any other view left with Temporary View Properties
  enabled.
- **Cancel** — close the window without changing anything.

Both restore options run the same transaction close recovery uses, so the
results are identical; they only change *when* the cleanup happens. Once a
document has nothing left to restore, its close-recovery arming is dropped so
later closes skip the view scan entirely. As with close recovery, the restored
state is only in the open session until the model is saved or synchronized.

The window still opens on a view that cannot take a temporary phase — the
phase list and Apply are disabled, so the restore options stay reachable.

When a file close is requested, EasyBIM cancels the close, restores tracked
phases, and clears every tracked or discoverable Temporary View Property in one
transaction. Close-Stop is armed per document only after the button applies a
temporary phase, so files where the button has not been used close normally.

After restoration, the Close-Stop dialog explains that the cleanup is currently
only in the open session. Choose one of the following actions:

- **Save and Close** — save the restored project, then close it
  only after Revit reports that the save completed successfully. Save As cases
  are handled through the same completion flow.
- **Synchronize and Close** — available for workshared files;
  opens Revit's normal Synchronize with Central/options command and closes only
  after `DocumentSynchronizedWithCentral` reports success. Synchronizing is
  required when the restored state must be reflected in the central model.
- **Keep File Open** — leave the restored document open. It remains restored in
  the current session, but it is not permanent until the file is saved (and, for
  a local workshared file, synchronized with central).

Save or synchronization cancellation, failure, unavailable commands, Revit
shutdown, and unsupported/non-cancellable close contexts leave the restored
document open. The Python `doc-closing` and `doc-closed` hooks, together with a
single `Idling` delegate installed at startup (`lib/easybim/idling.py`),
coordinate the per-document trigger/state and prevent duplicate close reposts.
Using Temp Phase in one file does not arm close recovery for other open files.

Normal users only need to update EasyBIM from GitHub and reload pyRevit. No DLL
staging, build step, or cache clearing is required. The standalone C# fallback
add-in and its source have been removed from the repository; the Python command
and hooks are the only Temp Phase implementation.

Clash Detection Mode is the one command that runs in a persistent pyRevit
engine, because it owns live Revit event handlers that must outlive the click
that created them. A persistent engine keeps its loaded modules across a
pyRevit reload, so the command drops its own modules on each launch when no
detection session is running - that is what lets an update take effect on the
next click instead of only after a Revit restart. Its main window shows a
`Build <timestamp>` stamp read from the files on disk: if that does not move
after an update, Revit is still loading the old files from somewhere else.

Diagnostics are emitted only through pyRevit's standard debug logging (enable
pyRevit debug mode to see markers such as `DocClosingCancelSucceeded` or
`TempPhaseRestoreCommitted`). The legacy per-event file log at
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log` is no longer written; the
folder can be deleted on machines where it exists.

## Tag Align (Misc Tools)

Tag Align copies one tag's placement onto other elements. Pick a reference tag,
then either align the tags that already exist or tag and align in one pass.

- **Select One Reference Tag** asks whether the tag can be aligned to any
  orientation. Answer yes and the offset rotates with the element, so a tag two
  feet above a horizontal pipe sits two feet off the side of a vertical one.
  Answer no and only elements at that same orientation are touched; the rest are
  named in the report so you know to add a second reference for them.
- **Select Multiple Reference Tags** matches per element type and orientation -
  one reference for horizontal walls, another for vertical, and so on.

All reference tags must share one tag family type and one element category, and
a different category is never matched. Three scopes are offered, widest first:

1. **All the families in the same category** (the default)
2. **Apply to different types, but only paired family**
3. **Exact same family and type match only**

The closest reference always wins, whatever the scope: one measured on the
element's own type beats one on a sibling type, which beats one from another
family in the category. A wider setting therefore only ever adds a fallback - it
never steals a target from a reference that matches it more precisely.

Anything that would put two different tags in the same place is reported as a
conflict, with a window to pick the winner per group, before a single tag moves.
That window also offers to narrow the scope one step, which is usually the
quickest way out: a set that is ambiguous across a whole category is often
perfectly well defined per family.

Elements are picked either one at a time - each click is processed immediately
and Esc ends the loop - or as a batch, where a small bar offers Filter, Select,
Deselect and Process while Revit's normal window and crossing selection does the
picking. A batch is one transaction, so one undo puts it all back.

The offset is measured from the element rather than from the sheet, so it
survives rotation, and it is stored with its source view scale, so a reference
measured at 1:100 reproduces the same printed distance at 1:50. Room, area and
space tags are not supported yet and are rejected when picked as a reference.

### Saving and reusing settings

**Save Settings** keeps the reference tags and every option under a name;
**Load Previous Settings** brings them back. A preset records its references by
*name* — category, family, type — never by ElementId, so it can be loaded into a
different project. Anything the target model does not have is named in the load
report rather than silently dropped; renaming a family or type in the model
breaks that reference in an old preset, by design.

There are three places to save, answering three different questions:

| Save to | Survives reopen | Other models | Your team |
|---|---|---|---|
| **This computer** — `%APPDATA%\pyRevit\pyRevit_EasyBIM_TagAlign_presets.json` | yes | yes | no |
| **This model** — stored inside the `.rvt` | yes | no | yes, after Sync to Central |
| **Shared folder** — a network drive or an ACC Desktop Connector folder | yes | yes | yes, immediately |

**For ACC cloud models**, saving into the model is the mechanism that reaches a
team: the settings travel with the model and land in central on the next
synchronise, so anyone who opens that cloud model has them. No Revit API writes
files into ACC Docs, so the alternative is a shared folder on an ACC Desktop
Connector path, which Revit treats as an ordinary local path.

A **Last used** preset is written automatically every time you run an align, so
Load has something in it even if you never press Save. If a loaded preset names
a tag family this model has not got, `Align` still works — tags are matched to a
reference by tag family name — while `Align & Tag` is greyed with the reason,
because nothing can create a tag from a type that is not there.

## Circuiting (Misc Tools)

A dropdown for batch work on electrical circuits.

### Update Circuit Rating

Circuit ratings are already carried by the equipment sitting on the circuit —
typically a downstream panelboard, whose own rating is what the feeder has to
be. This command reads that value off the elements and writes it onto the
circuits.

1. Pick the **current parameter on circuited elements**. Every parameter that
   actually holds a value somewhere is listed; parameters measured in amps come
   first, and each is labelled with where it was found (`Instance`, `Type`, or
   both — the instance value wins, the type value fills a gap).
2. Tick the **target circuit parameters**. Every writable numeric parameter on
   the circuits is offered, so a shared rating parameter works as well as the
   native ones; **Rating** (the trip rating) and **Frame** are ticked by
   default.
3. Review the list and press **Update**. All rows start checked; **All** and
   **None** toggle the lot, and **Cancel** writes nothing.

Per circuit, the **highest** value found across its elements wins — a tie goes
to an Electrical Equipment element, so a panel and a receptacle both reading
20 A credit the panel. The `From Element` column names the element the value
came from, and `Existing` shows what the ticked targets hold right now, so a
row that would change nothing is marked `no change`.

Elements without the parameter are simply ignored: a circuit is still listed
and still updated as long as *one* of its elements carries a value. A circuit
where none of them does cannot be updated, so it is not listed at all — the
count under the list says how many were skipped for that reason. Values are
written raw, with no rounding to standard breaker sizes, and the whole run is
one undo step.

#### The zero-amp report

Every run ends with a table of the circuits that are **still at zero amp** —
panel name, circuit number, circuit name. It is read back from the model after
the write, so it reflects what is actually there, and it covers *every* circuit
in the project, not just the ones the run touched: the circuits that were
skipped for want of a value, the ones you unticked, and any target parameter
that ended up blank all show up here. A circuit is listed if *any* of the
parameters you ticked reads zero or nothing. That makes the report the list of
what is left to do — when it is empty it says so.

### Circuit Schedule

Revit's System Browser shows every system in the model at once and cannot be
searched. This is the same idea narrowed to electricity: a docked panel holding
the whole distribution chain as one tree, with a search box.

The **service board sits at the root** — whatever no circuit feeds. Under it are
its circuits; under each circuit are the things that circuit feeds. A downstream
panel is not a leaf: it opens up into its own circuits, so a receptacle six
levels down is reached the way power actually reaches it, board by board.

Three kinds of row, and they are meant to look different:

| | | |
| --- | --- | --- |
| ▣ | **Board** | Bold. Its second line names its feeder — `fed from DP-2 / 9` — so every board says where it comes from. The root says `utility service`. |
| ▼ | **Circuit** | Its number sits in its own column so the numbers line up down the panel. The second line is the electrical reading: `225 A · 3P · 208 V · 30 kVA`. |
| ▪ | **Load** | Small and grey, always a leaf. Second line is the category and level. |

That split is the upstream/downstream story. **A board tells you where it comes
from; a circuit tells you where it goes.** Within any circuit, a child that is
itself a distribution board is bold and opens; a receptacle is small, grey and
does not — so a feeder to a sub-board never reads like a branch circuit. Colour
runs from dark at the service to pale far downstream, on the row glyph and its
left rule only, so it survives both Revit themes.

Selecting a row fills the **path line** at the top of the panel:
`MSB ▸ 3 ▸ DP-2 ▸ 9 ▸ LP-2 ▸ 12 ▸ Recept East`. That is what keeps "upstream"
legible after a search drops you six levels down with every parent scrolled off
the top.

**Double-click** a row — or select it and press **Show** — to select and zoom to
it in the active view. A circuit frames everything on it at once. **Refresh**
re-reads the model; your expanded rows and your search survive it. Nothing here
ever writes to the model.

#### Searching

Type a circuit number, a panel name, a load name or an element id. The tree
prunes to the branches that answer, keeping every parent so the chain back to
the service stays on screen, and opens them for you.

Circuit numbers are matched **as whole numbers, not as text**: `12` finds
circuit `12` and circuit `12,14,16`, and does **not** find `112`. In a project
with three-digit circuits a substring match makes number search useless, so it
is the one field that is not matched by substring. `LP-1/12` and `LP-1 12` both
work. Everything else — panel names, load names, categories — is plain
case-insensitive substring. **Esc** clears the box; **Enter** jumps to and shows
the first match.

#### What the model can throw at it

- A **spare or space** — a circuit with nothing on it — is listed and marked
  `spare`, the way it would be on a panel schedule.
- A circuit with **no panel assigned** cannot hang anywhere, so it goes in an
  `Unassigned circuits` group at the bottom rather than being dropped.
- A **board fed by two circuits** is drawn in full under the first and appears
  under the second in italics as `already shown under …`. Both feeders show it;
  the subtree is only drawn once.
- A **circular feed** — a board somehow upstream of itself — stops there in
  orange rather than recursing. It is a modelling error and the panel says so.

#### One restart, once

The panel docks the way Revit's own browsers do, and Revit only accepts a new
dockable panel while it is starting up. So the first time after installing or
updating the extension, **restart Revit** — a pyRevit reload is not enough. Until
you do, Circuit Schedule opens as a floating window pinned to the right instead,
which works the same but does not dock.

## Families Transfer (Family)

Collect loadable families from wherever they happen to be, then push them into
the other project files you have open — or write them out as `.rfa`.

The first page gathers the sources. Three cards, all the same shape — header,
`X selected, Y unchecked.`, Select All / Select None, a search box, and a list
of checkboxes. Dragging the window resizes the three lists; the headers and
buttons never move.

- **Selection from Recent Project** — whatever you had selected in the model
  when you launched, plus anything added since. **Load More from Recent
  Project** opens the full browser for the active project, grouped by
  category. That browser carries **Select in the model**, which drops you back
  into Revit to pick more and returns with them ticked.
- **Add Opened .rfa Files** — any family file already open in this session.
  These need no extraction at all; the open document *is* the family.
- **Selection from Revit Links** — families pulled out of a loaded Revit link.

Then choose the open project files to load into, and finish with **Export**,
**Transfer**, or **Transfer & Close All .rfa**. Long runs show a progress bar
and can be cancelled; anything already loaded stays loaded.

Every browser page carries **Hide Un-checked**, which collapses the list down
to what you have ticked — useful once a few hundred rows are on screen. It
filters the view only; nothing is deselected by turning it on.

### When a family is already in the target

You are asked, the same as loading a family by hand. A **Family Already
Exists** window offers to overwrite the existing version, overwrite it and its
parameter values, or cancel — with a **"Do this for all loading families"**
checkbox. Tick it and the rest of the transfer follows that answer without
asking again. Declining skips that family and the run carries on; the summary
lists it under Skipped rather than Failed. Overwriting keeps every placed
instance in the model and gives them the new definition.

Cancel deliberately ignores the checkbox: it means "not this one", so the next
family that clashes asks again.

You are only asked when Revit itself would be — when the family is already
there *and* actually different. A family identical to the one in the target
loads silently.

The window is built from the same widget Revit uses, so it looks like the
prompt an interactive load shows, but the checkbox is ours. Revit's own version
of it does not survive a transfer that loads one family at a time, which is why
this tool asks the question itself. Where the prompt cannot be shown at all,
the transfer overwrites without asking and reports what it replaced.

The one exception is a family arriving from a Revit link when that link refused
to hand over a family document. That path copies the element rather than loading
a family, and Revit's copy API has no overwrite at all — its only choices are
"use what the destination already has" or "cancel the paste". Those rows are
skipped, with the message naming the two routes that do work: open the linked
file as a target, or use **Load More from Recent Project**.

**Export** checks the folder once before it starts. If any of the files it is
about to write are already there, it says how many and lets you replace them or
pick a different folder.

### What can and cannot be picked in the model

**Select in the model** takes anything with a loadable family behind it:
model families, generic annotations, and tags — including room, area and space
tags, keynote tags and material tags.

It will not take **text notes, dimensions, spot dimensions, detail lines or
matchlines**, because those are system families with no family to load.
A matchline in particular is not a family at all: Revit gives it a category
and four sketch parameters, but no `Family`, no type, and no `.rfa`. To move
its appearance between projects use **Transfer Project Standards** (Object
Styles and line patterns), not this tool.

Section heads, callout heads, elevation marks, grid heads, level heads and
view reference tags *are* real loadable families, and they already appear in
the **Load More from Recent Project** browser. You just cannot reach them by
clicking, because the thing you click is the system element that references
them.

### Families from a Revit link

**The linked `.rvt` is never opened.** A loaded link is already a readable
document in memory, so the families are read straight out of it.

**Load More from Revit Links** opens a page listing the links; tick the ones
you want and press Next to browse their families. What you add there comes
back to the third card on page 1, where you can untick individual families
without going back in.

Each link is probed once, before the browser opens, so a link that will not
give up its families says so in one sentence instead of one failure line per
family. Getting a family *out* of a read-only document is the part Revit does
not guarantee, so there are three routes and the tool takes the first that
works:

1. If the linked file also happens to be **open in this session**, it is read
   from that document — no read-only question arises.
2. Otherwise the link itself is asked for the family. This is the normal path,
   and it behaves exactly like a project family: existing families in the
   target are overwritten.
3. If the link refuses, the family is **copied** into each target instead.
   A copy cannot overwrite: where the target already has a family of that
   name, that row is skipped and says so, because Revit would otherwise
   resolve the clash in favour of the target and report a success that changed
   nothing. **Export** has no third route — writing an `.rfa` needs a family
   document, which is the thing the link refused — so it reports the refusal.

Two instances of one link are one row, not two. Unloaded links are listed but
greyed, so a missing link looks missing rather than absent. Links nested
inside a link are not reachable through the API and are not listed.

## Families Downgrade (Family, Revit 2021+)

Rebuild loadable families for an older Revit. A family saved by a newer
release cannot be opened by an older one, and there is no "save as older
version" anywhere in Revit or its API - so this tool does not convert the
file. It **rebuilds** the family from its geometry and data, and it says so
on its first page.

One button, two modes, and you run the same button on both sides:

1. **Export downgrade packages** - in the newer Revit. Pick families exactly
   as in Families Transfer (this project, opened `.rfa` files, Revit links),
   choose a folder, and one sub-folder per family is written, named
   `<Family>.downgrade`, holding `manifest.json` and the family's solids as
   SAT files. Existing packages of the same name are replaced after one
   warning.
2. **Rebuild families from downgrade packages** - in the older Revit (or in
   the same one, to see what a round trip keeps). Point it at the folder,
   tick the packages, choose an output folder, and every package becomes a
   native `.rfa` of *that* Revit. No project needs to be open for this half.

Both modes end with a summary and write `families_downgrade_report.txt` next
to the outputs, listing per family everything that was not carried.

### What survives, and what does not

Carried: category and family settings, every family parameter (data type,
group, instance/type, shared GUID, formula), every named type and its values,
exact solid geometry (cylinders stay round), materials by name or by the
family parameter that drove them, subcategories with line colour and
weights, detail-level and plan visibility, **Visible** associations, MEP
connectors (system, shape, size, direction, primary and linked pairs,
parameter associations), symbolic and model lines, named reference planes.

Not carried, and always listed in the report: parametric geometry - the
rebuilt solids are static, so a type's dimensions no longer reshape them -
dimensions, labels and text, nested families (flattened into geometry), voids
as separate forms (their cuts are already in the exported solids), the wall
opening of a hosted family (a door rebuilt on a door template keeps the
template's opening), type catalogs, images, reporting parameters (rebuilt as
plain ones), and 2D families - annotations, tags, detail items, profiles,
title blocks - which are skipped with a reason, as are adaptive, mass and
in-place families.

### The options that matter

**One family per type.** Off, one package per family: the solids come from
the family's current type and every type keeps its parameter values. On, one
package per type, each with that type's exact geometry and connector
positions - the right choice for MEP fittings whose sizes differ per type.

**Geometry format.** SAT is Revit's own exchange format for solids and the
default. If the older Revit refuses the SAT files, export again with **DWG
with ACIS solids**: the same solids travel inside an AutoCAD 2007 file.

### How the rebuild bridges the versions

The parameter API changed shape between 2021 and 2026 (`ParameterType` and
`BuiltInParameterGroup` gave way to `ForgeTypeId`); the package records the
ForgeTypeId string, which Revit 2021 already understands for every measurable
type, and the rebuild picks the right `AddParameter` overload by asking the
running Revit what it has. Categories that only exist from 2022 (Plumbing
Equipment, Medical Equipment, Mechanical Control Devices, ...) land in the
category people used before, and the report names the swap. Templates come
from Revit's own template folder, hosting first (a face-based fixture is
built on `Generic Model face based` and given its category), and if that
folder is empty the run asks once for an `.rft`.

Geometry is imported origin-to-origin and checked against the exported
extents: a wrong unit header is re-read in feet, an offset is corrected, and
every body becomes a native solid - or, if one refuses, the group stays an
imported instance and the report says so. Connectors are placed on the face
that carries their exported origin; the API puts a connector at the centre of
its face, so a connector that shared a big face with others reports how far
it landed from where it was.

## Sheet Align (Sheet)

Sheet Align straightens a set of sheets by shifting everything each one owns as
a single block - title block, viewports, text, detail lines, images, schedules
and revision clouds all move together, keeping their positions relative to each
other.

Tick the sheets to fix, then pick one of two datums:

- **By Sheet Origin** moves each sheet's title block onto that sheet's own zero
  point. No reference sheet is involved. Every sheet ends up on the same
  footing, whatever state it started in.
- **By Title Block Origin** moves each sheet's title block to where the
  reference sheet's title block sits, so the whole set matches one good sheet.

Only the title block can serve as the landmark, because the sheet origin is the
same point on every sheet - comparing one sheet's origin to another's always
gives zero, and nothing would move. That is why the first option is a
normalisation rather than a match.

Model geometry is never touched. A sheet's contents are read through an
ownership test, not just "what is visible on the sheet", because a view-scoped
collector also hands back the walls and grids seen through the placed
viewports; moving one of those would translate real model geometry by a
paper-space vector.

Pinned elements are unpinned to move and pinned again afterwards. A pin that
will not go back is reported in the summary and the run carries on - it is
never silently skipped, and it never discards work that already succeeded. A
sheet with no title block is reported and skipped rather than aborting the run.

The whole run is one undo step.

## Linked Sheets Transfer (Sheet, Revit 2024+)

Linked Sheets Transfer reuses a Revit link's sheets. Pick a loaded link, tick its
sheets, and each one is recreated here: same title block in the same place on
the page, and one new plan view per viewport, cropped and scaled exactly like
the linked view and showing that link **By Linked View**.

The point is the alignment. Every viewport is positioned by **model
coordinates**, not by copying its position on the page, so a grid intersection
lands on exactly the spot on the title block that it occupies on the linked
sheet. Print the new sheet over the linked one and they overlay. That holds
whether the link is rotated, placed by shared coordinates a long way from the
origin, or drawn at a different scale from your view.

Every placement is checked at a **second** point, out at the corner of the
crop, and the report gives the miss in millimetres on the printed sheet. One
matching point only proves the translation; if that second figure is not
essentially zero, the view did not reproduce the linked scale or rotation, and
the row says so rather than leaving you to find it on a plot.

### Levels

A level that is copied and monitored from the link is used automatically -
that is a decision somebody already made, so the tool does not second-guess
it. Anything unmonitored is guessed by name, then by elevation, and the
**Matched by** column always says which. Pick a different level from the
dropdown at any point; a manual choice wins.

A level left **Unmapped** is not a blocker. Its views are skipped and counted,
and the rest of the sheet is still built.

### What is copied, and what is not

The tool always creates the sheet, creates each plan on its mapped level,
matches scale, crop, rotation and detail level, sets the link to By Linked
View, and aligns the viewports. Four options are ticked to start with - the
title block, its parameters, the viewport type and the view title position -
and the rest are yours to turn on:

| Off by default | What it does |
|---|---|
| Copy sheet parameters | The writable sheet parameters other than number and name |
| Copy sheet detailing | Text, detail lines, symbols, filled regions and images sitting on the linked sheet |
| Copy revision clouds | Off because a cloud adds its revision to the new sheet, and your revisions are not the link's |
| Apply the host view template of the same name | |
| Match scope box by name | A scope box cannot cross documents, so only the name is matched |
| Copy the linked view's annotations | See the warning below |
| Hide the other RVT links in the new views | They otherwise draw By Host View, which the linked sheet never showed |

**Copying the linked view's annotations draws them twice.** With the link set
to By Linked View, the linked view's own text, detail lines and filled regions
already come through the link. Copying them puts a second, editable set on
top. Turn it on only if you intend to switch that link off in the new views.
Sheet content is the opposite case: a sheet is not part of the linked model,
so nothing on it can arrive through the link and copying is the only way.

Tags and dimensions are never copied - they reference elements that do not
exist in your model. Legends and schedules cannot cross documents at all, and
a copied schedule would report your model's data rather than the link's, so
both are listed as skipped. This version reproduces **plan views only**;
sections, elevations, 3D views and drafting views are named on their sheet and
skipped.

### Before anything is written

A confirmation window shows the complete dry run - every sheet, every view,
the level mapping, everything skipped with its reason - and separately, every
change the run will make to *your* model, such as a title block family or a
viewport type copied in from the link. A name that already exists here always
keeps your definition; nothing in your model is redefined by the copy.

Sheet numbers that clash, either with a sheet you already have or with another
row in the batch, turn red and block the run. The prefix and suffix boxes are
usually the quickest way past that, or edit the **New number** column directly.

The whole run is one Ctrl+Z. Each sheet is written inside its own group, so
one sheet that fails is rolled back on its own and named in the report while
the rest still land.

## Load Parameters (Parameters, Revit 2023+)

Load Parameters puts shared parameters where they need to be: **into families**,
**into the project**, or both from one selection. The window stays open after a
load, so doing both is one extra click.

The left half is a table of shared parameters. It starts with every shared
parameter already in the model, and **Add from Shared Parameter File…** brings in
more from a `.txt`, grouped exactly as the file groups them. Two columns are
yours to set — **Group** (where the parameter lands in Family Types or in Project
Parameters) and **Type / Instance** — and both are pre-filled from what the
project already says, so the common case needs no editing at all.

Editing several rows at once works two ways: shift-select a range and use the
bar under the table, or change one selected row's dropdown and it spreads to the
rest of the selection. Ticking a row that is *not* part of the selection stays a
single-row action, so the spread never surprises you.

The right half is the targets, on two tabs:

- **Families** — everything loadable in this model, family files scanned from a
  folder, or both. Checking a family also ticks its category on the other tab;
  unticking a category stays unticked.
- **Project categories** — every category that accepts a bound parameter.

Then **Load to Families** or **Load to Project**. Nothing is written until a
confirmation window has shown you the complete dry run: every family and
parameter pair, what will be added, what will be updated, and what is skipped
with the reason.

Only shared parameters can be loaded. The GUID is what makes the parameter in the
family the *same* parameter the project knows; a non-shared one would be
recreated by name and would silently fail to line up. If a `.txt` you pick names
a parameter this model already has under a different GUID, it is refused rather
than added — two identically-named shared parameters make schedules and filters
pick whichever they like.

### Where things end up, and what can be undone

| Target | Written to | Undo |
|---|---|---|
| **A family in this model** | edited and reloaded into the project | one Ctrl+Z for the whole batch |
| **A family file in a folder** | its own `.rfa` on disk — never loaded into this project | none; a saved file has no undo |
| **Project categories** | the project's parameter bindings | one Ctrl+Z |

Because those are not the same promise, a cancelled run reports them separately:
project families roll back to nothing, and every `.rfa` already written is named
in the report. The project pass always runs first, so cancelling early costs
nothing.

**Family files saved in an older Revit are upgraded when they are written**, and
cannot be opened in that release again. The confirmation says how many, and you
have to tick the acknowledgement before the run starts. There is also an option
to mirror the folder into a different output folder instead of overwriting.

A family that already carries the parameter is skipped by default. Switch to
**Update its group and Type/Instance** and a second run fixes the group or the
instance/type flag in place instead — usually the reason you are running it
again.

Families nested inside another family are not updated; only the families in the
list are.


## My Ribbon (General)

My Ribbon puts buttons from **other pyRevit extensions, Revit's own tabs, other
add-ins and Dynamo graphs** on panels of your own. Paste a GitHub link, or pick
an extension or tab already on the computer, or browse pyRevit's catalogue of
community extensions, or choose `.dyn` files; tick the buttons you want; say
where they go — any panel on the EasyBIM tab, a new panel there, a tab of your
own, or a panel on another tab. Press **Apply** and they are there. No reload is
needed to re-arrange; only a newly downloaded extension or a new Dynamo graph
needs one, and My Ribbon offers it.

Two things make it safe to lean on:

- **A downloaded repository is installed as a normal pyRevit extension** (under
  `%APPDATA%\pyRevit\Extensions`, the folder pyRevit always scans). That is why
  **pyRevit ▸ Update** keeps it up to date, and Reload rebuilds it. My Ribbon
  runs no updater of its own — and neither does EasyBIM Auto Update, which
  updates only EasyBIM itself, so pyRevit ▸ Update is what refreshes the
  extensions you bring in here. Its original tab can stay hidden ("Hide its own
  tab", on by default for repositories you bring in this way, off for
  extensions that were already installed); its startup script and hooks keep
  working.
- **A placed button is the extension's own live button**, added to your panel —
  the same trick that puts EasyBIM's Slope and Flip Multiple on Revit's Modify
  tab. It runs the same command, greys out in the same situations, and shows the
  same icon and tooltip. Nothing is copied to disk. (Because it is the same
  object, a placed button keeps its own title; renaming copies is not in this
  version.) The same goes for buttons of Revit's own tabs and other add-ins.

The window has your **Sources** on the left and your **buttons** on the right as
a tree of tab › panel. Every session, on the first idle moment after pyRevit has
finished loading, My Ribbon rebuilds your panels and tabs from its settings; the
pyRevit tab is never hidden and is never a destination, so there is always a way
back. **Export…** writes the whole setup to a JSON file; **Import…** previews a
Merge or a Replace, downloads what the other computer had that this one lacks,
and stages the result for Apply. No credential is ever written to that file: a
private repository asks for a token that lives only for that download.

Things it tells you rather than guesses: a repository with no pyRevit tools is
deleted again with a message; a button whose extension is disabled, removed, or
too new for this Revit is marked missing on its row; a repository already
installed here is reused instead of downloaded twice; a download you cancel
leaves nothing behind.

### Tabs, Uninstall, Revit's own buttons, Dynamo graphs

**Show/Hide tabs...** (on the My buttons side) lists every ribbon tab with a
tick. EasyBIM stays on (this button lives there, so there is always a way back)
and so does Modify, Revit's editing tab; everything else - other add-ins,
Revit's own tabs, even the pyRevit tab (with a note: its Reload and Update live
there) - can be hidden. Confirm stages, **Apply** makes it real and closes the
window.

On the Sources side, **Remove** forgets a source and its placed buttons and
leaves every file alone. **Uninstall** also deletes what My Ribbon installed for
it - a downloaded repository, a Dynamo button - and is greyed for everything
that was already on the computer. Both happen on Apply.

The Installed card also lists **Revit's own tabs and other add-ins' tabs**.
Their buttons can be placed exactly like pyRevit's (it is the same live button
object); galleries and drop-down lists that only work on their own panel are
greyed with the reason. They can only ever be Removed.

**Add Dynamo graph...** makes a button out of a `.dyn` file. The button runs
the graph from where the file is, so later edits count the next time you click
(Ctrl+click opens it in Dynamo instead); its icon is Revit's own Dynamo icon
unless you pick a PNG; the title is yours. My Ribbon keeps a copy of the graph
in its own folder as a fallback and refreshes it on Apply, so a moved or deleted
file still runs its last version - the row says so, and **Locate graph...**
points it at the file again. A new graph needs one pyRevit reload, which Apply
offers. Custom nodes (`.dyf`) and Python-node scripts (`.py`) are refused with a
plain message; the window tells you whether the graph is Dynamo 1.x or 2.x,
which Python engines its nodes use and which packages it depends on.

### Stacks, separators and the slide-out

Panels of your own can use the ribbon's whole layout language, not just a
row of large buttons. Select a button and press **Stack with next** to put
it and the one below into a row of **small buttons** - press it again for
three, the most a stack holds; **Unstack** dissolves the row and the buttons
return to full size. A stacked button is a small copy that runs the same
command and greys out in the same situations - the original button is never
touched, so nothing shrinks on its home tab. **Add separator** draws a
vertical line you can move like any row, and **Add slide-out** folds
everything below it into the drop-down that opens under the panel (one fold
per panel). Up and Down move a stack as one block; dragging a button out of
its panel takes it out of its stack. Whole drop-downs stay full size - they
need their full-height arrow. Re-arranging still needs no reload, the layout
travels with **Export…**/**Import…**, and an older EasyBIM reading the same
settings file simply shows the buttons flat.

## Family Types (Family)

What Revit's type catalogue puts in a `.txt` file, shown as a table you can
actually read: one row per family type, one column per parameter, every value
editable in place. Add, rename and delete types; export the table to Excel and
import it back.

It runs two ways. In the **family editor** the open `.rfa` *is* the family, so
edits go straight into it and you save the file as usual. In a **project** you
pick a loaded family from a category-grouped list, the tool opens it behind the
window, and **Apply Changes** writes the edits and reloads the family — asking
the same *Family Already Exists* question a manual load asks, with the same
overwrite / overwrite-parameter-values / cancel choice and the same **"Do this
for all loading families"** tick. A family you already had open in the editor is
never closed behind your back.

Nothing reaches the family until **Apply Changes**. Until then every edit is
staged: changed cells go red, types marked for deletion grey out, and the status
line counts what is waiting. **Cancel** asks before dropping staged work, and
**Reload From Family** re-reads the family and discards it deliberately.

### Instance parameters are in the table

They belong there. A family type stores a value for *every* family parameter,
instance ones included — that per-type value is the default a new instance is
placed with, and it can differ from type to type, which is exactly what Revit's
own Family Types dialog means by "(default)". The column headers carry the same
marker, in the grid and in the export.

### What can and cannot be edited

Lengths, numbers, text and materials are editable, and **yes/no parameters are
checkboxes**. A material is matched by name within the family — materials are
unique by name there, so a name in a cell resolves to exactly one, and a name
the family does not have is reported rather than silently skipped.

Only three things grey a cell out, and the tooltip says which: formula-driven
(the formula is in the tooltip), reporting (Revit drives it from geometry), or
an element reference other than a material — a family type or image reference
has no name lookup a text cell could resolve. Everything else is offered for
editing; if Revit still refuses a particular write, it is reported as a line in
the Apply summary rather than pre-emptively locked.

### Editing many cells at once

Drag or shift-select cells down a column, then edit one of them — every selected
cell in that column takes the value. The same goes for ticking a checkbox. **Fill
Down** does it from a button instead: it copies the topmost selected cell's value
to the rest of the selection.

Row headers down the left select a whole row, which is what the New / Duplicate /
Delete Type buttons work from.

A band above the column headers marks where the type parameters end and the
instance parameters begin.

Two things Revit itself will not do: a family can never be left with no types at
all, so the last surviving type refuses to be deleted; and there is no way to
unset a family parameter, so clearing a cell is reported as skipped instead of
being written as a zero.

A family with no named types keeps its values on its current type, which has no
name. That shows as a single `(default)` row — values editable, rename and
delete off.

### The Excel round trip

**Export to Excel** writes the table as it currently looks, staged edits
included. Each parameter's header cell carries two lines: the parameter name,
then its **ElementId**. That id is what an import matches on, and it is on the
visible sheet rather than buried in metadata so you can see what matched.
Instance-parameter headers are blue, read-only headers red with their cells
locked and greyed, and the sheet is protected so only the editable cells accept
typing.

**Import from Excel** opens a **Specify Types** window, the same question a type
catalogue asks when a family is loaded. Every type in the file is listed with
what the file would do to it — **New**, **Changed**, **Unchanged** — and you
tick the ones to take. A **Delete family types that are not in the file**
checkbox, off by default, handles the other direction. What you tick is *staged*
into the grid as red cells; Apply is still the only thing that writes.

Types match by name, the way a type catalogue matches them, so a name the family
does not have becomes a new type — rename a type in the table rather than in the
spreadsheet, where a rename and a new type are indistinguishable. Parameters
match by that ElementId first and fall back to the name, so renaming a parameter
in Revit does not break a workbook exported before the rename. A column matching
nothing is reported, never guessed at.
