# e-transmit 2.0.8 — validated acquisition and complete reference inspection

## Scope

Repairs follow the two desktop report traces: unreadable ADC-acquired host bytes at
BasicFileInfo.Extract; PDFs failing destination normalization; native Revit link
paths pointing into inspection scratch; Content-library references resolved beside
the host instead of their saved absolute library location; and report-directory
metadata mistaken for an empty file reference.

## Changes

- Introduces read-only file-signature and bounded MS-CFB BasicFileInfo inspection.
  A native RVT and a ZIP composite named `.rvt` take separate acquisition paths.
  Container recognition is not proof that Revit can open a model.
- Composite extraction validates paths, case collisions, file/directory collisions,
  symlinks, encryption, entry counts, sizes and CRCs. It requires exactly one host
  member matching the selected source filename. Extracted dependencies are scoped
  to that composite and its recorded member hierarchy; no global basename search.
- Original/acquisition and extracted-host hashes are reported separately. Invalid
  payloads are rejected before Revit metadata calls. A failed/partial inventory is
  not retried in the model-finishing phase.
- Shell download completion now requires an exclusive read handle, as well as the
  expected stable file size. No automatic live/WIP refresh or cloud publication.
- Content-library references honor Revit's PathType.Content and saved absolute path.
  Only optional assembly-code library references get missing-library warnings.
- Native and external-resource information for the SAME reference is combined,
  rather than skipping external metadata for an ID already seen by the native API.
  Named Path fields are used; arbitrary identity/version fields are not filenames.
  Report Path fields can identify directories, whose accessible files are collected.
- Unicode Win32 IO handles long dependency destinations without renaming files or
  changing directory hierarchy. Revit's RVT/SaveAs path budget stays separate and
  is still checked. Registry settings and global .NET switches are not modified.
- Image/PDF relinking uses final-host-relative paths after SaveAs, not a long
  absolute path before SaveAs. Individual image reload failures are isolated.
- Non-TransmissionData RVT links are reloaded through the EXISTING element ID,
  never deleted/recreated. Known unloaded intent is restored. Final metadata uses
  the original element ID even when an inventory row has a resource suffix.
- Collected child RVTs are processed before their parent host in normal traversal.
- The in-Revit run opens final packaged models and checks recorded RVT/image paths
  (and RVT loaded status) without saving that verification document. Missing RVT
  sources defer this check rather than permit unnoticed cloud-source fallback.
  Copy, inventory, processing and model verification have separate report fields.

## Safety boundaries

The original RVT is never patched, saved, synchronized or published. Geometry and
model data are only written by Revit to private/package copies under the existing
upgrade/cleanup permissions. No source-tracking hooks or other EasyBIM buttons are
changed by this revision. Synthetic fixtures contain no customer models or IDs.

Desktop Connector is not a historical-version API. Full configured source paths
are preserved; the tool still will not substitute a similarly named model in
Shared, Consumed or WIP. A resource exposing only cloud IDs with no resolvable
file/member path is reported with its captured metadata, not guessed. Native RVTs
that remain unreadable in Revit, unsupported future versions, genuinely missing
files and insufficient permissions cannot be repaired by a byte-copy operation.

Point-cloud internal references and unsupported repathing remain review warnings.
Native Win32 long-path copy does not guarantee another application can read every
long output path; per-reference and final-opening errors remain visible. Directory
reparse points/junctions are refused on protected long-path writes.

## Verification

Portable tests and real Windows IronPython tests exercise binary copy checksums,
CFB/ZIP routing, hostile/corrupt archive rejection, exact scoped members, reference
metadata enrichment, Content libraries, long Windows file IO and processing order.
Revit API operations are simulated in CI. The final model-opening validation runs
inside the user's installed Revit; CI does not contain Autodesk Revit or an
authenticated Desktop Connector workspace.

A separately supplied native Revit 2024 model was read and passed through the actual
acquisition/copy code locally, with equal source/output hashes and no original
modification. That is a real-file container/copy test, not an actual Revit open test.
