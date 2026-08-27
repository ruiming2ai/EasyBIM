# -*- coding: utf-8 -*-
__title__ = "DWG: Open/Reload"
__author__ = "Erik Frits"
__helpurl__ = "https://www.erikfrits.com/blog/open-selected-dwg/"
__doc__ = "Open, reload or locate a selected linked DWG in the model."
# IMPORTS
from os import startfile
from os.path import dirname
from Autodesk.Revit.DB import ImportInstance, ModelPathUtils, BuiltInParameter, Transaction
from pyrevit.forms import WPFWindow, alert
from subprocess import Popen

# .NET IMPORTS
from clr import AddReference
AddReference("System")
# from System.Collections.Generic import List
from System.Diagnostics.Process import Start
from System.Windows.Window import DragMove
from System.Windows.Input import MouseButtonState

#_____________________________ MAIN
doc     = __revit__.ActiveUIDocument.Document
uidoc   = __revit__.ActiveUIDocument


class MyWindow(WPFWindow):
    """Add-in for opening selected DWG with 3 options"""

    def __init__(self, xaml_file_name):
        self.form = WPFWindow.__init__(self, xaml_file_name)
        self.dwg_linked = self.selected_dwg_ImportInstance.IsLinked

        if self.dwg_linked:
            self.selected_dwg_path          = self.get_import_instance_path()
            self.selected_dwg_folder_path   = dirname(self.selected_dwg_path)
            self.dwg_path.Text              = self.selected_dwg_path
            self.main_title.Text            = __title__
            self.ShowDialog()
        else:
            alert("Selected DWG instance is not linked.", __title__, exitscript=False)



    def get_import_instance_path(self):
        # type:(ImportInstance) -> str
        """Function to get a path of the selected ImportInstance if it is linked."""
        import_instance = self.selected_dwg_ImportInstance
        if import_instance.IsLinked:
            cad_linktype_id = import_instance.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsElementId()
            cad_linktype = doc.GetElement(cad_linktype_id)
            efr = cad_linktype.GetExternalFileReference()
            dwg_path = ModelPathUtils.ConvertModelPathToUserVisiblePath(efr.GetAbsolutePath())
            return dwg_path
        else:
            alert("Selected DWG instance is not linked.", __title__, exitscript=True)

    @property
    def selected_dwg_ImportInstance(self):
        """Try to get an ImportInstance from selected elements."""
        selected_elements = uidoc.Selection.GetElementIds()

        if not selected_elements:
            alert("No elements were selected. Please try again.", __title__, exitscript=True)

        for element_id in selected_elements:
            element = doc.GetElement(element_id)

            # FILTER SELECTION
            if type(element) == ImportInstance:
                dwg = element
                return dwg
        alert("Linked ImportInstance was not selected. Please try again.", __title__, exitscript=True)


    # GUI EVENT HANDLERS:
    def button_close(self,sender,e):
        """Stop application by clicking on a <Close> button in the top right corner."""
        self.Close()

    def header_drag(self,sender,e):
        """Drag window by holding LeftButton on the header."""
        if e.LeftButton == MouseButtonState.Pressed:
            DragMove(self)

    def button_open_dwg(self,sender,e):
        """Open DWG in a default application and close the GUI."""
        startfile(self.selected_dwg_path)
        self.Close()

    def button_open_folder(self,sender,e):
        """Open parent folder of DWG file and close the GUI."""
        startfile(self.selected_dwg_folder_path)
        self.Close()

    def button_copy_clipboard(self, sender, e):
        """Add DWG filepath to the copy clipboard."""
        command = 'echo ' + self.selected_dwg_path.strip() + '| clip'
        Popen(command, shell=True)
        self.button_copy_clipboard.Content = "Copied!"

    def button_reload(self, sender, e):
        """Reload selected DWG"""
        self.Close()
        t = Transaction(doc,__title__)
        t.Start()
        import_instance = self.selected_dwg_ImportInstance
        if import_instance.IsLinked:
            cad_linktype_id = import_instance.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsElementId()
            cad_linktype = doc.GetElement(cad_linktype_id)
            cad_linktype.Reload()
        t.Commit()


    def Hyperlink_RequestNavigate(self, sender, e):
        """Forwarding for a Hyperlink"""
        Start(e.Uri.AbsoluteUri)

if __name__ == '__main__':
    MyWindow("Script.xaml")