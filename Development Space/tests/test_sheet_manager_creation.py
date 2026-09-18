"""Exercise the real creation/copy/apply code at the unavailable Revit boundary."""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND = ROOT / "EasyBIM.tab" / "Sheet.panel" / "Sheet Manager.pushbutton"
NUMBER_ID, NAME_ID = -1006202, -1006203


class ElementId(int):
    InvalidElementId = -1


class IdList(list):
    Add = list.append


class Parameter:
    IsReadOnly = False
    StorageType = "String"

    def __init__(self, owner, param_id, name, value="", storage="String"):
        self.owner, self.Id, self.value = owner, param_id, value
        self.Definition = types.SimpleNamespace(Name=name)
        self.StorageType = storage

    def AsString(self):
        return self.value

    AsInteger = AsString
    AsDouble = AsString

    def Set(self, value):
        self.owner.doc.parameter_writes.append((self.Id, value))
        # Title-block sheet fields can write through to the owning sheet.
        if self.Id in (NUMBER_ID, NAME_ID):
            sheet = getattr(self.owner, "sheet", self.owner)
            setattr(sheet, "SheetNumber" if self.Id == NUMBER_ID else "Name",
                    value)
        self.value = value


class Element:
    def LookupParameter(self, name):
        return next((p for p in self.Parameters if p.Definition.Name == name),
                    None)


class Sheet(Element):
    IsPlaceholder = False
    IsTemplate = False

    def __init__(self, doc, sheet_id, number, name):
        self.doc, self.Id = doc, sheet_id
        self._number, self._name = number, name
        self._collection_id = -1
        self.Parameters = [Parameter(self, NUMBER_ID, "Sheet Number", number),
                           Parameter(self, NAME_ID, "Sheet Name", name),
                           Parameter(self, 101, "Drawn By", "AB")]
        self.revisions = [11, 22]

    @property
    def SheetNumber(self):
        return self._number

    @SheetNumber.setter
    def SheetNumber(self, value):
        self.doc.identities.append((self.Id, "number", value))
        self._number = value

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        self.doc.identities.append((self.Id, "name", value))
        self._name = value

    @property
    def SheetCollectionId(self):
        if self.doc.older_host:
            raise AttributeError("SheetCollectionId")
        return self._collection_id

    @SheetCollectionId.setter
    def SheetCollectionId(self, value):
        self.doc.collection_identities.append((self.SheetNumber, self.Name))
        self._collection_id = value

    def GetAdditionalRevisionIds(self):
        return self.revisions

    def SetAdditionalRevisionIds(self, values):
        if self.SheetNumber in self.doc.fail_revisions:
            raise ValueError("Revision is unavailable")
        self.revisions = list(values)

    @staticmethod
    def Create(doc, type_id):
        sheet = Sheet(doc, doc.next_id, "AUTO", "Unnamed")
        sheet._collection_id = doc.new_collection_id
        doc.next_id += 1
        doc.sheets[sheet.Id] = sheet
        doc.tblocks[sheet.Id] = [TitleBlock(doc, sheet, type_id)]
        for element in (sheet, doc.tblocks[sheet.Id][0]):
            for param in element.Parameters:
                if param.Id not in (NUMBER_ID, NAME_ID):
                    param.value = "" if param.StorageType == "String" else 0
        return sheet


class TitleBlock(Element):
    def __init__(self, doc, sheet, type_id=50):
        self.doc, self.sheet, self.OwnerViewId = doc, sheet, sheet.Id
        self.type_id = type_id
        self.Parameters = [
            Parameter(self, NUMBER_ID, "Sheet Number", sheet.SheetNumber),
            Parameter(self, NAME_ID, "Sheet Name", sheet.Name),
            Parameter(self, 201, "Office", "Seattle"),
            Parameter(self, 202, "Show Stamp", 1, "Integer"),
            Parameter(self, 203, "Offset", 2.5, "Double"),
            Parameter(self, 204, "Reference", 99, "ElementId"),
        ]

    def GetTypeId(self):
        return self.type_id


class Collector:
    def __init__(self, doc):
        self.doc, self.titleblocks = doc, False

    def OfClass(self, cls):
        return self

    def OfCategory(self, category):
        self.titleblocks = True
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        if self.titleblocks:
            return [t for blocks in self.doc.tblocks.values() for t in blocks]
        return list(self.doc.sheets.values())


