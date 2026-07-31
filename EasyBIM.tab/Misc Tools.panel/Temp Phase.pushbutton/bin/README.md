This command-level bin folder is intentionally not used for Temp Phase runtime
deployment.

EasyBIM ships the Revit-specific controller DLLs from the extension root bin
folder instead:

    bin\TempPhaseController.Revit2025.dll
    bin\TempPhaseController.Revit2026.dll

Normal users only need to update EasyBIM and reload pyRevit.
