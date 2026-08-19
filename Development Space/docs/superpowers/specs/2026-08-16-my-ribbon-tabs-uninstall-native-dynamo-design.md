# My Ribbon — Show/Hide tabs, Uninstall, native buttons, Dynamo graphs

*2026-08-16*

Follow-on to `2026-08-15-my-ribbon-design.md`. Round 1 (shared live ribbon
items, sources as installed pyRevit extensions, export/import) is verified in
Revit. This round adds the four things the user asked for after using it, with
the choices confirmed while planning: EasyBIM **and Modify** stay frozen
visible, the pyRevit tab may be hidden with a warning, native Revit and add-in
tabs are sources too, and a Dynamo button runs the **original** `.dyn` where it
lives (an export carries the path only).

## Tab visibility is one list

The registry gains `hidden_tabs: [tab titles]` (format stays 1; a file from
round 1 is read by unioning the per-source "hide its own tab" flags into it).
That list is the only truth the engine reads. The per-source checkbox is a
shortcut that adds or removes that source's tab names; the new **Show/Hide
tabs...** window edits the whole list at once and re-derives the per-source
flags (`sync_source_hide_flags`), so the two views never disagree. Removing a
source un-hides the tabs that were hidden because of it, unless another source
still hides them.

The engine never hides EasyBIM (the My Ribbon button lives there - it is the
only way back) or Modify (Revit's contextual editing tab): `PROTECTED_TABS`
moved from `("pyrevit",)` to `("easybim", "modify")`, and a destination tab is
never hidden either. The pyRevit tab can now be hidden; the window and the
per-source checkbox both say where Reload/Update live and that it can be turned
back on here. The window shows every non-contextual tab; a tab that is
invisible for some other reason (another add-in hid it) is shown unticked and
disabled so Apply never forces it on. Confirm stages; **Apply** makes it real
and now **closes the window** - an alert appears only when the report has
missing items or errors, and the reload question still comes when a source
needs one.

## Remove keeps files, Uninstall deletes them

Round 1's Remove quietly staged a folder deletion when My Ribbon had installed
the extension. That is now two buttons: **Remove** forgets the source and its
placed buttons and leaves every file alone (every kind); **Uninstall** also
stages deletion of what My Ribbon installed - a downloaded extension folder, a
repository clone (only when no other source still uses it), a Dynamo bundle -
and is disabled for everything else (installed extensions, native/add-in tabs),
with the tooltip saying so. Both take effect on Apply, after which pyRevit is
asked to reload when a folder went.

## Buttons of Revit's own tabs and other add-ins

The shared-object mechanism does not care who made the button. The Installed
card now lists, below the pyRevit extensions, **"Other tabs on the ribbon"**:
every non-contextual tab that is not a pyRevit extension's and not ours. Picking
one reads the live tab (`describe_ribbon_tab`): panels and items by AdWindows
type - `RibbonButton`/`RibbonToggleButton`/`RibbonCheckBox`/`RibbonRadioButton`
are buttons, `RibbonSplitButton`/`RibbonMenuButton`/`RibbonListButton`/
`RibbonChecklistButton`/`RibbonRadioButtonGroup` are drop-downs with children,
`RibbonRowPanel` stacks are looked through, separators and row breaks are
skipped, and anything else (galleries, combos, text boxes, sliders, labels)
comes through as kind `ribbon-<type>` and is greyed in the picker with a
reason. Rows show the item's live `Image` (an ImageSource, not a file), its
`Text` (or Id tail when untitled) and its `ToolTip` (string or
`RibbonToolTip` title/content). A placement records `control_id = item.Id`
and the path `[tab Id/Title, panel Id/Title, item Id/Text]`, which the engine
already resolves Id-first. These sources are a new kind `ribbon`; status is
*loaded* or *tab is not on the ribbon*; Uninstall is never offered.

## Dynamo graphs as buttons

pyRevit runs a `.dyn` bundle through its own Dynamo engine
(`DynamoBIMEngine.cs`): the graph path is `runtime.ScriptSourceFile` unless
the bundle's `engine: dynamo_path:` names another file - then that file runs.
So My Ribbon owns one small extension and writes real bundles into it:

```
%APPDATA%\pyRevit\Extensions\EasyBIM_MyRibbon.extension\
    README.txt
    My Ribbon Library.tab\Dynamo.panel\<Title>.pushbutton\
        script.dyn        copy of the graph - the fallback; refreshed on every Apply when the original is newer
        bundle.yaml       title, tooltip (source path, Python-node engines, packages, "Ctrl+click opens it in
                          Dynamo"), author, engine: {automate: true, dynamo_path: "<absolute original path>"}
        icon.png / icon.dark.png   the user's PNG for both themes, else the drawn look-alike shipped with My Ribbon
```

The library tab goes onto `hidden_tabs` the moment the first graph is added.
A new graph needs one pyRevit reload (pyRevit compiles the bundle); until then
the source reads *needs Apply* / *needs pyRevit reload*. After the reload the
button is shared onto the chosen panel like any other, and the engine gives it
**Revit's own Dynamo images** (`Image`/`LargeImage` copied from Manage ›
Visual Programming › Dynamo, found by title or Id tail, skipping our own
panels and the library tab) unless the user chose a PNG - the same trick
`view_template_ribbon` uses. The same object sits on the hidden library tab,
so that copy changes too; nobody sees it.

