# EasyBIM e-transmit 2.1.24

The current release restores copy-first native CAD/Revit repathing and fixes the
2.1.23 finalization step that could replace packaged paths with old saved paths.
See [2.1.24 release notes](e-transmit-2.1.24.md) for the current processing flow and
validation limits. Earlier workflow details below retain their version context.

For the copy-only repath test, leave **Simple copy and repath (native file links)**
checked (the default), enable **Repath**, and leave **Cleanup/Upgrade OFF**. The
host/cache copy is repathed through closed-file TransmissionData; unsupported
PDF/image or true ACC External Resource references are reported for manual repair
without opening the host in a worker. Uncheck the simple option for full API
repair. Selecting cleanup or upgrade retains document processing.

**Ribbon: EasyBIM > Links > e-transmit.** Update the entire EasyBIM extension and
reload pyRevit (restart Revit if a ribbon change is not visible). This is an
independent pyRevit implementation inspired by Autodesk eTransmit, not a wrapper
around Autodesk's add-in and not a claim of identical format coverage.

## Status and first use

Intended for Revit 2023 and newer with pyRevit's IronPython engine. No external
Python packages or extra installer are required. Filesystem and API-shaped tests
do not establish that company models open in Revit. See `e-transmit-2.1.12.md` for
release validation and use `e-transmit-desktop-checklist.md` for real-model acceptance.

Revit must be running. Source models do not need to be manually opened. The
button works with no project open. Open project documents appear in the source
list, with the active project checked by default. Browse Models and Browse Folder
also accept closed RVTs. Family documents and linked documents are not offered as
open host choices. The exported source is always the **saved file/cache**, not unsaved geometry in an
open model. If a selected open model is modified, e-transmit explicitly asks whether
to continue without saving, perform a normal in-place Save, or cancel. It never
substitutes Save As, Synchronize with Central, or Publish.

Select a short output location, such as `C:\Transmit`, then Transmit model(s).
Every run creates a new folder, and existing packages are never overwritten.
Source files may trigger normal Desktop Connector download-on-read. Cancel is
checked between operations and during chunked copies. An individual Revit open,
save, or provider hydration call cannot be interrupted by the Python progress UI.
Revit remains occupied while the command executes; this is not a separate worker.

## Revit link collection in 2.1.7

For an open local host, e-transmit reads Revit-link paths from the saved host
metadata (TransmissionData) when available and copies those identified saved RVT
files directly. This means unsaved link additions/removals are not the dependency
source of truth. The already-open document remains useful for non-RVT resources.
If saved Revit-link metadata cannot be read, the tool reports that condition and
falls back to the already exposed live reference list without opening a temporary
host merely to discover link paths.

Local linked RVTs are not reopened merely to compare document revisions or
discover nested dependencies. A loaded local link keeps its saved filesystem
path instead of being converted into an internal temporary-document identity.

ACC/cloud links still use identified cache acquisition where required. Collection
of a linked RVT stops at that file; nested dependencies inside linked RVTs are
not recursively collected. Repath and final model verification remain separate
package operations and may report issues without undoing successfully collected
link files.

## Live ACC and final verification in 2.1.9

Live ACC cloud-workshared hosts must be opened first through native **Revit Home → Autodesk Docs**. EasyBIM no longer offers the published APS/version picker as a normal e-transmit source, because a published model can lag the live workshared state. The command uses the ACC document currently loaded by Revit; if collaborators have synchronized newer work, use Revit's Reload Latest before transmitting when that state is required.

Ordinary local/network/file-based Revit links keep the existing `Links/Revit` hierarchy and are repathed with relative TransmissionData paths without opening the copied host. Same-name files keep their original filename and are separated by collision subfolders.

True ACC External Resource Revit links cannot be converted by TransmissionData alone. Those hosts are finalized only after every package dependency is in its final location: EasyBIM opens the copied host once, converts the cloud link to a packaged local relative resource, saves it, verifies the immediate path/load state in that same open document, and closes it. There is no second verification reopen.

