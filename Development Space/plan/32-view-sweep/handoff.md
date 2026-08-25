# 32 — View Sweep

Delete the working views nothing depends on — and grey every survivor in
place with the reason it stays.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 32 of 45 | Views | no | M | 8/10 | 7/10 |

## Main purpose

A model that has lived a year carries hundreds of working views — "Copy of
Section 12", one-off exports, abandoned 3D checks — and nobody deletes them,
because deleting the wrong one kills a callout that was sitting on a sheet.
So they accumulate, the browser becomes unreadable, and file open slows.
Cleaning by hand means checking each view against sheets and references one
at a time, which nobody will ever actually do.

View Sweep does the checking in one pass and shows its working. Every
non-template view is classified with a kept-because reason — placed on a
sheet, primary of a sheeted dependent, referenced by a named other view,
currently open, the active view, the model's starting view — and only what
no reason explains becomes a candidate, annotated with who created it and
who last changed it when the model is workshared. Kept views stay on screen,
greyed, with their reason in a tooltip: visible, never hidden, because trust
in a delete tool comes from seeing what it refused to touch and why.
Candidates start unchecked — deletion is opt-in, row by row — and each
checked view deletes inside its own nested transaction, so a view Revit
refuses rolls back alone while the rest land. The closing report re-collects
views from the document, so the count claims only what is actually gone.

