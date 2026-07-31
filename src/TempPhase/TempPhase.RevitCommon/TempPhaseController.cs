using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Events;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;
using System;
using System.Reflection;

namespace EasyBIM.TempPhase
{
    /// <summary>
    /// Shared entry point used by the three pyRevit C# hooks and the manual command.
    /// The static instance is intentional: pyRevit compiles each hook separately, but
    /// all hooks must share the same view-session and pending-close state.
    /// </summary>
    public sealed class TempPhaseController
    {
        private readonly TempPhaseService _service;

        private TempPhaseController()
        {
            _service = new TempPhaseService();
        }

        public static TempPhaseController Shared { get; } = new TempPhaseController();

        public void HandleDocumentClosing(object sender, DocumentClosingEventArgs args)
        {
            _service.OnDocumentClosing(sender, args);
        }

        public void HandleIdling(object sender, IdlingEventArgs args)
        {
            _service.OnIdling(sender, args);
        }

        public void HandleDocumentClosed(object sender, DocumentClosedEventArgs args)
        {
            _service.OnDocumentClosed(sender, args);
        }

        public Result ExecuteManualCommand(
            ExternalCommandData commandData,
            ref string message,
            ElementSet elements)
        {
            TempPhaseDiagnostics.LogCommandStart(
                commandData,
                Assembly.GetExecutingAssembly().Location);

            string validationError;
            if (!TempPhaseDiagnostics.ValidateControllerForHost(commandData, out validationError))
            {
                message = validationError;
                TempPhaseDiagnostics.ShowControllerLoadFailure(validationError);
                return Result.Failed;
            }

            try
            {
                TempPhaseDiagnostics.LogMessage("ManualCommandDispatch");
                UIApplication uiapp = commandData == null ? null : commandData.Application;
                Result result = _service.RunManualCommand(uiapp, ref message);
                TempPhaseDiagnostics.LogMessage("ManualCommandReturn result=" + result);
                return result;
            }
            catch (Exception ex)
            {
                message = ex.Message;
                TempPhaseDiagnostics.ReportCommandException("ControllerManualCommand", ex);
                return Result.Failed;
            }
        }
    }
}