Phase colors remain blue for collecting/repathing, purple for final ACC link conversion or final package verification, green for ready, amber for ready-with-review, red for incomplete, and gray for cancelled. `PACKAGE BUILT — VERIFYING FINAL PACKAGE` means package files are already in place and the tool is performing the final opening check. The progress surface itself is described in the 2.1.10 section below.

## Dockable progress in 2.1.10

e-transmit now uses a thin Revit dockable pane instead of pyRevit's floating prompt-bar progress overlay. The pane defaults to Revit's **Top** dock position, so Revit reserves layout space for it rather than drawing over the Revit title, ribbon, document tabs, or model canvas.

The strip keeps phase, current file/detail text, progress percentage, and Cancel as separate controls. It is shown only while e-transmit is running and hidden afterward. Phase colors remain blue for collecting/repathing, purple for ACC finalization/final verification, green for ready, amber for review, red for incomplete, and gray for cancelled.

The e-transmit command still occupies Revit during Revit-API operations. The dockable pane fixes visual obstruction; it does not make document-editing operations concurrent with an executing Revit command.

## Collection preflight in 2.1.11

**Repath off + Cleanup off + Upgrade off means no additional host opening.** An
already-open local/ACC host contributes its currently available direct-reference
inventory even when it is modified or its loaded/cache revision differs. The RVT bytes
copied to the package still come from the saved file/cache. A closed collect-only host
uses saved metadata and reports discovery limitations rather than silently opening it.

Cleanup or Upgrade may open/process the **package copy** even when Repath is off;
neither option implicitly enables repathing or final link-opening verification.

The old **Deep Inspection** and **Load Unloaded Files** controls are removed. Unloaded
direct Revit links appear in a separate per-link dialog. Links start unchecked:
available saved/cache bytes can still be collected without reloading; checking a link
explicitly authorizes a temporary source reload attempt. e-transmit snapshots the host
first, captures the identified link source, and restores the original unloaded state
before package processing. A user-local unload override is restored at the same scope.
Revit reload/unload operations can clear Undo history; restoring the load state does
not restore that history.

If a selected open host has unsaved changes, the choices are **Continue without
saving**, **Save and continue**, or **Cancel**. Save and continue uses only the existing
document's normal in-place Save and stops if a clean saved state is not produced.

Raw collect-only workshared or ACC-cache RVTs retain their original central/cloud
association internally. Starting in 2.1.12, EasyBIM marks the **final primary packaged
host** as a Revit transmitted file whenever closed-file TransmissionData is available.
Revit opens a transmitted workshared file detached from its central model, so this
keeps the fast no-host-open collection path while preventing the package host from
opening as an ordinary participant in the original central. If transmission marking
is unavailable, the report falls back to **Detach from Central → Preserve Worksets**
guidance. This opening/worksharing behavior does not repath external references.

## Transmitted primary hosts in 2.1.12

After final package delivery and any explicitly requested processing, EasyBIM uses
closed-file `TransmissionData` to mark each **primary workshared host** transmitted.
No Revit document open/save is required for this step. Before setting the flag, the
tool explicitly preserves every TransmissionData-backed file reference's current
path, path type and saved load intent, because Revit applies desired reference data
when a transmitted file opens.

True external-server/ACC resource references are not contained in TransmissionData
and are not rewritten by this operation. Directly collected linked RVTs are also not
modified by this feature. Repath behavior remains separate: Repath OFF keeps original
reference locations; Repath ON continues to update only supported package references.

The final package verifies the transmitted flag using Revit's closed-file API. Reports
and `files.csv` include `transmission_status`. `TRANSMITTED` means the recipient can
open the packaged host normally and Revit will treat the workshared file as detached
from its central model. `TRANSMIT_UNAVAILABLE` means use **Detach from Central →
Preserve Worksets** manually. Never synchronize a package copy to the source central.

## File Structure Organization

Each host gets an independent model-named folder with its original-named RVT,
reports, and one `Links` folder. Choose **By category** (default), **Retain original
folder structure**, or **All files together**. Save Settings remembers the choice.

