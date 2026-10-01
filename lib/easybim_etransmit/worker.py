# -*- coding: utf-8 -*-
"""Out-of-process Revit repair worker for e-transmit package copies.

The parent Revit session can already have the source local/central/cloud model
open. Revit refuses to open another copy of that same workshared identity in
the same process, which makes in-process package repathing impossible for those
hosts. This module launches the *same installed Revit version* as a second
process, hands it one JSON job through an inherited environment variable, and
waits for that process to repair/save the task-owned package copy.

The worker never opens or writes the user's source model. It receives only
EasyBIM-owned stage/target paths plus already-collected dependency targets.
"""
from __future__ import unicode_literals

import io
import json
import os
import shutil
import tempfile
import time
import traceback

from . import files as f
from . import VERSION


JOB_ENV = 'EASYBIM_ETRANSMIT_WORKER_JOB'
MODE_ENV = 'EASYBIM_ETRANSMIT_WORKER_MODE'
JOB_FORMAT = 1
DEFAULT_TIMEOUT_SECONDS = 7200
_WORKER_RUNNING = [False]
_WORKER_DONE = [False]
_DIALOG_HANDLER = [None]
_DIALOG_SOURCE = [None]
_FAILURE_HANDLER = [None]
_FAILURE_SOURCE = [None]
_SUPPRESSED_DIALOGS = []
_SUPPRESSED_FAILURES = []


class WorkerError(RuntimeError):
    pass


def _dialog_result_candidates(dialog_id='', message=''):
    """Prefer a non-blocking, non-save response for worker-only dialogs."""
    value=(f.text(dialog_id or '')+' '+f.text(message or '')).lower()
    # Never let the disposable worker save an unexpected document or sync.
    if any(word in value for word in ('save changes','synchronize','sync with central',
                                       'relinquish','overwrite source')):
        return [7,2,1,8,1001]  # IDNO, IDCANCEL, IDOK, IDCLOSE, first command.
    # For upgrade/transmitted/missing-reference warnings the useful action is
    # normally the first command or OK/Yes.
    if any(word in value for word in ('upgrade','transmitted','missing','not found',
                                       'unloaded','continue','open anyway')):
        return [1001,1,6,8,7,2]
    # Generic worker dialogs: dismiss rather than waiting forever. Revit
    # validates whether a code is legal for that concrete dialog.
    return [1,1001,6,7,8,2,5]


def _on_dialog_box_showing(sender, args):
    del sender
    dialog_id=f.text(getattr(args,'DialogId','') or '')
    message=f.text(getattr(args,'Message','') or '')
    accepted=None
    for code in _dialog_result_candidates(dialog_id,message):
        try:
            if bool(args.OverrideResult(int(code))):
                accepted=int(code)
                break
        except Exception:
            continue
    _SUPPRESSED_DIALOGS.append(dict(dialog_id=dialog_id,message=message,
                                    result_code=accepted,
                                    dismissed=accepted is not None,
                                    time=time.time()))


def _on_failures_processing(sender, args):
    del sender
    try:
        from Autodesk.Revit.DB import FailureSeverity, FailureProcessingResult
        accessor=args.GetFailuresAccessor()
        has_error=False
        for failure in list(accessor.GetFailureMessages()):
            try:
                severity=failure.GetSeverity()
                description=f.text(failure.GetDescriptionText() or '')
            except Exception:
                severity=None;description=''
            if severity==FailureSeverity.Warning:
                try:
                    accessor.DeleteWarning(failure)
                    _SUPPRESSED_FAILURES.append(dict(severity='Warning',
                                                     message=description,
                                                     action='DELETED'))
                except Exception:
                    pass
            else:
                has_error=True
                _SUPPRESSED_FAILURES.append(dict(severity=f.text(severity),
                                                 message=description,
                                                 action='ROLLBACK'))
        if has_error:
            try:accessor.SetClearAfterRollback(True)
            except Exception:pass
            args.SetProcessingResult(FailureProcessingResult.ProceedWithRollBack)
        else:
            args.SetProcessingResult(FailureProcessingResult.Continue)
    except Exception:
        # Never let the suppression handler itself become a worker blocker.
        pass


