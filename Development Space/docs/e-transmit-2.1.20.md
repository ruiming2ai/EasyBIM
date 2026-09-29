# EasyBIM e-transmit 2.1.20 — reference repath and load-state fixes

## Scope

2.1.20 addresses three real-Revit findings:

1. Revit links that were loaded in the open detached host could be delivered as
   unloaded.
2. CAD/PDF and other TransmissionData-backed references could be copied but not
   repathed when their live API row did not expose a usable load-state flag.
3. The detached retry label used the word "another", which was ambiguous.

## Revit link load state

For detached hosts, the open Revit document is the authoritative source for
whether a placed Revit link is currently loaded. The selected/saved RVT remains
the authoritative source of host bytes and saved reference paths.

When saved TransmissionData and the live detached document describe the same
Revit link, eTransmit now carries the live `loaded` state into the package
reference row. The engine then uses that state for:

- local/relative RevitLinkType.LoadFrom conversion;
- TransmissionData desired load intent;
- final transmitted-file metadata.

The final mark-transmitted pass now receives the intended Revit-link states and
writes those states explicitly instead of merely preserving whatever flag
happened to be stored after the previous SaveAs.

Because marking a workshared host transmitted is itself a final metadata rewrite,
a workshared primary host is opened and verified once more after that rewrite.
This prevents a package from being reported as verified if the final transmitted
file no longer matches the intended path/load state.

## CAD/PDF and other TransmissionData references

Previously `apply_metadata` skipped a reference whenever the inventory row had
`loaded=None`. That can occur for non-Revit external references even when
TransmissionData itself contains the exact saved reference and load status.

eTransmit now re-reads the exact TransmissionData entry and derives the load
intent from `GetLinkedFileStatus()`. This allows the path to be rewritten even
when the live inventory has no explicit load-state value.

This applies to TransmissionData-backed CAD/PDF/image/other file references as
well as Revit links. References that are not represented in TransmissionData
still use their existing Revit API-specific repair path (for example
ImageType.ReloadFrom for linked PDF/images).

## Detached recovery wording

The retry button is now:

**Load model from file location…**

The word "another" has been removed.

## Python engine decision

2.1.20 intentionally remains on the existing pyRevit/IronPython Revit-facing
engine.

The current failures were Revit API state/repath logic bugs, not Python language
limitations. The command is dominated by Revit/.NET/WPF work that must run
in-process on Revit's API thread. A wholesale CPython conversion would add
interop/runtime risk without fixing these behaviors.

Pure file/report/hash helpers can be separated into CPython or an external
worker later if there is a measured performance or library need, while keeping
the Revit API boundary in the proven in-process engine.
