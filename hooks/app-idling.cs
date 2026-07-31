using Autodesk.Revit.UI.Events;
using EasyBIM.TempPhase;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseIdlingHook
    {
        public void HandleIdling(object sender, IdlingEventArgs e)
        {
            TempPhaseController.Shared.HandleIdling(sender, e);
        }
    }
}
