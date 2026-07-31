using Autodesk.Revit.DB.Events;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseDocumentClosedHook
    {
        public void HandleDocumentClosed(object sender, DocumentClosedEventArgs e)
        {
            TempPhaseController.Shared.HandleDocumentClosed(sender, e);
        }
    }
}
