using System;
using System.IO;
using System.Reflection;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace EasyBIM.TempPhase
{
    /// <summary>
    /// Logging and host validation for the pyRevit command boundary.
    /// This class deliberately lives in the controller module so the standalone
    /// command and the pyRevit command report the same assembly identity.
    /// </summary>
    public static class TempPhaseDiagnostics
    {
        public static void LogCommandStart(ExternalCommandData commandData, string entryAssemblyPath)
        {
            string controllerPath = string.Empty;
            string controllerName = string.Empty;

            try
            {
                Assembly controllerAssembly = typeof(TempPhaseController).Assembly;
                controllerPath = controllerAssembly.Location ?? string.Empty;
                AssemblyName identity = controllerAssembly.GetName();
                controllerName = identity.Name ?? string.Empty;
            }
            catch (Exception ex)
            {
                LogMessage("CommandStartControllerInspectionFailed " + ExceptionText(ex));
            }

            LogMessage(
                "CommandStart"
                + " revitVersion=" + GetRevitVersion(commandData)
                + " entryAssembly=" + (entryAssemblyPath ?? string.Empty)
                + " loadedExtensionPath=" + GetDirectory(entryAssemblyPath)
                + " controllerAssembly=" + controllerName
                + " controllerPath=" + controllerPath);
        }

        public static bool ValidateControllerForHost(ExternalCommandData commandData, out string error)
        {
            error = string.Empty;
            string version = GetRevitVersion(commandData);
            string expectedName = "TempPhaseController.Revit" + version;
            string actualName = string.Empty;
            string actualPath = string.Empty;

            try
            {
                Assembly assembly = typeof(TempPhaseController).Assembly;
                AssemblyName identity = assembly.GetName();
                actualName = identity.Name ?? string.Empty;
                actualPath = assembly.Location ?? string.Empty;
            }
            catch (Exception ex)
            {
                error = "The Temp Phase controller could not be inspected: " + ExceptionText(ex);
                LogMessage("ControllerValidationFailed " + error);
                return false;
            }

            if (version != "2025" && version != "2026")
            {
                error = "This Temp Phase module supports Revit 2025 and 2026 only. Revit reported " + version + ".";
            }
            else if (!string.Equals(actualName, expectedName, StringComparison.OrdinalIgnoreCase))
            {
                error = "The Temp Phase module is for " + actualName
                    + ", but this Revit session requires " + expectedName + ".";
            }
            else if (string.IsNullOrWhiteSpace(actualPath) || !File.Exists(actualPath))
            {
                error = "The loaded Temp Phase controller has no readable assembly path.";
            }

            if (!string.IsNullOrEmpty(error))
            {
                LogMessage(
                    "ControllerValidationFailed"
                    + " revitVersion=" + version
                    + " expectedAssembly=" + expectedName
                    + " actualAssembly=" + actualName
                    + " controllerPath=" + actualPath
                    + " error=" + error);
                return false;
            }

            LogMessage(
                "ControllerValidationSucceeded"
                + " revitVersion=" + version
                + " assembly=" + actualName
                + " controllerPath=" + actualPath);
            return true;
        }

        public static void ReportCommandException(string source, Exception exception)
        {
            string detail = ExceptionText(exception);
            LogMessage((source ?? "Command") + "Exception " + detail);

            try
            {
                TaskDialog.Show(
                    AddinInfo.DialogTitle,
                    "The EasyBIM Temp Phase command could not start."
                    + Environment.NewLine + Environment.NewLine
                    + ShortExceptionText(exception)
                    + Environment.NewLine + Environment.NewLine
                    + "Check the diagnostic log at:" + Environment.NewLine
                    + AddinInfo.GetLogPath());
            }
            catch (Exception dialogException)
            {
                LogMessage("CommandExceptionDialogFailed " + ExceptionText(dialogException));
            }
        }

        public static void ShowControllerLoadFailure(string message)
        {
            string detail = string.IsNullOrWhiteSpace(message)
                ? "The Temp Phase controller module is missing or could not be loaded."
                : message;
            LogMessage("ControllerLoadFailure " + detail);

            try
            {
                TaskDialog.Show(
                    AddinInfo.DialogTitle,
                    detail
                    + Environment.NewLine + Environment.NewLine
                    + "Update EasyBIM, rebuild packaged TempPhaseController.Revit2025/2026 DLLs if needed, then reload pyRevit."
                    + Environment.NewLine + Environment.NewLine
                    + "Diagnostic log:" + Environment.NewLine
                    + AddinInfo.GetLogPath());
            }
            catch (Exception dialogException)
            {
                LogMessage("ControllerLoadFailureDialogFailed " + ExceptionText(dialogException));
            }
        }

        public static void LogMessage(string message)
        {
            try
            {
                string logPath = AddinInfo.GetLogPath();
                string directory = Path.GetDirectoryName(logPath);
                if (!Directory.Exists(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                File.AppendAllText(
                    logPath,
                    DateTime.Now.ToString("O") + " " + (message ?? string.Empty) + Environment.NewLine);
            }
            catch
            {
                // Diagnostics must never prevent the Revit command from returning.
            }
        }

        public static string GetRevitVersion(ExternalCommandData commandData)
        {
            try
            {
                if (commandData != null
                    && commandData.Application != null
                    && commandData.Application.Application != null)
                {
                    return commandData.Application.Application.VersionNumber ?? "unknown";
                }
            }
            catch (Exception ex)
            {
                LogMessage("RevitVersionInspectionFailed " + ExceptionText(ex));
            }

            return "unknown";
        }

        private static string GetDirectory(string path)
        {
            try
            {
                return string.IsNullOrWhiteSpace(path)
                    ? string.Empty
                    : (Path.GetDirectoryName(path) ?? string.Empty);
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string ExceptionText(Exception exception)
        {
            if (exception == null)
            {
                return string.Empty;
            }

            string text = exception.GetType().FullName + ": " + exception.Message;
            if (exception.InnerException != null)
            {
                text += " | Inner: " + ExceptionText(exception.InnerException);
            }

            if (!string.IsNullOrWhiteSpace(exception.StackTrace))
            {
                text += Environment.NewLine + exception.StackTrace;
            }

            return text;
        }

        private static string ShortExceptionText(Exception exception)
        {
            return exception == null
                ? "Unknown exception."
                : exception.GetType().Name + ": " + exception.Message;
        }
    }
}