class FailureOptions:
    def __init__(self):
        self.preprocessor = None

    def SetFailuresPreprocessor(self, value):
        self.preprocessor = value
        return self

    def SetForcedModalHandling(self, value):
        self.forced_modal = value
        return self


class Transaction:
    def __init__(self, doc, name):
        self.doc, self.name, self.status = doc, name, "Uninitialized"
        self.options = FailureOptions()
        doc.transactions.append(self)

    def Start(self):
        self.before_sheets = dict(self.doc.sheets)
        self.before_tblocks = dict(self.doc.tblocks)
        self.status = "Started"
        return self.status

    def GetFailureHandlingOptions(self):
        return self.options

    def SetFailureHandlingOptions(self, options):
        self.options = options

    def Commit(self):
        if self.doc.before_commit:
            self.doc.before_commit()
        numbers = {s.SheetNumber for key, s in self.doc.sheets.items()
                   if key not in self.before_sheets}
        if numbers & self.doc.throw_commit:
            raise ValueError("Commit exception")
        if numbers & self.doc.fail_commit:
            if self.options.preprocessor:
                message = types.SimpleNamespace(
                    GetSeverity=lambda: "Error",
                    GetDescriptionText=lambda: "Sheet Number is already in use.")
                accessor = types.SimpleNamespace(
                    GetFailureMessages=lambda: [message])
                self.options.preprocessor.PreprocessFailures(accessor)
            return self.RollBack()
        self.status = "Committed"
        return self.status

    def RollBack(self):
        self.doc.sheets = self.before_sheets
        self.doc.tblocks = self.before_tblocks
        self.status = "RolledBack"
        return self.status

    def GetStatus(self):
        return self.status

    def Dispose(self):
        if self.status == "Started":
            self.RollBack()

    def __enter__(self):
        self.Start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self.RollBack()
        else:
            self.Commit()


class TransactionGroup(Transaction):
    def Assimilate(self):
        if self.doc.fail_group:
            return self.RollBack()
        self.status = "Committed"
        return self.status

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self.RollBack()
        else:
            self.Assimilate()


class Document:
    def __init__(self):
        self.next_id, self.new_collection_id = 2, -1
        self.older_host = False
        self.fail_group = False
        self.identities, self.parameter_writes = [], []
        self.regenerated_identities, self.collection_identities = [], []
        self.transactions = []
        self.fail_revisions, self.fail_commit, self.throw_commit = set(), set(), set()
        self.before_commit = None
        self.after_regenerate = None
        template = Sheet(self, 1, "T001", "Template")
        self.sheets = {1: template}
        self.tblocks = {1: [TitleBlock(self, template)]}

    def Regenerate(self):
        self.regenerated_identities.extend(
            (s.SheetNumber, s.Name) for key, s in self.sheets.items() if key != 1)
        if self.after_regenerate:
            self.after_regenerate()


