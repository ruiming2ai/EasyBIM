# -*- coding: utf-8 -*-
"""Executed by run_auto_update_native.ps1 in actual IronPython, not unittest.

Only Revit/pyRevit session services are substituted. Repository discovery,
status, fetch, pull, tree/history comparison, disposal and Windows mutexes use
real LibGit2Sharp/.NET. All repositories and remotes are disposable local data.
"""
import clr
clr.AddReference("LibGit2Sharp")
import LibGit2Sharp as native
import imp
import os
from System import DateTimeOffset
from System.IO import File
from System.Threading import ManualResetEvent, Mutex, Thread, ThreadStart


class RepoInfo(object):
    def __init__(self, repo):
        self.repo = repo
        self.directory = repo.Info.WorkingDirectory
        self.name = "EasyBIM"
        self.last_commit_hash = str(repo.Head.Tip.Id.Sha)


class Git(object):
    libgit = native

    @staticmethod
    def get_repo(path):
        return RepoInfo(native.Repository(path))

    @staticmethod
    def compare_branch_heads(info):
        return info.repo.ObjectDatabase.CalculateHistoryDivergence(
            info.repo.Head.Tip, info.repo.Head.TrackedBranch.Tip)


class VersionManager(object):
    @staticmethod
    def get_pyrevit_repo():
        return None


class Host(object):
    def __init__(self, name):
        self.module = imp.load_source(name, module_path)
        self.store = {}
        self.messages = []
        self.fetches = self.pulls = self.reloads = 0
        module = self.module
        module._get_git = lambda: Git
        module._get_version_manager = lambda: VersionManager
        module._get_pyrevit_home = lambda: os.path.join(probe_dir, "unrelated-pyrevit")
        module._get_extension_root = lambda: fixture_dir
        module._get_envvar = lambda key, default=None: self.store.get(key, default)
        module._set_envvar = self.set_envvar
        module._show_message = lambda *a, **kw: self.messages.append((a, kw))
        module._get_native_updater = lambda: self
        module._get_session_manager = lambda: self
        assert module.record_loaded_revision()

    def set_envvar(self, key, value):
        self.store[key] = value
        return True

    def get_updates(self, info):
        self.fetches += 1
        native.Commands.Fetch(info.repo, info.repo.Head.TrackedBranch.RemoteName,
                              [], native.FetchOptions(), "EasyBIM native regression")
        return True

    def update_repo(self, info):
        self.pulls += 1
        signature = native.Signature("EasyBIM Native Test", "test@example.invalid", DateTimeOffset.Now)
        options = native.PullOptions()
        options.FetchOptions = native.FetchOptions()
        native.Commands.Pull(info.repo, signature, options)
        return RepoInfo(info.repo)

    def reload_pyrevit(self):
        self.reloads += 1
        assert self.module.record_loaded_revision()
        assert not self.module.queue_startup_auto_update(), "reload must not queue a second fetch"

    def startup(self):
        assert self.module.queue_startup_auto_update()
        return self.module.run_pending_startup_auto_update()

    def observe(self):
        self.module._SESSION_CHECK_AT[0] = 0.0
        if self.module.has_pending_session_refresh():
            return self.module.run_pending_session_refresh()
        return None


def publish(revision):
    File.WriteAllText(os.path.join(probe_dir, "remote.git", "refs", "heads", "main"), revision + "\n")


def assert_applied(host, result):
    assert result["verified"] and result["reload_status"] == "reloaded", result
    assert not host.store[host.module.AUTO_UPDATE_RUNNING_ENVVAR]
    current = Git.get_repo(fixture_dir)
    try:
        assert host.store[host.module.AUTO_UPDATE_LOADED_ENVVAR]["head"] == current.last_commit_hash
    finally:
        current.repo.Dispose()


# A receiver which already completed startup must notice a manual update.
receiver = Host("receiver")
assert receiver.startup()["status"] == receiver.module.STATUS_UP_TO_DATE
receiver.fetches = 0
writer = Host("writer")
publish(revisions[1])
assert_applied(writer, writer.module.run_manual_auto_update())
assert_applied(receiver, receiver.observe())
assert receiver.fetches == receiver.pulls == 0 and receiver.reloads == 1
assert not receiver.messages and receiver.observe() is None
print("PASS one manual update refreshes an already-started receiver without network or popup")

# An empty local commit seeded by PowerShell makes native Pull produce a merge.
info = Git.get_repo(fixture_dir)
try:
    assert len(list(info.repo.Head.Tip.Parents)) == 2
    assert str(info.repo.Head.Tip.Tree.Id) == str(info.repo.Head.TrackedBranch.Tip.Tree.Id)
finally:
    info.repo.Dispose()
print("PASS receiving verification accepts real file-neutral native merge history")

# A new Revit session's startup must also propagate its later update.
new_session = Host("new_session")
publish(revisions[2])
assert_applied(new_session, new_session.startup())
tracked = os.path.join(fixture_dir, "tracked.txt")
original = File.ReadAllText(tracked)
File.WriteAllText(tracked, original + "local edit\n")
try:
    blocked = receiver.observe()
    assert blocked["status"] == receiver.module.STATUS_LOCAL_CHANGES, blocked
    assert receiver.reloads == 1
    assert File.ReadAllText(tracked) == original + "local edit\n"
finally:
    File.WriteAllText(tracked, original)
assert_applied(receiver, receiver.observe())
assert receiver.reloads == 2 and receiver.fetches == receiver.pulls == 0
print("PASS new-session startup propagates next update; dirty receiver defers and preserves edits")

# Use a different native thread: a Mutex is recursive on its owning thread.
waiting = Host("waiting")
File.WriteAllText(tracked, original + "writer is replacing files\n")
waiting.module.record_loaded_revision()
File.WriteAllText(tracked, original)
assert waiting.store[waiting.module.AUTO_UPDATE_LOADED_ENVVAR] is None
ready, release = ManualResetEvent(False), ManualResetEvent(False)
thread_errors = []
def hold_writer_mutex():
    mutex = Mutex(False, waiting.module.AUTO_UPDATE_MUTEX_NAME)
    try:
        assert mutex.WaitOne(5000, False)
        ready.Set()
        assert release.WaitOne(10000, False)
        mutex.ReleaseMutex()
    except Exception as error:
        thread_errors.append(str(error))
        ready.Set()
    finally:
        mutex.Dispose()
thread = Thread(ThreadStart(hold_writer_mutex))
thread.Start()
try:
    assert ready.WaitOne(10000, False) and not thread_errors
    busy = waiting.startup()
    assert busy["status"] == waiting.module.STATUS_SKIPPED_LOCKED, busy
    assert waiting.fetches == 0
    assert not waiting.module.has_pending_startup_auto_update(), "busy retry must be throttled"
finally:
    release.Set()
    thread.Join(10000)
    ready.Dispose()
    release.Dispose()
assert not thread_errors
waiting.store[waiting.module.AUTO_UPDATE_RETRY_ENVVAR] = 0.0
assert waiting.module.has_pending_startup_auto_update()
assert_applied(waiting, waiting.module.run_pending_startup_auto_update())
assert waiting.reloads == 1
assert not waiting.module.has_pending_startup_auto_update()
print("PASS native mutex contention retries automatically and repairs a missing startup baseline")
print("Runtime: " + str(__import__("sys").version).splitlines()[0])
print("LibGit2Sharp: " + str(clr.GetClrType(native.Repository).Assembly.GetName().Version))
