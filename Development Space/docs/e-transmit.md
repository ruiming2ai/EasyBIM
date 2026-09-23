# EasyBIM e-transmit 2.0.0

**Ribbon: EasyBIM > Links > e-transmit.** Update the entire EasyBIM extension and
reload pyRevit (restart Revit if a ribbon change is not visible). This is an
independent pyRevit implementation inspired by Autodesk eTransmit, not a wrapper
around Autodesk's add-in and not a claim of identical format coverage.

## Status and first use

Intended for Revit 2023 and newer with pyRevit's IronPython engine. No external
Python packages or extra installer are required. Development checks run under
CPython; **Revit, IronPython, WPF, Desktop Connector and Windows integration have
not been executed in the development environment**. Start on a non-production
project and use `e-transmit-desktop-checklist.md` before office-wide deployment.

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

## Folder and filename preservation

Files keep their original basenames and relative directory hierarchy. Different
source roots are represented explicitly rather than merged by filename:

```
Transmittal_<time>/
  01_Host/
    Sources/
      Drive_D/Project/Models/Host.rvt
      Drive_D/Project/Architecture/References/Details.pdf
      Drive_D/Project/Electrical/References/Details.pdf
      Network/server/share/Project/References/Site.dwg
      ACC/Account/Project/Project Files/Consumed/Architecture/Model.rvt
    START_HERE.txt
    manifest.json
    REPORT.txt
    files.csv
    references.csv
    issues.csv
```

The standard `...\DC\ACCDocs\...` prefix is represented as `Sources/ACC/...`.
Custom/legacy Desktop Connector roots are preserved under their normal drive
hierarchy. A conflicting destination is an error, not permission to rename or
overwrite a file. Long output paths are reported; names are not truncated.

## Included files and coverage

All twelve file-category checkboxes start checked on every launch. Categories
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

A readable local/Desktop Connector path is copied from that exact location. The
tool does **not** search by basename, replace Shared/Consumed with WIP, fetch the
latest live authoring model, or use CollaborationCache/PacCache files.

For a cloud/server display path with no usable local filesystem path, add an
**exact source-prefix mapping** to the corresponding Connector/download folder:

```
BIM 360://Project/Project Files/Consumed
    -> C:\Users\you\DC\ACCDocs\Account\Project\Project Files\Consumed
```

The folder must represent the same configured source. Longest matching prefixes
win; ambiguous mappings and path traversal are rejected. Only the prefix is
mapped: the remaining hierarchy and filename are preserved. Mappings are logged.

Cloud-only host models require choosing their intended saved/downloaded file or
an exact mapping before transmission. The extension has **no ACC authentication
or historical-version download client**. External resource version IDs are
recorded when available, but a filesystem copy is not certified as that historical
cloud revision. Missing/unverifiable sources are reported, never silently
substituted. Desktop Connector may download the version available at the linked
location; verify controlled snapshots before delivery.

## Repath, upgrade and cleanup

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
