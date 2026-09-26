# e-transmit 2.1.9 Live ACC, Relative Repath, and Progress UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make e-transmit use only native Revit-opened ACC live models, keep ordinary file-based repathing closed-file and relative, combine unavoidable ACC conversion with final verification in one host open, and make the existing pyRevit progress strip readable without covering the Revit title.

**Architecture:** The selected open Revit document remains the authority for live ACC identity; published APS models are no longer selectable in the e-transmit UI. File-based local/network/Desktop Connector references continue to be copied into the existing `Links` hierarchy and repathed by `TransmissionData` using relative paths without opening the host. True ACC External Resource references are converted to packaged local references in one final open/save/verify pass of the copied host. The existing pyRevit `ProgressBar` is retained but offset below Revit's title area and given phase-aware text/color.

**Tech Stack:** pyRevit / IronPython 2.7, Autodesk Revit API 2023+, WPF/pyRevit forms, Python unittest, existing EasyBIM e-transmit engine/session/revit backends.

**Spec:** Approved conversation decisions after e-transmit 2.1.8 commit `ff0a1ef676aa655250b7606b16f355dbff391a89`; this plan is the durable specification for those decisions.

## Global Constraints

- ACC live models must be opened by the user through native Revit Home → Autodesk Docs; EasyBIM must not offer a published-model browser as a substitute for the live cloud-workshared state.
- Do not claim the open ACC document is newer than what Revit currently has loaded; unsaved in-memory edits remain excluded under the existing saved-state-only contract.
- Retain `Host.rvt` + `Links/<category>/...`; do not flatten all dependencies beside the host.
- Preserve original filenames. Different sources with the same basename must use collision subfolders, never filename mutation.
- Local/network/file-based Revit links must use relative package paths and must not require opening the host for repath.
- True ACC External Resource links may require one copied-host open; combine local conversion, save, and verification in that same final open. Do not reopen that host afterward.
- Closed saved hosts may still use the existing explicit deep-inspection path when metadata cannot expose requested non-file resources; already-open hosts must not be reopened merely for discovery.
- Keep the existing pyRevit progress UI; do not add another floating progress window.
- Progress strip must not cover the Revit document/application title. Prefer an offset strip below the title over transparency.
- Phase colors: blue Collecting/Repathing, purple Finalizing ACC Links/Verifying Final Package, green Ready, amber Ready—Review Issues, red Incomplete, gray Cancelled.
- No source model, ACC cache, or working Revit document may be moved, saved, synchronized, published, or otherwise modified by e-transmit.

## Review Focus

- Open ACC host with a true external-resource Revit link: exactly one copied-host open should perform conversion + save + verification, with no second verification open.
- Open local host with only file-based links: zero processing opens before the single optional final verification open; repath is relative through `TransmissionData`.
- Same-name different RVTs: both keep their original filename and resolve through distinct relative subfolders.
- Legacy/published ACC mode injected programmatically: UI path must refuse it rather than silently transmitting a published version.
- Progress placement when Revit is maximized / scaled: one initial positioning read, stable offset below title, filename stays readable while background document hooks run.

---

### Task 1: Enforce Native-Open ACC Source Policy

**Files:**
- Modify: `EasyBIM.tab/Links.panel/e-transmit.pushbutton/window.xaml`
- Modify: `lib/easybim_etransmit/ui.py`
- Create: `Development Space/tests/etransmit/test_219_live_acc_ui.py`

**Interfaces:**
- Consumes: existing `Choice.Mode`, open `Document.IsModelInCloud`, existing saved-file/local browsing.
- Produces: UI with no published ACC picker/sign-in path; `validate_source_modes(models)` rejects `PUBLISHED_VERSION`; native ACC guidance text.

