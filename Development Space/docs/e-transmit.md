# EasyBIM e-transmit 2.1.6

**Ribbon: EasyBIM > Links > e-transmit.** Update the entire EasyBIM extension and
reload pyRevit (restart Revit if a ribbon change is not visible). This is an
independent pyRevit implementation inspired by Autodesk eTransmit, not a wrapper
around Autodesk's add-in and not a claim of identical format coverage.

## Status and first use

Intended for Revit 2023 and newer with pyRevit's IronPython engine. No external
Python packages or extra installer are required. Filesystem and API-shaped tests
do not establish that company models open in Revit. See `e-transmit-2.1.6.md` for
release validation and use `e-transmit-desktop-checklist.md` for real-model acceptance.

Revit must be running. Source models do not need to be manually opened. The
button works with no project open. Open project documents appear in the source
list, with the active project checked by default. Browse Models and Browse Folder
also accept closed RVTs. Family documents and linked documents are not offered as
open host choices. The source is always the **saved file**, not unsaved geometry
in an open model; the command never saves, synchronizes or publishes an original.

Select a short output location, such as `C:\Transmit`, then Transmit model(s).
Every run creates a new folder, and existing packages are never overwritten.
Source files may trigger normal Desktop Connector download-on-read. Cancel is
checked between operations and during chunked copies. An individual Revit open,
save, or provider hydration call cannot be interrupted by the Python progress UI.
Revit remains occupied while the command executes; this is not a separate worker.

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
| Saved local/network RVT | Recursive saved reference scan, including unloaded links, deduplicated by canonical path. Default deep inspection opens detached internal copies for additional APIs. |
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
Otherwise, a single unambiguous native saved cache edition of the same linked
model is accepted, with its revision difference reported. When no linked document
is loaded, report that no loaded-revision comparison was possible. Different
candidates are never chosen by timestamp. Cache stability, hashes, native metadata
and Revit format are checked before accepting a copy. A different saved revision
is inspected for its own dependencies rather than borrowing the loaded inventory.

Separately browsed local/Desktop Connector files retain their exact source paths.
Exact prefix mappings must preserve the configured source, including Shared/Consumed.
The explicit **Add ACC published model** mode uses authenticated, version-specific
downloads. It is not a fallback for unavailable cache sources. PacCache is not used.

## Repath, upgrade and cleanup

**Load Unloaded Files** defaults on and loads successfully acquired Revit links
in exported copies. Turn it off to preserve their recorded load states. The option
is disabled when repathing is off and is retained with saved settings. Working
models are never reloaded. Existing link elements are repathed rather than recreated.

Packages and ZIPs contain no `_HostState` duplicate. Temporary staging outside the
deliverable supports rollback on failure or cancellation. Reports separate requested,
copied and verified Revit links; a copied host alone does not prove portability.

The default is deep inspection plus supported repathing, with upgrade and all
cleanup OFF. No source model transaction is performed. Inspection copies are
opened without a model tab and closed without saving. Metadata-only inspection
is faster but cannot discover all images/cloud/external references.

Native external references are repathed through TransmissionData in package
copies, preserving known load intent. Additional image and mapped external RVT
repath operations may require opening and saving a detached copy. Repath methods
not exposed by the API are reported as `MANUAL_REPAIR_REQUIRED`; they are not
faked by deleting and recreating instances. Point-cloud paths may need repair.

Saving an older model in the running Revit upgrades it. Unless Upgrade or Cleanup
is checked, additional repathing that would require that save is skipped with
`UPGRADE_CONSENT_REQUIRED`, and the saved model format is retained.

Cleanup options are: disable worksets, purge unused definitions, keep all
sheets/views, keep sheets and placed views, also retain selected view types,
remove all sheets but retain all views, or retain only selected view types.
Templates, unsupported special views and required primary views are protected.
A view/purge deletion that cascades into a retained view rolls back. Purge is
available only where the public `Document.GetUnusedElements` API exists.
Cleanup is applied to collected RVTs, including linked RVTs, not just the hosts.

Model processing is copy-only. If it fails, the baseline collected output file is
restored and the failure is reported. Workshared outputs may be marked transmitted
or saved as a new package central. **Never synchronize a package to the original
central.** A workshared file that could not be marked transmitted is explicitly
flagged; open that copy using Detach from Central.

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
