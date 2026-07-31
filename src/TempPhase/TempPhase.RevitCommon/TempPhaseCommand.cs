using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace EasyBIM.TempPhase
{
    [Transaction(TransactionMode.Manual)]
    public sealed class TempPhaseCommand : IExternalCommand
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