```
ET_<time>/
  Host/
    Host.rvt
    Links/
      Revit/Architecture.rvt
      CAD/Site.dwg
      IFC/Coordination.ifc
      PDF/Architecture-<identity>/Details.pdf
      PDF/Electrical-<identity>/Details.pdf
    START_HERE.txt
    manifest.json
    REPORT.txt
    files.csv
    references.csv
    issues.csv
```

Same-name sources receive distinct parent subfolders without changing filenames.
Repeated references to the same verified source share one target in that package.
Point-cloud Support folders, added dependency folders and analysis report trees
retain their internal paths even in flat mode. Decal images use the Images category.

Original mode represents drives/network shares/ACC roots beneath `Links` using the
existing source hierarchy. Files are prepared in local temporary storage before
checksum-verified delivery. There are no generated duplicate source mirrors or
short-path alias folders. Revit still has path restrictions: the exporter keeps
copied files and reports failed/deferred verification if the final path cannot be
opened. Move the complete package to a shorter path and repeat the opening check.

## Included files and coverage

All file-category checkboxes start checked on every launch. Categories
control collection, not guaranteed discovery or format-specific repathing.

| Source | Current behavior |
|---|---|
| Saved local/network RVT | Open hosts use available direct-reference inventory plus saved metadata where available. Closed collect-only hosts use saved metadata without hidden opening. Direct linked RVTs are copied but not recursively inspected. |
| CAD / IFC | Copy the original exposed source. No geometry conversion. CAD-internal Xrefs, images, fonts and other dependencies are **not parsed**; add them or use AutoCAD eTransmit for that portion. |
| Linked PDF / raster image | Copy the complete original file. No rasterization. Image repathing preserves PDF page and stored resolution when using the Revit API. |
| Point cloud | Copy the exposed source plus the adjacent `<RCP name> Support` tree when present. External RCS scans and internal RCP paths remain unverified; add other scan folders and verify in ReCap. |
| Navisworks | Copy exposed or explicitly added NWC/NWD/NWF originals. Some Revit coordination paths are not exposed by the supported API. Use Add Files for those. NWF internal references are not parsed. |
| Keynotes, assembly codes, decals, DWF markups, analysis reports, other external files | Collect accessible sources when exposed by the native/external-resource APIs. Report unresolved paths; arbitrary third-party private storage is not decoded. |
| Imported/embedded content | No reconstruction/extraction. It remains embedded in copied RVTs. |

Add Files and Add Folder allow explicit inclusion of dependencies not exposed by
Revit. Added RVTs are scanned like other RVTs. Keep output outside an added input
folder. Do not assume a checked category proves every file of that category was
found. The report and desktop opening check are part of transmission.

## ACC and exact source locations

Open cloud hosts use read-only snapshots of CollaborationCache, including custom
Revit.ini locations. Loaded or unloaded cloud links are identified by project/model
GUIDs and region from the saved host's references. The link type supplies its filename.
No APS sign-in is needed for this cache route.

Prefer a cache candidate matching both the loaded revision GUID and save count.
Otherwise, a single unambiguous native saved cache edition of the same identified
model may be accepted, with its revision difference reported. Different candidates
are never chosen by timestamp. Cache stability, hashes, native metadata and Revit
format are checked before accepting a copy. A revision difference by itself no
longer triggers another host opening; available direct-reference inventory from the
already-open document is used for discovery while the exported RVT bytes remain the
selected saved/cache edition.

Separately browsed local/Desktop Connector files retain their exact source paths.
Exact prefix mappings must preserve the configured source, including Shared/Consumed.
The e-transmit source UI does not provide an ACC published-version browser. For live
cloud-workshared state, open the model natively in Revit Home → Autodesk Docs first.
Existing cache identity/acquisition code is used only to collect the saved host/link
bytes associated with that open Revit document; PacCache is not used.

## Repath, upgrade and cleanup

Repath remains an explicit checkbox. With Repath off, reference locations in the
host are intentionally left unchanged. If Cleanup and Upgrade are also off, no
additional host opening or opening verification is performed. Recipients may need
Reload From, and workshared/cache host copies may need Detach from Central.

