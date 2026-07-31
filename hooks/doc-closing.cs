using Autodesk.Revit.DB.Events;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseDocumentClosingHook
    {
        public void HandleDocumentClosing(object sender, DocumentClosingEventArgs e)
        {
            TempPhaseController.Shared.HandleDocumentClosing(sender, e);
        }
    }
}
