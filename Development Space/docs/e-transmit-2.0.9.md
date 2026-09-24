# e-transmit 2.0.9 — host-only ACC link recovery and portable references

## Evidence from the two 2.0.8 desktop runs

The Lewis Hall run acquired an Autodesk Desktop Connector payload whose outer .rvt
was a ZIP composite. The archive itself contained both the host RVT and
UCB_Lewis Hall-A_R24.rvt, so 2.0.8 could extract both models, process them
child-first, and verify both packaged RVTs.

The Morrison Hall run was different. Desktop Connector returned a native CFB host-only
RVT. When the disposable inspection copy opened, Revit exposed
UCB_Morrison Hall-A_R24.rvt only at the temporary EasyBIM staging path. The saved
external-resource metadata in that run did not contain another usable file path, so
2.0.8 correctly refused to search for a same-named model but consequently omitted the
architectural RVT.

The Morrison run also exposed two additional portability gaps:
- long PDF/image sources were copied successfully but Revit rejected their >260
  physical package paths during ImageType reload;
- some image/PDF elements appeared only through ExternalResourceUtils and were
  therefore copied but classified as generic external resources and never repathed.

## 2.0.9 behavior

### Native host-only ACC links

When a Revit link exposed from the disposable inspection model points under EasyBIM's
own staging folder and there is no composite-archive provenance, e-transmit reconstructs
only the exact path relative to the original host's Desktop Connector folder.

For example:

    ADC/.../2-Morrison Hall/Host.rvt
    staging/.../UCB_Morrison Hall-A_R24.rvt

may resolve to:

    ADC/.../2-Morrison Hall/UCB_Morrison Hall-A_R24.rvt

only if Windows Shell confirms that exact item exists. The tool does not recursively
search by basename, choose a newer file, or cross into Shared, Consumed, or WIP
folders. If the exact candidate does not exist, the link remains unresolved.

Composite ZIP behavior from 2.0.8 remains authoritative when archive-member provenance
is available.

### Long linked PDFs/images

The full source hierarchy and original filename are still mirrored under Sources.
When that mirror path exceeds the practical Revit image-path budget, e-transmit creates
an additional short operational alias:

    _Refs/<Revit element id>/<unchanged original filename>

The packaged Revit model references this short alias. The complete Sources hierarchy
is retained for traceability. The manifest records mirror_target, target,
portable_alias, and the top-level aliases list. START_HERE explicitly tells the user
to keep both Sources and _Refs with the package.

### External-resource-only images

External resources whose Revit type is Image are treated as image links even when
ImageType enumeration did not return a matching row. Page number, resolution, load
state, and element identity are retained so the existing ImageType can be reloaded.
The element is not recreated.

### Optional support resources and warning noise

An unavailable SystemsAnalysisReport support directory is reported as
ANALYSIS_RESOURCE_UNAVAILABLE (warning), rather than a core file-copy failure.
If the directory exists, its files are still recursively collected.

External-resource version warnings are limited to actual Desktop Connector/cloud
identities. Local/network file timestamp-like resource versions remain recorded as
metadata without flooding the issue list.

## Deliberate remaining warnings

- An RCP and adjacent Support folder can be copied, but ReCap-internal/external scan
  completeness and safe Revit point-cloud repathing are not claimed.
- A source hard-coded to another user's Desktop or an unavailable mapped/network
  location remains an error. e-transmit does not substitute a similarly named file.
- If a cloud link exposes only an opaque model identity and neither archive provenance
  nor one exact Desktop Connector path, e-transmit reports it unresolved rather than
  guessing.
- Final packaged-RVT path/load verification is performed in the user's Revit session;
  CI does not run Autodesk Revit.

## Verification

The regression suite pins the distinction between composite and host-only ACC
acquisition, exact-sibling-only resolution, no-guess behavior, long-path mirror plus
_Refs alias behavior, external-only image classification, and optional analysis
resources. Portable Windows/Linux tests and official Windows IronPython 2.7 runtime
tests are required before merge.