def install_unattended_handlers(application):
    """Install non-interactive dialog/failure handling in the worker process."""
    if not is_worker_process() or application is None:
        return False
    try:
        from System import EventHandler
    except Exception:
        EventHandler=None

    dialog_source=None
    for candidate in (application,getattr(application,'uiapp',None)):
        if candidate is None:continue
        try:
            getattr(candidate,'DialogBoxShowing')
            dialog_source=candidate;break
        except Exception:
            pass
    if dialog_source is not None and _DIALOG_HANDLER[0] is None:
        try:
            from Autodesk.Revit.UI.Events import DialogBoxShowingEventArgs
            handler=(EventHandler[DialogBoxShowingEventArgs](_on_dialog_box_showing)
                     if EventHandler is not None else _on_dialog_box_showing)
            dialog_source.DialogBoxShowing += handler
            _DIALOG_HANDLER[0]=handler;_DIALOG_SOURCE[0]=dialog_source
        except Exception:
            pass

    failure_source=None
    candidates=[getattr(application,'ControlledApplication',None),
                getattr(application,'Application',None)]
    for candidate in candidates:
        if candidate is None:continue
        try:
            getattr(candidate,'FailuresProcessing')
            failure_source=candidate;break
        except Exception:
            pass
    if failure_source is not None and _FAILURE_HANDLER[0] is None:
        try:
            from Autodesk.Revit.DB.Events import FailuresProcessingEventArgs
            handler=(EventHandler[FailuresProcessingEventArgs](_on_failures_processing)
                     if EventHandler is not None else _on_failures_processing)
            failure_source.FailuresProcessing += handler
            _FAILURE_HANDLER[0]=handler;_FAILURE_SOURCE[0]=failure_source
        except Exception:
            pass
    return _DIALOG_HANDLER[0] is not None or _FAILURE_HANDLER[0] is not None


def _write_json(path, value):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    temp = path + '.tmp'
    with io.open(temp, 'w', encoding='utf-8') as out:
        out.write(f.text(json.dumps(value, ensure_ascii=False, indent=2)))
    if os.path.exists(path):
        os.remove(path)
    os.rename(temp, path)


def _read_json(path):
    with io.open(path, 'r', encoding='utf-8-sig') as stream:
        return json.loads(stream.read())


def is_worker_process(env=None):
    env = os.environ if env is None else env
    return f.text(env.get(MODE_ENV, '') or '').strip() == '1'


def pending_job_path(env=None):
    env = os.environ if env is None else env
    return f.text(env.get(JOB_ENV, '') or '').strip()


def has_pending_job(env=None):
    return bool(is_worker_process(env) and pending_job_path(env))


def _job_payload(stage, target, rows, options, application, job_dir):
    result_path = os.path.join(job_dir, 'result.json')
    status_path = os.path.join(job_dir, 'status.json')
    safe_options = dict(options or {})
    # Parent-only callbacks/identity are not JSON or worker concerns.
    safe_options.pop('_worker_pulse', None)
    safe_options.pop('_host_source', None)
    safe_options['verify_in_process'] = False
    safe_options['worker_mode'] = True
    return dict(
        format=JOB_FORMAT,
        tool_version=VERSION,
        version=f.text(getattr(application, 'VersionNumber', '') or ''),
        stage=stage,
        target=target,
        package_root=os.path.dirname(target),
        staging_root=os.path.dirname(stage),
        rows=list(rows or []),
        options=safe_options,
        result_path=result_path,
        status_path=status_path,
    )


def _current_revit_executable():
    try:
        from System.Diagnostics import Process
        value = f.text(Process.GetCurrentProcess().MainModule.FileName)
    except Exception as exc:
        raise WorkerError('Could not determine the running Revit executable: ' + f.text(exc))
    if not value or not os.path.isfile(value):
        raise WorkerError('The running Revit executable could not be located.')
    if os.path.basename(value).lower() != 'revit.exe':
        raise WorkerError('Separate repair requires Revit.exe; current process is ' + value)
    return value


def _start_process(executable, job_path):
    try:
        from System.Diagnostics import Process, ProcessStartInfo
        info = ProcessStartInfo()
        info.FileName = executable
        info.WorkingDirectory = os.path.dirname(executable)
        info.UseShellExecute = False
        info.CreateNoWindow = False
        info.EnvironmentVariables[MODE_ENV] = '1'
        info.EnvironmentVariables[JOB_ENV] = job_path
        process = Process()
        process.StartInfo = info
        if not process.Start():
            raise WorkerError('Windows did not start the separate Revit repair process.')
        return process
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError('Could not start the separate Revit repair process: ' + f.text(exc))


def _pump_windows_messages():
    try:
        from System.Windows.Forms import Application
        Application.DoEvents()
    except Exception:
        pass


def _stop_process(process):
    if process is None:
        return
    try:
        if process.HasExited:
            return
    except Exception:
        pass
    try:
        process.CloseMainWindow()
        process.WaitForExit(3000)
    except Exception:
        pass
    try:
        if not process.HasExited:
            process.Kill()
            process.WaitForExit(5000)
    except Exception:
        pass
    try:
        if process.HasExited:return
    except Exception:
        pass
    error=WorkerError('The separate Revit worker exit could not be confirmed. Keep its package and recovery files until the process exits.')
    error.worker_may_be_running=True
    raise error


