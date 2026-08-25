# 28 — Link Health

Everything Manage Links does not say — pins, worksets, duplicates, imports
posing as links — with fixes split honestly into one undo step and the load
operations that cannot join it.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 28 of 45 | Links | no | M | 8/10 | 7/10 |

## Main purpose

Manage Links tells you Loaded or Not Found and stops there. It does not tell
you that one link instance is unpinned and free to be nudged, that the
consultant model sits on the "Interiors" workset so closing worksets never
sheds it, that two instances of the same link sit exactly on top of each
other doubling every clash, that a path is absolute and will break on the
next machine, or that the DWG everyone believes is linked was actually
imported. Each of those is discovered the expensive way — at clash review,
at sync time, on the new hire's workstation.

Link Health reads all of it in one pass and puts it in one grid: status,
path type, attachment, pin state, workset, instance count, and a flag column
whose every icon carries its reason. Then it offers fixes — but split into
two groups the way the API actually splits them. Model fixes (pin the
unpinned, move links to a proper workset, delete exact-duplicate instances
by a stated deterministic rule) commit as one assimilated TransactionGroup
with per-fix rollback: one undo step, the house promise. Load operations
(unload, reload) cannot live inside a transaction at all, so they run
afterwards, behind their own acknowledgement, and the window says plainly
that Ctrl+Z will not bring them back. Blurring that line would break the
one-undo promise, so the UI makes it impossible to misread.

