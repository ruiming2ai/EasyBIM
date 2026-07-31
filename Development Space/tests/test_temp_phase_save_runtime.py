import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_modules():
    lib_root = ROOT / "lib" / "easybim"
    package = types.ModuleType("easybim")
    package.__path__ = [str(lib_root)]
    sys.modules["easybim"] = package

    close_spec = importlib.util.spec_from_file_location(
        "easybim.temp_phase_close", str(lib_root / "temp_phase_close.py")
    )
    close_module = importlib.util.module_from_spec(close_spec)
    sys.modules["easybim.temp_phase_close"] = close_module
    close_spec.loader.exec_module(close_module)
    package.temp_phase_close = close_module

    save_spec = importlib.util.spec_from_file_location(
        "easybim.temp_phase_save", str(lib_root / "temp_phase_save.py")
    )
    save_module = importlib.util.module_from_spec(save_spec)
    sys.modules["easybim.temp_phase_save"] = save_module
    save_spec.loader.exec_module(save_module)
    package.temp_phase_save = save_module
    return close_module, save_module


class FakeDocument(object):
    IsValidObject = True
    IsFamilyDocument = False
    IsLinked = False
    PathName = r"C:\Models\sample.rvt"
    DocumentId = 42
    Title = "sample.rvt"

    def GetHashCode(self):
        return 4200


class FakeSaveArgs(object):
    Cancellable = True

    def __init__(self, doc, status=None):
        self.Document = doc
        self.DocumentId = doc.DocumentId
        self.Status = status
        self.cancel_calls = 0

    def Cancel(self):
        self.cancel_calls += 1


class FakeApplication(object):
    IsClosing = False

    def __init__(self, doc):
        self.Documents = [doc]


class FakeUidoc(object):
    def __init__(self, doc):
        self.Document = doc


class FakeUiapp(object):
    def __init__(self, doc):
        self.ActiveUIDocument = FakeUidoc(doc)
        self.Application = FakeApplication(doc)
        self.posted = []

    def CanPostCommand(self, command_id):
        return True

    def PostCommand(self, command_id):
        self.posted.append(command_id)


class TempPhaseSaveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.close, self.save = _load_modules()
        self.doc = FakeDocument()
        self.uiapp = FakeUiapp(self.doc)
        self.save._MEMORY_STATE.clear()
        self.save._RUNTIME.pop("registration", None)

    def _fake_ui(self):
        class FakeCommandId(object):
            @staticmethod
            def LookupPostableCommandId(command):
                return ("command", command)

        class FakePostableCommand(object):
            Save = "Save"
            SaveAs = "SaveAs"

        return type(
            "FakeUi",
            (object,),
            {
                "RevitCommandId": FakeCommandId,
                "PostableCommand": FakePostableCommand,
            },
        )

    def test_save_is_cancelled_and_queued_when_temp_state_exists(self):
        args = FakeSaveArgs(self.doc)
        with mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": True},
        ):
            self.assertTrue(self.save.handle_document_saving(self.uiapp, args))

        self.assertEqual(1, args.cancel_calls)
        state = self.save._get_state()
        self.assertEqual(1, len(state["pending_saves"]))

    def test_save_without_temp_state_passes_through(self):
        args = FakeSaveArgs(self.doc)
        with mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": False},
        ):
            self.assertFalse(self.save.handle_document_saving(self.uiapp, args))
        self.assertEqual(0, args.cancel_calls)

    def test_idling_restores_and_reposts_save_once(self):
        args = FakeSaveArgs(self.doc)
        with mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": True},
        ):
            self.save.handle_document_saving(self.uiapp, args)

        with mock.patch.object(self.save, "_get_ui", return_value=self._fake_ui()), mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": True},
        ), mock.patch.object(
            self.save,
            "_restore_summary",
            return_value={"ok": True, "restored_phase_views": 1, "disabled_untracked_tvp_views": 1},
        ):
            self.assertTrue(self.save.handle_app_idling(self.uiapp))

        self.assertEqual(1, len(self.uiapp.posted))
        state = self.save._get_state()
        self.assertFalse(state["pending_saves"])
        self.assertTrue(state["save_guards"])

    def test_reposted_save_is_allowed_once(self):
        args = FakeSaveArgs(self.doc)
        with mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": True},
        ):
            self.save.handle_document_saving(self.uiapp, args)

        state = self.save._get_state()
        token = list(state["pending_saves"])[0]
        state["pending_saves"].pop(token)
        state["save_guards"][token] = {
            "action": "save",
            "expires_at": self.save.time.time() + 5,
        }
        repost_args = FakeSaveArgs(self.doc)
        self.assertFalse(self.save.handle_document_saving(self.uiapp, repost_args))
        self.assertEqual(0, repost_args.cancel_calls)
        self.assertFalse(self.save._get_state()["save_guards"])

    def test_cancelled_first_save_completion_preserves_pending_request(self):
        args = FakeSaveArgs(self.doc)
        with mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": True},
        ):
            self.save.handle_document_saving(self.uiapp, args)

        self.save.handle_document_saved(
            self.uiapp,
            FakeSaveArgs(self.doc, status="Cancelled"),
            action="save",
        )
        self.assertTrue(self.save._get_state()["pending_saves"])

    def test_save_as_uses_save_as_command(self):
        args = FakeSaveArgs(self.doc)
        with mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": True},
        ):
            self.save.handle_document_saving(self.uiapp, args, action="save_as")

        with mock.patch.object(self.save, "_get_ui", return_value=self._fake_ui()), mock.patch.object(
            self.save,
            "_collect_summary",
            return_value={"has_restore_work": False},
        ):
            self.save.handle_app_idling(self.uiapp)

        self.assertEqual("SaveAs", self.uiapp.posted[0][1])


if __name__ == "__main__":
    unittest.main()
