# My Ribbon — other people's buttons on panels of your own

*2026-08-15*

One EasyBIM button that lets a user bring buttons from **other pyRevit
extensions** onto their own ribbon: paste a GitHub link (or pick an extension
already installed, or one from pyRevit's catalogue), tick the buttons they
want, and say where they go — any panel on the EasyBIM tab, a new panel there,
a tab of their own, or a panel on another tab. The setup exports and imports
as one JSON file so a colleague gets the same ribbon.

The requirement that decided the design, asked while planning: **pyRevit's own
Update and Reload buttons must keep those tools updated.**

## The two halves

**Sources are real pyRevit extensions.** A GitHub link is cloned with
pyRevit's bundled git library (LibGit2Sharp, no git.exe) into
`%APPDATA%\pyRevit\Extensions\<Name>.extension` — the pyRevit convention that
the repository root *is* the extension, and the folder pyRevit always scans.
A catalogue entry is installed with pyRevit's own installer
(`extpackages.install`, dependencies included). An extension already on the
computer — EasyBIM itself, pyRevitTools — is simply used. Because every source
is an ordinary enabled extension, `versionmgr.updater.update_pyrevit()` — the
routine behind pyRevit ▸ Update and behind EasyBIM Auto Update — walks it,
pulls its repository (`Repository.Discover` walks up from the extension folder,
so a repository holding several `*.extension` sub-folders is found too) and
reloads. My Ribbon runs no updater of its own; the command-names test fails the
build if a `git_pull` or `update_pyrevit` call appears in it.

The one thing My Ribbon adds per source is **"Hide its own tab"** — on by
default for repositories the user brought in through My Ribbon (they came for
three buttons, not the tab), off for extensions that were already installed.

**Placements are shared ribbon items.** A registry says "button X of extension
E goes on panel P of tab T at position N". On Apply, and on the first Idling
tick of every session, the engine finds the live `Autodesk.Windows` item and
adds *that object* to the destination panel's `Source.Items` — the mechanism
`lib/easybim/modify_ribbon.py` has used in production since the Modify-tab
shortcuts were built. Same object ⇒ same command handler, and the enabled
state, icon and tooltip stay whatever the owning extension makes them, with
no bindings. Nothing is written to disk to place a button, and re-arranging
needs no pyRevit reload; only installing or removing an extension does.

## What was considered and rejected

- **Generated wrapper buttons that `exec` the source script.** The first
  draft. Works only for Python bundles, needs `__builtins__.__commandpath__`
  and `sys.path` patched before the exec, skips the source's `startup.py` and
  hooks, and keeps the clones outside pyRevit's roots — so pyRevit's Update
  never sees them. The requirement above kills it.
- **Junction or symlink the source bundle folder into our tree.** The source
  extension's `lib/` is not on a junctioned button's search path (pyRevit adds
  only the *ancestors'* `lib/` folders), and `shutil.rmtree` follows a junction
  on Python 2.7 and would delete the source. Rejected.
- **Copy the bundle folder.** Same import breakage; updates stop flowing.
- **A cloned `RibbonButton` (new object, same `CommandHandler`).** Only needed
  to *rename* or *re-icon* a placed copy, and it loses the free sync of
  enabled/icon/tooltip unless WPF bindings are added. Deferred (v1.5); v1
  places the shared object, so a placed button keeps its own title.

## What pyRevit calls things (verified in the release branch)

The live `RibbonItem.Id` is built by Revit from the *names* pyRevit passes:
tabs from the **folder** name, panels from their **title**
(`create_ribbon_panel(panel.ui_title)`), pull-downs from their **title**,
push buttons from the **folder** name (`PushButtonData(button.name, ...)`)
with `Text` set to the title afterwards. pyRevit's own `control_id`
(`CustomCtrl_%CustomCtrl_%<tab>%<panel>%<button>`) is therefore only *nearly*
the live Id whenever a panel or pull-down has a title that differs from its
folder. So a placement records both: the parsed `control_id` for an exact
match, and a **path of `{name, title}` pairs** — tab, panel, (container,)
button — that the engine walks level by level, matching each level's `Id`
tail, `Text`, `Name`, `Title` or `AutomationName` against either the folder
name or the title (`\n` and case folded, as `modify_ribbon` does). Stacks have
no ribbon object of their own — AdWindows renders them as a `RibbonRowPanel`
holding the buttons — so their level is left out of the path and the walk
looks through row panels.

## Where things live

```
%APPDATA%\pyRevit\Extensions\<Name>.extension     a downloaded repository (repo root == extension)
%APPDATA%\pyRevit\Extensions\.myribbon-tmp\        downloads in progress; renamed into place only when complete
%LOCALAPPDATA%\EasyBIM\My Ribbon\repos\<o>__<r>\   a repository that holds *.extension sub-folders; the folder holding
                                                    them is registered as an extra pyRevit root (user_config)
%APPDATA%\pyRevit\pyRevit_EasyBIM_MyRibbon.json    the registry (script.get_universal_data_file), format-versioned,
                                                    written beside-and-swap; a newer format is refused, not half-read
```

The registry is the single source of truth. Its `sources` carry kind
(`git` / `catalogue` / `installed`), URL and branch, extension name, tab
names, `installed_by_my_ribbon` and `hide_tab`; `destinations` carry tab,
panel and `own_tab`; `placements` carry source, destination, order, kind,
title, `control_id` and the path. Export writes the same document; Import
previews a **Merge** (sources matched by normalised `owner/repo` + branch or
by extension name, panels by tab + panel, duplicate buttons skipped) or a
**Replace**, downloads what this computer lacks, and stages the result for
Apply. Nothing machine-specific is in the file, and no credential ever is.

## Every session

`startup.py` calls `my_ribbon.queue_startup_apply()` and nothing else: it runs
while later extensions are still loading, so their tabs are not on the ribbon
yet. The Idling consumer `_run_my_ribbon_apply` applies the registry once per
load on the first idle tick — the first moment every tab exists — and it runs
**before** the auto-update on purpose: the update can end in a pyRevit
reload, whose own `startup.py` queues the apply again, so nothing here ever
runs inside an engine that reload is disposing, and the user sees their ribbon
before any network work starts. The apply is idempotent: what the previous
apply added (item, panel and tab objects, and the tabs it hid) is mirrored in
a pyRevit envvar and taken back first, then everything is added again in
registry order, hidden tabs are hidden, and panels or tabs we created that are
now empty and unused are removed. A registry with nothing to do and no
leftovers never touches the ribbon.

The **pyRevit tab is never hidden**, whatever the registry says: it carries
Reload, Update, Settings and Extensions, and hiding it would leave the user
with no way back if My Ribbon broke. A destination tab is never hidden either.

## Windows

**Manager** (`MyRibbonWindow`) — Sources on the left (name, origin, status:
*loaded* / *needs pyRevit reload* / *not installed here*, a "Hide its own tab"
tick; Add source…, Remove, Open folder, Add buttons from it…), the user's
buttons on the right as a tree Tab › Panel → items (Up, Down, Move to…,
Rename…, Remove, New panel…, New tab…), and a footer with Import…, Export…,
**Apply**, Close. Edits stage into a working copy; the status line counts
changes not applied; Close asks before losing them; Apply saves, deletes the
folders of removed sources My Ribbon had installed, applies, and offers a
pyRevit reload only when a source still needs one.

**Where from?** (`SourceSelectionWindow`) — three cards in the Families
Transfer shape: a git link (validated live; `owner/repo`, `.git`,
`/tree/branch/sub`, ssh forms and other hosts understood; a branch box),
installed extensions, and pyRevit's catalogue with a search box. Downloading
probes the web URL first (5 s) so offline, a typo, or a private repository
fail fast; a private one offers a sign-in whose token lives only for that
download; the clone runs under a cancellable progress bar into the temp
folder and is renamed into place only when complete; a repository already
installed here (matched by its git remote) is reused rather than downloaded
twice; a repository with no pyRevit tools is deleted again with a plain
message.

**Which buttons?** (`ButtonSelectionWindow`) — Tab › Panel groups of
check-boxes with the 16-px icon, title, tags (`Revit 2024+`,
`not shown in its own ribbon`, `whole drop-down`, `already placed in …`) and
the tooltip on hover; a drop-down can be taken whole (its children grey out)
or by child; search, Select All/None, Expand/Collapse. A banner says when the
extension has a startup script or hooks — and that they keep working, since
it is installed as a normal extension.

**Where to?** (`DestinationWindow`) — Tab combo (EasyBIM first, own tabs,
"New tab…", then the other ribbon tabs) and Panel combo (existing panels of
that tab, "New panel…"); also used by the manager's New panel… / New tab….
The pyRevit tab is refused as a destination.

**Import** preview and **Sign in** are small windows of their own.

## Corner cases carried by the code

| Case | Handling |
|---|---|
| Repository root is the extension / holds `X.extension` sub-folders / has no pyRevit tools | `<Name>.extension` under the pyRevit root; clone under `repos\` + register the sub-folder as a pyRevit root; deleted again with a message. A `.tab` folder without the `.extension` name is reported, not used. |
| Extension folder name already used by a different repository | `<Name> (owner).extension`, then a counter. |
| Same repository pasted twice, in another URL form, or already installed through pyRevit | Normalised key and git-remote match → reused. |
| Buttons of a freshly downloaded extension | Listed from pyRevit's parser before any reload; placed after the reload the manager offers. |
| A button missing this session (extension disabled or removed, Revit version too old, upstream renamed the folder) | Reported on its tree row with the reason; nothing else breaks. |
| Whole pull-down / single child / stack child | Shared as one item / child shared as a top-level button (verify in Revit) / row panels looked through. |
| pyRevit reload replaces ribbon objects | The next apply takes back stale references harmlessly and re-adds live ones. |
| Reload with the window open | Apply closes the window before `reload_pyrevit()`; the command engine is not persistent. |
| Removing a source | Its placements go with it; the folder is deleted only if My Ribbon installed it, only under My Ribbon's folders, read-only git objects included; the pyRevit root it registered is unregistered. |
| Renaming or re-iconing a placed copy | Not in v1 (shared object). |
| Windows folder names, non-ASCII titles | Names sanitised for the file system; AdWindows Ids are slugs, titles stay free text. |

## Still to verify in Revit

Nothing here has run inside Revit. In order of risk:

1. Paste a public repository whose root is an extension → it lands under
   `%APPDATA%\pyRevit\Extensions`, reload, its tab is hidden, the picked buttons
   sit on a new EasyBIM-tab panel and on a new tab of their own with icons and
   tooltips, and a click runs the tool.
2. pyRevit ▸ Update pulls a change in that repository and Reload keeps the
   placements; EasyBIM Auto Update at startup does the same.
3. A context-limited tool greys out on the copy exactly as at home.
4. A whole pull-down and a single pull-down child shared as a top-level button
   (fallback: share the whole pull-down).
5. An EasyBIM button placed on a custom tab with the EasyBIM tab left visible;
   an EasyBIM button placed on Misc Tools itself is not duplicated.
6. Uninstall a source through pyRevit's Extensions window → the manager shows
   it missing, nothing crashes; Remove in My Ribbon deletes a folder it
   installed and leaves one it did not.
7. Export on machine A, import on machine B → the same ribbon after one reload.
8. Cancel a download half-way → nothing left under `.myribbon-tmp` or the root.
9. A private repository with a token; a monorepo (`extensions/X.extension`)
   → root registered in pyRevit's settings and updated by pyRevit ▸ Update.
10. The hidden-tab flash after a reload is brief; created tabs and panels
    disappear again after Remove + Apply.
