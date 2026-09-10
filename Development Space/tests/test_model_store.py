"""The record the checkers keep inside the .rvt, driven over fakes.

Extensible Storage is faked closely enough to judge the real questions: is
the schema looked up before it is built, is exactly one transaction opened,
is it rolled back when the write throws, and does one tool's list survive
the other tool writing its own.
"""

import importlib.util
import json
import pathlib
import sys
import types
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "lib" / "easybim"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(LIB_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = _load("model_store")


# ---- the .NET names model_store reaches for, stubbed for CPython ----------

class _Guid(object):
    def __init__(self, text):
        self.text = text

    def __eq__(self, other):
        return isinstance(other, _Guid) and other.text == self.text

    def __hash__(self):
        return hash(self.text)


sys.modules.setdefault("System", types.ModuleType("System"))
sys.modules["System"].Guid = _Guid
sys.modules["System"].String = str
_clr = types.ModuleType("clr")
_clr.GetClrType = lambda value: value
sys.modules.setdefault("clr", _clr)


# ---------------------------------------------------------------- fakes


class Field(object):
    def __init__(self, name):
        self.name = name


class Schema(object):
    def __init__(self, guid, fields=("payload",)):
        self.guid = guid
        self._fields = list(fields)

    def GetField(self, name):
        return Field(name) if name in self._fields else None


class SchemaRegistry(object):
    """Schemas are registered per Revit session, not per document."""

    def __init__(self):
        self.by_guid = {}
        self.lookups = 0
        self.builds = 0

    def Lookup(self, guid):
        self.lookups += 1
        return self.by_guid.get(guid.text)


class SchemaBuilder(object):
    def __init__(self, guid, registry):
        self.guid = guid
        self._registry = registry
        self._fields = []

    def SetSchemaName(self, name):
        pass

    def SetVendorId(self, name):
        pass

    def SetReadAccessLevel(self, level):
        pass

    def SetWriteAccessLevel(self, level):
        pass

    def AddSimpleField(self, name, clr_type):
        self._fields.append(name)

    def Finish(self):
        self._registry.builds += 1
        schema = Schema(self.guid, self._fields)
        self._registry.by_guid[self.guid.text] = schema
        return schema


class Entity(object):
    def __init__(self, schema, value=None):
        self.schema = schema
        self.value = value

    def IsValid(self):
        return self.schema is not None

    class _Getter(object):
        def __init__(self, entity):
            self.entity = entity

        def __getitem__(self, _type):
            return lambda field: self.entity.value

    class _Setter(object):
        def __init__(self, entity):
            self.entity = entity

        def __getitem__(self, _type):
            def _set(field, value):
                self.entity.value = value
            return _set

    @property
    def Get(self):
        return Entity._Getter(self)

    @property
    def Set(self):
        return Entity._Setter(self)


class DataStorageElement(object):
    def __init__(self, element_id):
        self.Id = element_id
        self.entity = None

    def GetEntity(self, schema):
        if self.entity is None or self.entity.schema is not schema:
            return Entity(None)
        return self.entity

    def SetEntity(self, entity):
        self.entity = entity


class Doc(object):
    def __init__(self, family=False, linked=False):
        self.IsFamilyDocument = family
        self.IsLinked = linked
        self.elements = []
        self.next_id = 1


class Collector(object):
    def __init__(self, doc):
        self.doc = doc
        self.items = list(doc.elements)

    def OfClass(self, klass):
        self.items = [item for item in self.items if isinstance(item, klass)]
        return self

    def __iter__(self):
        return iter(self.items)


class Transaction(object):
    log = []

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.started = False
        self.committed = False
        self.rolled_back = False
        Transaction.log.append(self)

    def Start(self):
        self.started = True

    def Commit(self):
        self.committed = True

    def RollBack(self):
        self.rolled_back = True


def make_db(registry=None, create_fails=False, set_fails=False, no_storage=False):
    registry = registry or SchemaRegistry()

    def _create(doc):
        if create_fails:
            raise Exception("no permission to add elements")
        element = DataStorageElement(doc.next_id)
        doc.next_id += 1
        doc.elements.append(element)
        return element

    # One class for every fake document, the way Revit has one DataStorage:
    # a per-db subclass would make a storage element written by one db
    # invisible to the next, which is a fake artefact, not a real failure.
    DataStorageElement.Create = staticmethod(_create)

    def _entity(schema):
        if set_fails:
            class _Bad(Entity):
                @property
                def Set(self):
                    raise Exception("field is read-only")

            return _Bad(schema)
        return Entity(schema)

    db = types.SimpleNamespace(FilteredElementCollector=Collector, Transaction=Transaction)
    if not no_storage:
        db.ExtensibleStorage = types.SimpleNamespace(
            Schema=registry,
            SchemaBuilder=lambda guid: SchemaBuilder(guid, registry),
            AccessLevel=types.SimpleNamespace(Public=1),
            DataStorage=DataStorageElement,
            Entity=_entity,
        )
    db._registry = registry
    return db


def payload_of(doc):
    for element in doc.elements:
        if element.entity is not None:
            return json.loads(element.entity.value)
    return None


class SchemaTests(unittest.TestCase):
    def setUp(self):
        Transaction.log = []

    def test_the_schema_is_looked_up_before_it_is_built_and_never_built_twice(self):
        db = make_db()
        first, reason = store.get_schema(db)
        self.assertIsNotNone(first, reason)
        second, _reason = store.get_schema(db)
        self.assertIs(second, first)
        self.assertEqual(db._registry.builds, 1)

    def test_a_foreign_schema_under_the_same_id_is_reported_not_guessed(self):
        registry = SchemaRegistry()
        registry.by_guid[store.SCHEMA_GUID] = Schema(_Guid(store.SCHEMA_GUID), fields=("something_else",))
        schema, reason = store.get_schema(make_db(registry))
        self.assertIsNone(schema)
        self.assertIn("Another add-in", reason)

    def test_a_revit_without_extensible_storage_says_so(self):
        schema, reason = store.get_schema(make_db(no_storage=True))
        self.assertIsNone(schema)
        self.assertIn("no Extensible Storage", reason)

    def test_availability_refuses_family_and_linked_documents(self):
        db = make_db()
        self.assertEqual(store.availability(None, db=db)[0], False)
        self.assertIn("family", store.availability(Doc(family=True), db=db)[1])
        self.assertIn("linked", store.availability(Doc(linked=True), db=db)[1])
        self.assertTrue(store.availability(Doc(), db=db)[0])


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        Transaction.log = []
        self.db = make_db()
        self.doc = Doc()

    def test_an_empty_model_reads_as_nothing_ignored(self):
        self.assertEqual(store.read(self.doc, "damper_check", db=self.db), set())
        self.assertEqual(Transaction.log, [])  # reading never opens one

    def test_a_write_opens_exactly_one_transaction_and_commits(self):
        ok, reason = store.write(self.doc, "damper_check", [u"terminal:5", u"terminal:9"], db=self.db)
        self.assertTrue(ok, reason)
        self.assertEqual(len(Transaction.log), 1)
        self.assertTrue(Transaction.log[0].committed)
        self.assertFalse(Transaction.log[0].rolled_back)
        self.assertEqual(Transaction.log[0].name, store.TRANSACTION_NAME)
        self.assertEqual(store.read(self.doc, "damper_check", db=self.db),
                         set([u"terminal:5", u"terminal:9"]))

    def test_the_second_write_reuses_the_one_storage_element(self):
        store.write(self.doc, "damper_check", [u"terminal:5"], db=self.db)
        store.write(self.doc, "damper_check", [u"terminal:6"], db=self.db)
        self.assertEqual(len(self.doc.elements), 1)
        self.assertEqual(store.read(self.doc, "damper_check", db=self.db), set([u"terminal:6"]))

    def test_one_tool_never_clobbers_the_other(self):
        store.write(self.doc, "damper_check", [u"terminal:5"], db=self.db)
        store.write(self.doc, "fire_damper_check", [u"cross:a:1"], db=self.db)
        self.assertEqual(store.read(self.doc, "damper_check", db=self.db), set([u"terminal:5"]))
        self.assertEqual(store.read(self.doc, "fire_damper_check", db=self.db), set([u"cross:a:1"]))
        self.assertEqual(sorted(payload_of(self.doc)["tools"]), ["damper_check", "fire_damper_check"])

    def test_set_ignored_adds_and_removes_and_hands_back_what_the_model_holds(self):
        ok, keys, reason = store.set_ignored(self.doc, "damper_check", u"terminal:5", True, db=self.db)
        self.assertTrue(ok, reason)
        self.assertEqual(keys, set([u"terminal:5"]))
        ok, keys, _reason = store.set_ignored(self.doc, "damper_check", u"terminal:5", False, db=self.db)
        self.assertTrue(ok)
        self.assertEqual(keys, set())

    def test_setting_what_is_already_set_writes_nothing(self):
        store.set_ignored(self.doc, "damper_check", u"terminal:5", True, db=self.db)
        Transaction.log = []
        ok, keys, _reason = store.set_ignored(self.doc, "damper_check", u"terminal:5", True, db=self.db)
        self.assertTrue(ok)
        self.assertEqual(keys, set([u"terminal:5"]))
        self.assertEqual(Transaction.log, [])

    def test_a_finding_with_no_key_is_refused(self):
        ok, keys, reason = store.set_ignored(self.doc, "damper_check", u"  ", True, db=self.db)
        self.assertFalse(ok)
        self.assertEqual(keys, set())
        self.assertIn("no stable key", reason)


class FailureTests(unittest.TestCase):
    def setUp(self):
        Transaction.log = []

    def test_a_refused_write_rolls_back_and_names_the_reason(self):
        db = make_db(create_fails=True)
        doc = Doc()
        ok, reason = store.write(doc, "damper_check", [u"terminal:5"], db=db)
        self.assertFalse(ok)
        self.assertIn("permission", reason)
        self.assertTrue(Transaction.log[0].rolled_back)
        self.assertFalse(Transaction.log[0].committed)

    def test_a_failed_write_leaves_the_stored_list_alone(self):
        db = make_db()
        doc = Doc()
        store.write(doc, "damper_check", [u"terminal:5"], db=db)
        broken = make_db(db._registry, set_fails=True)
        ok, keys, reason = store.set_ignored(doc, "damper_check", u"terminal:9", True, db=broken)
        self.assertFalse(ok)
        self.assertEqual(keys, set([u"terminal:5"]))  # what the model still holds
        self.assertTrue(reason)

    def test_a_family_document_is_refused_before_any_transaction(self):
        db = make_db()
        ok, reason = store.write(Doc(family=True), "damper_check", [u"x"], db=db)
        self.assertFalse(ok)
        self.assertIn("family", reason)
        self.assertEqual(Transaction.log, [])


class PayloadTests(unittest.TestCase):
    def test_normalize_survives_anything(self):
        self.assertEqual(store.normalize(None)["tools"], {})
        self.assertEqual(store.normalize("nonsense")["tools"], {})
        self.assertEqual(store.normalize({"tools": {"a": "not a list"}})["tools"], {})
        cleaned = store.normalize({"tools": {"a": [u"b", u"a", u"a", u"", None]}})
        self.assertEqual(cleaned["tools"]["a"], [u"a", u"b"])

    def test_an_unreadable_payload_reads_as_empty(self):
        db = make_db()
        doc = Doc()
        store.write(doc, "damper_check", [u"terminal:5"], db=db)
        for element in doc.elements:
            element.entity.value = "{not json"
        self.assertEqual(store.read(doc, "damper_check", db=db), set())

    def test_a_newer_payload_is_read_as_far_as_it_is_understood(self):
        db = make_db()
        doc = Doc()
        store.write(doc, "damper_check", [u"terminal:5"], db=db)
        for element in doc.elements:
            element.entity.value = json.dumps(
                {"version": 99, "tools": {"damper_check": [u"terminal:5"]}, "future": {"x": 1}})
        self.assertEqual(store.read(doc, "damper_check", db=db), set([u"terminal:5"]))


if __name__ == "__main__":
    unittest.main()
