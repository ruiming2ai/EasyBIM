# 39 — Filter Manager

One matrix of every view filter across every template and view — proven
duplicates merged, orphans purged, template drift repaired.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 39 of 45 | Views | no | M | 7/10 | 7/10 |

## Main purpose

ParameterFilterElements breed: "Copy of Mech Supply 2" with identical
rules, a filter applied in one view template but forgotten in its
siblings, and a tail of orphans nobody dares purge because the native
dialog shows one view at a time. The result is templates that look
aligned but print differently, and a filter list too long to trust.

Filter Manager reads the whole picture in one pass — every filter, every
view, every template, who applies what with which overrides — into a
usage matrix, and stages governance actions against it: purge the
filters with zero uses, apply a filter to the sibling templates that
forgot it (overrides copied from a named donor), remove it where it does
not belong, rename, and merge duplicates. Duplicate detection is where
the honesty lives: a normalized rule signature — category set plus
decomposed rules — proves two filters identical, and a filter whose
rules do not decompose is classified "rules unreadable — never
auto-matched", so a merge is only ever offered between provably
identical filters. Everything stages red until Apply, commits as one
undo step with per-action rollback, and reports back from the re-read
matrix.

It earns rank 39 because the ecosystem is lopsided in exactly the way
the prior-art survey names: view template *transfer* is everywhere —
native Transfer Project Standards, View Settings Transfer inside
EasyBIM, every free package — while comparison and audit are almost
nowhere. The cross-template filter matrix is the underserved half:
native Revit has no such view at all, and the paid SmartViews-class
add-ins do filter creation, not audit-and-purge with
provable-duplicate honesty. Inside EasyBIM the lane is empty — View
Settings Transfer moves whole view settings between views; nothing
governs filters across the document. Usefulness sits at 7 because the
audience is the template owner, not every seat; when the template owner
needs it, nothing else exists.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Views.panel/Filter Manager.pushbutton/` beside View
  Settings Transfer. `script.py` thin launcher; modal windows, one
  command, no bridge. `bundle.yaml` two-line title "Filter\nManager",
  narrative tooltip naming the never-auto-matched rule,
  `author: Ruiming Liu`. Split: `filter_manager_state.py` (the rule
  signature normalizer, the usage matrix, duplicate grouping, staging
  model, action bucketing — pure dicts, desktop-tested),
  `filter_manager_revit.py` (collection pass, override reader/writer
  with capability probes, the executor, post-commit re-read),
  `filter_manager_ui.py` + `FilterManagerWindow.xaml`,
  `FilterManagerConfirm.xaml`, `FilterManagerReport.xaml`. The
  filter-rule decomposition reader is shared machinery with 38 Family
  Rename — whichever builds second hoists it to
  `lib/easybim/filter_rules.py`. A filter's identity is its name
  everywhere in the plan and the report, so the same audit reads
  identically in the next project.
- **Revit API route** — Collection:
  `FilteredElementCollector(doc).OfClass(ParameterFilterElement)` and
  `.OfClass(SelectionFilterElement)`; all views and templates via
  `OfClass(View)` minus browser/internal types, templates flagged by
  `View.IsTemplate`. Per view/template: `GetFilters()`,
  `GetFilterOverrides(id)`, `GetFilterVisibility(id)`, and
  `GetIsFilterEnabled(id)` behind a `hasattr` probe (landed
  mid-cycle). A view whose template controls Filters (derived from
  `GetNonControlledTemplateParameterIds()`) is counted as an effective
  use *via* its template, kept distinct from direct uses in the
  matrix. Signature: `GetElementFilter()` (probed; older documents may
  only answer the deprecated `GetRules()`), decomposed through
  `ElementLogicalAndFilter`/`ElementLogicalOrFilter.GetFilters()`,
  `ElementParameterFilter.GetRules()`, `FilterInverseRule.
  GetInnerRule()`, and per-kind `FilterRule` value reads
  (`RuleString`, `RuleValue`, epsilon for doubles), with the rule's
  parameter resolved to a *name* (built-in enum name, shared/project
  parameter name) so the signature is portable; plus `GetCategories()`
  as a sorted name set. Any step that throws or meets an unknown rule
  type marks the filter "rules unreadable — never auto-matched".
  Writes, each a nested `Transaction` inside one assimilated
  `TransactionGroup`: purge via `doc.Delete(filterId)`; apply via
  `view.AddFilter(id)` + `SetFilterOverrides(id, ogs)` with the donor
  view's `OverrideGraphicSettings` copied member-by-member behind
  capability probes (`SetSurfaceForegroundPatternId` on 2019+ vs the
  older projection/cut pattern members), an `InvalidElementId` fill
  pattern (deleted since the donor was set) copied without the pattern
  and noted; remove via `view.RemoveFilter(id)`; rename via the
  `Name` setter; merge = per using view, add the survivor with the
  duplicate's overrides, remove the duplicate, then delete it — each
  view its own nested transaction so a template that rejects an
  override rolls back alone.
- **The plan/apply cycle** — `build_plan` produces the matrix (filter ×
  template/view, direct vs via-template), the duplicate groups with
  their proof status, the zero-use list, and the staged action list:
  purge / apply(donor) / remove / rename / merge(survivor). The
  Confirmation window lists every staged action in plain sentences —
  "Apply 'Mech Supply' to 3 templates, overrides from E-Power Plan" —
  and the purge of any filter that is applied anywhere demands its own
  acknowledgement checkbox ("Purging removes it from 4 views."); a
  zero-use purge needs none, it is fully undoable and touches no view.
  Apply commits the group; the Report window re-reads the matrix from
  the committed model and shows Applied / Purged / Merged / Renamed /
  Skipped (named) / Failed (named, Revit's message), with the new
  zero-use count re-counted — the report answers "what does the
  document look like now", not "what did I just click".
- **Edge cases & honest limits** — Named buckets: "rules unreadable —
  never auto-matched", "selection-based — compared by name only"
  (SelectionFilterElements are element sets, inherently
  document-bound; they list, count, and purge, but never auto-match),
  "0 uses — purgeable", "unchecked", "filter gone since scan —
  refresh", "template rejects override — rolled back alone", "owned by
  {user}" in workshared documents. What the tool refuses to do, stated
  in the window: it never merges what it cannot prove identical — two
  filters that *look* alike but read partially stay separate, forever,
  with the reason; it does not create filters or edit rule values —
  governance, not authoring; it does not reorder filters within a view
  (`AddFilter` appends; order-dependent override precedence is native
  dialog work, and the tooltip says so); schedule filters and
  DWG-export layer mappings are different mechanisms, untouched. The
  matrix respects the house display caps — a 400-view document
  truncates a branch with an "… N more — capped" row rather than
  hanging the window.
- **Risks** — `GetElementFilter` decomposition is the trap the
  brainstorm names, sharpened: rules built by other add-ins, newer
  rule kinds, and value providers that are not
  `ParameterValueProvider` all round-trip partially — the classifier
  must treat *any* unrecognized node as poisoning the whole signature
  (unreadable), never compare a partial signature, and the state tests
  pin that a signature with one unknown node auto-matches nothing.
  Override copying across versions is the second trap: surface vs cut
  vs projection pattern members changed shape — capability probes per
  member, and the deleted-fill-pattern case must degrade to
  copied-without-pattern with a note, not throw mid-batch.
  Performance: filters × views is a quadratic read — one pass per
  view collecting all three per-filter reads into dicts, no per-cell
  API calls at UI time. Purge is the one action users fear: the
  applied-anywhere acknowledgement plus per-action rollback is the
  answer, and one Ctrl+Z restores the whole run.
- **Tests** — `test_filter_manager_state.py` pins signature
  normalization (category order, rule order, epsilon on doubles,
  inverse rules), the unknown-node-poisons-signature rule, duplicate
  grouping only among proven signatures, selection filters never
  auto-matching, direct vs via-template usage counting, staging and
  action bucketing, display capping, and counters zeroing on
  rollback. `test_filter_manager_command_names.py` pins bundle
  metadata, XAML↔handler wiring for the three windows, 96×96 icons,
  the IronPython AST scan, and forbidden-API pins.
  `test_filter_manager_revit.py` drives the adapter against fakes per
  API generation: `GetElementFilter` present / absent / throwing,
  `GetIsFilterEnabled` probed, override members per version, the
  deleted-pattern degrade, a template rejecting an override rolling
  back alone, merge repointing views before the delete, and the
  post-commit matrix re-read — nothing but ints and unicode crossing
  back.

## UI description

**Main window** — resizable modal in the Sheet Manager mould, header
"Filter Manager" over the DimGray subtitle "Every filter across every
template and view. Merges are offered only between provably identical
filters." Two cards side by side. Left **Filters card**: live-filter
Search, count line, and the filter list with per-row usage summary
("used in 3 templates, 7 views — 2 more via template" / "0 uses"),
sortable by uses; duplicate groups marked with a chip ("dup of Mech
Supply — proven"), unreadable rows marked "rules unreadable" with the
tooltip carrying why; row buttons **Rename** and **Purge** stage red in
place (purged rows strike through red). Right **Applied-in card**: for
the selected filter, the checkbox list of templates and views with
Select All / Select None and "Hide Un-checked" (rebuild-time filter);
ticking an unapplied row stages an apply (red), unticking an applied
row stages a remove (red); above the list a Donor ComboBox ("copy
overrides from…") feeding staged applies; via-template rows grey with
"controlled by template {name}" in the tooltip. Footer status left,
**Apply…** (`IsDefault`, disabled with a tooltip until something is
staged), **Cancel** (`IsCancel`).

> "64 filters — 9 with 0 uses, 3 duplicate pairs proven, 7 unreadable
> (never auto-matched)."

> "3 purges staged, 1 apply staged (donor: E-Power Plan), 1 merge
> staged. Nothing written."

**Confirmation window** — every staged action as a read-only sentence
list, grouped by kind; the applied-anywhere purge acknowledgement
checkbox ("Purging 'Old Supply' removes it from 4 views.") gating its
rows. Footer: "6 actions across 5 templates, 2 views. One undo step."
Buttons: **Apply 6 actions** (primary, inert until acknowledgements are
ticked), **Back**.

**Report window** — read-only table: Applied / Purged / Merged /
Renamed / Skipped (named) / Failed (named, Revit's message verbatim),
re-read from the committed model, expanders preserving state across
rebuilds. Buttons: **Close**.

> "2 purged, 1 merged (4 views repointed), 3 applies committed, 1
> failed (rolled back alone) — 7 filters with 0 uses remain, re-counted
> from the model."

### User operation flow

1. Ribbon: Views → Filter Manager. The one-pass scan builds the matrix;
   the window opens with usage counts and duplicate chips already
   resolved.
2. Sort the Filters card by uses; stage **Purge** on the 0-uses tail.
   Select a duplicate group's survivor and stage the merge from its
   chip.
3. Select "Mech Supply", tick the two sibling templates that forgot it,
   pick the donor view for overrides — both rows stage red.
4. Press **Apply…**; read the Confirmation sentences; tick the
   applied-anywhere purge acknowledgement.
5. **Apply 6 actions**. Nested transactions run; a template that
   rejects an override rolls back alone while the rest land.
6. Cancel path: **Cancel** / **Back** at any point before step 5 —
   staging never writes. An unticked staged row lands as "skipped —
   unchecked", never failed.
7. The Report window opens, re-read from the model. A skipped item
   reads: "'Old Return' — skipped: rules unreadable, merge never
   offered." A failed item reads: "Apply to 'RCP Template' — failed,
   rolled back: {Revit's message}."
8. Close. One Ctrl+Z restores every purge, apply, and merge.

## See also

- Existing: **View Settings Transfer** (moves whole view settings
  between views — transfer, where this is governance), **View Align**
  (panel neighbour), **Sheet Manager** (the two-card staged-grid mould
  this window follows).
- Plan siblings: **38 Family Rename** — shares the filter-rule
  decomposition reader (whichever builds second hoists
  `lib/easybim/filter_rules.py`), and its renames are what break the
  rule strings this tool audits. **32 View Sweep** — the other
  Views-panel audit; sweep retires views, this retires the filters
  they leave orphaned. **10 Families Purge** — the same reasoned-purge
  posture: every survivor carries its reason. **45 Text Types** — the
  same rogue-type governance shape applied to text.