Unloaded links preserve their original intended load state. There is no global
"load all unloaded files" switch. The preflight checklist is only permission to
temporarily reload selected source links when acquisition requires it; it does not
authorize leaving those links loaded in the working model or package.

Packages and ZIPs contain no `_HostState` duplicate. Temporary staging outside the
deliverable supports rollback on failure or cancellation. Reports separate requested,
copied and verified Revit links; a copied host alone does not prove portability.

Native file references supported by TransmissionData are repathed in closed package
copies using relative package paths. Additional image or external-resource operations
may require opening and saving a package copy. Methods not exposed by the API are
reported rather than simulated by recreating instances. Point-cloud paths may need
manual repair.

Saving an older model in the running Revit upgrades it. Unless Upgrade or Cleanup
is checked, additional repathing that would require that save is skipped with
`UPGRADE_CONSENT_REQUIRED`, and the saved model format is retained.

Cleanup options are: disable worksets, purge unused definitions, keep all
sheets/views, keep sheets and placed views, also retain selected view types,
remove all sheets but retain all views, or retain only selected view types.
Templates, unsupported special views and required primary views are protected.
A view/purge deletion that cascades into a retained view rolls back. Purge is
available only where the public `Document.GetUnusedElements` API exists.
Directly collected linked RVTs remain unchanged. Cleanup/upgrade applies only to package models explicitly processed by the host workflow.

Model processing affects package copies only. If it fails, the baseline collected
output file is restored and the failure is reported. A raw collect-only workshared/
ACC-cache copy can retain its original central association and therefore require
**Detach from Central → Preserve Worksets** when opened independently. Never
synchronize a package copy to the original central.

## Reports and verification

`START_HERE.txt` and `manifest.json` are always written, including partial runs.
The detailed-report option adds REPORT, files, references and issues CSVs.
SHA-256 values distinguish copied source snapshots from the final repathed RVTs.
CSV formula-leading values are escaped. Reports include project paths and may
include server resource identifiers; review them before external sharing.

- `COLLECTED`: no detected errors within the implemented scan; **not** an opening test.
- `NEEDS_REVIEW`: missing files, unverifiable dependencies, manual repathing, or other issues.
- `CANCELLED` / `FAILED`: partial output; do not issue as a complete package.

An optional ZIP contains the preserved hierarchy and reports. No automatic upload
or external sharing occurs. Always move/copy a test package to a different root,
open the copied host, and verify Manage Links, PDF pages, point clouds, Navisworks
and cleanup results before delivery. Do not publish a package merely because
checksums passed.

## Implementation and tests

Owned modules: `lib/easybim_etransmit/`. The new command refreshes only these
modules; it does not alter EasyBIM startup hooks or other tools. Destructive
settings are not remembered. Output/mapping/report preferences may be saved at
`%APPDATA%\EasyBIM\e-transmit\settings.json`.

From the repository root, with Python 3:

```
python -m unittest discover -s "Development Space/tests/etransmit" -v
```

See `e-transmit-validation.txt` for the execution record. API-shaped fakes test
control flow and safety boundaries, not Autodesk's implementation. No independent
reviewer or licensed Revit execution was available during development.

## Primary API references

- [TransmissionData](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d78d1e9c-1cee-1336-88d5-b605dacd077d.htm)
- [ImageTypeOptions](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/981135c3-777b-df9b-747f-60a35b74e00e.htm)
- [Image resolution](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/858dcd6b-5231-fb9b-b43a-7c1397c4265e.htm)
- [GetUnusedElements](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/dcb1f497-dfa6-bb3a-b9dd-f9a580f990f2.htm)
- [ExternalResourceReference](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/ffad9c15-8fc9-fbfd-f328-101533f4cf74.htm)
- [RevitLinkType.LoadFrom](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/bdb3a91e-9a0a-e68d-51da-c460535f5fd2.htm)

The icon artwork was generated and adapted into transparent light/dark ribbon
resources. No Autodesk logos or proprietary add-in binaries are distributed.
