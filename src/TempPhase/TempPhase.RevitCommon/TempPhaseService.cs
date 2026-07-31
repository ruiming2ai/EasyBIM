using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Events;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;

namespace EasyBIM.TempPhase
{
    internal sealed class TempPhaseService
    {
        private const double IdleRestoreThrottleSeconds = 0.25;
        private const double PendingCloseDelaySeconds = 0.05;
        private const double AllowCloseGuardSeconds = 8.0;

        private readonly Dictionary<string, ViewSession> _viewSessions;
        private readonly Dictionary<string, PendingCloseRequest> _pendingCloses;
        private readonly Dictionary<string, PendingCloseGuard> _allowCloseGuards;
        private readonly object _syncRoot;
        private DateTime _lastIdleAtUtc;

        public TempPhaseService()
        {
            _viewSessions = new Dictionary<string, ViewSession>(StringComparer.OrdinalIgnoreCase);
            _pendingCloses = new Dictionary<string, PendingCloseRequest>(StringComparer.OrdinalIgnoreCase);
            _allowCloseGuards = new Dictionary<string, PendingCloseGuard>(StringComparer.OrdinalIgnoreCase);
            _syncRoot = new object();
            _lastIdleAtUtc = DateTime.MinValue;
        }

        public static void WriteStartupFailure(Exception ex)
        {
            WriteStaticLog("OnStartupFailure " + ExceptionText(ex));
        }

        public void Log(string message)
        {
            WriteStaticLog(message);
        }

        public Result RunManualCommand(UIApplication uiapp, ref string message)
        {
            Log("ManualCommandBegin");
            try
            {
                UIDocument uidoc = uiapp == null ? null : uiapp.ActiveUIDocument;
                Document doc = uidoc == null ? null : uidoc.Document;
                View view = uidoc == null ? null : uidoc.ActiveView;
                if (!TempPhaseCompat.IsSupportedProjectDocument(doc))
                {
                    Log("ManualCommandNoProjectDocument");
                    ShowInfoDialog("Open a project document to use this tool.");
                    return Result.Failed;
                }

                if (!TempPhaseCompat.IsValidProjectView(view))
                {
                    Log("ManualCommandInvalidProjectView");
                    ShowInfoDialog("Open a valid project view to use this tool.");
                    return Result.Failed;
                }

                DocumentKey docKey = TempPhaseCompat.BuildDocumentKey(doc);
                int? viewId = TempPhaseCompat.ToInt(view.Id);
                if (!viewId.HasValue)
                {
                    ShowInfoDialog("Could not read active view id.");
                    return Result.Failed;
                }

                string sessionKey = TempPhaseCompat.BuildSessionKey(docKey, viewId.Value);
                ViewSession existing = GetSession(sessionKey);
                int? originalPhaseId = existing != null ? (int?)existing.OriginalPhaseId : TempPhaseCompat.GetViewPhaseId(view);
                if (!originalPhaseId.HasValue)
                {
                    ShowInfoDialog("Active view does not support editable Phase.");
                    return Result.Failed;
                }

                List<PhaseChoice> phases = TempPhaseCompat.CollectPhases(doc);
                if (phases.Count == 0)
                {
                    ShowInfoDialog("No phases are available in this document.");
                    return Result.Failed;
                }

                int templateId = existing != null
                    ? existing.TempTemplateId
                    : (TempPhaseCompat.GetTemporaryTemplateReferenceId(view) ?? viewId.Value);

                Log(
                    "ManualPhasePickerOpening"
                    + " view=" + TempPhaseCompat.GetViewDisplayName(view)
                    + " phaseCount=" + phases.Count);
                using (PhasePickerForm picker = new PhasePickerForm(TempPhaseCompat.GetViewDisplayName(view), phases, originalPhaseId.Value))
                {
                    System.Windows.Forms.DialogResult pickerResult = picker.ShowDialog();
                    Log("ManualPhasePickerClosed result=" + pickerResult);
                    if (pickerResult != System.Windows.Forms.DialogResult.OK)
                    {
                        return Result.Cancelled;
                    }

                    int? selectedPhaseId = picker.GetSelectedPhaseId();
                    if (!selectedPhaseId.HasValue)
                    {
                        return Result.Cancelled;
                    }

                    string error;
                    if (!ApplySelectedPhase(doc, view, selectedPhaseId.Value, templateId, out error))
                    {
                        message = error;
                        ShowInfoDialog(error);
                        Log("ManualApplyFailed " + error);
                        return Result.Failed;
                    }

                    ViewSession session = new ViewSession();
                    session.DocumentKey = docKey;
                    session.ViewId = viewId.Value;
                    session.ViewName = TempPhaseCompat.GetViewDisplayName(view);
                    session.OriginalPhaseId = originalPhaseId.Value;
                    session.SelectedPhaseId = selectedPhaseId.Value;
                    session.TempTemplateId = templateId;
                    session.LastSeenTvpActive = TempPhaseCompat.IsTvpActive(view);
                    session.UpdatedAtUtc = DateTime.UtcNow;

                    lock (_syncRoot)
                    {
                        _viewSessions[sessionKey] = session;
                    }

                    Log("ManualApplySuccess view=" + session.ViewName + " phase=" + session.SelectedPhaseId);
                    return Result.Succeeded;
                }
            }
            catch (Exception ex)
            {
                message = ex.Message;
                ShowInfoDialog("Could not apply the selected temporary phase.");
                Log("ManualApplyException " + ExceptionText(ex));
                return Result.Failed;
            }
        }

