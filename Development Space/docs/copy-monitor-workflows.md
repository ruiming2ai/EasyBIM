# Independent placement and Copy Monitor

## Current availability

The commands target Revit 2024–2027, but no release has been exercised in Revit
on the implementation machine. This is an implementation with guarded runtime
checks, not a Revit compatibility certification. See
[acceptance status](copy-monitor-compatibility.md).

Independent creation currently accepts native **OneLevelBased point family
instances** in the MEP/device categories listed in
`lib/easybim/independent_placement_revit.py`, plus Generic Models. Required reference
levels remain. Intended elevation comes from the transformed XYZ position and
is checked after regeneration and transaction commit.

Wall-, ceiling-, floor-, roof-, face- and work-plane-based families are refused,
even when their old physical host has disappeared. There is no certified hosted
conversion in this release. Adaptive, curve-based, in-place, nested component and
nested shared-family conversions are also unavailable. The commands do not
make temporary building hosts, static geometry substitutes, or native
Copy/Monitor relationships.

## Batch Duplicate Host

1. Leave **Copy Original Family Type** off to pick a local prototype, as before.
   **Monitored** defaults on.
2. Alternatively, enable **Copy Original Family Type** to use each selected
   reference instance's family/type and instance values, bypassing the local pick.
3. Select a document/link instance, categories and family types.
4. Set local X/Y/Z offsets and **Align Orientation**, then place. Back navigation
   retains the first-page choices.

Offsets use each reference's transformed local axes. Alignment follows those
axes, including reflected link placements. With alignment off, the prototype
orientation remains fixed. The independent path re-reads and caches current
link transforms; a failed read is reported instead of substituting identity.

Local prototypes outside the new category scope retain legacy copying.
Monitoring is unavailable for legacy copies, unlinked references, unsupported
reference categories and non-point references; the result identifies this.
Supported-category prototypes that need conversion are skipped, with no hosted
fallback. The original local family and its other instances are not changed.

## Copy Monitor

**Copy and Monitor** opens the same wizard with original-family copying and
monitoring enabled. Those first-page settings remain editable.

**Monitor Existing** pairs one explicitly selected linked family instance with
one local instance. Pairing stores a relationship and does not move or detach
the destination. Hosted destinations show **Conversion required** and cannot
use automatic movement until a compatible independent destination is available.
This release does not migrate or replace them automatically.

**Check Changes** reads currently loaded link contents and displays the check
time in UTC. It does not reload links or run in the background. Filter by status,
select visible rows, inspect details, and apply an action to the checked rows:

| Action | Behavior |
| --- | --- |
| Match Source | Apply the saved offset/orientation recipe. Original-family relationships also synchronize their isolated family type and compatible instance values. |
| Keep Relative Location | Save the current source-to-destination transform, including relative rotation and reflection. Subsequent source rotation rotates the retained offset. Parameter/type changes remain pending. |
| Accept Difference | Acknowledge the exact current source/local snapshots without changing the placement recipe. Later relevant edits reopen review. |
| Postpone | Leave the relationship pending without modifying the destination. |
| Stop Monitoring | Mark the relationship inactive; keep the destination and historical record. Works when source/destination geometry is unavailable. |
| Show Local | Select and frame the surviving local element. |

For local-prototype copies and explicitly paired existing instances, Match Source
resolves placement only. It does not overwrite them with the linked family's
parameters or type. Parameter differences remain visible and can be accepted
explicitly. Original-family relationships transfer compatible values.

Checks distinguish source edits, local edits and both changing. Rows are grouped
by status/link/family/type; details show prior/current source and local parameter
values. Informational calculated-placement notes do not create a pending issue.
Unresolved transfer exceptions do. Cancellation reports the checked count and
never implies that unchecked relationships are clear.

## Definitions, parameters and identity

Original types are loaded under isolated `EasyBIM_<family>_<id>` family names.
Native family definitions retain their formulas and parametric content; they
are not rebuilt from exported geometry. Conflicting existing definitions are
not overwritten. Preparation is cached within a batch; monitored definitions
are reusable across commands only while their recorded revisions still match.
Changed definitions get fresh isolated variants. Old variants are not purged.

Instance values are matched by shared GUID, built-in identifier, or an
unambiguous name/data-type/storage definition. Native type values are retained
and checked on import. Physical host, level, phase, workset and related placement
values are calculated or deliberately excluded and reported. Numeric values are
read back after regeneration and commit. Missing, ambiguous, calculated,
read-only and unmappable values are reported.

Materials and family-type references are resolved by stable identity or a unique
semantic match. Missing or ambiguous references are not guessed. Exact transfer
of every value is therefore not guaranteed. Duplicate-value label warnings,
such as repeated Marks, retain the requested values and are reported; other
Revit warnings/errors roll back the item.

Records are stored in versioned Extensible Storage on separate DataStorage
elements inside the destination RVT. They identify the link instance, linked
document, source UniqueId, destination UniqueId and placement recipe. Repeated
placements of one link document have distinct identities. An already active
source/placement mapping is refused. Copying a local instance does not clone its
monitoring relationship.

Missing sources or destinations retain records. Unloaded, missing and replaced
links have distinct review states. Replaced-document detection uses the linked
ProjectInformation UniqueId; file clones retaining the same model identity cannot
be distinguished solely by this identifier. Direct links are supported; nested
link chains cannot be paired in this release. No nearest-element matching occurs.

## Updates and rollback

Movement uses the current source placement, so a surviving source that moves
and loses its physical host can drive an already independent destination. Its
former wall or ceiling is not needed. This does not make a hosted original
family eligible for new independent copying.

Destination identity is retained during movement, mirroring and supported type
updates. Pinned, grouped, option-owned, nested, other-user-owned and connected
MEP destinations are refused when mutation is unsafe. Automatic connected-system
reconnection and hosted-instance replacement are unavailable. No local destination
is deleted as an automatic response to a missing source.

Each placement/update and its metadata use a common transaction-group rollback
boundary. Failed placements do not retain a partial family load or relationship.
Successful earlier items survive a later item failure or cancellation. Revit
Undo/Redo, save/reopen and worksharing behavior still require live acceptance.

## Performance and remaining acceptance

The code caches link transforms and type snapshots and does no full-model host
search. Family preparation is cached; checks compare snapshots without loading
families or inspecting detailed geometry.

The committed desktop benchmark measures only record/decision code. Revit API
collection, DataStorage serialization, family loading, actual placement, geometry,
connectors and WPF timing are excluded. Revit performance relative to legacy
Batch Duplicate Host has **not** been measured.

Required outstanding acceptance includes 2024–2027 placement, variable-length
fixtures, flipped/nested geometry, connector/circuit fidelity, tags/references,
host-loss scenarios, worksharing, Undo/Redo and save/reopen. Hosted conversion
remains disabled until geometry, formula and connector fidelity is demonstrated
in those environments.
