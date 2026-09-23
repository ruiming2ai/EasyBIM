# e-transmit desktop acceptance checklist

Not yet performed. Use non-production models and the same Revit major version
for the first test. Keep originals outside the output tree. Do not synchronize
package copies to the original central.

- Update the whole EasyBIM extension, reload/restart, and verify version 2.0.0.
  Check light/dark icons and the Links panel ordering; existing buttons still work.
- With no project open, select a closed RVT. With several projects open, verify
  the active project alone is checked by default. Test unsaved/new/cloud hosts.
- Verify all file categories default ON after changing and reopening the dialog.
  Upgrade/cleanup must always default OFF, including after saving preferences.
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