        public void OnIdling(object sender, IdlingEventArgs e)
        {
            UIApplication uiapp = sender as UIApplication;
            if (uiapp == null)
            {
                return;
            }

            ProcessPendingClose(uiapp);

            if ((DateTime.UtcNow - _lastIdleAtUtc).TotalSeconds < IdleRestoreThrottleSeconds)
            {
                return;
            }

            _lastIdleAtUtc = DateTime.UtcNow;

            UIDocument uidoc = uiapp.ActiveUIDocument;
            Document doc = uidoc == null ? null : uidoc.Document;
            View view = uidoc == null ? null : uidoc.ActiveView;
            if (!TempPhaseCompat.IsSupportedProjectDocument(doc) || !TempPhaseCompat.IsValidProjectView(view))
            {
                return;
            }

            DocumentKey docKey = TempPhaseCompat.BuildDocumentKey(doc);
            int? viewId = TempPhaseCompat.ToInt(view.Id);
            if (!viewId.HasValue)
            {
                return;
            }

            string sessionKey = TempPhaseCompat.BuildSessionKey(docKey, viewId.Value);
            ViewSession session = GetSession(sessionKey);
            if (session == null)
            {
                return;
            }

            bool tvpActive = TempPhaseCompat.IsTvpActive(view);
            if (session.LastSeenTvpActive && !tvpActive)
            {
                string error;
                if (RestoreTrackedView(doc, view, session, out error))
                {
                    RemoveSession(sessionKey);
                    Log("IdleRestoreSuccess view=" + session.ViewName);
                }
                else
                {
                    Log("IdleRestoreFailed view=" + session.ViewName + " error=" + error);
                }
                return;
            }

            session.LastSeenTvpActive = tvpActive;
            session.UpdatedAtUtc = DateTime.UtcNow;
        }

        public void OnDocumentClosing(object sender, DocumentClosingEventArgs e)
        {
            try
            {
                Document doc = TempPhaseCompat.ResolveDocument(e);
                if (!TempPhaseCompat.IsSupportedProjectDocument(doc))
                {
                    return;
                }

                DocumentKey docKey = TempPhaseCompat.BuildDocumentKey(doc);
                string closeKey = TempPhaseCompat.BuildCloseKey(e, docKey);
                if (ConsumeAllowCloseGuard(closeKey, docKey))
                {
                    Log("DocumentClosingAllowed title=" + docKey.Title);
                    return;
                }

                if (!TempPhaseCompat.EventIsCancellable(e))
                {
                    Log("DocumentClosingSkippedNonCancellable title=" + docKey.Title);
                    return;
                }

                TvpSummary summary = CollectSummary(doc);
                if (!summary.HasRestoreWork)
                {
                    return;
                }

                if (!TempPhaseCompat.TryCancelEvent(e))
                {
                    Log("DocumentClosingCancelFailed title=" + docKey.Title);
                    return;
                }

                PendingCloseRequest pending = new PendingCloseRequest
                {
                    CloseKey = closeKey,
                    DocumentKey = docKey,
                    DocumentTitle = docKey.Title,
                    QueuedAtUtc = DateTime.UtcNow,
                    NextTryAtUtc = DateTime.UtcNow.AddSeconds(PendingCloseDelaySeconds)
                };

                lock (_syncRoot)
                {
                    if (!string.IsNullOrEmpty(closeKey))
                    {
                        _pendingCloses[closeKey] = pending;
                    }
                }

                Log("DocumentClosingQueued title=" + docKey.Title);
            }
            catch (Exception ex)
            {
                Log("DocumentClosingException " + ExceptionText(ex));
            }
        }

