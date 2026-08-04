import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest


COMMAND_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab"
    / "Misc Tools.panel"
    / "Tag Align.pushbutton"
)


def _load(name):
    """Load a command module standalone, the way pyRevit puts its folder on
    sys.path at runtime."""
    if str(COMMAND_DIR) not in sys.path:
        sys.path.insert(0, str(COMMAND_DIR))
    spec = importlib.util.spec_from_file_location(
        name, str(COMMAND_DIR / "{0}.py".format(name))
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


state = _load("tag_align_state")
presets = _load("tag_align_presets")


def make_session(reference_count=1, **options):
    session = state.TagAlignSession()
    session.references = [
        state.ReferenceRule(
            tag_family_name="Door Tag",
            tag_type_name="Standard",
            tag_type_label="Door Tag : Standard",
            tag_category_signature="OST_DoorTags",
            host_category_signature="OST_Doors",
            host_category_name="Doors",
            host_family_name="Single-Flush",
            host_type_label="900 x {0}".format(index),
            view_scale=100,
            has_direction=True,
            angle_deg=float(index * 10),
            offset_view=(0.25, 2.5),
            offset_local=(0.25, 2.5),
            offset_normal=1.5,
        )
        for index in range(reference_count)
    ]
    for key, value in options.items():
        setattr(session, key, value)
    return session


def make_preset(session, name, saved_at=""):
    return state.preset_to_dict(session, name, saved_at=saved_at, saved_by="RML")


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.local = os.path.join(self.folder, "local.json")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def library(self, **kwargs):
        kwargs.setdefault("local_path", self.local)
        return presets.build_library(**kwargs)


class SourceAvailabilityTests(TempDirTestCase):
    def test_the_three_sources_are_always_listed(self):
        keys = [source.key for source in self.library().sources]
        self.assertEqual(
            [presets.SOURCE_LOCAL, presets.SOURCE_MODEL, presets.SOURCE_SHARED], keys
        )

    def test_model_and_shared_start_unavailable_with_a_reason(self):
        library = self.library()
        for key in (presets.SOURCE_MODEL, presets.SOURCE_SHARED):
            source = library.source(key)
            self.assertFalse(source.available, key)
            self.assertTrue(source.unavailable_reason, key)

    def test_model_source_carries_the_reason_it_was_given(self):
        library = self.library(model_unavailable_reason="This model is open read-only.")
        source = library.source(presets.SOURCE_MODEL)
        self.assertFalse(source.available)
        self.assertIn("read-only", source.unavailable_reason)

    def test_saving_to_an_unavailable_source_explains_itself(self):
        library = self.library()
        ok, error = library.save(presets.SOURCE_MODEL, make_preset(make_session(), "x"))
        self.assertFalse(ok)
        self.assertIn("document", error.lower())


class FileRoundTripTests(TempDirTestCase):
    def test_save_then_read_back_in_a_fresh_library(self):
        session = make_session()
        ok, error = self.library().save(presets.SOURCE_LOCAL, make_preset(session, "Doors"))
        self.assertTrue(ok, error)

        reopened = self.library()
        self.assertEqual(["Doors"], [info.name for info in reopened.infos()])

    def test_offsets_survive_the_round_trip_exactly(self):
        session = make_session()
        session.references[0].offset_view = (0.123456789, -9.87654321)
        self.library().save(presets.SOURCE_LOCAL, make_preset(session, "Precise"))

        loaded = self.library().get(presets.SOURCE_LOCAL, "Precise")
        restored = state.apply_preset(state.TagAlignSession(), loaded)
        self.assertAlmostEqual(0.123456789, restored.references[0].offset_view[0], places=9)
        self.assertAlmostEqual(-9.87654321, restored.references[0].offset_view[1], places=9)

    def test_options_and_scope_survive_the_round_trip(self):
        session = make_session(
            scope=state.SCOPE_EXACT_TYPE,
            scale_with_view=False,
            copy_leader=False,
            include_pinned=True,
            rotate_fallback=True,
            any_orientation=True,
            reference_mode=state.MODE_SINGLE,
        )
        self.library().save(presets.SOURCE_LOCAL, make_preset(session, "Options"))

        restored = state.apply_preset(
            state.TagAlignSession(), self.library().get(presets.SOURCE_LOCAL, "Options")
        )
        self.assertEqual(state.SCOPE_EXACT_TYPE, restored.scope)
        self.assertFalse(restored.scale_with_view)
        self.assertFalse(restored.copy_leader)
        self.assertTrue(restored.include_pinned)
        self.assertTrue(restored.rotate_fallback)
        self.assertTrue(restored.any_orientation)
        self.assertEqual(state.MODE_SINGLE, restored.reference_mode)

    def test_a_preset_never_carries_element_ids(self):
        session = make_session()
        session.references[0].tag_id = 123
        session.references[0].host_id = 456
        session.references[0].tag_type_id = 789

        text = json.dumps(make_preset(session, "Portable"))
        for value in ("123", "456", "789"):
            self.assertNotIn(value, text)

    def test_batch_ids_are_not_saved(self):
        session = make_session()
        session.batch_ids = [1, 2, 3]
        self.assertNotIn("batch_ids", make_preset(session, "x"))

    def test_saving_the_same_name_replaces_rather_than_duplicates(self):
        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(1), "Doors"))
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(3), "Doors"))

        rows = self.library().infos()
        self.assertEqual(1, len(rows))
        self.assertEqual(3, rows[0].reference_count)

    def test_delete_removes_only_the_named_preset(self):
        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "Keep"))
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "Drop"))

        ok, error = library.delete(presets.SOURCE_LOCAL, "Drop")
        self.assertTrue(ok, error)
        self.assertEqual(["Keep"], [info.name for info in self.library().infos()])

    def test_rename_keeps_the_content(self):
        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(2), "Old"))

        ok, error = library.rename(presets.SOURCE_LOCAL, "Old", "New")
        self.assertTrue(ok, error)

        rows = self.library().infos()
        self.assertEqual(["New"], [info.name for info in rows])
        self.assertEqual(2, rows[0].reference_count)

    def test_rename_refuses_an_empty_or_reserved_name(self):
        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "Old"))
        self.assertFalse(library.rename(presets.SOURCE_LOCAL, "Old", "   ")[0])
        self.assertFalse(
            library.rename(presets.SOURCE_LOCAL, "Old", state.LAST_USED_NAME)[0]
        )


