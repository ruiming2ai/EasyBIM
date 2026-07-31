using System;
using System.Collections.Generic;

namespace EasyBIM.TempPhase
{
    internal sealed class DocumentKey
    {
        public string PathKey;
        public string RuntimeKey;
        public string Title;
    }

    internal sealed class ViewSession
    {
        public DocumentKey DocumentKey;
        public int ViewId;
        public string ViewName;
        public int OriginalPhaseId;
        public int SelectedPhaseId;
        public int TempTemplateId;
        public bool LastSeenTvpActive;
        public DateTime UpdatedAtUtc;
    }

    internal sealed class TrackedViewInfo
    {
        public string SessionKey;
        public int ViewId;
        public string ViewName;
        public int OriginalPhaseId;
    }

    internal sealed class ViewInfo
    {
        public int ViewId;
        public string ViewName;
    }

    internal sealed class TvpSummary
    {
        public DocumentKey DocumentKey;
        public List<TrackedViewInfo> TrackedRestoreViews;
        public List<ViewInfo> DiscoverableTvpViews;
        public List<ViewInfo> UntrackedTvpViews;
        public bool HasRestoreWork;
        public bool HasBlockingViews;

        public TvpSummary()
        {
            TrackedRestoreViews = new List<TrackedViewInfo>();
            DiscoverableTvpViews = new List<ViewInfo>();
            UntrackedTvpViews = new List<ViewInfo>();
        }
    }

    internal sealed class RestoreResult
    {
        public bool Succeeded;
        public int RestoredTrackedCount;
        public int DisabledUntrackedCount;
        public List<string> FailedViews;
        public string Message;
        public List<string> ClearedSessionKeys;

        public RestoreResult()
        {
            FailedViews = new List<string>();
            ClearedSessionKeys = new List<string>();
            Message = string.Empty;
        }
    }

    internal sealed class PendingCloseRequest
    {
        public string CloseKey;
        public DocumentKey DocumentKey;
        public string DocumentTitle;
        public DateTime QueuedAtUtc;
        public DateTime NextTryAtUtc;
    }

    internal sealed class PendingCloseGuard
    {
        public string CloseKey;
        public DocumentKey DocumentKey;
        public DateTime ExpiresAtUtc;
    }

    internal sealed class PhaseChoice
    {
        public int PhaseId;
        public string PhaseName;

        public override string ToString()
        {
            return PhaseName;
        }
    }
}
