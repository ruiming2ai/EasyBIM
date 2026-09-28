# e-transmit desktop acceptance checklist

Not yet performed. Use non-production models and the same Revit major version
for the first test. Keep originals outside the output tree. Do not synchronize
package copies to the original central.

- Update the whole EasyBIM extension, reload/restart, and verify version 2.1.11.
  Check light/dark icons and the Links panel ordering; existing buttons still work.
- With no project open, select a closed RVT. With several projects open, verify
  the active project alone is checked by default. Test unsaved/new/cloud hosts.
- Verify all file categories default ON after changing and reopening the dialog.
  Upgrade/cleanup must always default OFF, including after saving preferences.
- Confirm there is no **Load Unloaded Files** or **Deep Inspection** checkbox.
  When unloaded direct Revit links exist, verify the separate checklist appears,
  starts unchecked, and offers Reload selected / Continue without reloading / Cancel.
  Originally unloaded links must remain unloaded after the run.
- Check the separate **File Structure Organization** dropdown: categories by default,
  original hierarchy and flat Links. Save/reopen each preference. Older settings
  must default to categories. Test same-name/case-only files without renaming.
- Confirm one host per model-named package, one Links folder, and no generated
  Sources, _Refs or _HostState. Repeated source references share a delivered file.
  Check RCP Support folders and overlapping added folders in every mode.
- Repeat Morrison and Anthropology's September 25 cache exports in Revit 2024.
  Morrison's architectural link must accept one unambiguous saved edition even
  when its GUID differs from the loaded revision at the same save count (70).
  Anthropology's unloaded link (element 4815243) must use its saved cloud identity
  to search the cache despite its empty path. If no usable cache exists, record
  the exact identity and roots searched; do not claim a successful link export.
- Confirm the blank Anthropology keynote configuration is UNCONFIGURED rather
  than an error, while a configured missing keynote file remains a failure.
- Confirm no `_HostState` folder or duplicate host appears in packages or ZIPs.
  Simulate a processing failure/cancellation and confirm the collected host survives.
  Verify requested/copied/verified RVT-link counts separately from total file counts.
- Collect a host plus nested/overlay/unloaded RVT links, linked CAD, two PDFs
  named Details.pdf in different folders, linked images, keynotes and a decal.
  Check original filenames, directory hierarchy and absence of hash/renamed files.
- Check a PDF with multiple imported pages linked as different types and a raster
  image with nondefault resolution. Copied PDFs must remain complete original
  files; after repathing, verify page, resolution, dimensions and unloaded state.
- Use an actual Desktop Connector Shared/Consumed source with a newer WIP model
  present elsewhere. Confirm only the configured source is copied. Test a missing
  prefix mapping, an ambiguous mapping, an offline placeholder and access denial.
  Verify that missing/historical cloud sources are flagged rather than substituted.
- Test an RCP with adjacent Support files and an external RCS. Verify the Support
  files are copied and external-scan warnings remain. Add the external scan folder.
  Confirm visibility and positions after relocating the complete package.
- Test a Navisworks coordination model. If its path is not exposed, add its NWC/NWD
  and verify the report/manual repair. NWF references are not automatically parsed.
- Relocate the output to another directory/drive. Open ONLY the copied host,
  deliberately handle transmitted/detached prompts, inspect Manage Links and
  verify that supported paths point into the new package and load states agree.
- Compare existing link instance identities, positions, linked tags and dimensions
  before/after ACC-to-local conversion. Inspect nested links from their exported
  saved editions; a differing loaded document must not supply the dependency list.
- Compare originals before/after: no saved changes, no Sync/Publish, no path edits.
  Package RVTs should retain the source format unless Upgrade/Cleanup was enabled.
- Test upgrade and each sheet/view cleanup option on copies. Verify protected
  templates, retained dependent/primary views, schedules placed on sheets and
  unsupported special views. Test Purge only on an API version exposing it.
- Test Disable worksets on a workshared COPY. Confirm output is non-workshared.
  Otherwise verify new/transmitted central behavior without contacting the original.
- Simulate missing dependencies and cancel during a large copy. Completed files
  may remain; incomplete files must not be published. Read partial-run reports.
  A slow provider or Revit API operation may only honor Cancel after it returns.
- Test ZIP with a package larger than 4 GB when available, unzip to a new path,
  compare manifest hashes, and repeat the opening test. Do not certify portability
  based only on ZIP integrity or an automated test count.

Record Revit build, pyRevit build/engine, Desktop Connector version, source types,
selected options, report issues and any screenshots. This checklist does not turn
an unperformed check into a pass.

2.1.4 delivery-specific checks:

- Repeat both real-model exports in all three layouts, then move and reopen each
  package. Check nested Revit links, images/PDF pages, positions and annotations.
- Use a long destination. Confirm filesystem delivery and checksums can complete,
  but final Revit errors are reported rather than inherited temporary success.
- Cancel during preparation and delivery; simulate delivery/rollback failure.
  Check original host preservation and the reported external recovery location.
- Select both ZIP options. The batch ZIP must contain package folders/reports,
  without embedding the generated individual package ZIPs.

2.1.5 cache-selection checks:

- Repeat the Morrison run with its direct and LinkedModels cache candidates.
  Confirm DIRECT_SAME_EDITION_PAIR, selected direct path, both revisions and
  both different checksums in REPORT.txt / DIAGNOSTICS.txt / manifest.json.