class LastUsedTests(TempDirTestCase):
    def test_last_used_is_pinned_above_everything_else(self):
        library = self.library()
        library.save(
            presets.SOURCE_LOCAL, make_preset(make_session(), "AAA", saved_at="2099-01-01")
        )
        library.save(
            presets.SOURCE_LOCAL,
            make_preset(make_session(), state.LAST_USED_NAME, saved_at="1999-01-01"),
        )

        rows = self.library().infos()
        self.assertTrue(rows[0].is_last_used)
        self.assertEqual("AAA", rows[1].name)

    def test_last_used_cannot_be_deleted_or_renamed(self):
        library = self.library()
        library.save(
            presets.SOURCE_LOCAL, make_preset(make_session(), state.LAST_USED_NAME)
        )

        ok, error = library.delete(presets.SOURCE_LOCAL, state.LAST_USED_NAME)
        self.assertFalse(ok)
        self.assertIn("automatically", error)

        ok, error = library.rename(presets.SOURCE_LOCAL, state.LAST_USED_NAME, "Mine")
        self.assertFalse(ok)
        self.assertIn("automatically", error)

    def test_the_rest_sort_newest_first_then_by_name(self):
        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "old", saved_at="2026-01-01 09:00"))
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "zeta", saved_at="2026-08-01 09:00"))
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "alpha", saved_at="2026-08-01 09:00"))

        self.assertEqual(
            ["alpha", "zeta", "old"], [info.name for info in self.library().infos()]
        )


class SharedFolderTests(TempDirTestCase):
    def test_a_picked_folder_becomes_a_file_inside_it(self):
        resolved = presets.resolve_shared_file("/team/bim")
        self.assertTrue(resolved.endswith(presets.SHARED_FILE_NAME))

    def test_a_folder_that_does_not_exist_yet_still_resolves_to_a_file(self):
        resolved = presets.resolve_shared_file(os.path.join(self.folder, "not-created"))
        self.assertTrue(resolved.endswith(presets.SHARED_FILE_NAME))

    def test_an_explicit_json_path_is_left_alone(self):
        self.assertEqual(
            "/team/bim/custom.json", presets.resolve_shared_file("/team/bim/custom.json")
        )

    def test_a_trailing_separator_does_not_produce_an_empty_segment(self):
        self.assertEqual(
            os.path.join("/team/bim", presets.SHARED_FILE_NAME),
            presets.resolve_shared_file("/team/bim/"),
        )

    def test_the_shared_path_is_remembered_between_sessions(self):
        team = os.path.join(self.folder, "team")
        ok, error = self.library().set_shared_path(team)
        self.assertTrue(ok, error)

        reopened = self.library()
        self.assertEqual(team, reopened.shared_path())
        self.assertTrue(reopened.source(presets.SOURCE_SHARED).available)

    def test_presets_from_both_files_appear_together_with_their_source(self):
        team = os.path.join(self.folder, "team")
        self.library().set_shared_path(team)

        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "Mine", saved_at="2026-08-02"))
        library.save(presets.SOURCE_SHARED, make_preset(make_session(), "Ours", saved_at="2026-08-01"))

        rows = self.library().infos()
        self.assertEqual(
            [("Mine", presets.SOURCE_LOCAL), ("Ours", presets.SOURCE_SHARED)],
            [(info.name, info.source.key) for info in rows],
        )

    def test_the_same_name_in_two_sources_stays_two_rows(self):
        team = os.path.join(self.folder, "team")
        self.library().set_shared_path(team)

        library = self.library()
        library.save(presets.SOURCE_LOCAL, make_preset(make_session(), "Doors"))
        library.save(presets.SOURCE_SHARED, make_preset(make_session(), "Doors"))

        rows = self.library().infos()
        self.assertEqual(2, len(rows))
        self.assertEqual(
            set([presets.SOURCE_LOCAL, presets.SOURCE_SHARED]),
            set(info.source.key for info in rows),
        )

    def test_saving_creates_a_shared_folder_that_does_not_exist_yet(self):
        team = os.path.join(self.folder, "brand", "new")
        self.library().set_shared_path(team)

        ok, error = self.library().save(
            presets.SOURCE_SHARED, make_preset(make_session(), "Ours")
        )
        self.assertTrue(ok, error)
        self.assertTrue(os.path.isfile(os.path.join(team, presets.SHARED_FILE_NAME)))