- [ ] **Step 1: Write failing UI contract tests**
  - Assert XAML has no `Add ACC published model...`, `Associate ACC download...`, or APS sign-in button.
  - Assert XAML tells users to open live ACC models through Revit Home / Autodesk Docs.
  - Assert exact prefix mappings and local saved-file browsing remain.
  - Assert `validate_source_modes()` rejects `PUBLISHED_VERSION` and accepts `LIVE_DOCUMENT`/`SAVED_FILE`.

- [ ] **Step 2: Run the new test and verify RED**

Run: `python -m unittest "Development Space/tests/etransmit/test_219_live_acc_ui.py" -v`
Expected: FAIL because published ACC controls/mode are still accepted.

- [ ] **Step 3: Implement source-policy changes**
  - Remove APS/published-model UI controls and their click handlers/imports from `ui.py`.
  - Keep mapping controls, renamed to an exact source-mapping section.
  - Add `validate_source_modes(models)` pure helper and call it before transmission.
  - For open cloud rows, display source mode text that makes native Revit-opened live ACC state explicit without claiming latest central state.

- [ ] **Step 4: Run focused UI tests GREEN**

Run: `python -m unittest "Development Space/tests/etransmit/test_219_live_acc_ui.py" "Development Space/tests/etransmit/test_button.py" -v`
Expected: PASS.

### Task 2: Preserve Relative Closed-File Repath and Same-Name Safety

**Files:**
- Modify: `lib/easybim_etransmit/revit.py`
- Modify: `lib/easybim_etransmit/engine.py` only if required by failing tests
- Create: `Development Space/tests/etransmit/test_219_relative_repath.py`

**Interfaces:**
- Consumes: `Backend.apply_metadata(path, target, rows, relative=True)`, layout-planned dependency targets.
- Produces: ordinary file Revit links repathed to package-relative paths without `open_copy`; collision-subfolder paths remain relative and filenames unchanged.

- [ ] **Step 1: Write failing/guard regression tests**
  - File-based local/network Revit links use `PathType.Relative` and expected `Links/Revit/...` relative targets.
  - Two different `Architecture.rvt` sources remain named `Architecture.rvt` in different subfolders and both paths are written distinctly.
  - Metadata-only repath does not call `open_copy` or `SaveAs`.

- [ ] **Step 2: Run focused tests RED or prove current behavior**

Run: `python -m unittest "Development Space/tests/etransmit/test_219_relative_repath.py" -v`
Expected: At least one new assertion fails if current metadata path handling is incomplete; if all pass, record that 2.1.8 already satisfies this approved requirement and do not add redundant production code.

- [ ] **Step 3: Implement only the minimal missing behavior**
  - Keep `Links` hierarchy and collision subfolders.
  - Do not introduce all-files-beside-host layout or renaming.

- [ ] **Step 4: Run focused tests GREEN**

### Task 3: Single Final Open for True ACC Conversion + Verification

**Files:**
- Modify: `lib/easybim_etransmit/revit.py`
- Modify: `lib/easybim_etransmit/engine.py`
- Create: `Development Space/tests/etransmit/test_219_acc_finalization.py`

**Interfaces:**
- Consumes: `Backend.finish(stage, target, rows, options)` and `Backend.verify_package(target, rows, options)`.
- Produces: backward-compatible processing result `{'issues': [...], 'verified_in_process': bool}` normalized by engine; true ACC external-resource finalization verifies the same open document after save; engine skips duplicate final reopen when `verified_in_process=True`.

- [ ] **Step 1: Write failing engine/backend tests**
  - Simulated true ACC row causes one `open_copy`, one save, one close, and zero later `verify_package` opens.
  - Local metadata-only host still performs closed-file repath then one final verification open.
  - Failed ACC conversion does not claim verification and does not skip normal failure reporting.
  - Verification checks local packaged target and requested load state after conversion.

- [ ] **Step 2: Run tests RED**

Run: `python -m unittest "Development Space/tests/etransmit/test_219_acc_finalization.py" -v`
Expected: FAIL because 2.1.8 closes after ACC processing and reopens for final verification.

