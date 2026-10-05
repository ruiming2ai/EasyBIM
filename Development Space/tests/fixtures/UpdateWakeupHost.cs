// Off-host API-shaped fixture only. This is NOT Autodesk's Revit runtime.
// Used to exercise IronPython's managed interface bridge and real .NET Timer.
using System;
using System.Threading;
namespace Autodesk.Revit.UI.Events {
    public sealed class ApplicationClosingEventArgs : EventArgs { }
}
namespace Autodesk.Revit.UI {
    public sealed class UIApplication {
        public event EventHandler<Events.ApplicationClosingEventArgs> ApplicationClosing;
        public void CloseForTest() {
            if (ApplicationClosing != null) ApplicationClosing(this, new Events.ApplicationClosingEventArgs());
        }
    }
    public interface IExternalEventHandler {
        void Execute(UIApplication app);
        string GetName();
    }
    public enum ExternalEventRequest { Accepted, Pending, Denied }
    public sealed class ExternalEvent : IDisposable {
        private readonly IExternalEventHandler handler;
        private int pending;
        public static ExternalEvent LastCreated;
        public readonly ManualResetEvent Raised = new ManualResetEvent(false);
        public int RaiseCount;
        public int RaiseThread;
        public bool Disposed;
        private ExternalEvent(IExternalEventHandler h) { handler = h; }
        public static ExternalEvent Create(IExternalEventHandler h) {
            LastCreated = new ExternalEvent(h);
            return LastCreated;
        }
        public ExternalEventRequest Raise() {
            if (Disposed) throw new ObjectDisposedException("test event");
            RaiseThread = Thread.CurrentThread.ManagedThreadId;
            Interlocked.Increment(ref RaiseCount);
            Raised.Set();
            return Interlocked.Exchange(ref pending, 1) == 0
                ? ExternalEventRequest.Accepted : ExternalEventRequest.Pending;
        }
        public void ExecuteForTest(UIApplication app) {
            Interlocked.Exchange(ref pending, 0);
            handler.Execute(app);
        }
        public string NameForTest() { return handler.GetName(); }
        public void Dispose() { Disposed = true; }
    }
}
