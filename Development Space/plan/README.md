# Extension Ideas — Ranked Plan

45 new pyRevit tool ideas for EasyBIM, brainstormed from the repository's own
design taste and ranked by usefulness and impact. Each idea owns one folder:

- `handoff.md` — the implementation brief: main purpose, basic implementation
  ideas (module layout, Revit API route, plan/apply cycle, edge cases, risks,
  tests), and the UI description with a user operation flow.
- `ui-mockup.md` — ASCII window wireframes and Mermaid operation diagrams
  (GitHub renders the Mermaid), generated as a visual companion; the handoff
  is authoritative.

`DESIGN-CONTEXT.md` in this folder is the shared context every handoff builds
on — design philosophy, architecture conventions, UI language, the existing
tool inventory, and the hard API walls. Read it before any handoff.

**How this list was made:** six readers analyzed the repo (AGENTS.md, README,
`lib/easybim`, tab scripts, XAML, tests); seven discipline-lens brainstormers
produced 60 raw ideas; a prior-art survey mapped what the free ecosystem
(pyRevit built-ins, DiRoots, EF-Tools, pyRevitMEP, common Dynamo packages)
already covers; the list was then curated — duplicates merged, saturated
territory cut, one gap the survey exposed added — and ranked. Rankings weigh
how often a production user reaches for the tool, time saved or errors
prevented per use, and how underserved the need is outside commercial add-ins.

## The ranking

23 of 45 ideas are MEP tools (marked ●).

