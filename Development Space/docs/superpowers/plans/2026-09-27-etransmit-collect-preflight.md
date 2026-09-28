# e-transmit 2.1.11 Collection and Explicit Preflight Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to execute this plan task by task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make collection avoid unnecessary host openings, replace the global unloaded-link switch with explicit per-link reload selection, and ask before saving modified source hosts.

**Architecture:** Keep the existing saved-file/cache acquisition, relative Links layout, and dockable progress pane. Separate read-only inventory/acquisition from explicitly authorized source Save or temporary link Reload, and from package-only repath/cleanup/upgrade. Centralize the opening policy so stale settings cannot re-enable discovery opens. Use a small preflight module for consent and reload restoration rather than a new worker-process or Save As architecture.

**Tech Stack:** pyRevit, IronPython 2.7-compatible Python, Revit Document/RevitLinkType APIs, WPF, unittest, GitHub Actions (Linux, Windows, IronPython).

**Spec:** The user's approved conversation through September 27, 2026, especially: Repath unchecked means no host reopening UNLESS cleanup or upgrade is explicitly enabled; remove Load Unloaded Files and Deep Inspection; use a per-link reload popup; offer Continue without saving / Save and continue / Cancel for modified selected hosts; do not introduce the rejected Save As workflow.

**Baseline:** main d3fd5c809f960f088520e5e2e6173df3a017eaeb, version 2.1.10. The verified source baseline has 415 portable tests (4 platform skips on Linux).

## Global Constraints

- Repath off + cleanup off + upgrade off: no host Document opening for discovery, normalization, repath or verification. Hashing/byte-copy checks remain.
- Cleanup OR upgrade explicitly enabled permits opening only package copies for those operations even with Repath off. It must not turn repathing on.
- Already-open hosts provide available inventory without a second discovery opening, regardless of IsModified or loaded/cache revision mismatch. Report inventory provenance separately from saved-byte provenance.
- Closed collect-only selections use saved metadata and clearly report limited dependency coverage. No automatic opening fallback.
- Retain native Revit-opened ACC source policy. No published-version picker/download substitution.
- Source hosts may receive only an explicitly selected normal in-place Save, or explicitly selected temporary link reload/restoration. No source SaveAs, Sync, Publish, close, activation switch, or cache modification.
- Preserve direct-link scope, original filenames, collision subfolders, support folders, and existing relative Links layout. Do not inspect nested linked models.
- Remove Deep Inspection and Load Unloaded Files from UI and remembered settings. Ignore historical values rather than silently honoring them.
- Unselected unloaded links remain eligible for copying when an identified saved source is available.
- Temporary reload is acquisition consent, not consent to leave a link loaded in either source or export.
- Snapshot host before temporary reload. Save consent happens before snapshot. Restore each changed source load state in finally; no save after temporary reload.
- Distinguish locally-unloaded user overrides from global unloading. Do not broaden a local unload into a global unload. Warn that reload/unload can clear Undo history; restoring load state cannot restore Undo.
- Keep existing output processing architecture; do not add automatic new-central/Save As normalization or a separate Revit worker system.
- Do not imply existing ACC conversion limitations are resolved merely by this release. Report failed or unverified repathing honestly.

## Review Focus

1. Modified cloud host with a differing saved revision and old deep=true settings must collect without opening when all processing options are off (Task 1).
2. Cleanup-only and upgrade-only must open the package copy without any metadata/link repath, and never save the source host (Tasks 1 and 4).
3. Unloaded cloud link with empty display path must use identity-matched type metadata, not a same-name guess; unchecked still means eligible for collection (Tasks 2 and 3).
4. Cancellation or failure during the second reload must restore all attempted source links, including a partially successful reload; failed restoration must be surfaced (Task 3).
5. Save cancellation/failure, unsaved-new documents, local-only unload overrides, and repeated batch invocations must not reuse consent or contaminate the frozen host snapshot (Tasks 2 and 3).

## Task 1: Opening policy and read-only inventory

**Files:** modify lib/easybim_etransmit/files.py, session.py, revit.py, engine.py; create Development Space/tests/etransmit/test_221_collection_policy.py.

**Interfaces:** add files.host_processing_requested(options) -> bool using only repath, cleanup, upgrade. SessionBackend.inventory_before_copy(source, options) returns a copy of the available open-host inventory without opening; SessionBackend.scan must obey the same policy. Existing finish/verification interfaces remain compatible.

- [ ] Write regression tests forbidding open_copy and Backend.scan for modified/mismatched open cloud hosts and collect-only closed files; assert native file bytes unchanged and all available direct references retained.
- [ ] Write separate cleanup-only and upgrade-only tests asserting document processing is permitted while repath remains false and no opening verification is added.
- [ ] Run focused tests and record the expected failures before production changes.
- [ ] Remove the revision-based discovery-opening requirement for open hosts. Bind available cloud references to saved cache identities without opening; preserve useful warnings with accurate wording.
- [ ] Gate closed-file inspection and any package normalization by explicit processing options. Prevent stale deep/load_unloaded_files preferences from overriding the new behavior.
- [ ] Run focused tests, then commit this independently testable change.

## Task 2: Source-save consent and unloaded type discovery

**Files:** create lib/easybim_etransmit/preflight.py; modify session.py and revit.py; create Development Space/tests/etransmit/test_221_preflight.py.

