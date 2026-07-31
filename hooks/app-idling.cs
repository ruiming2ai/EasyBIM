using System;
using System.IO;
using System.Reflection;
using Autodesk.Revit.UI.Events;

namespace EasyBIM.TempPhaseHooks
{
    public class TempPhaseIdlingHook
    {
        public void HandleIdling(object sender, IdlingEventArgs e)
        {
            InvokeController("HandleIdling", sender, e, typeof(IdlingEventArgs), "AppIdling");
        }

        private static void InvokeController(string methodName, object sender, object eventArgs, Type eventArgsType, string hookName)
        {
            try
            {
                Assembly controllerAssembly = FindControllerAssembly(sender);
                if (controllerAssembly == null)
                {
                    Log(hookName + "HookControllerMissing");
                    return;
                }

                Type controllerType = controllerAssembly.GetType("EasyBIM.TempPhase.TempPhaseController", false);
                if (controllerType == null)
                {
                    Log(hookName + "HookControllerTypeMissing assembly=" + controllerAssembly.GetName().Name);
                    return;
                }

                PropertyInfo sharedProperty = controllerType.GetProperty("Shared", BindingFlags.Public | BindingFlags.Static);
                MethodInfo handler = controllerType.GetMethod(
                    methodName,
                    BindingFlags.Public | BindingFlags.Instance,
                    null,
                    new Type[] { typeof(object), eventArgsType },
                    null);

                if (sharedProperty == null || handler == null)
                {
                    Log(hookName + "HookControllerMemberMissing assembly=" + controllerAssembly.GetName().Name);
                    return;
                }

                object controller = sharedProperty.GetValue(null, null);
                handler.Invoke(controller, new object[] { sender, eventArgs });
            }
            catch (Exception ex)
            {
                Exception detail = ex is TargetInvocationException && ex.InnerException != null
                    ? ex.InnerException
                    : ex;
                Log(hookName + "HookException " + ExceptionText(detail));
            }
        }

        private static Assembly FindControllerAssembly(object sender)
        {
            string version = GetRevitVersion(sender);
            string expectedName = string.IsNullOrWhiteSpace(version)
                ? string.Empty
                : "TempPhaseController.Revit" + version;
            Assembly mismatchedController = null;

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                string assemblyName = GetAssemblyName(assembly);
                if (string.IsNullOrWhiteSpace(assemblyName))
                {
                    continue;
                }

                if (!string.IsNullOrWhiteSpace(expectedName)
                    && string.Equals(assemblyName, expectedName, StringComparison.OrdinalIgnoreCase))
                {
                    return assembly;
                }

                if (assemblyName.StartsWith("TempPhaseController.Revit", StringComparison.OrdinalIgnoreCase))
                {
                    mismatchedController = assembly;
                }
            }

            if (!string.IsNullOrWhiteSpace(expectedName))
            {
                try
                {
                    return Assembly.Load(expectedName);
                }
                catch (Exception ex)
                {
                    Log("AppIdlingHookAssemblyLoadFailed expectedAssembly=" + expectedName + " " + ExceptionText(ex));
                }
            }

            Assembly extensionModule = LoadControllerFromPath(FindControllerPath(version), "AppIdling");
            if (extensionModule != null)
            {
                return extensionModule;
            }

            if (mismatchedController != null)
            {
                Log(
                    "AppIdlingHookWrongYearController"
                    + " expectedAssembly=" + expectedName
                    + " actualAssembly=" + mismatchedController.GetName().Name);
            }

            return null;
        }

        private static Assembly LoadControllerFromPath(string controllerPath, string hookName)
        {
            Log(
                hookName
                + "HookControllerPathCandidate path="
                + (controllerPath ?? string.Empty)
                + " exists="
                + File.Exists(controllerPath ?? string.Empty));

            if (string.IsNullOrWhiteSpace(controllerPath) || !File.Exists(controllerPath))
            {
                return null;
            }

            try
            {
                return Assembly.LoadFrom(controllerPath);
            }
            catch (Exception ex)
            {
                Log(hookName + "HookAssemblyLoadFromFailed path=" + controllerPath + " " + ExceptionText(ex));
                return null;
            }
        }

        private static string FindControllerPath(string version)
        {
            if (string.IsNullOrWhiteSpace(version))
            {
                return string.Empty;
            }

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                string location = GetAssemblyLocation(assembly);
                if (string.IsNullOrWhiteSpace(location))
                {
                    continue;
                }

                string extensionRoot = FindExtensionRoot(Path.GetDirectoryName(location));
                if (string.IsNullOrWhiteSpace(extensionRoot))
                {
                    continue;
                }

                string controllerPath = Path.Combine(
                    extensionRoot,
                    "bin",
                    "TempPhaseController.Revit" + version + ".dll");
                if (File.Exists(controllerPath))
                {
                    return controllerPath;
                }
            }

            return string.Empty;
        }

        private static string FindExtensionRoot(string startDirectory)
        {
            try
            {
                DirectoryInfo directory = string.IsNullOrWhiteSpace(startDirectory)
                    ? null
                    : new DirectoryInfo(startDirectory);
                while (directory != null)
                {
                    if (File.Exists(Path.Combine(directory.FullName, "Extension.yaml")))
                    {
                        return directory.FullName;
                    }

                    directory = directory.Parent;
                }
            }
            catch
            {
            }

            return string.Empty;
        }

        private static string GetAssemblyLocation(Assembly assembly)
        {
            try
            {
                return assembly == null ? string.Empty : (assembly.Location ?? string.Empty);
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string GetRevitVersion(object sender)
        {
            string version = GetStringProperty(sender, "VersionNumber");
            if (!string.IsNullOrWhiteSpace(version))
            {
                return version;
            }

            object application = GetObjectProperty(sender, "Application");
            return GetStringProperty(application, "VersionNumber");
        }

        private static object GetObjectProperty(object source, string propertyName)
        {
            try
            {
                return source == null
                    ? null
                    : source.GetType().GetProperty(propertyName).GetValue(source, null);
            }
            catch
            {
                return null;
            }
        }

        private static string GetStringProperty(object source, string propertyName)
        {
            try
            {
                object value = GetObjectProperty(source, propertyName);
                return value == null ? string.Empty : value.ToString();
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string GetAssemblyName(Assembly assembly)
        {
            try
            {
                return assembly == null ? string.Empty : (assembly.GetName().Name ?? string.Empty);
            }
            catch
            {
                return string.Empty;
            }
        }

        private static void Log(string message)
        {
            try
            {
                string path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "EasyBIM",
                    "Temp Phase",
                    "logs",
                    "events.log");
                string directory = Path.GetDirectoryName(path);
                if (!Directory.Exists(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                File.AppendAllText(path, DateTime.Now.ToString("O") + " " + (message ?? string.Empty) + Environment.NewLine);
            }
            catch
            {
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

            return text;
        }
    }
}