The Where-from page got a fourth card (the Installed and Catalogue cards gave
up 40 px of minimum height each): **Add Dynamo graph...** picks one or more
`.dyn` files; each gets a small window for the title (prefilled from the
graph's own name or the file name), the icon, and what was read from the file:
Dynamo 2.x (JSON) or 1.x (XML), "contains Python nodes (CPython3,
IronPython2)" (from `ConcreteType`/`Engine` of the nodes), "uses packages: ..."
(`NodeLibraryDependencies`). Then one Where-to for all of them.

Refused with a plain sentence: a `.dyf` (custom node) or a graph saved as one,
a `.py` (a Python-node script is not a runnable graph), anything that is not a
graph. A graph already added is reported, not duplicated (key = normalised
path). The bundle folder name is the title made Windows-safe and unique
against both the folders on disk and the sources staged in the same session.
When the original file disappears the last copy keeps running and the source
reads *graph file missing (last copy kept)*; **Locate graph...** re-points it.
A bundle is never written without a graph (no original and no earlier copy), so
pyRevit never sees a bundle without `script.dyn`. Remove deletes the bundle
(it is nothing but what My Ribbon wrote); the graph file is never touched.
Export carries the path, title, icon path and bundle name; on another computer
a path that does not exist shows as missing until Locate fixes it.

## Reviewed before commit

An adversarial read of the round found and fixed: Locate... changed the path
but the bundle's `dynamo_path` was never rewritten (the sync now rewrites
`bundle.yaml` whenever it no longer says what the source says, and drops
`dynamo_path` while the original is missing so the copy really runs); two
sources could share one bundle folder (every Apply now makes bundle names
unique across the registry and the disk, renaming placements along, and runs
before the save); a crafted bundle name from an import file could write
outside the library (bundle names are validated on read and on write - one
plain `<Name>.pushbutton`); imported Dynamo sources could never be deleted
(their bundle is always ours); Show/Hide ▸ Confirm dropped hidden tabs that
were not on the ribbon at that moment (kept; the library tab is not listed);
un-ticking one source's tab un-hid a tab another source still hides; a never-
written bundle counted as deleted and asked for a reload; a placed graph
titled "Dynamo" could pass for Revit's Dynamo button (Dynamo's own panel wins
and placed items are excluded); live items are classified by their base types,
so an add-in subclass of `RibbonButton` is still a button.

## What was verified by reading, what needs Revit

Read: `DynamoBIMEngine.cs` takes `dynPath` from `ExecEngineConfigs.dynamo_path`
when set; pyRevit's bundle metadata compares `automate` to the string `'true'`
(YAML scalars arrive as strings), which the written `automate: true` satisfies;
the Dynamo engine needs `script.dyn` present for the bundle to be recognised
as a Dynamo command, hence the copy.

In Revit (added to the AGENTS handoff row): (1) Show/Hide hides an add-in tab
and the pyRevit tab on Apply; EasyBIM/Modify cannot be unticked; un-hiding
brings them back; (2) Uninstall a downloaded repository - folder gone after
Apply + reload; Remove keeps the files; (3) a native button (Annotate › Tag by
Category) and an add-in button placed on a tab of your own - click works, greys
out as at home; a whole split button; (4) add a `.dyn`, reload, the button
appears with Revit's Dynamo icon, the click runs it, an edit to the graph on
disk runs next time, Ctrl+click opens Dynamo, a custom PNG shows, moving the
graph makes the source missing and Locate fixes it; (5) Apply closes the
window; (6) export/import across machines with a native tab, an absent add-in
tab and a Dynamo path that does not exist.
