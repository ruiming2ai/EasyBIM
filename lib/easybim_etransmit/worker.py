# -*- coding: utf-8 -*-
"""Persistent out-of-process Revit repair worker for e-transmit.

One disposable Revit process is reused for every repair job in the current
batch. The user's working Revit process never opens, saves or closes package
copies. The worker processes one RVT at a time, suppresses modal Revit UI, saves
the package copy, and waits for the next job until the parent ends the batch.
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


JOB_ENV = 'EASYBIM_ETRANSMIT_WORKER_JOB'
MODE_ENV = 'EASYBIM_ETRANSMIT_WORKER_MODE'
JOB_FORMAT = 2
DEFAULT_TIMEOUT_SECONDS = 7200

_WORKER_RUNNING = [False]
_WORKER_LAST_JOB = [None]
_AUTOMATION_INSTALLED = [False]
_DIALOG_HANDLER = [None]
_FAILURE_HANDLER = [None]
_SUPPRESSED = []


class WorkerError(RuntimeError):
    pass


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


def _safe_remove(path):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def is_worker_process(env=None):
    env = os.environ if env is None else env
    return f.text(env.get(MODE_ENV, '') or '').strip() == '1'


def pending_job_path(env=None):
    env = os.environ if env is None else env
    return f.text(env.get(JOB_ENV, '') or '').strip()


def has_pending_job(env=None):
    return bool(is_worker_process(env) and pending_job_path(env))


def _job_payload(stage, target, rows, options, application, session_dir, job_id):
    result_path = os.path.join(session_dir, 'result-{0}.json'.format(job_id))
    status_path = os.path.join(session_dir, 'status-{0}.json'.format(job_id))
    safe_options = dict(options or {})
    safe_options.pop('_worker_pulse', None)
    safe_options.pop('_host_source', None)
    safe_options['verify_in_process'] = False
    safe_options['worker_mode'] = True
    # Mirror the early e-transmit behavior: make the package copy its own
    # workshared central before relative image/PDF paths are saved. Do not mark
    # this repaired copy transmitted afterward.
    safe_options['package_central'] = True
    return dict(
        format=JOB_FORMAT,
        action='REPAIR',
        job_id=int(job_id),
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
        return
    try:
        process.CloseMainWindow()
        process.WaitForExit(5000)
    except Exception:
        pass
    try:
        if not process.HasExited:
            process.Kill()
            process.WaitForExit(5000)
    except Exception:
        pass


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


class WorkerSession(object):
    """One Revit process reused sequentially for every host in a batch."""

    def __init__(self, application, cancelled=None, pulse=None,
                 timeout_seconds=None, process_factory=None, executable=None,
                 sleeper=None, clock=None):
        self.application = application
        self.cancelled = cancelled
        self.pulse = pulse
        self.timeout = int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        self.process_factory = process_factory
        self.executable = executable
        self.sleep = sleeper or time.sleep
        self.clock = clock or time.time
        self.session_dir = tempfile.mkdtemp(prefix='EasyBIM_ET_RevitWorker_')
        self.job_path = os.path.join(self.session_dir, 'job.json')
        self.process = None
        self.job_id = 0
        self.failed = False
        self.process_id = None

    def _alive(self):
        if self.process is None:
            return False
        try:
            return not bool(self.process.HasExited)
        except Exception:
            return True

    def _ensure_process(self):
        if self._alive():
            return False
        if self.process is not None:
            try:self.process.Dispose()
            except Exception:pass
            self.process = None
        exe = self.executable or _current_revit_executable()
        self.process = (self.process_factory(exe, self.job_path)
                        if self.process_factory else _start_process(exe, self.job_path))
        return True

    def _wait(self, payload):
        started = self.clock()
        last_phase = ''
        while True:
            if self.cancelled and self.cancelled():
                _stop_process(self.process)
                self.failed = True
                raise f.Cancelled()

            if os.path.isfile(payload['result_path']):
                result = _read_json(payload['result_path'])
                if int(result.get('job_id', -1)) != int(payload['job_id']):
                    self.sleep(0.05)
                    continue
                if result.get('status') != 'SUCCEEDED':
                    self.failed = True
                    message = result.get('message') or 'The separate Revit repair failed.'
                    raise WorkerError(message + ' Diagnostics: ' + self.session_dir)
                return result

            if os.path.isfile(payload['status_path']):
                try:
                    status = _read_json(payload['status_path'])
                    if int(status.get('job_id', -1)) == int(payload['job_id']):
                        phase = f.text(status.get('phase', '') or '')
                        if phase and phase != last_phase:
                            last_phase = phase
                            if self.pulse:
                                self.pulse('SEPARATE REVIT REPAIR | ' + phase, 0, 1)
                except Exception:
                    pass

            try:
                if self.process.HasExited:
                    self.failed = True
                    raise WorkerError(
                        'The separate Revit repair process exited before saving a result. '
                        'Diagnostics: ' + self.session_dir)
            except AttributeError:
                pass

            if self.clock() - started > self.timeout:
                _stop_process(self.process)
                self.failed = True
                raise WorkerError(
                    'Separate Revit repair timed out after {0} seconds. Diagnostics: {1}'.format(
                        self.timeout, self.session_dir))

            _pump_windows_messages()
            self.sleep(0.25)

    def run(self, stage, target, rows, options):
        f.validate_destination_path(stage)
        f.validate_destination_path(target)
        self.job_id += 1
        payload = _job_payload(stage, target, rows, options,
                               self.application, self.session_dir, self.job_id)
        _safe_remove(payload['result_path'])
        _safe_remove(payload['status_path'])
        _write_json(self.job_path, payload)
        started_new = self._ensure_process()
        result = self._wait(payload)
        _merge_rows(rows, result.get('rows', []))
        self.process_id = result.get('process_id')
        raw = result.get('processing_result')
        if isinstance(raw, dict):
            raw = dict(raw)
        else:
            raw = dict(issues=list(raw or []), verified_in_process=False)
        raw['worker_repaired'] = True
        raw['worker_process_id'] = self.process_id
        raw['worker_session_reused'] = bool(not started_new and self.job_id > 1)
        raw['suppressed_dialogs'] = list(result.get('suppressed_dialogs') or [])
        raw['package_central'] = True
        return raw

    def close(self):
        if self.process is not None and self._alive():
            try:
                self.job_id += 1
                _write_json(self.job_path, dict(
                    format=JOB_FORMAT, action='STOP', job_id=self.job_id))
                # Bounded poll count keeps shutdown deterministic even
                # under test clocks; real runtime still gives Revit ~20 seconds.
                for unused in range(80):
                    if not self._alive():
                        break
                    _pump_windows_messages()
                    self.sleep(0.25)
            except Exception:
                pass
            if self._alive():
                _stop_process(self.process)
        if self.process is not None:
            try:self.process.Dispose()
            except Exception:pass
            self.process = None
        if not self.failed:
            try:shutil.rmtree(self.session_dir)
            except Exception:pass


def run_separate_revit(application, stage, target, rows, options,
                       cancelled=None, pulse=None, timeout_seconds=None,
                       process_factory=None, executable=None, sleeper=None,
                       clock=None):
    """Backward-compatible one-job wrapper around WorkerSession."""
    session = WorkerSession(application, cancelled, pulse, timeout_seconds,
                            process_factory, executable, sleeper, clock)
    try:
        return session.run(stage, target, rows, options)
    finally:
        session.close()


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


def _record_suppressed(kind, args, result_code=None, note=''):
    _SUPPRESSED.append(dict(
        kind=kind,
        dialog_id=f.text(getattr(args, 'DialogId', '') or ''),
        message=f.text(getattr(args, 'Message', '') or ''),
        result=result_code,
        note=f.text(note or ''),
        time=time.time()))


def _install_automation_handlers(application):
    """Install noninteractive worker handlers on UIApplication or startup application."""
    if _AUTOMATION_INSTALLED[0] or application is None:
        return
    dialog_source=application if hasattr(application,'DialogBoxShowing') else None
    failure_source=getattr(application,'Application',None)
    if failure_source is None:
        failure_source=getattr(application,'ControlledApplication',None)
    try:
        def on_dialog(sender, args):
            del sender
            accepted = None
            # Standard OK/Yes/Retry first, then Revit command links. The worker
            # owns only disposable package copies, so continuing is preferred to
            # a modal wait. Every choice is recorded in the worker result.
            for code in (1, 6, 4, 1001, 1002, 1003, 1004):
                try:
                    if args.OverrideResult(code):
                        accepted = code
                        break
                except Exception:
                    continue
            _record_suppressed('DialogBoxShowing', args, accepted,
                               '' if accepted is not None else 'OverrideResult was not accepted.')
        _DIALOG_HANDLER[0] = on_dialog
        if dialog_source is not None:
            dialog_source.DialogBoxShowing += _DIALOG_HANDLER[0]
        else:
            raise RuntimeError('No DialogBoxShowing event source is available.')
    except Exception as exc:
        _SUPPRESSED.append(dict(kind='DialogHandlerInstall',message=f.text(exc),time=time.time()))

    try:
        from pyrevit import DB
        def on_failures(sender, args):
            del sender
            accessor = None
            try:
                accessor = args.GetFailuresAccessor()
                try:accessor.DeleteAllWarnings()
                except Exception:pass
                remaining = []
                try:remaining = list(accessor.GetFailureMessages())
                except Exception:remaining = []
                has_error = False
                for failure in remaining:
                    try:
                        severity = failure.GetSeverity()
                        if f.text(severity) not in ('Warning', 'None'):
                            has_error = True
                    except Exception:
                        has_error = True
                if has_error:
                    args.SetProcessingResult(DB.FailureProcessingResult.ProceedWithRollBack)
                    _SUPPRESSED.append(dict(kind='FailuresProcessing',message='Errors rolled back without modal UI.',time=time.time()))
                else:
                    args.SetProcessingResult(DB.FailureProcessingResult.Continue)
            except Exception as exc:
                _SUPPRESSED.append(dict(kind='FailuresProcessingHandler',message=f.text(exc),time=time.time()))
        _FAILURE_HANDLER[0] = on_failures
        if failure_source is not None:
            failure_source.FailuresProcessing += _FAILURE_HANDLER[0]
        else:
            raise RuntimeError('No FailuresProcessing event source is available.')
    except Exception as exc:
        _SUPPRESSED.append(dict(kind='FailuresHandlerInstall',message=f.text(exc),time=time.time()))

    _AUTOMATION_INSTALLED[0] = True


def install_startup_handlers(application):
    if not is_worker_process():
        return False
    _install_automation_handlers(application)
    return True


def _worker_result(job, status, message='', processing_result=None, rows=None,
                   suppressed=None):
    value = dict(status=status, message=f.text(message or ''),
                 job_id=int(job.get('job_id', -1)),
                 processing_result=processing_result, rows=list(rows or []),
                 suppressed_dialogs=list(suppressed or []),
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
    options['package_central'] = True
    rows = list(job.get('rows') or [])
    backend = Backend(DB, app, f.text(job.get('package_root') or os.path.dirname(target)))
    backend.set_staging_root(f.text(job.get('staging_root') or os.path.dirname(stage)))
    raw = backend.finish(stage, target, rows, options)
    return raw, rows


def run_pending(sender=None):
    """Consume sequential jobs in one disposable Revit process."""
    if not is_worker_process():
        return False
    if _WORKER_RUNNING[0]:
        return True

    job_path = pending_job_path()
    if not job_path or not os.path.isfile(job_path):
        return False

    uiapp = _resolve_uiapp(sender)
    if uiapp is None:
        return True
    _install_automation_handlers(uiapp)

    try:
        job = _read_json(job_path)
    except Exception:
        return True

    job_id = int(job.get('job_id', -1))
    if job.get('action') == 'STOP':
        _WORKER_LAST_JOB[0] = job_id
        _request_exit(uiapp)
        return True
    if job_id < 0 or job_id == _WORKER_LAST_JOB[0]:
        return True

    _WORKER_RUNNING[0] = True
    suppressed_start = len(_SUPPRESSED)
    try:
        _write_json(job['status_path'], dict(
            job_id=job_id, phase='repairing package copy', started_at=time.time()))
        raw, rows = _run_job(job, uiapp)
        _write_json(job['result_path'],
                    _worker_result(job, 'SUCCEEDED',
                                   processing_result=raw, rows=rows,
                                   suppressed=_SUPPRESSED[suppressed_start:]))
    except Exception as exc:
        try:
            current = locals().get('job') or _read_json(job_path)
            _write_json(current['result_path'],
                        _worker_result(current, 'FAILED',
                                       message=f.text(exc) + '\n' + traceback.format_exc(),
                                       rows=current.get('rows', []),
                                       suppressed=_SUPPRESSED[suppressed_start:]))
        except Exception:
            pass
    finally:
        _WORKER_LAST_JOB[0] = job_id
        _WORKER_RUNNING[0] = False
    return True