def _merge_rows(original, returned):
    by_key = {}
    for row in returned or []:
        key = (f.text(row.get('kind', '')),
               f.text(row.get('element_id', row.get('id', ''))))
        by_key[key] = row
    for row in original or []:
        key = (f.text(row.get('kind', '')),
               f.text(row.get('element_id', row.get('id', ''))))
        updated = by_key.get(key)
        if updated:
            row.update(updated)


def run_separate_revit(application, stage, target, rows, options,
                       cancelled=None, pulse=None, timeout_seconds=None,
                       process_factory=None, executable=None, sleeper=None,
                       clock=None):
    """Run one package-copy repair in another Revit process and wait for save."""
    f.validate_destination_path(stage)
    f.validate_destination_path(target)
    job_dir = tempfile.mkdtemp(prefix='EasyBIM_ET_RevitWorker_')
    job_path = os.path.join(job_dir, 'job.json')
    payload = _job_payload(stage, target, rows, options, application, job_dir)
    _write_json(job_path, payload)

    timeout = int(timeout_seconds or options.get('worker_timeout_seconds') or
                  DEFAULT_TIMEOUT_SECONDS)
    process = None
    accepted_success=False
    sleep = sleeper or time.sleep
    now = clock or time.time
    started = now()
    try:
        exe = executable or _current_revit_executable()
        process = (process_factory(exe, job_path) if process_factory
                   else _start_process(exe, job_path))
        last_phase = ''
        while True:
            if cancelled and cancelled():
                _stop_process(process)
                raise f.Cancelled()

            if os.path.isfile(payload['result_path']):
                result = _read_json(payload['result_path'])
                if result.get('status') != 'SUCCEEDED':
                    message = result.get('message') or 'The separate Revit repair failed.'
                    raise WorkerError(message + ' Diagnostics: ' + job_dir)
                raw = result.get('processing_result')
                if options.get('independent_host') and (
                        not isinstance(raw, dict) or not raw.get('host_finalized')
                        or not raw.get('saved_references_checked')):
                    details='; '.join(f.text(i.get('message','')) for i in
                                     (raw.get('issues') or []) if isinstance(i,dict)) if isinstance(raw,dict) else ''
                    raise WorkerError('The worker did not confirm an untransmitted, saved package host. '+details+' Diagnostics: ' + job_dir)
                # A result is written after every document closes. Also require
                # process exit before allowing the parent to publish or recover.
                process.WaitForExit(10000)
                _stop_process(process)
                _merge_rows(rows, result.get('rows', []))
                accepted_success=True
                if isinstance(raw, dict):
                    raw = dict(raw)
                    raw['worker_repaired'] = True
                    raw['worker_process_id'] = result.get('process_id')
                    raw['worker_suppressed_dialogs'] = list(result.get('suppressed_dialogs') or [])
                    raw['worker_suppressed_failures'] = list(result.get('suppressed_failures') or [])
                    return raw
                return dict(issues=list(raw or []), verified_in_process=False,
                            worker_repaired=True,
                            worker_process_id=result.get('process_id'),
                            worker_suppressed_dialogs=list(result.get('suppressed_dialogs') or []),
                            worker_suppressed_failures=list(result.get('suppressed_failures') or []))

            if os.path.isfile(payload['status_path']):
                try:
                    status = _read_json(payload['status_path'])
                    phase = f.text(status.get('phase', '') or '')
                    if phase and phase != last_phase:
                        last_phase = phase
                        if pulse:
                            pulse('SEPARATE REVIT REPAIR | ' + phase, 0, 1)
                except Exception:
                    pass

            try:
                if process.HasExited:
                    raise WorkerError(
                        'The separate Revit repair process exited before saving a result. '
                        'Diagnostics: ' + job_dir)
            except AttributeError:
                pass

            if now() - started > timeout:
                _stop_process(process)
                raise WorkerError(
                    'Separate Revit repair timed out after {0} seconds. Diagnostics: {1}'.format(
                        timeout, job_dir))

            _pump_windows_messages()
            sleep(0.25)
    except Exception:
        # Keep the job/result/status directory on failure for diagnosis.
        if process is not None:_stop_process(process)
        raise
    finally:
        if process is not None:
            try:
                if os.path.isfile(payload['result_path']):
                    process.WaitForExit(10000)
            except Exception:
                pass
            try:
                if not process.HasExited and os.path.isfile(payload['result_path']):
                    _stop_process(process)
            except Exception:
                pass
            try:
                process.Dispose()
            except Exception:
                pass
        # A successful result is already reflected in the package/report, so
        # don't leave a large trail of one-shot job files in %TEMP%.
        try:
            if os.path.isfile(payload['result_path']):
                result = _read_json(payload['result_path'])
                if accepted_success and result.get('status') == 'SUCCEEDED':
                    shutil.rmtree(job_dir)
        except Exception:
            pass


