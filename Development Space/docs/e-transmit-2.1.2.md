# e-transmit 2.1.2 — verified saved-cache copies, without saving the working model

Historical release: see [2.1.3](e-transmit-2.1.3.md) for the current saved-link
revision policy, unloaded-link acquisition and removal of packaged `_HostState` copies.

## What changed

Open-model rows now default to **saved local/cache state**, including locally
cached cloud-linked models already loaded in the selected host. Unsaved edits
are excluded by explicit user request. The ordinary UI does not invoke Save,
SaveAs, Synchronize with Central, Publish, Reload, or Close on any working host
or linked document, and does not relocate the active working document.

This supersedes the 2.1.0 active-host SaveAs workflow and the unpublished 2.1.1
SaveAs patch. The former SaveAs implementation is retained only as an explicit
programmatic opt-in for compatibility tests; the UI does not authorize or call it.

## Acquisition rules

1. Read each open cloud document's project/model GUIDs and DocumentVersion
   (version GUID and save count). Names and timestamps are not revision identities.
2. Discover the current Revit release's CollaborationCache. Read the user's
   Revit.ini `[CloudModelCache] CacheLocation` when configured, plus the current
   default location. Settings are never changed. Ignore CentralCache, PacCache,
   backup directories and filesystem junctions/symlinks.
3. Consider only native `.rvt` files whose basename is the exact cloud model GUID
   under the exact project-GUID directory. This is a bounded cache search, not a
   recursive search across ACC for a similar filename.
4. Copy each candidate read-only to an isolated temporary location. Check byte
   count, timestamps and SHA-256; hash the source again to detect concurrent
   changes. BasicFileInfo is read on the isolated snapshot, not the cache file.
5. Loaded linked models and unmodified primary models require both revision
   fields to match. A modified primary host can use one unambiguous saved edition
   of its exact identity; its unsaved edits are explicitly excluded. Different
   candidates are never selected just because one has a newer modification date.
6. Reject missing, ambiguous, changing, unreadable, unsupported-format or
   revision-mismatched candidates. The manifest's cache_evidence contains roots,
   expected identity, candidate paths, actual revisions and rejection reasons.
7. Preserve a byte-identical `_HostState` baseline for every acquired primary
   open model before optional plugin inspection, repathing or cleanup. All Revit
   opening/detachment/SaveAs for normalization is restricted to disposable copies
   or the exported package. A guard rejects any attempt to process a working
   Document returned unexpectedly by Revit.

For ordinary local open models, copy the identified saved file and verify its
saved revision. Unsaved-only models without a proven saved source are reported,
not silently serialized. Detached-source tracking is allowed only with matching
revision evidence. No fallback to a published cloud model occurs for an open row.
The explicit **Add ACC published model** mode remains separate.

## Saved state versus dependency inventory

An unmodified document with a matching saved revision can supply its existing
loaded reference inventory without reopening the model merely for discovery.
For a modified host, unsaved reference additions/deletions are not authoritative
for the saved file: the disposable saved copy is inspected instead. Saved native
cloud ModelPath GUIDs are retained before conversion to display strings. A saved
link can be bound to a loaded link only with matching persisted identity, never
merely the same element ID or filename.

If a cached linked model is unavailable, the primary host and `_HostState`
baseline remain retained. Automatic host repathing/cleanup is deferred when RVT
links are missing. Such a result is not a complete portable linked-model package.

## Folders, ZIPs and reports

Each job folder and its optional ZIP use the Revit model name, not a numeric job
index. A duplicate job name receives a folder/ZIP suffix `(2)` etc.; the actual
RVT filename remains unchanged. Separate jobs are sequential on the Revit thread.

    ET_<timestamp>/
      <Model name>/
        <Model name>.rvt
        _HostState/<snapshot ID>/<Model name>.rvt
        Sources/...
        _Refs/...                 (only when required)
        START_HERE.txt
        manifest.json
      <Model name>.zip             (when selected)
      batch.json

`START_HERE.txt` identifies the saved-state basis and explicitly says unsaved
edits are excluded. It separates host files copied, protected baseline integrity,
and packaged RVTs actually reopened/path-checked. A native header/metadata check
is not a successful Revit opening test. Do not delete `_Refs` or `_HostState` from
a delivered package. Do not synchronize export copies to a production central.

## Installation and first test

Update the **whole EasyBIM extension** from main, including its existing native
file-I/O helper, and restart Revit. Confirm version **2.1.2**. No additional Python
packages or Autodesk application registration are needed for this cache route.

Open the intended cloud host normally and leave its existing links as loaded.
Select the open-model row, choose a new output folder and transmit. Do not choose
Add ACC published model for this test. There is no SaveAs confirmation in this
saved-state workflow. The skip-unresolved-cloud-downloads option does not skip
identified local cache copies. Keep upgrade and cleanup off for the first test.

Review `cache_evidence`, `state_basis`, `saved_document_version`, the protected
host hash and final verification fields in the report. Missing cache candidates
are diagnostic failures, not permission to clear/repair caches or pick a newer
published edition. The source may legitimately be unavailable or inconsistent.

## Validation boundaries

Tests exercise real binary reads, copies, hashes, filesystem layouts and ZIPs;
Revit metadata and document operations are mocked. Windows/IronPython CI tests
the actual runtime and shipped Win32 helper. No licensed Revit session or real
company CollaborationCache is available in CI. This release does not certify a
successful Morrison export until a desktop test opens its copied models.

Autodesk documents a recovery-style copy-elsewhere, detach and save workflow for
cached RVTs, while cautioning against manipulating live caches during normal
operation. This implementation only reads cache files and rejects detected races;
it is not a cache-repair tool or a guarantee that every cached RVT is reusable.

RCP internal/external scan completeness, CAD-internal Xrefs, NWF dependencies,
private plugin spreadsheet associations and inaccessible network paths remain
explicit review boundaries. No direct cache/working-document writes, cache deletion,
cache repair, token extraction or undocumented RVT binary rewriting is performed.

## Primary references

- Autodesk cache identity example: https://blog.autodesk.io/refreshment-cloud-model-path-angle-and-direction/
- Cache configuration: https://help.autodesk.com/cloudhelp/2025/ENU/Revit-Customize/files/GUID-DAB9A3A1-2C12-4729-B842-16B533EBF436.htm
- Cloud cache location/options: https://help.autodesk.com/cloudhelp/2024/ENU/Revit-Customize/files/GUID-703D6F39-67BF-4DA1-843A-062ED7664FCE.htm
- DocumentVersion semantics: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/3574fa56-016e-b146-1499-b3b1c9129705.htm
- GetDocumentVersion: https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/177e0f88-29a7-7e66-5db8-9f8ae03ae086.htm
- Cached RVT recovery workflow: https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-retrieve-previously-synced-or-saved-Revit-models-after-losing-access-to-the-ACC-or-BIM-360-project.html
