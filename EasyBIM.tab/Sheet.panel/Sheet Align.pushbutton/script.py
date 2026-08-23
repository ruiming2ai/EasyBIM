# -*- coding: utf-8 -*-
"""Shift everything a sheet owns as one block, in paper space."""

# pylint: disable=import-error,invalid-name,broad-except
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import RoutedEventHandler
from System.Windows.Controls import CheckBox
from System.Windows.Controls.Primitives import ButtonBase

from pyrevit import DB
from pyrevit import forms
from pyrevit import revit
from pyrevit import script


logger = script.get_logger()


from easybim import sheet_content
from easybim import sheet_titleblocks
from easybim.compat import eid_to_int as _eid_int
from easybim.compat import safe_text as _safe_text


TITLE_BLOCK_CATEGORY_INT = sheet_content.builtin_category_int(
    getattr(DB.BuiltInCategory, "OST_TitleBlocks", None))


class SheetRow(object):
    """One sheet in the pick list."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.sheet_id_int = _eid_int(sheet.Id)
        self.sheet_number = _safe_text(getattr(sheet, "SheetNumber", ""))
        self.sheet_name = _safe_text(getattr(sheet, "Name", ""))
        self.display_name = "{} - {}".format(self.sheet_number, self.sheet_name)
        self.search_blob = self.display_name.lower()
        self.is_checked = False


class RunStats(object):
    def __init__(self):
        self.sheets_selected = 0
        self.sheets_aligned = 0
        self.elements_moved = 0
        self.pinned_unpinned = 0
        self.pinned_restored = 0
        # Notes are non-blocking. A sheet without a title block must not cost
        # every other sheet its alignment.
        self.notes = []

    def add_note(self, text):
        self.notes.append(_safe_text(text))


def _collect_sheets(doc):
    try:
        sheets = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.ViewSheet)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []

    rows = [SheetRow(sheet) for sheet in sheets
            if not bool(getattr(sheet, "IsTemplate", False))]
    rows.sort(key=lambda x: (x.sheet_number.lower(), x.sheet_name.lower()))
    return rows


class SheetAlignWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)

        self.active_doc = revit.doc
        self._sheet_rows = _collect_sheets(self.active_doc)
        self._checked_sheet_ids = set()

        # One handler on the list rather than one per row: the template is
        # stamped per row, so a Click attribute in it would attach a handler
        # for every sheet in the model.
        self.sheet_lb.AddHandler(
            ButtonBase.ClickEvent,
            RoutedEventHandler(self.sheet_checkbox_click))

        self.ref_sheet_cb.ItemsSource = self._sheet_rows
        if self._sheet_rows:
            self.ref_sheet_cb.SelectedIndex = 0

        self._refresh_sheet_list()
        self._sync_reference_enabled()

    # -- UI ----------------------------------------------------------------

    def _set_status(self, text):
        self.status_tb.Text = _safe_text(text)

    def _selected_mode(self):
        if self.by_title_block_rb.IsChecked:
            return sheet_content.MODE_TITLE_BLOCK_ORIGIN
        return sheet_content.MODE_SHEET_ORIGIN

    def _sync_reference_enabled(self):
        """The reference sheet only means anything in title-block mode."""
        wanted = self._selected_mode() == sheet_content.MODE_TITLE_BLOCK_ORIGIN
        self.ref_sheet_cb.IsEnabled = wanted
        self.ref_sheet_label.IsEnabled = wanted

    def align_mode_changed(self, sender, args):
        del sender, args
        if not hasattr(self, "ref_sheet_cb"):
            return
        self._sync_reference_enabled()

    def _visible_rows(self):
        token = _safe_text(self.sheet_search_tb.Text).strip().lower()
        if not token:
            return list(self._sheet_rows)
        return [row for row in self._sheet_rows if token in row.search_blob]

    def _refresh_sheet_list(self):
        visible = self._visible_rows()
        for row in visible:
            row.is_checked = row.sheet_id_int in self._checked_sheet_ids

        self.sheet_lb.ItemsSource = visible
        self.sheet_count_tb.Text = (
            "Visible sheets: {} | Checked (visible): {} | Checked (all): {}".format(
                len(visible),
                len([row for row in visible if row.is_checked]),
                len(self._checked_sheet_ids),
            )
        )
        self._set_status("{} sheet(s) in this model.".format(len(self._sheet_rows)))

    def sheet_search_changed(self, sender, args):
        del sender, args
        self._refresh_sheet_list()

    def sheet_checkbox_click(self, sender, args):
        # Every ButtonBase click inside the list bubbles here, so the source
        # has to be checked before acting on it.
        del sender
        source = getattr(args, "OriginalSource", None)
        if not isinstance(source, CheckBox):
            return

        row = getattr(source, "DataContext", None)
        if row is None or not hasattr(row, "sheet_id_int"):
            return

        if bool(source.IsChecked):
            self._checked_sheet_ids.add(row.sheet_id_int)
        else:
            self._checked_sheet_ids.discard(row.sheet_id_int)
        self._refresh_sheet_list()

    def check_all_visible_click(self, sender, args):
        del sender, args
        for row in self._visible_rows():
            self._checked_sheet_ids.add(row.sheet_id_int)
        self._refresh_sheet_list()

    def clear_sheets_click(self, sender, args):
        del sender, args
        self._checked_sheet_ids.clear()
        self._refresh_sheet_list()

    def cancel_click(self, sender, args):
        del sender, args
        self.Close()

    # -- run ---------------------------------------------------------------

    def _build_summary_text(self, stats, headline):
        lines = [
            headline,
            "Sheets selected: {}".format(stats.sheets_selected),
            "Sheets aligned: {}".format(stats.sheets_aligned),
            "Elements moved: {}".format(stats.elements_moved),
            "Pinned unpinned: {}".format(stats.pinned_unpinned),
            "Pinned restored: {}".format(stats.pinned_restored),
            "Notes: {}".format(len(stats.notes)),
        ]
        if stats.notes:
            lines.append("")
            lines.append("Notes (up to 200 rows):")
            for note in stats.notes[:200]:
                lines.append("- {}".format(note))
            if len(stats.notes) > 200:
                lines.append("... {} additional note(s) omitted.".format(
                    len(stats.notes) - 200))
        return "\n".join(lines)

    def _plan_sheet_moves(self, rows, mode, reference_point, stats):
        """One entry per sheet: what to move, and by how much.

        Every shift is read before the transaction opens. Sheets are
        independent of one another and nothing here is derived from a value a
        write could stale, so there is no measure-after-regenerate dance.
        """
        plans = []
        for row in rows:
            title_block, extra = sheet_titleblocks.first_title_block(
                DB, self.active_doc, row.sheet)
            if title_block is None:
                stats.add_note(
                    "{}: sheet has no title block; skipped.".format(row.display_name))
                continue
            if extra:
                stats.add_note(
                    "{}: sheet has {} extra title block(s); the first one was used.".format(
                        row.display_name, extra))

            current = sheet_titleblocks.location_point(title_block)
            target = sheet_content.target_point_for(mode, current, reference_point)
            if target is None:
                stats.add_note(
                    "{}: title block has no location point; skipped.".format(
                        row.display_name))
                continue

            shift = sheet_titleblocks.title_block_shift(_xyz(target), current)
            if shift is None:
                stats.add_note(
                    "{}: already in position.".format(row.display_name))
                continue

            title_block_id = _eid_int(title_block.Id)
            owned = sheet_content.owned_elements(
                DB, self.active_doc, row.sheet, _eid_int,
                TITLE_BLOCK_CATEGORY_INT, keep_title_block_id=title_block_id)
            owned[title_block_id] = title_block

            move_ids = sheet_titleblocks.plan_sheet_move(
                sorted(owned.keys()),
                [],                       # nothing is exempt: the sheet moves whole
                True,
                title_block_id=title_block_id,
            )
            plans.append({
                "label": row.display_name,
                "shift": shift,
                "elements": [owned[x] for x in move_ids if x in owned],
            })
        return plans

    def _apply_sheet_moves(self, plans, stats):
        for plan in plans:
            shift = plan["shift"]
            label = plan["label"]
            pinned = []
            moved = 0

            for element in plan["elements"]:
                if sheet_content.unpin_if_pinned(element):
                    pinned.append((element, label))
                    stats.pinned_unpinned += 1

                ok_move, reason = sheet_content.move_element(DB, element, shift)
                if not ok_move:
                    stats.add_note("{}: {}".format(label, reason))
                    continue
                moved += 1

            restored, failures = sheet_content.restore_pins(pinned)
            stats.pinned_restored += restored
            for pin_label, pin_reason in failures:
                stats.add_note("{}: {}".format(pin_label, pin_reason))

            if moved:
                stats.sheets_aligned += 1
                stats.elements_moved += moved

    def run_click(self, sender, args):
        del sender, args

        if not self.active_doc:
            forms.alert("No active document found.", title="Sheet Align")
            return

        rows = [row for row in self._sheet_rows
                if row.sheet_id_int in self._checked_sheet_ids]
        if not rows:
            forms.alert("Select at least one sheet.", title="Sheet Align")
            return

        mode = self._selected_mode()
        reference_point = None
        if mode == sheet_content.MODE_TITLE_BLOCK_ORIGIN:
            reference_row = self.ref_sheet_cb.SelectedItem
            if reference_row is None:
                forms.alert("Select a reference sheet.", title="Sheet Align")
                return

            reference_block, _ = sheet_titleblocks.first_title_block(
                DB, self.active_doc, reference_row.sheet)
            if reference_block is None:
                forms.alert(
                    "The reference sheet has no title block, so there is nothing to align to.",
                    title="Sheet Align")
                return

            reference_point = sheet_titleblocks.location_point(reference_block)
            if reference_point is None:
                forms.alert(
                    "The reference title block has no location point.",
                    title="Sheet Align")
                return

        stats = RunStats()
        stats.sheets_selected = len(rows)

        plans = self._plan_sheet_moves(rows, mode, reference_point, stats)
        if not plans:
            summary = self._build_summary_text(
                stats, "Sheet Align had nothing to move.")
            self._set_status("Nothing to move.")
            forms.alert(summary, title="Sheet Align")
            return

        tx_group = DB.TransactionGroup(self.active_doc, "Sheet Align")
        tx = None
        try:
            tx_group.Start()
            tx = DB.Transaction(self.active_doc, "Apply Sheet Align")
            tx.Start()
            self._apply_sheet_moves(plans, stats)
            tx.Commit()
            tx_group.Assimilate()
        except Exception as ex:
            if tx:
                try:
                    tx.RollBack()
                except Exception:
                    pass
            try:
                tx_group.RollBack()
            except Exception:
                pass
            summary = self._build_summary_text(
                stats, "Sheet Align aborted. Transaction rolled back.\nReason: {}".format(ex))
            self._set_status("Apply failed and was rolled back.")
            forms.alert(summary, title="Sheet Align", warn_icon=True)
            return

        summary = self._build_summary_text(stats, "Sheet Align completed.")
        self._set_status("Done. Aligned {} of {} selected sheet(s).".format(
            stats.sheets_aligned, stats.sheets_selected))
        forms.alert(summary, title="Sheet Align")


def _xyz(point):
    return DB.XYZ(float(point[0]), float(point[1]), float(point[2]))


def main():
    if not revit.doc:
        forms.alert("No active document found.", title="Sheet Align")
        return

    window = SheetAlignWindow("SheetAlignWindow.xaml")
    window.ShowDialog()


if __name__ == "__main__":
    main()