- Confirm host and Links files exist; repeat Anthropology's unloaded ACC link.
- Check all three layouts, requested load states, placement, linked annotations,
  PDFs/images and reopening after moving each package.
- Confirm the working document remains unsaved/unmodified by the exporter and
  cache bytes remain unchanged. Conflicting editions/accounts must still fail.
- On failed host acquisition, confirm link discovery reads NOT_PERFORMED, not an
  implication that there are no links. Final-location verification stays separate.

2.1.6 unmodified-host checks:

- Repeat Greek Theater with the host unmodified and confirm the unique saved
  cache edition is collected despite its different loaded revision GUID.
- Confirm SAVED_CACHE_DIFFERS_FROM_LOADED shows both revision GUIDs and save
  counts; dependencies must come from the saved snapshot, not live inventory.
- Repeat Morrison and Anthropology, including relocation, and inspect real
  link paths, requested load states, placement, PDFs/images and annotations.


2.1.7 direct Revit-link collection checks:

- Open a local host with one or more local/network Revit links. Confirm each saved
  RVT is copied directly into Links/Revit without an intermediate linked-model
  inspection step.
- Confirm a loaded local link keeps its saved filesystem source rather than an
  open:// temporary document identity. A differing saved-file DocumentVersion
  must not block collection.
- Make an unsaved Revit-link add/remove/path change in the open host. Confirm
  saved TransmissionData remains authoritative for Revit-link collection.
- Put another Revit link inside a collected linked RVT. Confirm it is not
  recursively collected; only links directly discovered from the selected host
  are in scope.
- Repeat with an ACC link. Confirm its identified local cache edition can still be
  acquired and copied, while the linked RVT itself is not recursively inspected.
- With Repath enabled, verify the packaged host points to the collected link files.
  Repath/verification failure must not delete a successfully copied dependency.


2.1.9 live-ACC / finalization / progress checks:

- For a live ACC cloud-workshared host, open it first from Revit Home → Autodesk Docs. Confirm the e-transmit dialog has no APS sign-in, published-model picker, or published-version source mode.
- If another user has synchronized after the host was opened, use Revit Reload Latest when that newer state is required. Confirm e-transmit itself never Syncs, Publishes, or saves the working source.
- For a local/network host with ordinary RVT links, confirm repath uses relative Links/Revit paths without a preparatory host document open. Same-name links must retain their filenames in separate collision subfolders.
- For a true ACC External Resource Revit link, confirm the copied host is opened exactly once after dependencies reach final package paths. That one open must convert to a local relative link, save, verify target/load state, and close; no second verification reopen.
- Relocate the complete package and open only the copied host. Confirm converted ACC links resolve to packaged relative RVTs and preserve requested load state, placement, linked tags and dimensions.
- Confirm the pyRevit progress strip sits below the Revit title so the document filename remains readable. Check blue Collecting/Repathing, purple Finalizing ACC Links / Verifying Final Package, green Ready, amber Ready—Review Issues, red Incomplete and gray Cancelled.
- During ordinary final verification, confirm the status reads PACKAGE BUILT — VERIFYING FINAL PACKAGE. Review timings.csv for finalize_and_verify_host / verify_final_host so remaining Revit-open cost is measurable.


2.1.10 dockable-progress checks:

- Start an e-transmit and confirm no floating pyRevit prompt bar overlays the Revit title, ribbon, document tabs, or model canvas.
- Confirm the temporary **EasyBIM e-transmit** pane is docked at the top of Revit's dockable-pane region and Revit reserves layout space for it.
- Confirm phase, long filename/detail text, progress bar, percentage and Cancel remain individually readable at normal and high-DPI scaling.
- Check the approved blue/purple/green/amber/red/gray phase colors and confirm the pane hides when the command finishes.
- Click Cancel during a chunked copy and confirm cancellation is honored. Revit open/save/provider calls may still return before cancellation can be observed.


2.1.11 collection-preflight checks:

- With Repath, Cleanup and Upgrade all OFF, export the same already-open local and
  ACC hosts used in the September 27 tests. Confirm timings contain **no additional
  host revit_open/revit_save**, even when the host is modified or its saved/cache
  revision differs. The host RVT bytes must come from saved state.
- Repeat collect-only with a closed RVT. Confirm discovery is metadata-only and
  does not silently open the host. Missing unsupported references must be reported.
- Turn Cleanup ON with Repath OFF, then Upgrade ON with Repath OFF. Copied-host
  processing may open/save the package copy, but reference paths must not be
  intentionally repathed and final link-opening verification must remain skipped.
- Modify a selected open host and test all preflight choices: Continue without
  saving, Save and continue, Cancel. Save must be normal in-place Save only and
  must stop if the document remains modified; never SaveAs, Sync or Publish.
- Test an unloaded link whose saved/cache file is already identifiable: leave it
  unchecked and confirm the file can still be collected without source reload.
- Test an unloaded ACC link that needs acquisition: select it, confirm the source
  host is snapshotted first, then confirm its original global/local unloaded state
  is restored after success, failure and cancellation. Review Undo-history warning.
- Open a raw collect-only workshared/ACC-cache host using **Detach from Central →
  Preserve Worksets**. Confirm START_HERE/REPORT explains that copying bytes does
  not sever central/cloud association and that Detach does not repath links.
- With Repath enabled, verify supported packaged paths actually point into Links.
  Keep the known ACC same-session LoadFrom restriction visible as a failure rather
  than reporting a verified package when conversion cannot complete.
