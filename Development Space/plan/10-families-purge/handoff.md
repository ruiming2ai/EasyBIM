# 10 — Families Purge

A reasoned Purge Unused: per-type "why purgeable" reasons, search, per-item rollback, and an honest unknown-kept bucket.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 10 of 45 | Family | no | M | 9/10 | 8/10 |

## Main purpose

Revit's Purge Unused is a flat checkbox tree that never says *why* a thing is
purgeable, and the scary cases — a type that only lives in a legend, a shared
nested family with zero user-placed instances — are exactly the ones it
explains least. So people either purge everything blind on a Friday or purge
nothing for the life of the project, and the model drags every dead type to
every sync.

Families Purge does one read pass and attaches a reason to every row. Usage per
FamilySymbol is built from a single histogram of `GetTypeId()` over every
non-type element — which catches instances, tags, and anything else that
reports a type — plus three probed sources the histogram cannot see: legend
components, shared-nested placement, and view filters whose string rules name
the family or type. A candidate is a type with zero use *anywhere*; a family
whose every type is a candidate becomes a family deletion. Anything a probe
cannot prove is marked "unknown use — kept" and is never purgeable: the tool's
first rule is that `Document.Delete` on a used type does not refuse, it
*cascades* — it would take the instances with it — so the plan must be right
before the write, and a delete-time tripwire (below) backstops even that.