def _resolve_uiapp(sender=None):
    try:
        from pyrevit import HOST_APP
        value = getattr(HOST_APP, 'uiapp', None)
        if value is not None:
            return value
    except Exception:
        pass
    if sender is not None and hasattr(sender, 'ActiveUIDocument') and hasattr(sender, 'Application'):
        return sender
    return None


def _request_exit(uiapp):
    try:
        from Autodesk.Revit.UI import RevitCommandId, PostableCommand
        command = RevitCommandId.LookupPostableCommandId(PostableCommand.ExitRevit)
        uiapp.PostCommand(command)
        return True
    except Exception:
        return False


def _worker_result(job, status, message='', processing_result=None, rows=None):
    value = dict(status=status, message=f.text(message or ''),
                 processing_result=processing_result, rows=list(rows or []),
                 suppressed_dialogs=list(_SUPPRESSED_DIALOGS),
                 suppressed_failures=list(_SUPPRESSED_FAILURES),
                 completed_at=time.time())
    try:
        from System.Diagnostics import Process
        value['process_id'] = int(Process.GetCurrentProcess().Id)
    except Exception:
        value['process_id'] = None
    return value


def _run_job(job, uiapp):
    if int(job.get('format', 0) or 0) != JOB_FORMAT:
        raise WorkerError('Unsupported e-transmit worker job format.')
    if job.get('tool_version') and job['tool_version']!=VERSION:
        raise WorkerError('Worker EasyBIM version mismatch: expected '+f.text(job['tool_version'])+', loaded '+VERSION+'. Check the installed extension path.')
    if uiapp is None:
        raise WorkerError('UIApplication is not available in the worker Revit process.')
    app = uiapp.Application
    expected = f.text(job.get('version', '') or '')
    actual = f.text(getattr(app, 'VersionNumber', '') or '')
    if expected and actual and expected != actual:
        raise WorkerError('Worker Revit version mismatch: expected {0}, started {1}.'.format(
            expected, actual))

    stage = f.text(job.get('stage', '') or '')
    target = f.text(job.get('target', '') or '')
    if not os.path.isfile(stage):
        raise WorkerError('Worker stage RVT is missing: ' + stage)

    from pyrevit import DB
    from .revit import Backend

    options = dict(job.get('options') or {})
    options['verify_in_process'] = False
    options['worker_mode'] = True
    rows = list(job.get('rows') or [])
    backend = Backend(DB, app, f.text(job.get('package_root') or os.path.dirname(target)))
    backend.set_staging_root(f.text(job.get('staging_root') or os.path.dirname(stage)))
    raw = backend.finish(stage, target, rows, options)
    return raw, rows


def run_pending(sender=None):
    """Consume one inherited worker job on Revit Idling and request process exit."""
    if not is_worker_process():
        return False
    if _WORKER_DONE[0]:
        uiapp = _resolve_uiapp(sender)
        if uiapp is not None:
            _request_exit(uiapp)
        return True
    if _WORKER_RUNNING[0]:
        return True

    job_path = pending_job_path()
    if not job_path:
        return False

    uiapp = _resolve_uiapp(sender)
    # During application initialization HOST_APP.uiapp can still be None. The
    # job must wait for the next Idling tick instead of failing before Revit is
    # interactive enough to open a document.
    if uiapp is None:
        return True

    install_unattended_handlers(uiapp)
    _SUPPRESSED_DIALOGS[:] = []
    _SUPPRESSED_FAILURES[:] = []

    _WORKER_RUNNING[0] = True
    try:
        job = _read_json(job_path)
        _write_json(job['status_path'], dict(phase='repairing package copy',
                                             started_at=time.time()))
        raw, rows = _run_job(job, uiapp)
        _write_json(job['result_path'],
                    _worker_result(job, 'SUCCEEDED',
                                   processing_result=raw, rows=rows))
    except Exception as exc:
        try:
            job = locals().get('job') or _read_json(job_path)
            message = f.text(exc)
            _write_json(job['result_path'],
                        _worker_result(job, 'FAILED',
                                       message=message + '\n' + traceback.format_exc(),
                                       rows=job.get('rows', [])))
        except Exception:
            pass
    finally:
        _WORKER_RUNNING[0] = False
        _WORKER_DONE[0] = True
        # Keep worker mode set so EasyBIM's ordinary startup/auto-update consumers
        # never run in this disposable process.
        try:
            os.environ[JOB_ENV] = ''
        except Exception:
            pass
        if uiapp is not None:
            _request_exit(uiapp)
    return True
