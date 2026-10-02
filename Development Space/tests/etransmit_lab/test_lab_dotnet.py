"""Real .NET event-binding smoke check on Windows IronPython (not Revit)."""
import sys
import unittest

class DotNetEvents(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'cli', 'Requires real Windows IronPython/.NET')
    def test_dynamic_event_binding_used_by_evidence_observer(self):
        import clr
        clr.AddReference('System.Windows.Forms')
        from System import EventHandler
        from System.Windows.Forms import Button
        button=Button();seen=[]
        handler=EventHandler(lambda sender,args:seen.append(True))
        try:
            getattr(button,'Click').__iadd__(handler)
            button.PerformClick()
            self.assertEqual([True],seen)
            getattr(button,'Click').__isub__(handler)
            button.PerformClick()
            self.assertEqual([True],seen)
        finally:button.Dispose()

if __name__=='__main__':unittest.main()