The rank is earned by breadth-per-effort: Batch Link loads links and DWG
Open/Reload opens them — neither audits; pyRevit's preflight checks list
links read-only; native Revit has no pin/workset/duplicate/import-vs-link
view anywhere. This tool is the bookkeeping half of the Links panel's weekly
ritual — 27 Site Check is the geodesy half — and it composes the
`link_reload` helpers the extension already ships instead of growing a
second reload path.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Links.panel/Link Health.pushbutton/` beside Batch Link.
  `script.py` thin launcher; `bundle.yaml` two-line title "Link\nHealth",
  narrative tooltip naming the walls (no nested-link enumeration, no
  per-category link visibility), `author: Ruiming Liu`.
  `link_health_state.py` (flag derivation from row dicts, duplicate
  grouping and the keep-tiebreak, fix-plan shaping with the two-group
  separation enforced in data — a load op physically cannot enter the
  transaction plan list — report shaping), `link_health_revit.py` (the one
  pass, the model-fix executor, the post-group load-op runner, post-commit
  re-read), `link_health_ui.py` + XAML per window. Compose from
  `lib/easybim`: `link_reload` (`collect_manage_links_elements`,
  `get_linked_cad_type_ids`, `is_linked_manage_link_element`,
  `reload_link_element`) — this is the second consumer that justifies that
  module's existence, and any gap it has (per-element unload) is added
  there, not copied here — plus `excel_workbook` and `compat`. If 27 Site
  Check ships too, the RevitLinkInstance row collector hoists as their
  shared piece.
- **Revit API route** — One pass:
  `ExternalFileUtils.GetAllExternalFileReferences(doc)` → per reference,
  `GetLinkedFileStatus()` (Loaded / Unloaded / LocallyUnloaded / NotFound /
  InClosedWorkset), the path via `GetAbsolutePath()` rendered through
  `ModelPathUtils.ConvertModelPathToUserVisiblePath` (cloud paths included),
  and `PathType` (Absolute flagged as fragile); `RevitLinkType.AttachmentType`
  for the Attachment/Overlay column — attachment is also the only honest
  thing this tool says about nested links: the wall says they are
  unreachable, so it never pretends to enumerate them. Instances:
  `FilteredElementCollector(doc).OfClass(RevitLinkInstance)` rows carrying
  `Pinned`, `WorksetId`, `GetTotalTransform()`; a link type with zero
  instances is flagged "loaded, never placed". CAD:
  `CADLinkType` plus `ImportInstance.IsLinked` via the lib helpers separates
  true links from imports. Duplicates: instances of one type grouped, pairs
  whose transforms answer `AlmostEqual` flagged as exact duplicates. Workset
  hygiene (workshared docs only): one
  `WhereElementIsNotElementType()` histogram of `WorksetId` finds link
  instances sharing a workset with model elements — the "closing worksets
  never sheds it" bug. Model fixes, `TransactionGroup` "Link health fixes",
  assimilated, one nested `Transaction` per fix: `instance.Pinned = True`;
  workset move via `get_Parameter(ELEM_PARTITION_PARAM).Set(workset_id)`
  after a `WorksharingUtils.GetCheckoutStatus` probe — OwnedByOtherUser is a
  named skip carrying the owner from `GetWorksharingTooltipInfo`; duplicate
  deletion keeps one instance by the stated rule (pinned first, else lowest
  ElementId — the oldest) and `doc.Delete`s the rest. Load operations run
  strictly **after** the group closes — `RevitLinkType.Unload(None)`,
  `UnloadLocally` offered behind a `hasattr` probe where the API has it,
  reload through `link_reload.reload_link_element` — because
  Unload/Reload throw inside an open transaction; the executor enforces the
  order and the revit test pins it. No ExternalEvent, no Idling — modal
  window, sequential command.
- **The plan/apply cycle** — `build_plan` computes both groups from the
  audit rows: every model fix as an element-level step ("Pin ARCH-Central
  instance 402711", "Move MEP-Central to workset 'Z-Links'", "Delete
  duplicate instance 40288 — keeping 402711: pinned"), and every load op as
  a separate list with its warning text. The confirmation window shows the
  two groups under their own headers; the load-op group's acknowledgement
  tick — "I understand unload/reload happens outside the undo step" — gates
  only that group, so a user can apply model fixes alone without ever
  seeing the tick. Apply commits the group first, then runs load ops one by
  one with the footer narrating ("Reloading MEP-Central — this can take
  minutes on large models…"). The report reads back from the committed
  model: pin states and worksets re-read, duplicate counts recounted,
  statuses re-fetched — and it never claims a rolled-back fix.
- **Edge cases & honest limits** — Named buckets: "skipped — owned by
  {user}", "skipped — not a workshared document" (workset fixes greyed with
  this reason), "skipped — unloaded, nothing to pin", "import, not a link —
  listed; the API cannot convert it" (deleting-and-relinking needs the
  source file and a human), "nested links present (attachment) — not
  enumerable", "near-duplicate — transforms differ by {d} mm, not deleted"
  (only *exact* duplicates are ever offered for deletion; anything else is
  a flag for a human, because a 50 mm offset might be the design). The
  "loaded, never placed" deletion fix ships default-unticked. Per-category
  link visibility is stated out of scope in the tooltip — the wall says it
  is unreadable through 2026.
- **Risks** — The transaction boundary is the design's spine: load
  operations genuinely cannot join the TransactionGroup, and any future
  edit that lets one leak into the model-fix list breaks the one-undo
  promise — the state module's two-list separation plus a revit-test order
  pin are the guards, and the two-group UI must make the split impossible
  to misread. Duplicate deletion needs a watertight tiebreak or it becomes
  the bug it prevents: the rule is deterministic, printed in the plan row,
  and tested for ties (both pinned, both unpinned). Workset moves require
  an editable element and a workshared document — fail closed otherwise.
  Reload times are unbounded on big central models; the footer narration
  and per-link sequencing (never a frozen dialog) are the honest handling.
- **Tests** —
  - `test_link_health_state.py` pins flag derivation from fixed rows
    (absolute path, unpinned, shared workset, never placed, import vs
    link), exact-vs-near duplicate classification at the tolerance edge,
    the keep-tiebreak including ties, the structural two-group separation
    (a load op in the transaction plan is an assertion failure), and
    report counters zeroing on rollback.
  - `test_link_health_command_names.py` pins bundle.yaml metadata, XAML
    handler wiring across the three windows, 96×96 icon pairs, the
    IronPython AST scan, and a pin that `link_reload` is imported from
    `easybim`, never re-implemented.
  - `test_link_health_revit.py` drives the adapter against fakes shaped
    like both ExternalFileReference generations: status enum values
    including InClosedWorkset, a checkout refusal, `UnloadLocally` present
    and absent, transform pairs for the duplicate probe, and an
    order-asserting fake that throws if Unload is called while the
    transaction group is open.
  - `test_link_health_xlsx.py` pins the audit export — one row per
    link/import with every flag and reason surviving into Excel.

## UI description

**Main window** — resizable modal, root `Grid Margin="14"`, rows Auto/*/Auto.
Header: "Link Health" over the DimGray subtitle "Everything Manage Links
does not say." Body, two stacked cards. **Audit card**: a read-only grid,
one row per link or import — columns Name / Status / Path type / Attachment
/ Pinned / Workset / Instances / Flags — flag icons with the full reason in
the tooltip ("unpinned — free to be nudged"; "shares workset 'Interiors'
with 1,204 model elements"; "2 instances at the same position"); healthy
rows show a green check. **Fixes card**: checkboxes in two visibly separate
groups — "One undo step" (Pin 3 unpinned instances · Move 2 links to
workset [ComboBox of worksets] · Delete 1 exact-duplicate instance —
keeping the pinned one · Delete 1 never-placed link type) and "Load
operations (not undoable)" (Reload 1 out-of-date link · Unload 1 link),
the second group greyed until its acknowledgement checkbox is ticked.
Unavailable fixes grey with the reason in a tooltip, never vanish. Footer
status left: "6 findings, 2 fixes checked. Nothing written." Buttons right:
**Apply Fixes…** (primary, disabled with tooltip until a fix is checked),
**Export to Excel**, **Close**.

**Confirmation window** — the complete plan, element by element, in the same
two groups: each model fix one row with its target and reason, each load op
one row under the restated warning "these run after the undo step and
cannot be rolled back with it." Footer status: "4 model fixes in one undo
step; 1 load operation after it." Buttons: **Apply** (primary), **Back**.

**Report window** — Fixed / Skipped / Failed expanders for the model group,
then a separate "Load operations" section with per-link outcomes. A skipped
row reads "Skipped — owned by user jsmith"; a failed row carries Revit's
message. Footer status: "4 fixed, 1 skipped, 0 failed. 1 link reloaded
(outside the undo step). Statuses re-read from the model." Buttons:
**Export to Excel**, **Close**.

### User operation flow

1. Ribbon: Links → Link Health. The pass runs ("Reading 9 external
   references…") and the Main window opens with the audit grid — exportable
   as-is for the coordination meeting, touching nothing.
2. Tick fixes. Ticking anything in the load-operations group first requires
   its acknowledgement checkbox; until then the group is greyed with the
   tooltip "acknowledge that load operations are not undoable."
3. Press **Apply Fixes…** and read the element-level plan in the
   Confirmation window.
4. Press **Apply**. The model group commits first (per-fix rollback inside
   one undo step), then any load ops run one by one with the footer
   narrating each.
5. Cancel path: **Back** / **Close** before step 4 — nothing written.
   There is no mid-run cancel between the group and the load ops by design:
   the plan said exactly what both halves would do.
6. The Report window opens, re-read from the model. A skipped item looks
   like "Skipped — owned by user jsmith", listed under the model group; a
   load-op failure reads "Reload failed — {Revit's message}", clearly
   outside the undo step.
7. Close. One Ctrl+Z reverts every model fix; the report already said the
   reload stays.

## See also

- Existing EasyBIM: **Batch Link** (loads what this audits), **DWG
  Open/Reload** — and `lib/easybim/link_reload`, which this composes as its
  second consumer — plus the passive **Coordination Review** at file open,
  the natural future surface for a one-line "2 link findings" nudge.
- Plan siblings: **27 Site Check** — the geodesy half of the same Links
  ritual (this tool flags *unpinned*; whether the link actually drifted is
  Site Check's answer), and the shared link-row collector is the hoist
  moment. **14 Clash Sweep** — duplicate link instances double its
  findings; fixing them here first keeps that report honest. **08 Warnings
  Watch** — the same audit-then-trend instinct applied to warnings, and
  **24 Space Sync** — the Links-panel neighbour that depends on healthy
  links to read from.
