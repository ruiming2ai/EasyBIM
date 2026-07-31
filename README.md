# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase recovery (Revit 2025-2026)

The Temp Phase command is implemented as a pyRevit 6.5 C# command and three
typed hooks. The hooks cancel a close only when temporary view properties need
restoration, defer the transaction to app-idling, prompt the user, and repost
the typed PostableCommand.Close command when requested.

Normal users do not build or stage anything. Update EasyBIM from GitHub and
reload pyRevit. EasyBIM ships both controller DLLs in the extension root `bin`
folder, and the command/hook loaders choose the matching file for the active
Revit version:

    bin\TempPhaseController.Revit2025.dll
    bin\TempPhaseController.Revit2026.dll

Release maintainers can rebuild the packaged DLLs from the EasyBIM repository:

    .\build\Build-TempPhase.ps1
    .\build\Build-TempPhase.ps1 -VerifyOnly

If the button opens a blank pyRevit window, close Revit, clear that year's
generated pyRevit extension assembly/cache once, update EasyBIM, then reload
pyRevit. The command logs its Revit version, loaded
extension/controller paths, loader/type-load failures, picker events, and
transaction completion at `%APPDATA%\EasyBIM\Temp Phase\logs\events.log` and
shows a Revit TaskDialog for missing or wrong-year modules. The original
standalone add-in remains the fallback until runtime parity is verified in both
Revit versions.
