# VBA type-parameter sync layer (optional)

When `vbaProject.bin` exists in this folder, "Export Schedules to Excel"
writes macro-enabled `.xlsm` files in which editing ANY type-parameter cell
automatically copies the value to every other row of the same element type.
Without the file, exports are plain `.xlsx` and rely on the red conditional
formatting to flag diverging type-parameter cells.

`vbaProject.bin` is a compiled Office binary that can only be produced by
Excel itself, so it must be authored once by hand:

## One-time authoring steps

1. Open Excel and create a NEW blank workbook with exactly one sheet
   (fresh workbook required: the sheet's internal codename must be `Sheet1`).
2. Optionally rename the sheet tab to `Export` (cosmetic only).
3. Press `Alt+F11` to open the VBA editor. In the project tree, double-click
   **ThisWorkbook** (NOT the sheet module).
4. Paste the entire contents of `ThisWorkbook_TypeSync.vba.txt` into it.
5. Save the workbook as a macro-enabled workbook (`.xlsm`), e.g.
   `donor.xlsm`, then close Excel completely.
6. Rename `donor.xlsm` to `donor.zip` (or open it with 7-Zip) and extract
   `xl/vbaProject.bin`.
7. Copy `vbaProject.bin` into this folder, next to this README.

Delete or rename the file to revert to plain `.xlsx` exports at any time.

## Notes

- Users may see an "Enable Macros"/"Enable Content" prompt when opening the
  exported files; if macros are blocked by policy, the file still works and
  the red conflict formatting remains active.
- Whether to commit `vbaProject.bin` to git is your call; if not, add
  `vba/vbaProject.bin` to `.gitignore`.
