using System;
using System.IO;
using System.Reflection;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

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
            string entryAssemblyPath = string.Empty;

            try
            {
                entryAssemblyPath = Assembly.GetExecutingAssembly().Location;
                string version = GetRevitVersion(commandData);
                Log(
                    "PyRevitCommandStart"
                    + " revitVersion=" + version
                    + " entryAssembly=" + entryAssemblyPath
                    + " entryAssemblyDirectory=" + GetDirectory(entryAssemblyPath));

                Assembly controllerAssembly = FindControllerAssembly(version);
                if (controllerAssembly == null)
                {
                    string error = "The Temp Phase controller module is not loaded for Revit " + version + ".";
                    Log("ControllerModuleMissing expectedAssembly=TempPhaseController.Revit" + version);
                    ShowFailure(error + Environment.NewLine + Environment.NewLine
                        + "Expected: TempPhaseController.dll in this command bundle's bin folder.");
                    message = error;
                    return Result.Failed;
                }

                Log(
                    "ControllerModuleLoaded"
                    + " assembly=" + controllerAssembly.GetName().Name
                    + " controllerPath=" + controllerAssembly.Location);

                Type controllerType = controllerAssembly.GetType(
                    "EasyBIM.TempPhase.TempPhaseController",
                    false);
                if (controllerType == null)
                {
                    throw new TypeLoadException(
                        "EasyBIM.TempPhase.TempPhaseController was not found in "
                        + controllerAssembly.GetName().Name + ".");
                }

                PropertyInfo sharedProperty = controllerType.GetProperty(
                    "Shared",
                    BindingFlags.Public | BindingFlags.Static);
                MethodInfo executeMethod = controllerType.GetMethod(
                    "ExecuteManualCommand",
                    BindingFlags.Public | BindingFlags.Instance,
                    null,
                    new Type[]
                    {
                        typeof(ExternalCommandData),
                        typeof(string).MakeByRefType(),
                        typeof(ElementSet)
                    },
                    null);

                if (sharedProperty == null || executeMethod == null)
                {
                    throw new MissingMethodException(
                        "The loaded Temp Phase controller does not expose ExecuteManualCommand.");
                }

                object controller = sharedProperty.GetValue(null, null);
                object[] arguments = new object[] { commandData, message, elements };
                Log("ControllerManualCommandInvoke");
                object result = executeMethod.Invoke(controller, arguments);
                message = arguments[1] as string;
                Result commandResult = result is Result ? (Result)result : Result.Failed;
                Log("PyRevitCommandReturn result=" + commandResult);
                return commandResult;
            }
            catch (Exception ex)
            {
                Exception detailException = ex is TargetInvocationException && ex.InnerException != null
                    ? ex.InnerException
                    : ex;
                message = detailException.Message;
                Log("PyRevitCommandException " + ExceptionText(detailException));
                ShowFailure(
                    "The EasyBIM Temp Phase command could not start."
                    + Environment.NewLine + Environment.NewLine
                    + detailException.GetType().Name + ": " + detailException.Message);
                return Result.Failed;
            }
        }

        private static Assembly FindControllerAssembly(string version)
        {
            string expectedName = "TempPhaseController.Revit" + version;
            Assembly mismatchedController = null;
            Assembly[] loadedAssemblies = AppDomain.CurrentDomain.GetAssemblies();
            foreach (Assembly assembly in loadedAssemblies)
            {
                try
                {
                    string assemblyName = assembly.GetName().Name;
                    if (string.Equals(assemblyName, expectedName, StringComparison.OrdinalIgnoreCase))
                    {
                        return assembly;
                    }

                    if (!string.IsNullOrWhiteSpace(assemblyName)
                        && assemblyName.StartsWith("TempPhaseController.Revit", StringComparison.OrdinalIgnoreCase))
                    {
                        mismatchedController = assembly;
                    }
                }
                catch (Exception ex)
                {
                    Log("ControllerAssemblyInspectionException " + ExceptionText(ex));
                }
            }

            try
            {
                return Assembly.Load(expectedName);
            }
            catch (Exception ex)
            {
                Log("ControllerAssemblyLoadException expectedAssembly=" + expectedName + " " + ExceptionText(ex));
            }

            if (mismatchedController != null)
            {
                Log(
                    "ControllerModuleWrongYearLoaded"
                    + " expectedAssembly=" + expectedName
                    + " actualAssembly=" + mismatchedController.GetName().Name);
            }

            return mismatchedController;
        }

        private static string GetRevitVersion(ExternalCommandData commandData)
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
                Log("RevitVersionInspectionException " + ExceptionText(ex));
            }

            return "unknown";
        }

        private static void ShowFailure(string message)
        {
            try
            {
                TaskDialog.Show(
                    "EasyBIM Temp Phase",
                    message
                    + Environment.NewLine + Environment.NewLine
                    + "Diagnostic log:" + Environment.NewLine
                    + GetLogPath());
            }
            catch (Exception ex)
            {
                Log("PyRevitCommandDialogException " + ExceptionText(ex));
            }
        }

        private static void Log(string message)
        {
            try
            {
                string path = GetLogPath();
                string directory = Path.GetDirectoryName(path);
                if (!Directory.Exists(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                File.AppendAllText(
                    path,
                    DateTime.Now.ToString("O") + " " + (message ?? string.Empty) + Environment.NewLine);
            }
            catch
            {
            }
        }

        private static string GetLogPath()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "EasyBIM",
                "Temp Phase",
                "logs",
                "events.log");
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
    }
}
