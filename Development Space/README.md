# Development Space

This folder contains development-only materials for EasyBIM:

- `tests/` for local verification
- `docs/` for design notes and planning records

pyRevit does not run files from this folder during normal Revit startup, model
open, idle, save, sync, or button execution. Keep runtime extension changes and
development-only changes in separate commits when a deployment branch will
cherry-pick production fixes.