        public void OnDocumentClosed(object sender, DocumentClosedEventArgs e)
        {
            string closedPath = TempPhaseCompat.GetClosedDocumentPath(e);
            string closedTitle = TempPhaseCompat.GetClosedDocumentTitle(e);
            List<string> removeKeys = new List<string>();

            lock (_syncRoot)
            {
                foreach (KeyValuePair<string, ViewSession> pair in _viewSessions)
                {
                    if (pair.Value == null || pair.Value.DocumentKey == null)
                    {
                        removeKeys.Add(pair.Key);
                        continue;
                    }

                    if (!string.IsNullOrEmpty(closedPath) &&
                        string.Equals(pair.Value.DocumentKey.PathKey, closedPath, StringComparison.OrdinalIgnoreCase))
                    {
                        removeKeys.Add(pair.Key);
                        continue;
                    }

                    if (string.IsNullOrEmpty(closedPath) &&
                        !string.IsNullOrEmpty(closedTitle) &&
                        string.IsNullOrEmpty(pair.Value.DocumentKey.PathKey) &&
                        string.Equals(pair.Value.DocumentKey.Title, closedTitle, StringComparison.OrdinalIgnoreCase))
                    {
                        removeKeys.Add(pair.Key);
                    }
                }

                foreach (string key in removeKeys)
                {
                    _viewSessions.Remove(key);
                }
            }

            string closedKey = TempPhaseCompat.BuildCloseKey(
                e,
                new DocumentKey
                {
                    PathKey = closedPath,
                    Title = closedTitle
                });
            lock (_syncRoot)
            {
                if (!string.IsNullOrEmpty(closedKey))
                {
                    _pendingCloses.Remove(closedKey);
                    _allowCloseGuards.Remove(closedKey);
                }

                if (!string.IsNullOrEmpty(closedPath))
                {
                    List<string> pendingKeys = _pendingCloses
                        .Where(pair => pair.Value != null
                            && pair.Value.DocumentKey != null
                            && string.Equals(pair.Value.DocumentKey.PathKey, closedPath, StringComparison.OrdinalIgnoreCase))
                        .Select(pair => pair.Key)
                        .ToList();
                    foreach (string key in pendingKeys)
                    {
                        _pendingCloses.Remove(key);
                        _allowCloseGuards.Remove(key);
                    }
                }
            }

            if (removeKeys.Count > 0)
            {
                Log("DocumentClosedCleanup count=" + removeKeys.Count);
            }
        }

