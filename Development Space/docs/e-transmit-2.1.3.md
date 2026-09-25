# e-transmit 2.1.3 — saved ACC links and one delivered host

## Report-driven changes

The September 25 Morrison run found its architectural model under the configured
CollaborationCache project's `LinkedModels` folder. The loaded edition and saved
cache had save count 70 but different revision GUIDs; 2.1.2 rejected the copy.
2.1.3 prefers an exact revision match and otherwise permits one unambiguous saved
cache edition of the same model, explicitly reporting the difference.

Anthropology's saved host exposed an unloaded link with no display/file path but
valid project/model GUIDs and region. It now registers a cache source from that
identity without requiring a loaded Document. The Revit link type supplies its
filename. Whether that workstation actually holds a usable cache still requires
a desktop export. The separate blank keynote configuration is recorded as
UNCONFIGURED; configured missing files remain errors.

## Acquisition and processing

- Native format, Revit version, stable source bytes, checksums and identity remain
  required. Conflicting candidates are rejected; timestamps are not revision order.
- `CACHED_CLOUD_REFERENCE` sources carry cloud identity without a live Document.
  Expected loaded revision may be absent; the actual saved revision is required.
- `SAVED_CACHE_DIFFERS_FROM_LOADED` records both revisions. `SAVED_CACHE_NO_LOADED_REVISION`
  explicitly identifies saved-only acquisition. Neither claims the latest ACC model.
- A different saved edition supplies its own dependency and plugin inventory.
  Nested cloud references use the same identity-based acquisition recursively.
- Exported cache copies are normalized through a detached open and independent
  SaveAs when processing is enabled. Cloud references use a local
  ExternalResourceReference and LoadFrom on existing link types. Relative paths
  are stored after saving; no replacement link instances are created.
- No Save, SaveAs, Sync, Publish, Reload or Close is called on working documents
  in the saved-state UI workflow. Unsaved edits remain excluded. Cache sources
  are read only, and no published download is substituted for an open source.

## Interface and reports

**Load Unloaded Files** defaults ON and applies to packaged Revit links. OFF
preserves recorded states. It is disabled when Repath is off and persists through
Save Settings. `load_unloaded_files` is the corresponding option; reference rows
retain `loaded`/`original_loaded` and separately record `package_loaded`.

No `_HostState` folder is created or zipped. A host's acquisition checksum remains
in its file record. Temporary rollback copies are outside the package and are
removed after processing; missing RVTs or failed processing leave the collected
host intact. If filesystem access prevents rollback, retain the untouched
temporary original and report its recovery path instead of deleting it or
claiming the damaged output is verified. Legacy programmatic live-SaveAs recovery files remain outside the
package and are not deleted because they can still be working documents.

The manifest `counts` and text reports include `revit_links_requested`,
`revit_links_copied` and `revit_links_verified`. These count reference elements
per owner, not duplicate API representations or file totals. Link diagnostics
include element ID, cloud identity, expected revision, cache roots/candidates
and rejection reasons. CSV reports include original/requested load states.
Copied files, successfully reopened RVTs and verified references remain separate.

## Validation and limits

Automated regressions cover both reported failures, exact-match preference,
ambiguous/missing/invalid caches, changed source bytes, nested cycles, saved
dependency inventories, empty/configured keynotes, cloud-to-local overload use,
load-state checking, checkbox persistence, cancellation rollback and ZIP contents.
See `e-transmit-validation.txt` for the executed runtime checks.

No Revit installation or the company's ACC cache is available on this development
machine. Real Revit 2024 opening, moved-package portability, linked tags and
annotations remain desktop acceptance checks. Filesystem/API-shaped tests cannot
certify those outcomes. CAD-internal dependencies, external RCP scans, inaccessible
plugin schemas and missing template-report directories remain reported boundaries.

## Autodesk references

- [Copy cached models elsewhere, detach and save independently](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-retrieve-previously-synced-or-saved-Revit-models-after-losing-access-to-the-ACC-or-BIM-360-project.html)
- [CreateLocalResource](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/457745f0-5346-77ed-444b-554295ebb14b.htm)
- [DocumentVersion: GUID and save count identify an edition](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/3574fa56-016e-b146-1499-b3b1c9129705.htm)
