# EasyBIM e-transmit 2.1.11 — collection preflight

## Approved behavior

**Repath off + Cleanup off + Upgrade off means no additional host opening.**
The tool reads the existing open local/ACC document's available references and
copies the identified saved file/cache. Modified state or a different cached
revision does not trigger another host inspection. Closed sources use available
saved metadata and report incomplete discovery rather than silently opening.
Cleanup or Upgrade explicitly permits the copied-model opens those operations
need even with Repath off; it does not enable repathing or final link verification.

The Deep Inspection and Load Unloaded Files controls are removed. Old saved
preferences for those controls do not override the new behavior. Repath retains
its existing checkbox; supported file links use relative TransmissionData paths,
while resource types requiring the document API still require processing.

## Explicit preflight

A modified selected open host prompts once: **Continue without saving**, **Save
and continue**, or **Cancel**. Only Save and continue calls normal in-place Save.
No SaveAs, Sync, Publish, document closing, or relocation is substituted. A source
that needs native saving or whose save fails stops preflight. Earlier explicitly
requested saves can already have completed. Inventory is captured after the choice.

Unloaded direct Revit link types appear in a separate checkbox dialog. All are
unchecked initially. **Reload selected and continue**, **Continue without
reloading**, and **Cancel** are available. Unchecked links are still collected
when their identified saved sources are available. Type names are read even when
GetLinkDocument returns no document, preserving unloaded ACC link identity/name.

Selected primary hosts are snapshotted before temporary reloads. Each selected
link is reloaded, its identified saved bytes acquired, and its original unloaded
state restored before output processing. A user-specific local unload override
is restored as such. Cancellation and acquisition failures also restore state;
a restore failure stops with explicit Manage Links guidance. Reload may clear
Undo history and change the modified flag; the dialog warns that restoring
Unloaded cannot undo those side effects. No source Save follows temporary reload.

Exported load intent remains originally unloaded; the obsolete global load flag
cannot force it loaded. A collect-only byte copy retains saved load state, not
unsaved/user-specific overrides. Repath writes intended load state where supported.

## Opening the result

A copied workshared/cloud-cache RVT is **not automatically an independent central
or detached model**. In Revit File > Open, select **Detach from Central**, then
**Preserve Worksets**, to review a workshared copy independently. Do not synchronize
it back to the original central. Detaching does not repair external link paths.

START_HERE and REPORT now carry this guidance and state when Repath was not
selected. Manifest/file-report metadata records opening guidance; existing Revit
verification is labeled DETACH_IF_WORKSHARED, not evidence of a normal open.
No new SaveAs/new-central export workflow is introduced.

## Limits and validation

This release does not claim to fix Revit's same-session multiple-host LoadFrom
restriction. An ACC repath can still fail when its link is shared by other open
documents; the error remains explicit and the collected host is retained. A
successful copy/checksum is not proof of a portable or normally opening package.
Nested linked-RVT processing is unchanged and remains excluded.

Portable tests cover option combinations, modified/mismatched cache inventory,
closed-file collect-only, unloaded naming, real file acquisition with simulated
Revit state, explicit saves, global/local unload restoration, cancellation and
failure paths. Actual Revit/pyRevit testing is still required for UI dialogs,
Reload/Save API behavior, ACC conversion, and recipient opening.

### Workstation acceptance

1. With all three processing options off, an already-open ACC host should record
   no additional host opens, including with unsaved changes (Continue chosen).
2. Test the same case with Cleanup or Upgrade enabled: copied-model processing is
   allowed, but original paths remain unchanged when Repath is off.
3. Select an unloaded link for reload, then verify original Manage Links state is
   restored and acquired file is included. Cancel during acquisition and recheck.
4. Choose no temporary reload: available unloaded files still copy; unavailable
   sources are reported rather than substituted.
5. Confirm Save choice uses the same working model/path, never SaveAs or Sync.
6. Open a workshared collected host detached with preserved worksets. Do not infer
   normal-open success from the detached verification result.
