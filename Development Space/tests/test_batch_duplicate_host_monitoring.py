import ast
import importlib.util
import pathlib
import sys
import unittest
from types import SimpleNamespace
ROOT = pathlib.Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "EasyBIM.tab/Misc Tools.panel/Batch Duplicate Host.pushbutton"

def state_module():
    spec = importlib.util.spec_from_file_location("bdh_state_test", BUNDLE/"batch_duplicate_host_state.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

class WizardMonitoring(unittest.TestCase):
    def test_monitor_default_and_back_state(self):
        s = state_module().WizardState()
        self.assertTrue(s.monitored)
        self.assertFalse(s.copy_original)
        s = state_module().WizardState(monitored=False, copy_original=True)
        self.assertFalse(s.monitored)
        self.assertTrue(s.copy_original)

    def test_original_type_skips_local_source_pick(self):
        tree = ast.parse((BUNDLE/"script.py").read_text(encoding="utf-8"))
        run = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_run")
        calls = []
        class SourceWindow:
            should_select=True
            monitored=True
            copy_original=True
            def __init__(self,*args,**kwargs): pass
            def ShowDialog(self): pass
        class CategoryWindow:
            result="cancel"; was_back=False
            def __init__(self,*args,**kwargs): pass
            def ShowDialog(self): pass
        def pick(uidoc):
            calls.append("pick")
            raise AssertionError("Original-family flow must not pick a local source")
        ns = dict(__title__="test", SCRIPT_DIR=str(BUNDLE), os=__import__("os"),
                  revit=SimpleNamespace(uidoc=object(),doc=SimpleNamespace(ActiveView=object())),
                  WizardState=state_module().WizardState, STEP_SOURCE="source",
                  STEP_CATEGORIES="categories", STEP_FAMILY_TYPES="family_types", STEP_OFFSET="offset",
                  SourceSelectionWindow=SourceWindow, CategorySelectionWindow=CategoryWindow,
                  _pick_source_element=pick, get_target_documents=lambda doc: [],get_categories=lambda doc: [])
        exec(compile(ast.Module(body=[run],type_ignores=[]),"wizard","exec"),ns)
        ns["_run"]()
        self.assertEqual([],calls)

class CurrentReferenceFrame(unittest.TestCase):
    def load(self):
        tree=ast.parse((BUNDLE/"batch_duplicate_host_revit.py").read_text(encoding="utf-8"))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_independent_reference_frame")
        ns=dict(DB=SimpleNamespace(LocationPoint=str))
        exec(compile(ast.Module(body=[function],type_ignores=[]),"reference","exec"),ns)
        return ns["_independent_reference_frame"]
    def test_unavailable_link_transform_is_not_replaced_by_identity(self):
        def unavailable(): raise ValueError("link unavailable")
        link=SimpleNamespace(UniqueId="link",GetTotalTransform=unavailable)
        with self.assertRaisesRegex(ValueError,"link unavailable"):
            self.load()(SimpleNamespace(Location="point"),link,None,None,{})
    def test_current_transform_is_cached_per_link_instance(self):
        from unittest.mock import Mock
        link=SimpleNamespace(UniqueId="link",GetTotalTransform=Mock(return_value="current-transform"))
        adapter=SimpleNamespace(instance_frame=Mock(return_value="resolved-frame"))
        reference=SimpleNamespace(Location="point")
        cache={}; frame=self.load()
        self.assertEqual("resolved-frame",frame(reference,link,None,adapter,cache))
        frame(reference,link,None,adapter,cache)
        link.GetTotalTransform.assert_called_once()
        adapter.instance_frame.assert_called_with(reference,"current-transform")