| # | Idea | MEP | Effort | U·I | What it is |
|---|------|-----|--------|-----|------------|
| 01 | [Circuit Check](01-circuit-check/handoff.md) | ● | S | 9·8 | Read-only QA sweep of every circuit against its own numbers: overload %, rating vs frame vs panel main, poles vs voltage, junk load names |
| 02 | [Parameter Check](02-parameter-check/handoff.md) | | M | 9·8 | The zero-amp report generalized: rule-based data-completeness report for any parameters on any categories, with Show and Excel export |
| 03 | [Slope Check](03-slope-check/handoff.md) | ● | M | 8·9 | Audit whole gravity systems for flat runs, reversals, and per-diameter minimum fall — read-only, no tagging required |
| 04 | [Open Ends](04-open-ends/handoff.md) | ● | S | 9·7 | Model-wide sweep of unconnected MEP connectors across all domains, grouped, searchable, zoomable |
| 05 | [System Schedule](05-system-schedule/handoff.md) | ● | M | 9·7 | The Circuit Schedule dockable tree reborn for duct and pipe systems, with search and breadcrumb |
| 06 | [Load Names](06-load-names/handoff.md) | ● | M | 9·8 | Pattern-driven batch writer for circuit load names from rooms, levels, and categories — staged red before Apply |
| 07 | [Dim Overrides](07-dim-overrides/handoff.md) | | S | 8·9 | List every overridden dimension and separate the harmless retype from the printed lie |
| 08 | [Warnings Watch](08-warnings-watch/handoff.md) | | M | 9·7 | Dockable warnings browser with per-central-model history, trends, and creator attribution |
| 09 | [System Isolate](09-system-isolate/handoff.md) | ● | S | 9·7 | One pick → the whole run traced, isolated, and framed in a reusable named 3D view |
| 10 | [Families Purge](10-families-purge/handoff.md) | | M | 9·8 | A reasoned Purge Unused: per-type "why purgeable" reasons, search, per-item rollback, honest unknown-kept bucket |
| 11 | [Reference Check](11-reference-check/handoff.md) | | S | 8·8 | Find every section head and view reference that will print "?" — checked against the set actually being issued |
| 12 | [Legend Place](12-legend-place/handoff.md) | | M | 8·7 | Place and re-align named legends and schedules at the same spot across the whole sheet set |
| 13 | [Sleeve Place](13-sleeve-place/handoff.md) | ● | L | 8·9 | Batch sleeve/opening placement where services cross linked structure, sized from the service and its insulation |
| 14 | [Clash Sweep](14-clash-sweep/handoff.md) | ● | M | 8·8 | The missing day-one batch pass for Clash Detection Mode, plus a portable name-keyed clash status round-trip |
| 15 | [Connection Check](15-connection-check/handoff.md) | ● | L | 9·9 | The deep connector rule engine: mismatched mates, flow direction, size steps, and family authoring vs the systems instances sit on |
| 16 | [Panel Sheets](16-panel-sheets/handoff.md) | ● | M | 8·8 | Create the missing panel schedule views and flow them onto sheets in planned columns |
| 17 | [Where Used](17-where-used/handoff.md) | | L | 8·8 | Reverse dependency lookup before rename/unload/delete: what breaks, as a searchable tree |
| 18 | [Phase Balance](18-phase-balance/handoff.md) | ● | M | 7·8 | Survey A/B/C balance across all panels and rebalance via slot moves with per-swap rollback |
| 19 | [Circuit Renumber](19-circuit-renumber/handoff.md) | ● | M | 8·7 | Scheme-driven circuit renumbering — odd/even sides, walk order, grouped spares — commercial-only territory today |
| 20 | [Batch Insulation](20-batch-insulation/handoff.md) | ● | M | 8·8 | Spec-driven insulation rules (system × size → thickness) applied model-wide with a first-match plan |
| 21 | [Circuit Excel](21-circuit-excel/handoff.md) | ● | M | 8·8 | Excel round-trip straight off ElectricalSystems — ratings and load names edited by the engineer, no schedule needed |
| 22 | [Voltage Drop](22-voltage-drop/handoff.md) | ● | M | 8·8 | Per-circuit voltage drop computed from model length, load, and wire size, with a named not-calculable bucket |
| 23 | [Invert Stamp](23-invert-stamp/handoff.md) | ● | M | 8·8 | Write invert elevations re-based to the survey point onto pipes, fittings, and fixtures |
| 24 | [Space Sync](24-space-sync/handoff.md) | ● | L | 8·9 | Diff-and-sync MEP Spaces against the architect's linked Rooms, with explicit phases and orphan safety |
| 25 | [Families Reload](25-families-reload/handoff.md) | | M | 8·8 | Reload project families from the office library folder by name, fail-closed on ambiguity |
| 26 | [Family Audit](26-family-audit/handoff.md) | | M | 8·8 | Size, CAD content, nesting depth, and version for every loaded family — the 80 MB culprit found in minutes |
| 27 | [Site Check](27-site-check/handoff.md) | | M | 7·9 | Compare shared coordinates across all links at once and name the one that drifted |
| 28 | [Link Health](28-link-health/handoff.md) | | M | 8·7 | Pin state, worksets, duplicate instances, import-vs-link: one audit with fail-closed fixes |
| 29 | [Issue Register](29-issue-register/handoff.md) | | M | 7·7 | The sheets-by-revisions issue matrix as an Excel round trip — extends Revision Manager |
| 30 | [Open Routine](30-open-routine/handoff.md) | | M | 9·7 | Replay a per-model morning ritual: worksets, active workset, link reloads, working views |
| 31 | [Detail Renumber](31-detail-renumber/handoff.md) | | S | 8·7 | Renumber detail viewports by their position on the sheet, two-passing Revit's duplicate constraint |
| 32 | [View Sweep](32-view-sweep/handoff.md) | | M | 8·7 | Delete unused working views with reference-aware "kept because" reasons |
| 33 | [Penetration Schedule](33-penetration-schedule/handoff.md) | ● | M | 7·8 | The numbered opening schedule the structural engineer actually wants — paired, audited, exportable |
| 34 | [Spare Capacity](34-spare-capacity/handoff.md) | ● | S | 8·7 | Every panel's fill, demand, and open slots side by side — "is there room on LP-2?" in one window |
| 35 | [Power Sweep](35-power-sweep/handoff.md) | ● | S | 8·7 | Find every device that should be circuited and is not |
| 36 | [Air Balance](36-air-balance/handoff.md) | ● | L | 8·9 | Design CFM in from the load calc; space and system totals reconciled against equipment capacity |
| 37 | [Batch Runner](37-batch-runner/handoff.md) | | L | 7·9 | A friendly batch loop over many models, reusing the office's own print sets and exports |
| 38 | [Family Rename](38-family-rename/handoff.md) | | S | 8·7 | Convention-driven batch renaming with collision pre-check and a staged grid |
| 39 | [Filter Manager](39-filter-manager/handoff.md) | | M | 7·7 | The cross-template filter matrix: duplicates, orphans, and template drift |
| 40 | [Smoke Test](40-smoke-test/handoff.md) | | L | 5·9 | The in-Revit harness that drains the "Still to verify in Revit" backlog — outsized strategic value for this repo's own development loop |
| 41 | [Keynote Audit](41-keynote-audit/handoff.md) | | S | 7·7 | Check the model against the keynote table: broken keys, unused entries — the verification half of the Keynote panel |
| 42 | [Parameter Audit](42-parameter-audit/handoff.md) | | L | 7·9 | Find and repair same-name/different-GUID shared parameter clashes already in the model |
| 43 | [One-Line Draft](43-one-line-draft/handoff.md) | ● | L | 7·9 | Commit the electrical distribution skeleton to a drafting view the drawing set can carry |
| 44 | [Fixture Units](44-fixture-units/handoff.md) | ● | M | 7·8 | Localize where Revit's WSFU/DFU rollup breaks and check totals against the sizing chart |
| 45 | [Text Types](45-text-types/handoff.md) | | M | 7·7 | Count, audit, and remap rogue text types model-wide — the used rogues purge cannot touch |

## Curation notes

- **Merged:** idea 15 Connection Check absorbs two overlapping brainstorm
  results (physical joint rules + family connector authoring vs systems); the
  plain disconnect sweep stays separate as idea 04 Open Ends because it is an
  S-effort daily tool while 15 is the deep L-effort rule engine. Both share
  one connector-graph walker — whichever builds second hoists it to
  `lib/easybim`.
- **Added by curation:** idea 19 Circuit Renumber fills a gap the prior-art
  survey named explicitly (configurable renumbering is commercial-only). It
  shares its slot-move core with idea 18 Phase Balance.
- **Cut:** 16 raw ideas fell below the line — mostly saturated territory
  (batch exporters, generic find-replace renaming), fabrication-adjacent scope
  (trapeze hangers), or niche-over-effort losers (elevation band agreements,
  command sequencing, session journaling, sheet snapshots, DWG hygiene,
  workset bulk-assign, ownership maps, ribbon profiles, ceiling re-host,
  system rename, flex duct lengths, shared-parameter-file tidy).
- Effort: S = days, M = 1–2 weeks, L = multi-week. U·I = usefulness · impact,
  each out of 10.