        private void ProcessPendingClose(UIApplication uiapp)
        {
            List<PendingCloseRequest> ready = new List<PendingCloseRequest>();
            DateTime now = DateTime.UtcNow;
            lock (_syncRoot)
            {
                foreach (PendingCloseRequest pending in _pendingCloses.Values.ToList())
                {
                    if (pending != null && now >= pending.NextTryAtUtc)
                    {
                        ready.Add(pending);
                        _pendingCloses.Remove(pending.CloseKey);
                    }
                }
            }

            foreach (PendingCloseRequest pending in ready)
            {
                try
                {
                    Document doc = ResolveOpenDocument(uiapp, pending.DocumentKey);
                    if (!TempPhaseCompat.IsSupportedProjectDocument(doc))
                    {
                        Log("PendingCloseSkippedMissingDocument title=" + pending.DocumentTitle);
                        continue;
                    }

                    UIDocument activeUidoc = uiapp.ActiveUIDocument;
                    Document activeDoc = activeUidoc == null ? null : activeUidoc.Document;
                    if (!TempPhaseCompat.DocumentKeyMatches(
                        TempPhaseCompat.BuildDocumentKey(activeDoc),
                        TempPhaseCompat.BuildDocumentKey(doc)))
                    {
                        Log("PendingCloseSkippedInactiveDocument title=" + pending.DocumentTitle);
                        continue;
                    }

                    TvpSummary summary = CollectSummary(doc);
                    RestoreResult result = summary.HasRestoreWork
                        ? RestoreSummary(doc, summary)
                        : CreateAlreadyRestoredResult();

                    bool closeNow = ShowCloseDecisionDialog(result.Message);
                    Log("PendingCloseDialog title=" + pending.DocumentTitle + " closeNow=" + closeNow);

                    if (!closeNow)
                    {
                        continue;
                    }

                    if (!TryPostCloseCommand(uiapp, doc, pending.CloseKey))
                    {
                        ShowInfoDialog("Temporary View Properties were restored, but the file close command could not be restarted.");
                    }
                }
                catch (Exception ex)
                {
                    Log("PendingCloseException " + ExceptionText(ex));
                }
            }
        }

        private Document ResolveOpenDocument(UIApplication uiapp, DocumentKey targetKey)
        {
            if (uiapp == null || targetKey == null)
            {
                return null;
            }

            try
            {
                foreach (Document doc in uiapp.Application.Documents)
                {
                    DocumentKey currentKey = TempPhaseCompat.BuildDocumentKey(doc);
                    if (TempPhaseCompat.DocumentKeyMatches(currentKey, targetKey))
                    {
                        return doc;
                    }
                }
            }
            catch (Exception ex)
            {
                Log("ResolveOpenDocumentException " + ExceptionText(ex));
            }

            return null;
        }

        private bool TryPostCloseCommand(UIApplication uiapp, Document doc, string closeKey)
        {
            if (uiapp == null || doc == null)
            {
                return false;
            }

            UIDocument activeUidoc = uiapp.ActiveUIDocument;
            Document activeDoc = activeUidoc == null ? null : activeUidoc.Document;
            if (!TempPhaseCompat.DocumentKeyMatches(TempPhaseCompat.BuildDocumentKey(activeDoc), TempPhaseCompat.BuildDocumentKey(doc)))
            {
                Log("PostCloseCommandSkippedInactiveDocument title=" + TempPhaseCompat.BuildDocumentKey(doc).Title);
                return false;
            }

            try
            {
                RevitCommandId commandId = RevitCommandId.LookupPostableCommandId(PostableCommand.Close);
                if (commandId == null || !uiapp.CanPostCommand(commandId))
                {
                    Log("PostCloseCommandUnavailable command=Close");
                    return false;
                }

                PendingCloseGuard guard = new PendingCloseGuard
                {
                    CloseKey = closeKey,
                    DocumentKey = TempPhaseCompat.BuildDocumentKey(doc),
                    ExpiresAtUtc = DateTime.UtcNow.AddSeconds(AllowCloseGuardSeconds)
                };

                lock (_syncRoot)
                {
                    _allowCloseGuards[closeKey] = guard;
                }

                uiapp.PostCommand(commandId);
                Log("PostCloseCommandSuccess command=Close");
                return true;
            }
            catch (Exception ex)
            {
                Log("PostCloseCommandAttemptFailed command=Close error=" + ExceptionText(ex));
                lock (_syncRoot)
                {
                    _allowCloseGuards.Remove(closeKey);
                }
            }

            return false;
        }

        private bool ConsumeAllowCloseGuard(string closeKey, DocumentKey docKey)
        {
            PendingCloseGuard guard = null;
            lock (_syncRoot)
            {
                if (!string.IsNullOrEmpty(closeKey))
                {
                    _allowCloseGuards.TryGetValue(closeKey, out guard);
                }
            }

            if (guard == null)
            {
                return false;
            }

            if (DateTime.UtcNow > guard.ExpiresAtUtc)
            {
                lock (_syncRoot)
                {
                    _allowCloseGuards.Remove(closeKey);
                }
                return false;
            }

            if (!TempPhaseCompat.DocumentKeyMatches(guard.DocumentKey, docKey))
            {
                return false;
            }

            lock (_syncRoot)
            {
                _allowCloseGuards.Remove(closeKey);
            }
            return true;
        }