The reference classification is the whole safety story, and it is where the
free ecosystem fails: the floating Dynamo graphs for this famously delete
views that callouts reference, and pyRevit's wipe tools are all-or-nothing.
The reference APIs landed mid-cycle, so they are capability-probed; on a
Revit without them, every section, callout, and elevation is kept with the
reason "references unverifiable on this version" — fail closed, never guess.
Within EasyBIM the lane is clear: View Align and View Settings Transfer do
placement and template transfer, not lifecycle. This is the Views panel's
version of the reasoned purge that 10 Families Purge brings to families.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Views.panel/View Sweep.pushbutton/`. `script.py` thin; one
  modal main window, a native-mimicry confirmation TaskDialog, a report
  window — no bridge, no persistent engine (see the no-preview limit
  below). `bundle.yaml` two-line title "View\nSweep", narrative tooltip
  naming the kept-because rule, `author: Ruiming Liu`. Split:
  `view_sweep_state.py` (classification over plain dicts with a fixed
  reason precedence, grouping by view type, display capping, skip
  bucketing — pure Python), `view_sweep_revit.py` (the one-pass scan into
  dicts, the deleter, the re-collect), `view_sweep_ui.py` +
  `ViewSweepWindow.xaml` + `ViewSweepReport.xaml`. Nothing hoists yet:
  the classification is this tool's own, and the first second consumer
  (17 Where Used wants the same reference graph) does the hoist when it
  arrives.
- **Revit API route** — `FilteredElementCollector` `OfClass(View)`
  `WhereElementIsNotElementType`, minus `View.IsTemplate`, minus
  `ViewSheet` (sheets are Sheet Manager's business, never swept), minus
  browser/internal view types. Placement: one pass over `Viewport`
  collecting `ViewId`, one over `ScheduleSheetInstance` collecting
  `ScheduleId` — excluding instances whose `IsTitleblockRevisionSchedule`
  is true, so the revision schedule inside a title block family is never
  mistaken for a placed schedule view. Dependents:
  `View.GetPrimaryViewId()` / `GetDependentViewIds()` — a primary with
  any sheeted dependent is kept. References:
  `View.GetReferenceCallouts()` / `GetReferenceSections()` /
  `GetReferenceElevations()` per view, each id resolved to its target
  through `ReferenceableViewUtils.GetReferencedViewId()` — all of it
  `hasattr`-probed since these landed mid-cycle; a failed probe keeps
  every section/callout/elevation with "references unverifiable on this
  version". Session state: `UIDocument.GetOpenUIViews()` plus the active
  view. The starting view via
  `StartingViewSettings.GetStartingViewSettings(doc).ViewId`.
  Attribution: `WorksharingUtils.GetWorksharingTooltipInfo(doc, id)` —
  resolved for candidates only, never for the kept majority, and only in
  workshared documents; ownership pre-checked with
  `WorksharingUtils.GetCheckoutStatus`, where a view owned by another
  user is a named skip, never attempted. Deletion: `doc.Delete(viewId)`
  in one nested `Transaction` per view inside one assimilated
  `TransactionGroup`, under a cancellable `forms.ProgressBar`; counters
  zero on any rollback.
- **The plan/apply cycle** — `build_plan` is the classification itself:
  one reason per view under a fixed precedence (sheeted → primary of
  sheeted dependent → referenced by view X, named → starting view →
  active → open → version-fallback), candidates being the unexplained
  remainder. The main window is the plan: kept views greyed in place with
  reasons, candidates checkable, nothing checked by default. The primary
  button opens the **Confirmation dialog** — TaskDialog command links in
  the native Review-Warnings register, plan counts in the message, and a
  verification checkbox: "Delete 38 views. Everything drawn only in them
  — detail lines, text, dimensions — is deleted with them. One undo
  step." Apply runs per view; the Report window then re-collects from the
  document: Deleted / Skipped (named) / Failed (named, with Revit's own
  reason), and the remaining view count re-counted from the model.
- **Edge cases & honest limits** — Kept-because buckets as above, plus
  the named skips: "unchecked", "owned by <user>", "view gone since scan
  — refresh". Failures carry Revit's message verbatim (some deletes are
  refused for reasons only Revit knows at commit time; per-view rollback
  is the answer, not prediction). Grouping expanders respect the house
  display cap — a 3,000-view model truncates a branch with an "… N more —
  capped" row rather than hanging the window. Honest limits, stated in
  the window: a dependency living outside the model's own reference graph
  — an add-in's stored view id, a colleague's habit — cannot be detected,
  which is exactly why nothing is checked by default; and there is no
  preview, because previewing a candidate means opening it, which changes
  the very open-view state the scan just recorded — v1 lists and
  attributes, it does not open.
- **Risks** — Reference-API coverage by version is the safety story: the
  fallback must genuinely fail closed, and the fakes must prove it (a
  model scanned on old Revit keeps every viewer-type view). Missing one
  placement class means deleting a sheeted view — `Viewport` vs
  `ScheduleSheetInstance` vs title-block revision schedules is the known
  trio, and the revit tests pin each. Performance: thousands of views
  times tooltip-info lookups is the trap — attribution runs for
  candidates only, and the scan is one pass into dicts. A cascade is
  inherent to `doc.Delete` — view-specific annotation goes with the view
  — so the confirmation wording carries it, not the fine print. A
  cancelled batch keeps its committed nested transactions: the group
  assimilates what landed and the report says "cancelled after N of M".
- **Tests** — `test_view_sweep_state.py` pins the reason precedence (one
  reason wins, in order), the fail-closed version fallback flag turning
  viewer types into kept rows, candidate derivation, per-type grouping
  and capping, skip bucketing, and counters zeroing on rollback.
  `test_view_sweep_command_names.py` pins bundle metadata, XAML↔handler
  wiring for both windows, 96×96 icons, the IronPython AST scan, and
  forbidden-API pins. `test_view_sweep_revit.py` drives the adapter
  against fakes per API generation: reference APIs present/absent,
  `ReferenceableViewUtils` resolution, the `ScheduleSheetInstance` /
  title-block-revision-schedule exclusion, starting-view and open-view
  capture, checkout statuses, a delete refusal rolling back alone, and
  the post-commit re-collect.

## UI description

**Main window** — resizable modal, header "View Sweep" over the DimGray
subtitle "Finds views no sheet or view depends on. Kept views stay
visible with their reason." One card: search TextBox, Select All / Select
None (candidates only), count line "214 candidates, 38 checked.", and the
checkbox list grouped in expanders by view type — columns Name / Creator
/ Last Changed By, the two attribution columns hidden entirely in
non-workshared models rather than shown empty. Kept views sit greyed in
place, checkbox disabled, reason in the tooltip: "kept — referenced by
Section A-A". A "Hide kept views" checkbox filters at rebuild time (the
Hide Un-checked precedent); the search flips visibility only, so checks
survive it. Footer: status left, **Delete Checked…** (`IsDefault`,
disabled with a tooltip until something is checked), **Cancel**
(`IsCancel`).

> "486 views scanned — 272 kept with reasons, 214 candidates. Nothing has
> been deleted yet."

> "References unverifiable on this version — 61 sections kept closed."

**Confirmation dialog** — native-mimicry TaskDialog: "Delete 38 views?"
with command links "Delete 38 views (one undo step)" / "Cancel", the
counts per view type in the message, and the verification checkbox
"Everything drawn only in these views is deleted with them." The delete
link stays inert until the checkbox is ticked.

**Report window** — read-only WPF table: Deleted / Skipped (named) /
Failed (named, Revit's reason verbatim), re-collected from the document.
**Close** only.

> "37 deleted, 1 failed (Revit: last elevation of its type), 176
> candidates remain — re-counted from the model. One undo step."

### User operation flow

1. Ribbon: Views → View Sweep. The one-pass scan runs; the window opens
   with every view classified, kept rows greyed, candidates unchecked.
2. Read the reasons. Expand a view type; search "Copy of" to surface the
   usual suspects — checks survive the filter.
3. Check candidates row by row, or Select All within what the filter
   shows and then uncheck the survivors-by-judgement. An unchecked
   candidate is "skipped — unchecked", never failed.
4. **Delete Checked…** opens the Confirmation dialog; tick the
   verification checkbox; choose the delete command link.
5. Deletion runs per view under the cancellable progress bar. Cancelling
   keeps what committed and marks the rest "cancelled after N of M".
6. The Report window opens with counts re-collected from the model. A
   skipped item reads: "Working 3D — RL — skipped: owned by jsmith"; a
   failed item reads: "Section 9 — failed: Revit refused the delete
   (reason verbatim)."
7. Close. One Ctrl+Z in Revit restores every deleted view.
8. Cancel path: **Cancel** (or Esc) in the Main window, or Cancel in the
   Confirmation dialog, ends the run with nothing deleted — the scan
   never writes.

## See also

- Existing: **View Align** and **View Settings Transfer** (the Views
  panel neighbours — placement and templates, where this is lifecycle),
  **Sheet Manager** (owns sheet lifecycle; the reason sheets are out of
  scope here).
- Plan siblings: **10 Families Purge** — the same reasoned-purge posture
  ("why purgeable" there, "kept because" here) and the same
  per-item-rollback discipline. **17 Where Used** — the
  dependency-before-delete engine; when it builds, the reference graph
  hoists and both tools read one implementation. **39 Filter Manager** —
  the other Views-panel audit, for templates and filters.
