# e-transmit 2.1.11 Collection Preflight Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Track work with the checkboxes below.

**Goal:** Remove unnecessary discovery openings, replace the global unloaded-link option with explicit temporary reload selection, and make source-save and recipient-opening requirements clear.

**Architecture:** Keep saved-file/cache acquisition and the current docked progress UI. Use the already-open Document for inventory without requiring a cache revision match. Closed-file discovery is metadata-only unless Repath, Cleanup or Upgrade explicitly permits document processing. Source saving and temporary reloading are isolated in a preflight module with explicit consent and restoration.

**Tech Stack:** pyRevit / IronPython 2.7; Revit 2023+ API; WPF; unittest; GitHub connector.

**Spec:** User-approved conversation through “unchecked = no host reopening unless cleanup and upgrade options”; resumed with the report that copied models require Detach from Central. Base: `d3fd5c809f960f088520e5e2e6173df3a017eaeb` (2.1.10).

## Global Constraints

- Repath unchecked + Cleanup unchecked + Upgrade unchecked = no additional host opens, including discovery and opening verification.
- Cleanup or Upgrade explicitly permits required copied-host opens even when Repath is unchecked; it does NOT authorize changing reference paths.
- Repath remains the existing checkbox; local/network supported references retain closed-file relative repath.
- Remove Deep Inspection and Load Unloaded Files from the dialog and remembered settings. Ignore stale load-unloaded preferences.
- Already-open hosts supply available inventory even when modified or cache revision differs. Report the inventory basis honestly; copied bytes remain saved-state only.
- Closed collect-only sources use available metadata and disclose unsupported discovery, without hidden opening.
- Explicit unsaved-state choices: Continue without saving, Save and continue, Cancel. Save means normal in-place Save only; never SaveAs, Sync or Publish.
- Snapshot selected hosts before temporary source link reloads. Copy available unloaded files without requiring reload.
- Show a per-link reload popup. Only checked top-level Revit links may be reloaded. Unchecked links remain included where their saved files can be identified.
- Restore initially unloaded links after acquisition, on success, cancellation and failure. Preserve per-user unload overrides. Warn that Revit reload can clear Undo history and restoration cannot restore it.
- Keep original package filenames and collision folders, no nested dependency expansion, no published-model fallback.
- No new detached/new-central export mode or automatic source relocation. Recipient guidance must explain that a raw workshared/cache copy retains its original central association.

## Review Focus

1. Modified ACC host with mismatched revision and Repath off must collect live-exposed PDFs/cloud links without any host opening.
2. Cleanup/Upgrade with Repath off must remain usable and not change link paths or trigger link verification.
3. Unloaded cloud link with a blank display path must retain its actual RevitLinkType name and GUIDs, not be dropped or matched by basename.
4. Partial reload failure/cancellation must still attempt restoring all touched links and must surface restoration failures rather than silently continue.
5. Source-save consent, snapshot ordering, local-unload overrides and old settings must not cause unexpected SaveAs/Sync/Publish or saved load-state changes.

## Task 1 — Discovery and processing permission

**Files:** `session.py`, `revit.py`, `files.py`, `engine.py`; `test_221_collection_policy.py`.

- [x] Add failing tests for live ACC inventory with modified/mismatched revisions; closed collect-only ignores legacy deep=True; cleanup/upgrade exceptions; stale load-unloaded=True preserves original state.
- [x] Add a single host-processing permission helper based on Repath/Cleanup/Upgrade. Use it at closed discovery and processing boundaries.
- [x] Reuse live inventory for cloud as well as local hosts; preserve existing saved local RVT metadata enrichment, and report live/saved basis. Bind identified unloaded cloud links without requiring an open child.
- [x] Remove revision-driven discovery opens; keep file identity/cache integrity validation and never silently claim exact revision correspondence.
- [x] Ensure cleanup-only processing is not blocked just because an RVT is unresolved, since it is not repathing. Do not repath or verify links when Repath is off.
- [x] Run focused tests and full portable suite; update old expectations only when superseded by this specification.

## Task 2 — Explicit preflight saving and selected temporary reload

**Files:** new `preflight.py`, `session.py`, `revit.py`; `test_221_preflight.py`.

**Interfaces:** `save_selected(choices, decision) -> event list`; `unloaded_links(registry, keys) -> choices`; `TemporaryReloads(registry, selected, pulse)` context manager with `acquire()` and restoration diagnostics. No implicit UI access in pure policies.

- [x] Write tests for no-save/cancel, in-place save, failure without fallback, original model path identity unchanged, no duplicate save of same doc.
- [x] Enumerate actual top-level RevitLinkTypes and retain names/status/identity regardless of loaded child availability.
- [x] Write tests for host snapshot-before-reload, selected-only acquisition, unselected cached inclusion, restoration after partial failures/cancel, and local-unload override preservation.
- [x] Implement reload via documented Revit APIs, outside transactions, with no shared-coordinate save. Snapshot/acquire a loaded child before restoring the original state.
- [x] Preserve pre-reload inventory states and expose save/reload outcomes in package source context; do not let temporary IsModified changes recapture the host.
- [x] Run focused tests and suite.

## Task 3 — Dialog integration and migration

**Files:** `ui.py`, `window.xaml`, new `unloaded_links.xaml`; UI tests.

- [x] Write failing checks removing obsolete controls, ignoring stale persisted settings, and retaining independent Repath/Cleanup/Upgrade options.
- [x] Prompt modified selected source docs before inventory capture. Provide all three decisions, no default saving.
- [x] Show an unloaded-link checklist only when there are unloaded direct links; include source/state/detail and explicit Undo warning. Cancel performs no source reload.
- [x] Run the temporary acquisition context before the normal batch; restoration completes before finalization. Report failures, but allow independently collectable files to proceed.
- [x] Keep the dockable progress pane, current colors, cancellation and timing telemetry.
- [x] Run UI/integration tests including real filesystem fake-Revit batch.

## Task 4 — Detach diagnosis, reports, release

**Files:** `engine.py`, release/checklist docs, version; report tests.

- [x] Add recipient opening guidance to START_HERE/REPORT and machine-readable metadata for unchanged workshared/cloud copies. Distinguish detached API verification from normal UI open.
- [x] State that copying RVT bytes does not sever central association and Detach does not repath links. Do not report all raw files as already detached or corrupt.
- [x] Bump version to 2.1.11; document exact options and known ACC same-session conversion limitation (not fixed by this bounded release).
- [x] Run full suite, syntax checks, XML parsing and diff review. Record actual test counts and Revit runtime limitations.
- [ ] Commit plan + implementation through GitHub connector; run Linux/Windows/IronPython CI and merge to main when green. Confirm actual main SHA and avoid claiming workstation validation.

## Validation commands

`python -m unittest discover -s "Development Space/tests/etransmit" -v`

Focused tests: `test_221_collection_policy.py`, `test_221_preflight.py`; existing API-shaped and UI regression suite. Revit workstation acceptance: collect-only open ACC + unloaded link; reload-selected then confirm both original and package intended unloaded state; Cleanup/Upgrade without Repath; recipient Detach opening.

## Execution record

- Baseline: 415 tests, 4 platform skips, Linux Python; all passed.
- Latest detach observation: examine original journal bytes because Files parsed journals as invalid UTF-16. Do not infer corruption. Earlier exports retained/restored original cloud-cache host bytes.

- Local implementation: 438 tests passed (4 platform skips). New regression cases were observed failing before production changes. Python/WPF runtime remains a workstation acceptance requirement.
- Review: preserve local unload overrides; stop on state changes after selection; report unknown central association rather than infer detachment from changed bytes.
