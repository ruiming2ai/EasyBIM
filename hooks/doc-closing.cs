using System;
using Autodesk.Revit.DB.Events;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseDocumentClosingHook
    {
        public void HandleDocumentClosing(object sender, DocumentClosingEventArgs e)
        {
            try
            {
                TempPhaseController.Shared.HandleDocumentClosing(sender, e);
            }
            catch (Exception ex)
            {
                TempPhaseDiagnostics.LogMessage("DocClosingHookException " + ex);
            }
        }
    }
}
