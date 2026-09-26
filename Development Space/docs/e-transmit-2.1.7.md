# e-transmit 2.1.7 — direct saved Revit-link collection

## Purpose

This release simplifies Revit-link collection to match the intended workflow:
collect the saved files Revit already identifies instead of reopening linked RVTs
to prove revision equality or discover nested dependencies.

## Behavior

- Open local hosts use saved TransmissionData for Revit-link locations when
  available. Live inventory remains available for non-RVT resources and is only
  a fallback for Revit links if saved metadata cannot be read.
- Loaded local Revit links retain their saved local/network path instead of being
  rewritten to an internal open:// document key for acquisition.
- Local saved RVTs are copied even when their saved DocumentVersion differs from
  the revision currently loaded in memory. The command does not save or sync the
  working document.
- Collected Revit-link RVTs are direct-copy dependencies. They are not reopened
  to discover nested RVT/PDF/image/point-cloud dependencies.
- ACC/cloud acquisition remains identity-based and may use the verified local
  Revit cache. After acquisition, the linked RVT is likewise not recursively
  inspected.
- Repath and final opening verification remain separate from collection. A model
  processing issue does not turn an already copied dependency into a missing
  source file.

## Regression coverage

New regressions cover local loaded-link path preservation, saved host link
metadata overriding unsaved live link rows, direct local host collection without
temporary RVT inspection, local saved-file revision mismatch, preservation of ACC
cache identity from a local host, and the no-nested-inspection rule. Existing
tests that required recursive linked-model discovery were updated to the new
explicit scope.

Real Revit acceptance is still required for local/network and ACC models,
including final repathing, relocation, link load state and annotations.