- [ ] **Step 3: Implement processing result normalization in engine**
  - Accept legacy list returns from test backends as `issues` + `verified_in_process=False`.
  - Accept dict return from Revit backend.
  - Preserve all existing rollback/cancellation semantics.

- [ ] **Step 4: Verify ACC links before closing the processing document**
  - After conversion/save, reuse the already-open copied host to check immediate Revit link target/load state.
  - Return `verified_in_process=True` only when those checks complete with no error.
  - Do not create a second open just for verification.

- [ ] **Step 5: Run focused tests GREEN**

### Task 4: Revit-Integrated Phase-Aware Progress Strip

**Files:**
- Modify: `lib/easybim_etransmit/ui.py`
- Modify: `lib/easybim_etransmit/engine.py` only to emit structured phase labels if needed
- Modify: `Development Space/tests/etransmit/test_runtime_progress.py`
- Create: `Development Space/tests/etransmit/test_219_progress_ui.py`

**Interfaces:**
- Consumes: existing `pulse(label,current,total)` callback and `TransferProgressBar`.
- Produces: `progress_phase(label)` -> phase/color/display text; `TransferProgressBar.set_phase(...)`; one-time position offset below Revit title while retaining cancel support.

- [ ] **Step 1: Write failing progress tests**
  - Collect/copy maps to blue, repath to blue, ACC finalization and verification to purple, terminal states to green/amber/red/gray.
  - Filename remains in progress text and is not substituted into the Revit window title area.
  - `TransferProgressBar.update_window()` reads host geometry once and adds a stable vertical offset below the Revit title area.
  - Existing background-document-hook regression still reports one positioning read.

- [ ] **Step 2: Run tests RED**

- [ ] **Step 3: Implement phase mapper and offset positioning**
  - Keep pyRevit's native `ProgressBar`; no second WPF progress window.
  - Use a robust brush setter only when the active pyRevit progress control exposes the relevant WPF property; phase text must remain correct even if color application is unavailable in an older pyRevit build.
  - Do not make transparency a dependency.

- [ ] **Step 4: Update pulse labels**
  - Explicitly emit `PACKAGE BUILT — VERIFYING FINAL PACKAGE` before final host verification.
  - Explicitly emit `FINALIZING ACC LINKS` during true ACC local conversion.

- [ ] **Step 5: Run focused progress tests GREEN**

### Task 5: Version, Documentation, Full Regression, and Release

**Files:**
- Modify: `lib/easybim_etransmit/__init__.py` -> `VERSION = '2.1.9'`
- Create: `Development Space/docs/e-transmit-2.1.9.md`
- Modify: `Development Space/docs/e-transmit.md`
- Modify: `Development Space/docs/e-transmit-desktop-checklist.md`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: documented 2.1.9 behavior and workstation acceptance checklist.

- [ ] **Step 1: Document approved scope and rejected paths**
  - Native Revit-opened ACC only for live cloud state.
  - Published APS picker removed from normal e-transmit UI.
  - Relative `Links` repath, same-name collision subfolders, single-open ACC conversion/verification, final verification status UI.
  - Explicitly state that actual latest-central status depends on what Revit currently has loaded / Reload Latest; e-transmit does not silently publish/sync.

- [ ] **Step 2: Run full portable suite**

Run: `python -m unittest discover -s "Development Space/tests/etransmit" -v`
Expected: all tests pass, platform skips only.

- [ ] **Step 3: Run syntax/diff checks and targeted scripts**

Run: `python "Development Space/tests/etransmit/test_219_live_acc_ui.py" && python "Development Space/tests/etransmit/test_219_relative_repath.py" && python "Development Space/tests/etransmit/test_219_acc_finalization.py" && python "Development Space/tests/etransmit/test_219_progress_ui.py"`
Expected: all PASS.

- [ ] **Step 4: Commit implementation and push branch**

- [ ] **Step 5: Require GitHub Actions Linux, Windows, and IronPython jobs GREEN before merging to `main`**
