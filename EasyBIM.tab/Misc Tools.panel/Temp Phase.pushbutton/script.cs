using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseCommand
{
    [Transaction(TransactionMode.Manual)]
    public class Script : IExternalCommand
    {
        public Result Execute(
            ExternalCommandData commandData,
            ref string message,
            ElementSet elements)
        {
            return TempPhaseController.Shared.ExecuteManualCommand(
                commandData,
                ref message,
                elements);
        }
    }
}
