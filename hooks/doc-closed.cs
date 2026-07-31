using System;
using Autodesk.Revit.DB.Events;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseDocumentClosedHook
    {
        public void HandleDocumentClosed(object sender, DocumentClosedEventArgs e)
        {
            try
            {
                TempPhaseController.Shared.HandleDocumentClosed(sender, e);
            }
            catch (Exception ex)
            {
                TempPhaseDiagnostics.LogMessage("DocClosedHookException " + ex);
            }
        }
    }
}
