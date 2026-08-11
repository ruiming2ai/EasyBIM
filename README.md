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

## Families Transfer (Misc Tools)

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

## Linked Sheets Copy (Sheet, Revit 2024+)

Linked Sheets Copy reuses a Revit link's sheets. Pick a loaded link, tick its
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