        private TvpSummary CollectSummary(Document doc)
        {
            TvpSummary summary = new TvpSummary();
            summary.DocumentKey = TempPhaseCompat.BuildDocumentKey(doc);
            List<string> staleKeys = new List<string>();
            HashSet<int> trackedIds = new HashSet<int>();

            lock (_syncRoot)
            {
                foreach (KeyValuePair<string, ViewSession> pair in _viewSessions)
                {
                    ViewSession session = pair.Value;
                    if (session == null || !TempPhaseCompat.DocumentKeyMatches(session, summary.DocumentKey))
                    {
                        continue;
                    }

                    View view = doc.GetElement(TempPhaseCompat.CreateElementId(session.ViewId)) as View;
                    if (!TempPhaseCompat.IsValidProjectView(view))
                    {
                        staleKeys.Add(pair.Key);
                        continue;
                    }

                    trackedIds.Add(session.ViewId);
                    summary.TrackedRestoreViews.Add(new TrackedViewInfo
                    {
                        SessionKey = pair.Key,
                        ViewId = session.ViewId,
                        ViewName = session.ViewName,
                        OriginalPhaseId = session.OriginalPhaseId
                    });
                }

                foreach (string staleKey in staleKeys)
                {
                    _viewSessions.Remove(staleKey);
                }
            }

            try
            {
                FilteredElementCollector collector = new FilteredElementCollector(doc).OfClass(typeof(View));
                foreach (View view in collector)
                {
                    if (!TempPhaseCompat.IsValidProjectView(view))
                    {
                        continue;
                    }

                    if (!TempPhaseCompat.IsTvpActive(view))
                    {
                        continue;
                    }

                    int? viewId = TempPhaseCompat.ToInt(view.Id);
                    if (!viewId.HasValue)
                    {
                        continue;
                    }

                    ViewInfo info = new ViewInfo();
                    info.ViewId = viewId.Value;
                    info.ViewName = TempPhaseCompat.GetViewDisplayName(view);
                    summary.DiscoverableTvpViews.Add(info);

                    if (!trackedIds.Contains(viewId.Value))
                    {
                        summary.UntrackedTvpViews.Add(info);
                    }
                }
            }
            catch (Exception ex)
            {
                Log("CollectSummaryException " + ExceptionText(ex));
            }

            summary.TrackedRestoreViews = summary.TrackedRestoreViews.OrderBy(v => v.ViewName).ToList();
            summary.DiscoverableTvpViews = summary.DiscoverableTvpViews.OrderBy(v => v.ViewName).ToList();
            summary.UntrackedTvpViews = summary.UntrackedTvpViews.OrderBy(v => v.ViewName).ToList();
            summary.HasBlockingViews = summary.DiscoverableTvpViews.Count > 0;
            summary.HasRestoreWork = summary.TrackedRestoreViews.Count > 0 || summary.UntrackedTvpViews.Count > 0;
            return summary;
        }

