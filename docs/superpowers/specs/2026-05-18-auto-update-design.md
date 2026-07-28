# EasyBIM Auto Update Design

Date: 2026-05-18
Status: Superseded on 2026-07-28

## Summary

EasyBIM Auto Update is a convenience wrapper around the native pyRevit `Update` tool.

On each new Revit session, EasyBIM should:

- run once during extension startup
- call pyRevit's native update entry point
- rely on pyRevit for update checks, update output, and reload behavior
- avoid any EasyBIM-owned update engine

The `Auto Update` button under `EasyBIM.tab/Misc Tools.panel` should execute the same native pyRevit update entry point on demand.

## Product Decision

EasyBIM does not implement its own extension updater.

The implementation calls `pyrevit.versionmgr.updater.update_pyrevit()`, matching the native pyRevit `Update.smartbutton` script. EasyBIM keeps only a small session guard so startup auto update does not loop after pyRevit reloads.

## Components

### `startup.py`

- runs when pyRevit loads `EasyBIM.extension`
- checks the session guard
- marks the session before invoking update
- calls `auto_update.run_startup_auto_update()`

### `lib/easybim/auto_update.py`

- owns the once-per-session guard helpers
- exposes `run_startup_auto_update()`
- exposes `run_manual_auto_update()`
- delegates both flows to pyRevit's native updater

### `Auto Update.pushbutton`

- calls `run_manual_auto_update()`
- does not add custom status dialogs or custom update handling

## Data Flow

Startup:

1. Revit starts.
2. pyRevit loads EasyBIM.
3. `startup.py` checks whether startup update already ran in this Revit session.
4. If not, it marks the session and calls `run_startup_auto_update()`.
5. EasyBIM calls pyRevit's native update entry point.
6. pyRevit handles update output and reload behavior.
7. If pyRevit reloads, `startup.py` runs again and exits because the guard is set.

Manual:

1. User clicks EasyBIM `Auto Update`.
2. The button calls `run_manual_auto_update()`.
3. EasyBIM calls pyRevit's native update entry point.
4. pyRevit handles update output and reload behavior.

## Testing Strategy

- Unit-test the startup guard behavior.
- Unit-test that startup auto update calls the native pyRevit update entry point once.
- Unit-test that manual auto update calls the same native pyRevit update entry point once.
- Unit-test that native updater exceptions are not swallowed by the helper, matching normal pyRevit button execution.
- Syntax-check `startup.py`, the EasyBIM Auto Update button script, and `lib/easybim/auto_update.py`.

## References

- pyRevit native Update smartbutton source: `extensions/pyRevitCore.extension/pyRevit.tab/pyRevit.panel/Update.smartbutton/script.py`
- pyRevit updater reference: <https://docs.pyrevitlabs.io/reference/pyrevit/versionmgr/updater/>
