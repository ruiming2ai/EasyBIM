using System;
using System.Collections.Generic;
using System.Reflection;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace EasyBIM.TempPhase
{
    internal static class TempPhaseCompat
    {
        public static DocumentKey BuildDocumentKey(Document doc)
        {
            if (doc == null)
            {
                return new DocumentKey();
            }

            string path = string.Empty;
            try
            {
                path = SafeText(doc.PathName).Trim().ToLowerInvariant();
            }
            catch
            {
                path = string.Empty;
            }

            return new DocumentKey
            {
                PathKey = path,
                RuntimeKey = SafeText(doc.GetHashCode()),
                Title = SafeText(GetPropertyValue(doc, "Title")).Trim()
            };
        }

        public static bool DocumentKeyMatches(ViewSession session, DocumentKey key)
        {
            if (session == null || session.DocumentKey == null || key == null)
            {
                return false;
            }

            if (!string.IsNullOrEmpty(key.RuntimeKey) &&
                string.Equals(session.DocumentKey.RuntimeKey, key.RuntimeKey, StringComparison.Ordinal))
            {
                return true;
            }

            if (!string.IsNullOrEmpty(key.PathKey) &&
                string.Equals(session.DocumentKey.PathKey, key.PathKey, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            return false;
        }

        public static bool DocumentKeyMatches(DocumentKey left, DocumentKey right)
        {
            if (left == null || right == null)
            {
                return false;
            }

            if (!string.IsNullOrEmpty(left.RuntimeKey) &&
                !string.IsNullOrEmpty(right.RuntimeKey) &&
                string.Equals(left.RuntimeKey, right.RuntimeKey, StringComparison.Ordinal))
            {
                return true;
            }

            if (!string.IsNullOrEmpty(left.PathKey) &&
                !string.IsNullOrEmpty(right.PathKey) &&
                string.Equals(left.PathKey, right.PathKey, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            return false;
        }

        public static string BuildSessionKey(DocumentKey key, int viewId)
        {
            string basis = !string.IsNullOrEmpty(key.PathKey) ? key.PathKey : key.RuntimeKey;
            return basis + "|" + viewId;
        }

        public static bool IsSupportedProjectDocument(Document doc)
        {
            if (doc == null)
            {
                return false;
            }

            if (ToBool(GetPropertyValue(doc, "IsFamilyDocument")))
            {
                return false;
            }

            object linked = GetPropertyValue(doc, "IsLinked");
            if (linked != null && ToBool(linked))
            {
                return false;
            }

            return true;
        }

        public static bool IsValidProjectView(View view)
        {
            if (view == null)
            {
                return false;
            }

            object valid = GetPropertyValue(view, "IsValidObject");
            if (valid != null && !ToBool(valid))
            {
                return false;
            }

            object isTemplate = GetPropertyValue(view, "IsTemplate");
            if (isTemplate != null && ToBool(isTemplate))
            {
                return false;
            }

            return true;
        }

        public static List<PhaseChoice> CollectPhases(Document doc)
        {
            List<PhaseChoice> phases = new List<PhaseChoice>();
            if (doc == null)
            {
                return phases;
            }

            try
            {
                foreach (Phase phase in doc.Phases)
                {
                    int? phaseId = ToInt(phase.Id);
                    if (!phaseId.HasValue)
                    {
                        continue;
                    }

                    phases.Add(new PhaseChoice
                    {
                        PhaseId = phaseId.Value,
                        PhaseName = SafeText(GetPropertyValue(phase, "Name"))
                    });
                }
            }
            catch
            {
                return new List<PhaseChoice>();
            }

            return phases;
        }

        public static int? GetViewPhaseId(View view)
        {
            Parameter parameter = GetViewPhaseParameter(view);
            if (parameter == null)
            {
                return null;
            }

            try
            {
                return ToInt(parameter.AsElementId());
            }
            catch
            {
                return null;
            }
        }

        public static bool SetViewPhaseId(View view, int phaseId)
        {
            Parameter parameter = GetViewPhaseParameter(view);
            if (parameter == null)
            {
                return false;
            }

            try
            {
                return parameter.Set(CreateElementId(phaseId));
            }
            catch
            {
                return false;
            }
        }

        public static int? GetTemporaryTemplateReferenceId(View view)
        {
            if (!IsValidProjectView(view))
            {
                return null;
            }

            try
            {
                int? directId = ToInt(view.GetTemporaryViewPropertiesId());
                if (directId.HasValue && directId.Value > 0)
                {
                    return directId.Value;
                }
            }
            catch
            {
            }

            string[] getterNames = new string[]
            {
                "GetTemporaryViewPropertiesId",
                "GetTemporaryViewPropertiesViewId"
            };

            string[] propertyNames = new string[]
            {
                "TemporaryViewPropertiesId",
                "TemporaryViewPropertiesViewId"
            };

            for (int i = 0; i < getterNames.Length; i++)
            {
                MethodInfo method = view.GetType().GetMethod(getterNames[i], System.Type.EmptyTypes);
                if (method == null)
                {
                    continue;
                }

                try
                {
                    int? id = ToInt(method.Invoke(view, null));
                    if (id.HasValue && id.Value > 0)
                    {
                        return id.Value;
                    }
                }
                catch
                {
                }
            }

            for (int i = 0; i < propertyNames.Length; i++)
            {
                int? id = ToInt(GetPropertyValue(view, propertyNames[i]));
                if (id.HasValue && id.Value > 0)
                {
                    return id.Value;
                }
            }

            return null;
        }

        public static bool IsTvpActive(View view)
        {
            if (!IsValidProjectView(view))
            {
                return false;
            }

            Type modeType = view.GetType().Assembly.GetType("Autodesk.Revit.DB.TemporaryViewMode");
            object enumValue = TryGetTemporaryViewPropertiesEnum(modeType);

            if (modeType != null && enumValue != null)
            {
                MethodInfo method = view.GetType().GetMethod("IsTemporaryViewModeEnabled", new Type[] { modeType });
                if (method != null)
                {
                    try
                    {
                        return ToBool(method.Invoke(view, new object[] { enumValue }));
                    }
                    catch
                    {
                    }
                }
            }

            MethodInfo legacyMethod = view.GetType().GetMethod("IsTemporaryViewPropertiesModeEnabled", System.Type.EmptyTypes);
            if (legacyMethod != null)
            {
                try
                {
                    return ToBool(legacyMethod.Invoke(view, null));
                }
                catch
                {
                }
            }

            return false;
        }

        public static bool DisableTvp(View view)
        {
            if (!IsValidProjectView(view))
            {
                return false;
            }

            try
            {
                view.DisableTemporaryViewMode(TemporaryViewMode.TemporaryViewProperties);
                return true;
            }
            catch
            {
            }

            Type modeType = view.GetType().Assembly.GetType("Autodesk.Revit.DB.TemporaryViewMode");
            object enumValue = TryGetTemporaryViewPropertiesEnum(modeType);
            if (modeType == null || enumValue == null)
            {
                return false;
            }

            MethodInfo method = view.GetType().GetMethod("DisableTemporaryViewMode", new Type[] { modeType });
            if (method == null)
            {
                return false;
            }

            try
            {
                method.Invoke(view, new object[] { enumValue });
                return true;
            }
            catch
            {
                return false;
            }
        }

        public static bool EnableTvp(View view, int? templateId)
        {
            if (!IsValidProjectView(view))
            {
                return false;
            }

            try
            {
                ElementId directTemplateId = templateId.HasValue && templateId.Value > 0
                    ? CreateElementId(templateId.Value)
                    : view.Id;
                view.EnableTemporaryViewPropertiesMode(directTemplateId);
                return true;
            }
            catch
            {
            }

            MethodInfo method = view.GetType().GetMethod("EnableTemporaryViewPropertiesMode", new Type[] { typeof(ElementId) });
            if (method == null)
            {
                return false;
            }

            List<ElementId> ids = new List<ElementId>();
            if (templateId.HasValue && templateId.Value > 0)
            {
                ids.Add(CreateElementId(templateId.Value));
            }

            try
            {
                ids.Add(view.Id);
            }
            catch
            {
            }

            for (int i = 0; i < ids.Count; i++)
            {
                try
                {
                    method.Invoke(view, new object[] { ids[i] });
                    return true;
                }
                catch
                {
                }
            }

            return false;
        }

        public static bool EventSucceeded(object eventArgs)
        {
            if (eventArgs == null)
            {
                return false;
            }

            object status = GetPropertyValue(eventArgs, "Status");
            if (status == null)
            {
                return true;
            }

            string text = SafeText(status).Trim();
            if (string.IsNullOrEmpty(text))
            {
                return true;
            }

            return string.Equals(text, "Succeeded", StringComparison.OrdinalIgnoreCase);
        }

        public static bool EventIsCancellable(object eventArgs)
        {
            if (eventArgs == null)
            {
                return false;
            }

            object cancellable = GetPropertyValue(eventArgs, "Cancellable");
            if (cancellable == null)
            {
                return true;
            }

            return ToBool(cancellable);
        }

        public static bool TryCancelEvent(object eventArgs)
        {
            if (!EventIsCancellable(eventArgs))
            {
                return false;
            }

            PropertyInfo cancelProperty = eventArgs.GetType().GetProperty("Cancel", BindingFlags.Instance | BindingFlags.Public);
            if (cancelProperty != null && cancelProperty.CanWrite)
            {
                try
                {
                    cancelProperty.SetValue(eventArgs, true, null);
                    return true;
                }
                catch
                {
                }
            }

            MethodInfo cancelMethod = eventArgs.GetType().GetMethod("Cancel", Type.EmptyTypes);
            if (cancelMethod != null)
            {
                try
                {
                    cancelMethod.Invoke(eventArgs, null);
                    return true;
                }
                catch
                {
                }
            }

            return false;
        }

        public static Document ResolveDocument(object eventArgs)
        {
            if (eventArgs == null)
            {
                return null;
            }

            object doc = GetPropertyValue(eventArgs, "Document");
            if (doc is Document)
            {
                return (Document)doc;
            }

            MethodInfo getDocument = eventArgs.GetType().GetMethod("GetDocument", System.Type.EmptyTypes);
            if (getDocument != null)
            {
                try
                {
                    return getDocument.Invoke(eventArgs, null) as Document;
                }
                catch
                {
                }
            }

            return null;
        }

        public static string GetEventDocumentId(object eventArgs)
        {
            if (eventArgs == null)
            {
                return string.Empty;
            }

            object documentId = GetPropertyValue(eventArgs, "DocumentId");
            if (documentId == null)
            {
                return string.Empty;
            }

            string id = SafeText(documentId).Trim();
            return string.IsNullOrEmpty(id) ? string.Empty : id;
        }

        public static string BuildCloseKey(object eventArgs, DocumentKey documentKey)
        {
            string eventId = GetEventDocumentId(eventArgs);
            if (!string.IsNullOrEmpty(eventId))
            {
                return "document-id:" + eventId;
            }

            if (documentKey != null && !string.IsNullOrEmpty(documentKey.PathKey))
            {
                return "path:" + documentKey.PathKey;
            }

            if (documentKey != null && !string.IsNullOrEmpty(documentKey.RuntimeKey))
            {
                return "runtime:" + documentKey.RuntimeKey;
            }

            return string.Empty;
        }

        public static string GetClosedDocumentPath(object eventArgs)
        {
            return SafeText(GetPropertyValue(eventArgs, "PathName")).Trim().ToLowerInvariant();
        }

        public static string GetClosedDocumentTitle(object eventArgs)
        {
            return SafeText(GetPropertyValue(eventArgs, "Title")).Trim();
        }

        public static string GetViewDisplayName(View view)
        {
            return SafeText(GetPropertyValue(view, "Name")).Trim();
        }

        public static int? ToInt(object value)
        {
            if (value == null)
            {
                return null;
            }

            ElementId elementId = value as ElementId;
            if (elementId != null)
            {
                try
                {
                    object rawValue = GetPropertyValue(elementId, "Value");
                    if (rawValue != null)
                    {
                        return Convert.ToInt32(rawValue);
                    }

                    rawValue = GetPropertyValue(elementId, "IntegerValue");
                    if (rawValue != null)
                    {
                        return Convert.ToInt32(rawValue);
                    }

                    return null;
                }
                catch
                {
                    return null;
                }
            }

            try
            {
                return Convert.ToInt32(value);
            }
            catch
            {
                return null;
            }
        }

        public static string SafeText(object value)
        {
            try
            {
                return value == null ? string.Empty : value.ToString();
            }
            catch
            {
                return string.Empty;
            }
        }

        private static Parameter GetViewPhaseParameter(View view)
        {
            if (!IsValidProjectView(view))
            {
                return null;
            }

            try
            {
                return view.get_Parameter(BuiltInParameter.VIEW_PHASE);
            }
            catch
            {
                return null;
            }
        }

        public static ElementId CreateElementId(int value)
        {
            ConstructorInfo ctor = typeof(ElementId).GetConstructor(new Type[] { typeof(long) });
            if (ctor != null)
            {
                return (ElementId)ctor.Invoke(new object[] { Convert.ToInt64(value) });
            }

            ctor = typeof(ElementId).GetConstructor(new Type[] { typeof(int) });
            if (ctor != null)
            {
                return (ElementId)ctor.Invoke(new object[] { value });
            }

            throw new InvalidOperationException("Could not construct ElementId.");
        }

        public static bool CanPostCommand(UIApplication uiapp, RevitCommandId commandId)
        {
            if (uiapp == null || commandId == null)
            {
                return false;
            }

            MethodInfo method = uiapp.GetType().GetMethod("CanPostCommand", new Type[] { typeof(RevitCommandId) });
            if (method == null)
            {
                return true;
            }

            try
            {
                return ToBool(method.Invoke(uiapp, new object[] { commandId }));
            }
            catch
            {
                return false;
            }
        }

        private static object TryGetTemporaryViewPropertiesEnum(Type modeType)
        {
            if (modeType == null)
            {
                return null;
            }

            try
            {
                return Enum.Parse(modeType, "TemporaryViewProperties");
            }
            catch
            {
                return null;
            }
        }

        private static object GetPropertyValue(object source, string propertyName)
        {
            if (source == null)
            {
                return null;
            }

            PropertyInfo property = source.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public);
            if (property == null)
            {
                return null;
            }

            try
            {
                return property.GetValue(source, null);
            }
            catch
            {
                return null;
            }
        }

        private static bool ToBool(object value)
        {
            try
            {
                return Convert.ToBoolean(value);
            }
            catch
            {
                return false;
            }
        }
    }
}
