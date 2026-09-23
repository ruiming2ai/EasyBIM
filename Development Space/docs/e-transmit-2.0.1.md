# e-transmit 2.0.1: output-path and zero-copy reporting fix

## Reported failure

A Desktop Connector RVT selected from the file browser was downloaded normally.
Transmission subsequently produced only reports: `Destination is too long` and
an empty host list. The previous dialog nevertheless said it had "finished".
The destination guard ran before the source copy; this error was not evidence
that Desktop Connector had failed to download the model.

## Changes

- Shorten only generated wrappers: `ET_<timestamp>/01/` instead of
  `Transmittal_<timestamp>/01_<full model name>/`. The full original model
  filename still appears inside the preserved `Sources` hierarchy.
- Keep original filenames and all source subfolders unchanged. Do not flatten
  same-named PDFs, rename project folders, or alter Shared/Consumed mappings.
- Check selected model and explicitly added paths before starting. If the final
  path is still too long, keep the dialog open and offer to select a shorter
  output folder before creating a report-only package. Nested dependencies are
  checked when discovered; this preflight does not claim complete discovery.
- Report actual destination length and path. Budget for Windows UTF-16 path
  units, short temporary files and the `.0001` suffix used by RVT backups.
  No Windows registry changes and no extended-length paths passed to Revit.
- Use short, exclusively created sibling copy temporaries rather than appending
  a 41-character suffix to a potentially long original filename. Use short
  internal RVT inspection/processing names under the package's `_work` folder.
- Establish the source snapshot after the first source read/rewind, allowing a
  provider's initial download-on-read to settle its metadata. Continue checking
  size/mtime during collection and verifying destination hashes. No forced
  refresh, cloud-version lookup, basename search or live-model substitution.
- Zero/missing host copies now set `FAILED`, show `Host models copied: 0 / 1`,
  and explicitly state that no host was copied. Failed file rows remain in the
  manifest; failed hosts are no longer listed as successfully packaged models.
- Show copied-host/file counts and the first issue details in the completion
  dialog instead of the vague "finished with 1 issue(s)" message.

## First desktop test

Update the whole EasyBIM extension from `main` and reload pyRevit/restart Revit.
The e-transmit dialog must show version **2.0.1**. Select the same saved Desktop
Connector file. Keep upgrade and cleanup disabled for the first test.

Use a short, writable destination (for example, an existing `C:\ET` folder).
A long OneDrive output location may still exceed the path budget when combined
with a deep source hierarchy; the new prompt will identify that before starting
for selected sources. A completely arbitrary long output path cannot be made
Revit-portable without changing source names, which this tool does not do.

Read the copied-host count and reports, then open a copied model from the package
without synchronizing to the source central. Initial download-on-read cannot be
interrupted by the Python progress dialog; source/network/provider errors are
reported separately and still require resolution.

## Scope and verification

Changes are confined to `lib/easybim_etransmit/`, the new path regression tests,
and this note. No existing buttons, panel layouts, startup code, hooks, shared
EasyBIM modules, or GitHub workflow definitions are changed by this fix.

The local CPython suite passes **92 tests** (77 existing and 15 new). New tests
exercise Windows path budgets, unchanged source trees, preflight UI control flow,
short copy/inspection/processing temporaries, download-on-first-read metadata,
failed-host manifests, and honest completion messages. Windows/Linux CI runs the
same suite after publication. These are filesystem and API-shaped tests, not
licensed Revit, IronPython, WPF or Desktop Connector integration tests.

Primary references checked for the fix:
- Microsoft path limits: https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation
- Autodesk download behavior: https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Automatic-file-syncing-with-Desktop-Connector.html