**Interfaces:** preflight.prepare_sources(choices, ask_save, cancelled=None) -> consent records; preflight.unloaded_links(registry, host_keys) -> link choice records containing owner/type identity, name, original load state and local-unload scope. Registry exposes refresh of available inventory and safe registration of an identified direct link after reload.

- [ ] Write tests for Continue without saving, Save and continue, Cancel, save failure, no prior saved path, linked/non-selected documents, and no SaveAs/Sync/Publish/Close calls.
- [ ] Write tests for unloaded type discovery with no loaded instance Document, duplicate instances, nested types, and empty external-resource display paths with a valid type name.
- [ ] Run RED tests, then implement explicit in-place Save only. Refuse unsupported normal saves without substituting SaveAs or synchronization; refresh inventory after a successful save.
- [ ] Enumerate direct RevitLinkType metadata and use robust Element.Name access plus captured external-resource identity. Do not infer identity from basename.
- [ ] Preserve original load intent separately from current effective loaded state. Return useful popup details without opening the host.
- [ ] Run GREEN tests and commit.

## Task 3: Selected temporary reload, snapshot isolation and restoration

**Files:** extend preflight.py/session.py; create lib/easybim_etransmit/unloaded_links.xaml; extend test_221_preflight.py and add integration coverage.

**Interfaces:** preflight.reload_selected(registry, selected_links, pulse=None, cancelled=None) returns diagnostics. It freezes the affected primary host snapshots before any reload; captures acquired link identities/bytes while available; always restores changed links before package processing. UI selection is explicit, unchecked by default, scoped to the current run.

- [ ] Write tests for snapshot-before-reload ordering; copy-without-reload of available unchecked links; successful reload; rejected/failed reload; cancel before and during reload; restoration errors; local-user override restoration; unchanged source paths.
- [ ] Implement per-link Reload only for selected direct types. For a local-unload override, use the documented override APIs and restore that same scope. Do not open closed worksets or reload other links implicitly.
- [ ] Capture usable saved link data before restoration, without replacing the frozen host snapshot. Update only the selected link's acquisition metadata while preserving its original package load intent.
- [ ] In finally restore any attempted state change without consulting cancellation, verify restoration, and surface errors. Never save the working host after reloading.
- [ ] Build a WPF checkbox popup with columns for host/link, original state and saved-source status; actions Reload selected and continue / Continue without reloading / Cancel. Explain Undo-history side effects.
- [ ] Run focused tests and commit.

## Task 4: UI, preferences, and reporting

**Files:** modify ui.py, window.xaml, engine.py and relevant existing UI tests; add test_221_ui.py.

**Interfaces:** run() performs selected-source save consent before Registry creation, then read-only inventory, unloaded-link selection, selected acquisition/restoration, and existing batch execution. No popup response or source-save consent is persisted.

- [ ] Write tests asserting DeepScan and LoadUnloadedFiles controls are absent, Repath remains, cleanup/upgrade are not disabled by Repath, and old settings do not trigger loading or inspection.
- [ ] Remove obsolete controls/bindings/settings. Update disclosure text to distinguish explicit Save consent from automatic source changes.
- [ ] Wire both preflight prompts and cancellation without creating a package on preflight cancellation. Keep dockable progress behavior and readable operation labels.
- [ ] Report collect-only mode as files collected with original references retained, not a portable/repathed package. Keep verification statuses accurate and record necessary-opening purpose and preflight diagnostics.
- [ ] Repath preserves originally unloaded links; ignore obsolete load_unloaded_files=true inputs, including historical saved preferences.
- [ ] Run UI and integration tests, updating only assertions whose old behavior is deliberately superseded, and commit.

## Task 5: Release verification and delivery

**Files:** version __init__.py -> 2.1.11; create Development Space/docs/e-transmit-2.1.11.md; update main documentation, workstation checklist and this plan's completion record.

- [ ] Run python -m unittest discover -s "Development Space/tests/etransmit" -v; syntax compilation, XAML parse, git diff --check, and targeted source-mutation audit.
- [ ] Review the entire change for scope, source safety, stale settings, restoration and clear partial-failure reporting. Preserve known ACC multiple-document conversion restriction unless independently fixed/tested.
- [ ] Provide the plan as a downloadable Markdown artifact and preserve it in the repository.
- [ ] Push changes through the GitHub connector; run Linux, Windows and IronPython CI and merge to main only after successful checks. Verify final main SHA and CI.
- [ ] Report exact validation completed; distinguish portable/API-shaped tests from real Revit workstation behavior and do not claim measured speed gains without another workstation run.

## Workstation acceptance

- Open the same ACC test model. With Repath, cleanup and upgrade off, verify timings contain no extra host revit_open/revit_save calls and copied host bytes equal the frozen saved source.
- Repeat with cleanup only and upgrade only: a copied-host opening is permitted, original host stays open at its original path, references are not intentionally repathed.
- Test modified host Continue/Save/Cancel, including a failed native save. No Sync/Publish or source-window switch.
- Test an unloaded local link with existing bytes (no reload needed), an unloaded ACC link selected for reload, Continue without reloading, and cancellation. Source and exported saved load intent must not be accidentally changed.
- With Repath enabled, verify actual packaged paths and load intent. Existing provider restrictions must be reported as failures, never as verified success.
