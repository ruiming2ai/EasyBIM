# -*- coding: utf-8 -*-
"""Real IronPython/.NET scheduling; an explicitly FAKE Revit event pump.

The previous native script supplies receiver/Git fixtures in the shared scope.
This verifies managed interface creation, timer thread separation, coalescing,
reload teardown, ApplicationClosing and local-checkout recovery. It does not
validate Autodesk's event timing or ribbon UI in an actual Revit process.
"""
import clr
import imp
import os
from System.Threading import Thread

clr.AddReferenceToFileAndPath(test_api_dll)
from Autodesk.Revit.UI import ExternalEvent, UIApplication

wakeup = imp.load_source('native_wakeup', wakeup_module_path)
wakeup.INTERVAL_MS = 30  # Fast fixture; production remains 10 seconds.
store = {}
wakeup._get_runtime = lambda: store.get('runtime')
def set_runtime(value):
    store['runtime'] = value
    return True
wakeup._set_runtime = set_runtime
main_thread = Thread.CurrentThread.ManagedThreadId
uiapp = UIApplication()
callbacks = []

def run_receiver(app):
    assert Thread.CurrentThread.ManagedThreadId == main_thread
    assert app == uiapp
    callbacks.append('execute')
    result = receiver.observe()
    if result is not None:
        assert_applied(receiver, result)

# Missing baseline after startup: recover using real clean local Git, no fetch.
receiver.store[receiver.module.AUTO_UPDATE_LOADED_ENVVAR] = None
previous_reload_count = receiver.reloads
assert wakeup.install(uiapp, run_receiver)
first = ExternalEvent.LastCreated
assert first.NameForTest() == 'EasyBIM installed-revision check'
assert first.Raised.WaitOne(5000, False), 'real .NET Timer did not signal'
Thread.Sleep(100)
assert first.RaiseThread != main_thread, 'timer should be a worker callback'
assert first.RaiseCount == 1, 'pending event must be coalesced'
assert not callbacks, 'timer must never execute update/Git/UI work'
first.ExecuteForTest(uiapp)
assert len(callbacks) == 1 and receiver.reloads == previous_reload_count + 1
assert receiver.fetches == receiver.pulls == 0 and not receiver.messages
print('PASS real .NET timer signals a managed interface; update runs only on the simulated API thread')

# Replacement during Execute must not dispose the currently executing event.
def replace_inside_execute(app):
    assert wakeup.install(app, run_receiver)
    assert not first.Disposed
store['runtime'].callback = replace_inside_execute
first.ExecuteForTest(uiapp)
second = ExternalEvent.LastCreated
assert first.Disposed and not second.Disposed
assert second.Raised.WaitOne(5000, False)
second.ExecuteForTest(uiapp)
assert len(callbacks) == 2
assert not second.Disposed
uiapp.CloseForTest()
assert second.Disposed
count = second.RaiseCount
Thread.Sleep(100)
assert second.RaiseCount == count
first.Raised.Dispose()
second.Raised.Dispose()
print('PASS reload defers old event disposal, retains replacement and closes the native timer on ApplicationClosing')

# Same-revision retry after transient reload failure (native repository checks).
receiver.store[receiver.module.AUTO_UPDATE_LOADED_ENVVAR] = None
original_reload = receiver.reload_pyrevit
def fail_reload():
    raise RuntimeError('transient simulated loader failure')
receiver.reload_pyrevit = fail_reload
failed = receiver.observe()
assert failed['reload_status'] == 'failed'
retry = receiver.store[receiver.module.AUTO_UPDATE_REFRESH_FAILED_ENVVAR]
assert retry['attempts'] == 1
retry['retry_at'] = 0.0
receiver.reload_pyrevit = original_reload
assert_applied(receiver, receiver.observe())
print('PASS native receiving-session verification recovers the same revision after a transient reload failure')