class InModelSourceTests(TempDirTestCase):
    """The in-model source through injected callables - what the Revit layer
    supplies once Extensible Storage is wired in."""

    def setUp(self):
        TempDirTestCase.setUp(self)
        self.stored = [""]

    def _reader(self):
        return self.stored[0]

    def _writer(self, text):
        self.stored[0] = text
        return True, "Saved into the model."

    def test_round_trip_through_the_callables(self):
        library = self.library(model_reader=self._reader, model_writer=self._writer)
        ok, message = library.save(presets.SOURCE_MODEL, make_preset(make_session(2), "In model"))
        self.assertTrue(ok, message)
        self.assertTrue(self.stored[0])

        reopened = self.library(model_reader=self._reader, model_writer=self._writer)
        rows = reopened.infos()
        self.assertEqual(["In model"], [info.name for info in rows])
        self.assertEqual(presets.SOURCE_MODEL, rows[0].source.key)
        self.assertEqual(2, rows[0].reference_count)

    def test_a_writer_that_refuses_is_reported_not_raised(self):
        def _refuse(text):
            del text
            return False, "Another user has it checked out."

        library = self.library(model_reader=self._reader, model_writer=_refuse)
        ok, error = library.save(presets.SOURCE_MODEL, make_preset(make_session(), "x"))
        self.assertFalse(ok)
        self.assertIn("checked out", error)

    def test_a_reader_that_throws_degrades_to_no_presets(self):
        def _boom():
            raise RuntimeError("schema conflict")

        library = self.library(model_reader=_boom, model_writer=self._writer)
        self.assertEqual([], library.infos())
        self.assertTrue(any("schema conflict" in error for error in library.errors()))


class DamagedStoreTests(TempDirTestCase):
    def test_a_corrupt_file_reports_and_keeps_going(self):
        with open(self.local, "w") as handle:
            handle.write("{not json")

        library = self.library()
        self.assertEqual([], library.infos())
        self.assertTrue(library.errors())

    def test_one_broken_source_does_not_hide_the_other(self):
        team = os.path.join(self.folder, "team")
        self.library().set_shared_path(team)
        self.library().save(presets.SOURCE_SHARED, make_preset(make_session(), "Ours"))

        with open(self.local, "w") as handle:
            handle.write("<<<garbage")

        # The shared path lives in the local file, so pass it explicitly - the
        # point of the test is that a readable source still lists.
        library = self.library(shared_path=team)
        self.assertEqual(["Ours"], [info.name for info in library.infos()])

    def test_a_newer_format_is_refused_with_an_upgrade_message(self):
        with open(self.local, "w") as handle:
            json.dump({"format": 99, "presets": []}, handle)

        library = self.library()
        self.assertEqual([], library.infos())
        self.assertTrue(any("newer EasyBIM" in error for error in library.errors()))

    def test_a_payload_that_is_not_a_dict_is_refused(self):
        self.assertRaises(state.PresetFormatError, state.read_payload, ["nope"])

    def test_presets_that_are_not_dicts_are_dropped(self):
        payload = state.read_payload({"format": 1, "presets": [{"name": "ok"}, "junk", 7]})
        self.assertEqual([{"name": "ok"}], payload["presets"])

    def test_an_empty_store_reads_as_an_empty_payload(self):
        payload = state.read_payload(None)
        self.assertEqual([], payload["presets"])
        self.assertEqual(state.PRESET_FORMAT_VERSION, payload["format"])


if __name__ == "__main__":
    unittest.main()
