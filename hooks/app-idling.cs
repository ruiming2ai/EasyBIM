using System;
using Autodesk.Revit.UI.Events;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseIdlingHook
    {
        public void HandleIdling(object sender, IdlingEventArgs e)
        {
            try
            {
                TempPhaseController.Shared.HandleIdling(sender, e);
            }
            catch (Exception ex)
            {
                TempPhaseDiagnostics.LogMessage("AppIdlingHookException " + ex);
            }
        }
    }
}