def load_revit_module():
    db = types.SimpleNamespace(
        BuiltInParameter=types.SimpleNamespace(SHEET_NUMBER=NUMBER_ID,
                                              SHEET_NAME=NAME_ID),
        BuiltInCategory=types.SimpleNamespace(OST_TitleBlocks="TitleBlocks"),
        StorageType=types.SimpleNamespace(String="String", Integer="Integer",
                                         Double="Double", ElementId="ElementId"),
        TransactionStatus=types.SimpleNamespace(
            Uninitialized="Uninitialized", Started="Started",
            Committed="Committed", RolledBack="RolledBack", Pending="Pending"),
        FailureSeverity=types.SimpleNamespace(Error="Error", Warning="Warning",
                                             DocumentCorruption="DocumentCorruption"),
        FailureProcessingResult=types.SimpleNamespace(
            Continue="Continue", ProceedWithRollBack="ProceedWithRollBack"),
        IFailuresPreprocessor=object, ElementId=ElementId, ViewSheet=Sheet,
        FilteredElementCollector=Collector, Transaction=Transaction,
        TransactionGroup=TransactionGroup,
        SubTransaction=lambda doc: Transaction(doc, "Subtransaction"))
    pyrevit = types.ModuleType("pyrevit")
    pyrevit.DB = db
    pyrevit.HOST_APP = types.SimpleNamespace()
    pyrevit.framework = types.SimpleNamespace(get_type=lambda cls: cls)
    pyrevit.revit = types.SimpleNamespace(
        Transaction=lambda name, doc: Transaction(doc, name),
        TransactionGroup=lambda name, doc: TransactionGroup(doc, name))
    pyrevit.script = types.SimpleNamespace(get_logger=lambda: None)
    with mock.patch.dict(sys.modules, {"pyrevit": pyrevit}), \
            mock.patch.object(sys, "path", [str(COMMAND), str(ROOT / "lib")] + sys.path):
        spec = importlib.util.spec_from_file_location(
            "sheet_manager_creation_under_test", COMMAND / "sheet_manager_revit.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.clr_id_list_factory = lambda: IdList
    return module


class SheetCreationTests(unittest.TestCase):
    def setUp(self):
        self.api = load_revit_module()
        self.doc = Document()

    def create(self):
        return self.api._create_sheet_from_template(
            self.doc, self.doc.sheets[1], self.doc.tblocks[1][0], "E1", "Details")

    def apply(self, numbers=("E1", "E2"), template=True):
        rows = [self.api.state.SheetRowBase(None, n, "Details") for n in numbers]
        for row in rows:
            row.template_sheet_id = 1 if template else None
        changes = self.api.state.StagedChanges()
        changes.pending_sheets = rows
        results = self.api.state.ApplyResults()
        sheets_by_id = dict(self.doc.sheets)
        tb_map = dict(self.doc.tblocks)
        self.api._apply_creates(self.doc, changes, sheets_by_id, tb_map,
                                results, ElementId)
        return rows, results, sheets_by_id

    def test_template_copy_never_writes_sheet_identity_through_titleblock(self):
        sheet = self.create()
        self.assertEqual((sheet.SheetNumber, sheet.Name), ("E1", "Details"))
        self.assertFalse([write for write in self.doc.parameter_writes
                          if write[0] in (NUMBER_ID, NAME_ID)])
        self.assertEqual(self.doc.identities,
                         [(2, "number", "E1"), (2, "name", "Details"),
                          (2, "number", "E1"), (2, "name", "Details")])

    def test_name_lookup_collision_cannot_write_target_titleblock_identity(self):
        # A project parameter with the same name can resolve to a built-in target.
        for p in self.doc.tblocks[1][0].Parameters:
            if p.Id in (NUMBER_ID, NAME_ID):
                p.Id = 300 + abs(p.Id)
        self.create()
        self.assertFalse([write for write in self.doc.parameter_writes
                          if write[0] in (NUMBER_ID, NAME_ID)])

    def test_excel_identity_precedes_regeneration_and_collection_changes(self):
        self.doc.new_collection_id = 42
        sheet = self.create()
        self.assertEqual(sheet.SheetCollectionId, -1)
        self.assertTrue(self.doc.regenerated_identities)
        self.assertTrue(all(pair == ("E1", "Details")
                            for pair in self.doc.regenerated_identities))
        self.assertEqual(self.doc.collection_identities, [("E1", "Details")])

    def test_copies_supported_values_and_additional_revisions_on_older_host(self):
        self.doc.older_host = True
        sheet = self.create()
        titleblock = self.doc.tblocks[sheet.Id][0]
        self.assertEqual(titleblock.GetTypeId(), 50)
        self.assertEqual(sheet.LookupParameter("Drawn By").value, "AB")
        self.assertEqual(titleblock.LookupParameter("Office").value, "Seattle")
        self.assertEqual(titleblock.LookupParameter("Show Stamp").value, 1)
        self.assertEqual(titleblock.LookupParameter("Offset").value, 2.5)
        self.assertEqual(titleblock.LookupParameter("Reference").value, 0)
        self.assertEqual(sheet.revisions, [11, 22])

    def test_each_row_is_committed_before_becoming_applied(self):
        rows = [self.api.state.SheetRowBase(None, n, "Details")
                for n in ("E1", "E2")]
        for row in rows:
            row.template_sheet_id = 1
        observed = []
        self.doc.before_commit = lambda: observed.append(
            [(row.sheet_id, row.is_pending) for row in rows])
        changes = self.api.state.StagedChanges()
        changes.pending_sheets = rows
        results = self.api.state.ApplyResults()
        self.api._apply_creates(self.doc, changes, dict(self.doc.sheets),
                                dict(self.doc.tblocks), results, ElementId)
        self.assertEqual(observed, [[(None, True), (None, True)],
                                    [(2, False), (None, True)]])
        self.assertEqual(results.created_count, 2)
        self.assertEqual([self.doc.sheets[row.sheet_id].SheetNumber for row in rows],
                         ["E1", "E2"])
        self.assertEqual(results.errors, [])

    def test_exception_after_create_leaves_no_orphan_and_row_can_retry(self):
        self.doc.fail_revisions.add("E1")
        rows, results, index = self.apply()
        self.assertEqual(sorted(s.SheetNumber for s in self.doc.sheets.values()),
                         ["E2", "T001"])
        self.assertIsNone(rows[0].sheet_id)
        self.assertTrue(rows[0].is_pending)
        self.assertEqual(results.created_count, 1)
        self.assertEqual(set(index), set(self.doc.sheets))
        self.assertEqual(results.errors[0].sheet, "E1")
        self.assertIn("revision", results.errors[0].new_value.lower())
        self.doc.fail_revisions.clear()
        changes = self.api.state.StagedChanges()
        changes.pending_sheets = [rows[0]]
        self.api._apply_creates(self.doc, changes, index, dict(self.doc.tblocks),
                                results, ElementId)
        self.assertFalse(rows[0].is_pending)
        self.assertEqual(results.created_count, 2)

    def test_commit_rollback_retains_failed_row_without_false_success(self):
        self.doc.fail_commit.add("E1")
        rows, results, index = self.apply()
        self.assertEqual(results.created_count, 1)
        self.assertIsNone(rows[0].sheet_id)
        self.assertTrue(rows[0].is_pending)
        self.assertEqual(set(index), set(self.doc.sheets))
        self.assertEqual([item.sheet for item in results.sheet_changes], ["E2"])
        self.assertEqual(results.applied_cells, [(rows[1], "number"), (rows[1], "name")])
        self.assertEqual(results.errors[0].sheet, "E1")
        self.assertIn("commit", results.errors[0].new_value.lower())
        self.assertIn("Sheet Number is already in use", results.errors[0].new_value)

    def test_commit_exception_rolls_back_before_continuing(self):
        self.doc.throw_commit.add("E1")
        rows, results, index = self.apply()
        self.assertEqual(results.created_count, 1)
        self.assertTrue(rows[0].is_pending)
        self.assertEqual(sorted(s.SheetNumber for s in self.doc.sheets.values()),
                         ["E2", "T001"])
        self.assertEqual(set(index), set(self.doc.sheets))
        self.assertIn("Commit exception", results.errors[0].new_value)

    def test_default_sheet_creation_also_waits_for_commit(self):
        self.doc.fail_commit.add("E1")
        rows, results, index = self.apply(template=False)
        self.assertEqual(results.created_count, 1)
        self.assertTrue(rows[0].is_pending)
        self.assertEqual(set(index), set(self.doc.sheets))

    def test_does_not_accept_identity_changed_during_final_regeneration(self):
        def change_name():
            for key, sheet in self.doc.sheets.items():
                if key != 1:
                    sheet.Name = "Changed by updater"
        self.doc.after_regenerate = change_name
        rows, results, index = self.apply(numbers=("E1",))
        self.assertEqual(results.created_count, 0)
        self.assertTrue(rows[0].is_pending)
        self.assertEqual(set(self.doc.sheets), {1})
        self.assertEqual(set(index), {1})
        self.assertIn("Verify Excel sheet number/name", results.errors[0].new_value)

    def test_unfinished_rollback_stops_before_creating_next_row(self):
        self.doc.fail_revisions.add("E1")
        rollback = Transaction.RollBack

        def delayed_rollback(transaction):
            rollback(transaction)
            transaction.status = "Pending"
            return "RolledBack"

        with mock.patch.object(Transaction, "RollBack", delayed_rollback):
            with self.assertRaisesRegex(RuntimeError, "E1"):
                self.apply()
        self.assertEqual(set(self.doc.sheets), {1})

    def test_template_batch_returns_only_committed_sheets(self):
        self.doc.fail_commit.add("E1")
        imports = [types.SimpleNamespace(sheet_number=n, sheet_name="Details")
                   for n in ("E1", "E2")]
        created, failures = self.api.create_sheets_from_template(self.doc, 1, imports)
        self.assertEqual([sheet.SheetNumber for sheet in created], ["E2"])
        self.assertEqual(len(failures), 1)
        self.assertIn("E1", failures[0])
        self.assertIn("Sheet Number is already in use", failures[0])

    def test_failure_preprocessor_keeps_warnings_and_reports_errors(self):
        handler = self.api._SheetCreationFailures()
        messages = []
        accessor = types.SimpleNamespace(GetFailureMessages=lambda: messages)
        messages.append(types.SimpleNamespace(GetSeverity=lambda: "Warning"))
        self.assertEqual(handler.PreprocessFailures(accessor), "Continue")
        messages.append(types.SimpleNamespace(
            GetSeverity=lambda: "Error", GetDescriptionText=lambda: "Duplicate sheet"))
        self.assertEqual(handler.PreprocessFailures(accessor), "ProceedWithRollBack")
        self.assertEqual(handler.messages, ["Duplicate sheet"])

    def test_group_abort_restores_all_created_rows_for_retry(self):
        changes = self.api.state.StagedChanges()
        changes.pending_sheets = [self.api.state.SheetRowBase(None, n, "Details")
                                  for n in ("E1", "E2")]
        self.doc.throw_commit.add("E2")
        sheets_by_id = dict(self.doc.sheets)
        rollback = Transaction.RollBack
        failed = []

        def fail_once(transaction):
            if transaction.name.endswith(" E2") and not failed:
                failed.append(True)
                raise RuntimeError("Rollback failed")
            return rollback(transaction)

        with mock.patch.object(Transaction, "RollBack", fail_once):
            with self.assertRaisesRegex(RuntimeError, "Rollback failed"):
                self.api.apply_staged_changes(
                    self.doc, changes, sheets_by_id, dict(self.doc.tblocks), {})
        self.assertEqual(set(self.doc.sheets), {1})
        self.assertEqual(set(sheets_by_id), {1})
        self.assertEqual([(r.sheet_id, r.is_pending) for r in changes.pending_sheets],
                         [(None, True), (None, True)])

    def test_group_finalization_failure_does_not_report_applied_rows(self):
        changes = self.api.state.StagedChanges()
        row = self.api.state.SheetRowBase(None, "E1", "Details")
        changes.pending_sheets = [row]
        sheets_by_id = dict(self.doc.sheets)
        self.doc.fail_group = True
        with self.assertRaisesRegex(ValueError, "commit"):
            self.api.apply_staged_changes(
                self.doc, changes, sheets_by_id, dict(self.doc.tblocks), {})
        self.assertEqual((row.sheet_id, row.is_pending), (None, True))
        self.assertEqual(set(self.doc.sheets), {1})
        self.assertEqual(set(sheets_by_id), {1})

    def test_apply_group_preserves_successes_and_leaves_failed_excel_rows_staged(self):
        changes = self.api.state.StagedChanges()
        rows = [self.api.state.SheetRowBase(None, n, "Details")
                for n in ("E1", "E2")]
        batch = self.api.state.CustomizedExcelBatch()
        for row in rows:
            row.template_sheet_id = 1
            batch.include_pending_row(row)
        changes.pending_sheets = rows
        self.doc.fail_commit.add("E1")
        sheets_by_id = dict(self.doc.sheets)
        results = self.api.apply_staged_changes(
            self.doc, changes, sheets_by_id, dict(self.doc.tblocks), {})
        batch.record_applied(results.applied_cells)
        self.assertEqual(results.created_count, 1)
        self.assertEqual(set(sheets_by_id), set(self.doc.sheets))
        self.assertEqual(sorted(s.SheetNumber for s in self.doc.sheets.values()),
                         ["E2", "T001"])
        self.assertEqual(batch.select(changes).pending_sheets, [rows[0]])

    def test_stale_template_reports_row_error_and_continues_other_rows(self):
        class StaleTemplate:
            @property
            def IsPlaceholder(self):
                raise RuntimeError("Template was deleted")

        changes = self.api.state.StagedChanges()
        changes.pending_sheets = [self.api.state.SheetRowBase(None, n, "Details")
                                  for n in ("E1", "E2")]
        changes.pending_sheets[0].template_sheet_id = 1
        results = self.api.state.ApplyResults()
        self.api._apply_creates(self.doc, changes, {1: StaleTemplate()},
                                dict(self.doc.tblocks), results, ElementId)
        self.assertTrue(changes.pending_sheets[0].is_pending)
        self.assertEqual(results.created_count, 1)
        self.assertEqual(results.errors[0].sheet, "E1")
        self.assertIn("Template was deleted", results.errors[0].new_value)


if __name__ == "__main__":
    unittest.main()