        private RestoreResult RestoreSummary(Document doc, TvpSummary summary)
        {
            RestoreResult result = new RestoreResult();
            Transaction transaction = new Transaction(doc, "Temp Phase: Restore Temporary View Properties");

            try
            {
                transaction.Start();

                foreach (TrackedViewInfo tracked in summary.TrackedRestoreViews)
                {
                    View view = doc.GetElement(TempPhaseCompat.CreateElementId(tracked.ViewId)) as View;
                    if (!TempPhaseCompat.IsValidProjectView(view))
                    {
                        result.FailedViews.Add(tracked.ViewName);
                        continue;
                    }

                    bool ok = true;
                    if (TempPhaseCompat.IsTvpActive(view) && !TempPhaseCompat.DisableTvp(view))
                    {
                        ok = false;
                    }

                    if (ok && !TempPhaseCompat.SetViewPhaseId(view, tracked.OriginalPhaseId))
                    {
                        ok = false;
                    }

                    if (ok)
                    {
                        result.RestoredTrackedCount++;
                        result.ClearedSessionKeys.Add(tracked.SessionKey);
                    }
                    else
                    {
                        result.FailedViews.Add(tracked.ViewName);
                    }
                }

                foreach (ViewInfo info in summary.UntrackedTvpViews)
                {
                    View view = doc.GetElement(TempPhaseCompat.CreateElementId(info.ViewId)) as View;
                    if (!TempPhaseCompat.IsValidProjectView(view))
                    {
                        result.FailedViews.Add(info.ViewName);
                        continue;
                    }

                    if (!TempPhaseCompat.IsTvpActive(view))
                    {
                        continue;
                    }

                    if (TempPhaseCompat.DisableTvp(view))
                    {
                        result.DisabledUntrackedCount++;
                    }
                    else
                    {
                        result.FailedViews.Add(info.ViewName);
                    }
                }

                transaction.Commit();
                Log(
                    "RestoreTransactionCommitted"
                    + " tracked=" + result.RestoredTrackedCount
                    + " untracked=" + result.DisabledUntrackedCount
                    + " failed=" + result.FailedViews.Count);
                result.Succeeded = result.FailedViews.Count == 0;
                ClearSessions(result.ClearedSessionKeys);
                result.Message = BuildRestoreMessage(result);
                return result;
            }
            catch (Exception ex)
            {
                try
                {
                    transaction.RollBack();
                }
                catch
                {
                }

                result.Succeeded = false;
                result.Message = "Temporary View Properties could not be restored before closing." + Environment.NewLine + ExceptionText(ex);
                Log("RestoreSummaryException " + ExceptionText(ex));
                return result;
            }
        }

        private bool RestoreTrackedView(Document doc, View view, ViewSession session, out string error)
        {
            error = string.Empty;
            Transaction transaction = new Transaction(doc, "Temp Phase: Restore Phase");

            try
            {
                transaction.Start();
                if (!TempPhaseCompat.SetViewPhaseId(view, session.OriginalPhaseId))
                {
                    error = "Could not restore the original phase.";
                    transaction.RollBack();
                    return false;
                }

                transaction.Commit();
                Log(
                    "RestoreTrackedTransactionCommitted"
                    + " view=" + TempPhaseCompat.GetViewDisplayName(view)
                    + " phase=" + session.OriginalPhaseId);
                return true;
            }
            catch (Exception ex)
            {
                try
                {
                    transaction.RollBack();
                }
                catch
                {
                }

                error = ExceptionText(ex);
                return false;
            }
        }

        private bool ApplySelectedPhase(Document doc, View view, int selectedPhaseId, int templateId, out string error)
        {
            error = string.Empty;
            Transaction transaction = new Transaction(doc, "Temp Phase: Apply Phase");

            try
            {
                transaction.Start();

                if (TempPhaseCompat.IsTvpActive(view) && !TempPhaseCompat.DisableTvp(view))
                {
                    error = "Could not disable Temporary View Properties before applying the selected phase.";
                    transaction.RollBack();
                    return false;
                }

                if (!TempPhaseCompat.SetViewPhaseId(view, selectedPhaseId))
                {
                    error = "Could not apply the selected phase.";
                    transaction.RollBack();
                    return false;
                }

                if (!TempPhaseCompat.EnableTvp(view, templateId))
                {
                    error = "Could not re-enable Temporary View Properties after applying the selected phase.";
                    transaction.RollBack();
                    return false;
                }

                transaction.Commit();
                Log(
                    "ManualApplyTransactionCommitted"
                    + " view=" + TempPhaseCompat.GetViewDisplayName(view)
                    + " phase=" + selectedPhaseId);
                return true;
            }
            catch (Exception ex)
            {
                try
                {
                    transaction.RollBack();
                }
                catch
                {
                }

                error = ExceptionText(ex);
                return false;
            }
        }

        private ViewSession GetSession(string sessionKey)
        {
            lock (_syncRoot)
            {
                ViewSession session;
                _viewSessions.TryGetValue(sessionKey, out session);
                return session;
            }
        }

