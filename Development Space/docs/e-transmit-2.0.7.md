# e-transmit 2.0.7: automatic source resolution

## User-facing change

Normal use should no longer require "Use saved copy" or source-prefix mappings just
because the selected model is detached or a link is exposed through Autodesk Docs.

- Saved local/open models continue to use Document.PathName directly.
- DocumentOpening records the exact file Revit was asked to open before a detached
  document loses its PathName. DocumentOpened binds that source to the open document.
- For an already-open detached document, e-transmit can read the current Revit journal
  as a read-only fallback and recover the matching opening path when it is present.
- Autodesk Docs/BIM 360 display URIs can resolve to one exact Desktop Connector
  hierarchy at the configured/default ACCDocs workspace. No filename-only search,
  newest-model selection, WIP substitution, or Shared/Consumed substitution occurs.
- Manual saved-copy and prefix-mapping controls remain only as ambiguity/recovery
  fallbacks; they are not intended as a normal step.

Revit intentionally reports an empty Document.PathName for detached documents, which
is why the pre-open path has to be captured instead of inferred after detaching.

## Remaining issues from the 2.0.6 desktop test

- AssemblyCodeTable/Uniformat resources are resolved against Revit's configured
  library roots. If the exact optional resource is not installed, that is a warning,
  not a failed transmittal; another filename is never substituted.
- Duplicate ExternalResource entries that refer to the same Revit element as an
  already-copied saved link are treated as aliases. A blank or inspection-scratch
  display path no longer creates a false missing-source error.
- Exact Revit library filenames are used. No "closest" or newer library resource is
  substituted.
- Point-cloud RCP warnings and REPATH_NOT_AVAILABLE warnings remain meaningful:
  the source can be copied while internal/external scan completeness or safe API
  repathing still requires verification.

## Safety and limits

Automatic resolution is identity-based. If two Desktop Connector accounts expose the
same exact hierarchy, e-transmit reports ambiguity instead of guessing. A true
server/cloud reference for which Revit and Desktop Connector expose no resolvable
file identity remains unresolved rather than silently choosing a similarly named model.

The source-tracking additions to the existing document-opening/opened hooks are wrapped
in isolated try/except blocks and do not save, synchronize, publish, detach, or otherwise
modify the source model.

## Verification

The e-transmit suite includes automatic-source regressions for detached opening capture,
journal fallback, exact Desktop Connector hierarchy, ambiguous hierarchy rejection,
Revit library resource lookup, optional Uniformat behavior, staging-alias deduplication,
and blank external-resource aliases. These run in the portable Windows/Linux suite and
under official Windows IronPython 2.7.12.

File IO, hashes, path mapping and reports are exercised for real. Revit API/WPF
integration and authenticated company ACC workspaces remain desktop acceptance tests.