It earns rank 10 because purging is universal (every discipline, every
project), the native tool is actively distrusted, and the free ecosystem's
answer — pyRevit's Wipe tools — deletes whole classes of things rather than
executing a reasoned per-type plan. The differentiators are exactly the house
patterns: reasons on every row, search over the tree, a complete dry-run plan,
per-family rollback inside one undo step, and a report that re-reads the model
and refuses to claim work that rolled back. The deep interactive version of
"what uses this?" is 17 Where Used; this tool needs only the binary answer
(zero use / some use / unknown) and stays M-effort by not building that tree.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Family.panel/Families Purge.pushbutton/` beside Family Types
  and Families Downgrade. `families_purge_state.py` (usage classification,
  candidate/kept/unknown bucketing, purge ordering, plan and report shaping —
  pure dicts), `families_purge_revit.py` (the histogram pass, legend/nested/
  filter probes, the delete executor with the tripwire),
  `families_purge_ui.py` + XAML for the three windows. The tree browser
  composes `lib/easybim/circuit_schedule_state`'s generic tree/search engine
  (category → family → type, search flips `is_visible`, selection survives).
  Excel export goes through `lib/easybim/excel_workbook`. Nothing new hoists
  yet; the usage-probe layer is the piece 17 Where Used would generalize —
  keep it dict-in/dict-out so that hoist is a move, not a rewrite.
- **Revit API route** — One `FilteredElementCollector(doc).OfClass(FamilySymbol)`
  for the inventory; one `WhereElementIsNotElementType()` pass building the
  `GetTypeId()` histogram (this deliberately counts instances inside groups
  and design options — conservative by design). Probed sources: legend
  components via `OST_LegendComponents`, each row's target type read from
  `BuiltInParameter.LEGEND_COMPONENT` `AsElementId()` — resolution differs
  across API generations, so the read is capability-probed and a per-generation
  fake ships in tests; shared-nested placement via
  `FamilyInstance.SuperComponent != null`, reported as "nested in {host
  family}" (nested-only types are kept — deleting them would gut the host's
  display); view filters via `ParameterFilterElement`, walking
  `GetElementFilter()` (older `GetRules()`) for string rules on family/type
  name parameters and matching by each rule's own evaluator. Deletion:
  `TransactionGroup` "Purge families", assimilated, with one nested
  `Transaction` per family; inside it `doc.Delete(ids)` returns the actually
  deleted ids — **the tripwire**: any returned id outside the planned set for
  that family (its types, the family itself when all types go) rolls that
  nested transaction back and re-buckets the family as "kept — deleting it
  would also delete {n} other elements", with a sample id. Delete types
  before their family; deleting the last type may take the family with it,
  which the planned set anticipates. Worksharing checkout failures surface as
  exceptions → that family rolls back alone and lands under Failed with
  Revit's own message. No ExternalEvent, no Idling, no version gate beyond
  the probes: the window is modal and the write is one command.
- **The plan/apply cycle** — `build_plan` produces the full bucketed
  inventory: per type, its usage line ("0 instances · 0 legends · not nested ·
  no filter") and bucket (purgeable / kept-with-reason / unknown-kept), and
  the derived family-level deletions. The confirmation window shows the
  *complete* plan — every deletion with its reason, every near-miss with why
  it is kept — and gates the write behind an acknowledgement checkbox:
  "I understand deleted types are recoverable only by Ctrl+Z in this
  session." The write runs under a cancellable `forms.ProgressBar`
  (per-family steps); cancel stops the remaining families, the group
  assimilates what committed, and the report says so plainly. The report
  window re-reads the model after commit — remaining type count, families
  actually gone — and its Purged / Skipped / Failed expanders zero their
  counters for anything rolled back, so it never claims work that is gone. A
  closing line handles the cascade of freed dependencies: "2 families became
  unused during this purge — run again to see them."
- **Edge cases & honest limits** — Named buckets, all visible in place and
  greyed rather than hidden: "kept — {n} instances", "kept — placed in legend
  {name}", "kept — nested in {host}", "kept — named by view filter {name}",
  "unknown use — kept" (any probe that threw), "kept — Revit reports {n}
  dependent elements" (the tripwire, post-run). Scope is loadable families
  only, on purpose: no materials, line patterns, view types, or system-family
  types (native Purge covers those), and nothing *inside* a family document —
  purging a family's internal nested fat is Families Downgrade / family
  editing territory, and the tool says so in its tooltip. In-place families
  are listed and labelled "in-place". A wholesale probe failure fails closed
  and stays bounded: if legend reading is down, every type in a
  legend-placeable category downgrades to unknown-kept; if one filter's rules
  are unreadable, only candidates in *that filter's declared categories*
  downgrade — the footer names the cause either way.
- **Risks** — "Unused" is unprovable in full: Revit's internal dependencies
  (keynote assignments, view type defaults, Family Type parameters in other
  families) surface only at delete time — which is why the per-family nested
  transactions, the returned-ids tripwire, and the unknown-kept bucket are
  the design, not a fallback. The cascade behaviour of `Document.Delete` is
  the tool's central hazard; a plan bug deletes placed work, so the state
  module's bucketing logic carries the heaviest test load. Legend-component
  resolution across generations is the flakiest probe. The histogram pass on
  a 5,000-family hospital model is the performance ceiling — it is one linear
  pass by design; anything per-type-times-per-element is the trap.
- **Tests** —
  - `test_families_purge_state.py` pins bucketing from fixed dicts: histogram
    plus each probe source, nested-only kept, filter-match kept, the bounded
    probe-failure downgrades, family-level promotion when all types are
    candidates, purge ordering, tripwire re-bucketing, and report counters
    zeroing on rollback.
  - `test_families_purge_command_names.py` pins bundle.yaml metadata, XAML
    handler wiring across all three windows, 96×96 icon pairs, the IronPython
    AST scan, and zero Revit imports in the state module.
  - `test_families_purge_revit.py` drives the adapter against fakes per API
    generation: legend-parameter shapes old and new, a `Delete` fake
    returning extra ids to spring the tripwire, a checkout-refusal exception,
    and the last-type-deletes-family cascade.
  - `test_families_purge_xlsx.py` pins the Excel export of the report through
    `excel_workbook`.

## UI description

**Main window** — shaped like Revit's own purge dialog, but with reasons.
Header: "Families Purge" over "Every unused type, with the reason it is safe —
and every kept type, with the reason it is not." Body card: a category-grouped
checkbox tree (category → family → type) on the shared tree engine; purgeable
rows read `0 instances · 0 legends · not nested · no filter` and start
checked; kept rows sit greyed in place with their reason — never hidden. Count
line: "214 purgeable — 198 selected, 16 unchecked." Select All / Select None,
a Search box (type names by substring), and a "Hide kept" toggle that filters
at rebuild time. Footer status left: "214 purgeable, 38 kept, 12 unknown —
kept." Buttons right: **Purge…** (primary, disabled with tooltip when nothing
is checked), **Export to Excel**, **Cancel**.

**Confirmation window** — the complete dry run as a read-only table grouped by
family: rows "Delete type {name} — 0 uses found", family rows "Delete family
{name} — all 4 types unused", and the kept near-misses inline for context.
Above the footer, the acknowledgement checkbox: "I understand deleted types
are recoverable only by Ctrl+Z in this session." **Purge 198 types** stays
disabled until it is ticked. Footer status: "198 types across 61 families will
be deleted; 38 kept, 12 unknown — kept."

**Report window** — Purged / Skipped / Failed expanders (state preserved
across rebuilds), each row carrying its reason — a tripwire row reads
"kept — deleting it would also delete 3 other elements"; a checkout row reads
Revit's own message. Footer status: "196 purged, 2 failed (rolled back), 52
kept. Model now holds 1,412 types. 2 families became unused during this purge
— run again to see them." Buttons: **Export to Excel**, **Close**.

### User operation flow

1. Ribbon: Family → Families Purge. The read pass runs (status "Scanning
   4,812 elements…"), then the Main window opens with the bucketed tree.
2. Search, expand, untick anything to keep (an unticked purgeable row is a
   user choice — it will land under Skipped, never Failed). "Hide kept"
   narrows the view; kept rows otherwise stay greyed in place.
3. Press **Purge…**. The Confirmation window shows the complete plan.
4. Tick the acknowledgement, press **Purge 198 types**. Cancellable progress
   runs per family.
5. Cancel path A: **Cancel** on either window before the tick — nothing has
   been written, the model is untouched. Cancel path B: cancelling the
   progress bar mid-run stops the remaining families; what committed stands
   as one undo step and the report says "cancelled after 61 of 214".
6. The Report window opens, read back from the committed model. A skipped
   item looks like: "Skipped — unchecked by user"; a failed one like:
   "Failed — rolled back: owned by user jsmith."
7. Export to Excel for the record if wanted; Close. One Ctrl+Z restores
   everything the run deleted.

## See also

- Existing EasyBIM: **Family Types** (the type-table grid this shares the
  Family panel with), **Families Downgrade** (the tool that owns
  *inside-the-family* fat), **Families Transfer**, and **Circuit Schedule** —
  whose generic tree/search engine this composes.
- Plan siblings: **17 Where Used** — the deep interactive answer to this
  tool's "unknown use — kept" bucket; its probe layer should grow out of this
  one's. **26 Family Audit** — audit finds the heavy families, purge removes
  the dead ones; a natural pair. **25 Families Reload**, **38 Family Rename**
  (same panel neighbours), **45 Text Types** — the used rogues purge cannot
  touch, and **32 View Sweep** — the same delete-with-kept-because pattern
  applied to views.
