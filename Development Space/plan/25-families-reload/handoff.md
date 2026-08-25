# 25 — Families Reload

Point at the office library, see exactly which loaded families match a file on
disk, and reload them in one undo step — with the overwrite question asked
once, honestly.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 25 of 45 | Family | no | M | 8/10 | 8/10 |

## Main purpose

The office library gets fixed — a corrected door swing, a re-mapped connector,
a parameter added for the new schedule — and every live project keeps its
stale copy until someone notices in a clash report or a schedule gap. Reloading
by hand means hunting each `.rfa` through the folder tree and answering the
"Family Already Exists" prompt once per family, so in practice it happens
rarely and partially: the three families someone remembered, not the
thirty-one that changed.

Families Reload turns that into one pass. It scans one or more library folders
(remembered across sessions), matches the `.rfa` files against the families
loaded in the document **by exact name** — the file has no ElementId, and an
ElementId would mean nothing in the next document anyway — and shows three
buckets before anything runs: matched, in-project-but-not-in-library ("rogue —
listed, untouched"), and in-library-only (count shown, ignored). Ambiguity
fails closed: two same-named files in different subfolders make that family
unreloadable-from-here, greyed with both paths in the tooltip. Per matched
file it shows what can actually be known — the saved-in Revit version from the
file header and the OS file date — under a footnote stating plainly that Revit
records no load date, so the tool never claims the project copy is older or
newer than the file. The reload itself is `doc.LoadFamily` per file inside one
assimilated TransactionGroup, reusing the shared `family_load_options` prompt
verbatim; declines are Skipped, never Failed; each family rolls back alone.

It earns rank 25 because the commercial suites sell exactly this button and
the free ecosystem does not have it with the honesty layer: fail-closed
ambiguity, pre-flight version checks instead of mid-run crashes, and a
post-commit type diff read back from the model. Families Transfer moves
families between open documents and links; it cannot pull from files on disk,
and this tool deliberately does nothing else — no library browsing, no
categorised shopping UI, just "make the project match the library where the
names agree, and name everything it will not touch."

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Family.panel/Families Reload.pushbutton/` beside Families
  Transfer and Families Downgrade. `script.py` thin launcher; `bundle.yaml`
  two-line title "Families\nReload", narrative tooltip naming what it refuses
  (no load-date claims, no renamed-file matching), `author: Ruiming Liu`.
  `families_reload_state.py` (folder-scan result shaping, the backup-file
  pattern, name matching and ambiguity detection, version comparison,
  bucketing, plan and type-diff report shaping — pure dicts),
  `families_reload_revit.py` (the loaded-family inventory pass,
  `BasicFileInfo` reads, the LoadFamily executor, post-commit re-read),
  `families_reload_ui.py` + one XAML per window. Reuse from `lib/easybim`:
  `family_load_options.build_overwrite_prompt` and `FamilyLoadOptions` exactly
  as Families Transfer uses them — this is the third consumer of the module
  that exists precisely so this prompt is never copied — plus `excel_workbook`
  for the report export and `compat` for ElementId values. Library folders
  persist per-user through the tool's own pyRevit config section
  (`script.get_config()`), stored as plain path strings — identity by path,
  nothing session-bound. Nothing new hoists yet; the `BasicFileInfo` reader is
  the piece a future library-governance tool would want, so keep it a
  standalone dict-in/dict-out function.
- **Revit API route** — Inventory:
  `FilteredElementCollector(doc).OfClass(Family)` → name, category,
  `IsInPlace` (in-place families are never matchable — no file exists), and
  the current type-name list via `GetFamilySymbolIds()` (needed for the
  post-commit diff). Disk side: bounded `os.walk` over each remembered folder
  collecting `*.rfa`, skipping Revit backup names (`Name.0001.rfa` — the
  `.\d{4}.rfa` suffix), with a file-count budget (default 5,000) that
  truncates with an honest footer count rather than hanging. Per matched
  file, `DB.BasicFileInfo.Extract(path)` supplies `IsFamily` (a project file
  dropped in the folder is a named skip) and the saved-in version —
  capability-probed as `getattr(info, "Format", None)` falling back to the
  older `SavedInVersion`; an `Extract` that throws marks the row "header
  unreadable — skipped" rather than gambling mid-run. Version gate: parse the
  year and compare against `doc.Application.VersionNumber`; newer-than-session
  rows grey pre-flight with the reason. Write:
  `TransactionGroup` "Reload families", assimilated; one nested `Transaction`
  per family; `doc.LoadFamily(path, options)` with a `FamilyLoadOptions`
  instance built once for the batch so the "do this for all loading families"
  tick actually covers the batch (the whole reason the lib module exists —
  Revit's own tick dies per call). `begin(name)` before each load;
  `options.declined` after it separates Skipped — declined from Failed. A
  LoadFamily exception rolls that family's nested transaction back alone; in
  a workshared document the failure row carries the checkout picture from
  `WorksharingUtils.GetCheckoutStatus` and the owner from
  `GetWorksharingTooltipInfo`. No ExternalEvent, no Idling — the window is
  modal and the write is one command.
- **The plan/apply cycle** — `build_plan` computes the full bucketed match:
  per matched family its file path, saved-in version, file date, and current
  type-name list; the rogue list; the ambiguous list with all clashing paths;
  the greyed too-new and unreadable rows with reasons. The confirmation
  window shows every family that will load with its full path, and states the
  prompt policy: "Families that differ from the file will ask Overwrite /
  Overwrite with parameter values / Cancel — Cancel skips that family." No
  acknowledgement tick: the run is one undo step and nothing here is
  irreversible; the per-family overwrite prompt is the consent surface, kept
  native-shaped on purpose. The write runs under a cancellable
  `forms.ProgressBar`; cancel stops the remaining families, the group
  assimilates what committed, and the report says "cancelled after 12 of 31".
  The report re-reads type lists from the committed model and leads with the
  type diff: a family whose list **shrank** is the loud callout, because a
  reload that drops types re-maps placed instances — the one genuinely
  surprising outcome; a family whose list **grew** gets the quieter note
  "n new types — a type renamed in the library leaves the old name behind;
  check for stale types", which is the far more common drift.
- **Edge cases & honest limits** — Named buckets, all visible: "rogue — in
  project, not in library" (listed, untouched — also the only trace of a
  renamed library file, and the footnote says so); "ambiguous — two files
  claim this name" (both paths shown); "saved in Revit {year} — newer than
  this session"; "header unreadable — skipped"; "in-place — no file to reload
  from"; "skipped — declined at the overwrite prompt"; "failed — {Revit's
  message / owned by user}". The tool refuses to guess at renamed files
  (no fuzzy matching — a wrong reload is worse than a missed one), refuses to
  claim which side is newer (Revit records no load date; the columns are
  facts, the judgment is the user's), and never loads a family that is not
  already in the project — this is Reload, not Load, and the library-only
  count exists so nobody thinks those files were touched.
- **Risks** — Name matching is the entire identity: a renamed library file
  silently unmatches, mitigated only by the rogue bucket keeping the orphan
  visible — and 38 Family Rename, which rewrites project family names to
  convention, changes match identity for this tool; the See-also says run
  renames first. LoadFamily into a workshared project fails on ownership —
  per-family failures carrying the checkout status, never a dead batch.
  Very large libraries make the folder scan the slow step — the file-count
  budget and per-folder counts are the guard. The type-diff promise depends
  on capturing type names **before** the group commits and re-reading after;
  do not read "before" lazily inside the loop or the diff lies for families
  the prompt re-entered.
- **Tests** —
  - `test_families_reload_state.py` pins the backup-name pattern (`.0001.rfa`
    skipped, `Model 0001.rfa` kept), exact-name matching, ambiguity
    fail-closed with both paths retained, version-compare boundaries
    (same year loads, newer greys, unparseable skips), bucketing, the
    type-diff shrink/grow classification, and report counters zeroing for
    rolled-back families.
  - `test_families_reload_command_names.py` pins bundle.yaml metadata, XAML
    handler wiring across the three windows, 96×96 icon pairs, the IronPython
    AST scan, and a pin that `family_load_options` is imported from
    `easybim`, never re-implemented in the bundle.
  - `test_families_reload_revit.py` drives the adapter against fakes:
    `BasicFileInfo` with `Format` vs only `SavedInVersion` vs throwing,
    `LoadFamily` returning False after a decline, throwing on checkout, and a
    prompt fake proving one batch asks once when apply-to-all is ticked —
    plus the assertion that nothing but ints and unicode crosses back.
  - `test_families_reload_xlsx.py` pins the Excel export of the report,
    including the diff callout rows.

## UI description

**Main window** — resizable modal, root `Grid Margin="14"`, rows Auto/*/Auto.
Header: "Families Reload" over the DimGray subtitle "Reloads loaded families
from the library folders, matched by name." Body, two cards side by side. Left
**Library folders card**: a short list of folder paths with **Add Folder…** /
**Remove**, remembered across sessions; under it a small scan summary line
("214 .rfa files scanned, 3 backups ignored"). Right **Matched families
card**: checkbox list with Search (name by substring), Select All / Select
None, count line "31 matched — 29 selected, 2 unchecked.", columns Name /
Category / Saved in / File date; too-new, unreadable, and ambiguous rows
greyed in place with the reason in a tooltip; beneath the list the footnote:
"Revit records no load date — these columns describe the file, not which side
is newer." Footer status left: "31 matched, 4 rogue (listed), 2 ambiguous —
skipped, 96 library-only ignored." Buttons right: **Reload…** (primary,
disabled with tooltip until a row is checked), **Export to Excel**,
**Cancel**.

**Confirmation window** — the complete plan as a read-only table: one row per
family, "Reload {name} ← {full path}", grouped by folder, with the prompt
policy sentence above the footer. Footer status: "29 families will reload from
2 folders. One undo step." Buttons: **Reload 29 families** (primary),
**Back**.

**Report window** — the type-diff callout on top when it has content ("1
family lost types: Door-Double — 2 types gone; placed instances re-mapped"),
then Reloaded / Skipped / Failed expanders, state preserved across rebuilds. A
skipped row reads "Skipped — declined at the overwrite prompt"; a failed row
reads "Failed — rolled back: owned by user jsmith." Footer status: "27
reloaded, 1 skipped, 1 failed (rolled back). Type counts re-read from the
model." Buttons: **Export to Excel**, **Close**.

### User operation flow

1. Ribbon: Family → Families Reload. Remembered folders load; the scan runs
   with the footer reading "Scanning 2 folders…"; the Main window fills.
2. Adjust folders if needed (Add Folder… rescans), search, untick families to
   leave stale on purpose — an unticked match will land under Skipped, never
   Failed.
3. Press **Reload…**; review the Confirmation window's full path list.
4. Press **Reload 29 families**. The cancellable progress bar runs per
   family; the "Family Already Exists" prompt appears only for families that
   actually differ, and its "do this for all" tick silences the rest of the
   batch.
5. Cancel path A: **Cancel** / **Back** before step 4 — nothing written.
   Cancel path B: cancelling the progress bar stops the remaining families;
   what committed stands as one undo step and the report says so.
6. The Report window opens, read back from the committed model, diff callout
   first. Export to Excel for the record if wanted; Close. One Ctrl+Z
   restores every reloaded family to its pre-run state.

## See also

- Existing EasyBIM: **Families Transfer** (between open documents and links —
  and the reason `family_load_options` lives in lib), **Family Types** (the
  type-table this tool's diff callout sends you to), **Families Downgrade**
  (same panel).
- Plan siblings: **26 Family Audit** — audit says which families are heavy or
  suspect, reload brings the fixed ones in; a natural chain. **10 Families
  Purge** (same panel, the dead-type end of the same hygiene). **38 Family
  Rename** — renames change this tool's match identity; run renames before a
  library sync. **17 Where Used** — what a reload's type re-map actually
  touched.
