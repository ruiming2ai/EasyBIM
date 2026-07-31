# EasyBIM Temp Phase C# fallback

The normal EasyBIM pyRevit workflow no longer loads a Temp Phase controller DLL.
The Python button and Python event hooks handle phase assignment, close
cancellation, deferred restoration, and close reposting without per-machine
deployment steps.

This directory is retained as the standalone C# fallback and as a reference
implementation for Revit 2025 and 2026. Its version-specific projects may be
built by release maintainers, but users must not stage those DLLs into EasyBIM
or add a pyRevit `modules:` entry.

Keep the standalone add-in enabled until both Revit versions pass the Python
close-recovery acceptance tests.
