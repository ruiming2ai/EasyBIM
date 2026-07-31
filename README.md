# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase recovery (Revit 2025-2026)

The Temp Phase command is implemented as a pyRevit 6.5 C# command and three
typed hooks. The hooks cancel a close only when temporary view properties need
restoration, defer the transaction to app-idling, prompt the user, and repost
the typed PostableCommand.Close command when requested.

Build and stage the controller for the Revit installation being tested:

    .\build\Build-TempPhase.ps1 -RevitVersion 2025
    .\build\Build-TempPhase.ps1 -RevitVersion 2026

Only the matching TempPhaseController.dll should be present in the live
pyRevit extension. The original standalone add-in remains the
fallback until runtime parity is verified in both Revit versions.