        private void RemoveSession(string sessionKey)
        {
            lock (_syncRoot)
            {
                if (_viewSessions.ContainsKey(sessionKey))
                {
                    _viewSessions.Remove(sessionKey);
                }
            }
        }

        private void ClearSessions(IList<string> sessionKeys)
        {
            lock (_syncRoot)
            {
                for (int i = 0; i < sessionKeys.Count; i++)
                {
                    string sessionKey = sessionKeys[i];
                    if (_viewSessions.ContainsKey(sessionKey))
                    {
                        _viewSessions.Remove(sessionKey);
                    }
                }
            }
        }

        private static RestoreResult CreateAlreadyRestoredResult()
        {
            RestoreResult result = new RestoreResult();
            result.Succeeded = true;
            result.Message = "All Temporary View Properties were already restored before closing.";
            return result;
        }

        private static string BuildRestoreMessage(RestoreResult result)
        {
            string header = result.FailedViews.Count == 0
                ? "All Temporary View Properties were restored before closing."
                : "Temporary View Properties restore completed with issues before closing.";

            string message = header + Environment.NewLine
                + "Tracked views restored: " + result.RestoredTrackedCount + Environment.NewLine
                + "Additional TVP views disabled: " + result.DisabledUntrackedCount;

            if (result.FailedViews.Count > 0)
            {
                message += Environment.NewLine
                    + "Failed views:" + Environment.NewLine
                    + string.Join(Environment.NewLine, result.FailedViews.ToArray());
            }

            return message;
        }

        private static bool ShowCloseDecisionDialog(string message)
        {
            try
            {
                using (System.Windows.Forms.Form form = new System.Windows.Forms.Form())
                {
                    form.Text = AddinInfo.DialogTitle;
                    form.StartPosition = System.Windows.Forms.FormStartPosition.CenterScreen;
                    form.Width = 540;
                    form.Height = 320;
                    form.FormBorderStyle = System.Windows.Forms.FormBorderStyle.FixedDialog;
                    form.MaximizeBox = false;
                    form.MinimizeBox = false;

                    System.Windows.Forms.TextBox textBox = new System.Windows.Forms.TextBox();
                    textBox.Left = 12;
                    textBox.Top = 12;
                    textBox.Width = 500;
                    textBox.Height = 220;
                    textBox.Multiline = true;
                    textBox.ReadOnly = true;
                    textBox.ScrollBars = System.Windows.Forms.ScrollBars.Vertical;
                    textBox.Text = message;
                    form.Controls.Add(textBox);

                    System.Windows.Forms.Button closeButton = new System.Windows.Forms.Button();
                    closeButton.Text = "Close File Now";
                    closeButton.DialogResult = System.Windows.Forms.DialogResult.OK;
                    closeButton.Left = 288;
                    closeButton.Top = 244;
                    closeButton.Width = 110;
                    form.Controls.Add(closeButton);

                    System.Windows.Forms.Button cancelButton = new System.Windows.Forms.Button();
                    cancelButton.Text = "Cancel Close";
                    cancelButton.DialogResult = System.Windows.Forms.DialogResult.Cancel;
                    cancelButton.Left = 404;
                    cancelButton.Top = 244;
                    cancelButton.Width = 108;
                    form.Controls.Add(cancelButton);

                    form.AcceptButton = closeButton;
                    form.CancelButton = cancelButton;

                    return form.ShowDialog() == System.Windows.Forms.DialogResult.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        private static void ShowInfoDialog(string message)
        {
            try
            {
                TaskDialog.Show(AddinInfo.DialogTitle, message);
            }
            catch
            {
            }
        }

        private static string ExceptionText(Exception ex)
        {
            return ex == null ? string.Empty : ex.GetType().Name + ": " + ex.Message;
        }

        private static void WriteStaticLog(string message)
        {
            try
            {
                string logPath = AddinInfo.GetLogPath();
                string directory = Path.GetDirectoryName(logPath);
                if (!Directory.Exists(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                File.AppendAllText(logPath, DateTime.Now.ToString("O") + " " + message + Environment.NewLine);
            }
            catch
            {
            }
        }
    }
}
