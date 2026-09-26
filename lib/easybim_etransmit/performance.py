# -*- coding: utf-8 -*-
"""Small, synchronous, thread-local profiling. No Revit work or extra file reads.

Elapsed and exclusive phase times use perf_counter or .NET Stopwatch. UTC is
only a timestamp, never the duration clock. Final telemetry serialization is
explicitly excluded from the measured interval.
"""
from __future__ import unicode_literals
import functools
import threading
import time

try:
    text = unicode
except NameError:
    text = str


def _clock():
    if hasattr(time, 'perf_counter'):
        return time.perf_counter, 'perf_counter', True
    try:
        from System.Diagnostics import Stopwatch
        if Stopwatch.IsHighResolution:
            return (lambda: float(Stopwatch.GetTimestamp()) / float(Stopwatch.Frequency)), 'Stopwatch', True
    except ImportError:
        pass
    if hasattr(time, 'monotonic'):
        return time.monotonic, 'monotonic', True
    return time.time, 'wall_clock_fallback', False


_NOW, CLOCK, MONOTONIC = _clock()
_LOCAL = threading.local()


def current():
    return getattr(_LOCAL, 'collector', None)


class Collector(object):
    def __init__(self, clock=None):
        self.clock = clock or _NOW
        self.clock_name = 'test_clock' if clock else CLOCK
        self.monotonic = True if clock else MONOTONIC
        self.started_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.started = self.clock()
        self.stopped = None
        self.stack = []
        self.operations = []
        self.phases = {}
        self.omitted = 0
        self.count = 0
        self.copy_bytes = 0
        self.hash_bytes = 0
        self.previous = None
    def __enter__(self):
        self.previous = current(); _LOCAL.collector = self
        return self
    def __exit__(self, *args):
        self.stopped = self.clock(); _LOCAL.collector = self.previous
    def snapshot(self):
        elapsed = max(0.0, (self.stopped if self.stopped is not None else self.clock()) - self.started)
        phases = [dict(phase=k, seconds=round(v, 6)) for k,v in sorted(self.phases.items())]
        accounted = sum(x['seconds'] for x in phases)
        return dict(schema_version=1, clock=self.clock_name, monotonic=self.monotonic,
                    started_utc=self.started_utc, elapsed_seconds=round(elapsed, 6),
                    scope='Processing only; excludes user dialogs and final telemetry serialization.',
                    phase_basis='Exclusive wall time; nested operations are not double counted.',
                    phases=phases, other_seconds=round(max(0.0,elapsed-accounted),6),
                    operation_count=self.count, omitted_operation_rows=self.omitted,
                    byte_basis='Completed logical IO only; failed-operation partial bytes are not known. Not physical disk traffic.',
                    copy_bytes=self.copy_bytes, hash_bytes=self.hash_bytes,
                    operations=list(self.operations))


class Span(object):
    def __init__(self, phase, operation, file='', target=''):
        self.collector = current(); self.phase = phase; self.operation = operation
        self.file = file if isinstance(file, (str, text)) else ''
        self.target = target if isinstance(target, (str, text)) else ''
        self.meta = {}; self.children = 0.0
    def __enter__(self):
        c = self.collector
        if c:
            self.started = c.clock()
            self.parent = c.stack[-1] if c.stack else None
            self.phase = self.phase or (self.parent.phase if self.parent else 'file_io')
            c.stack.append(self)
        return self
    def __exit__(self, typ, exc, tb):
        c = self.collector
        if not c: return False
        elapsed = max(0.0,c.clock()-self.started)
        own = max(0.0,elapsed-self.children)
        c.stack.pop()
        if self.parent: self.parent.children += elapsed
        c.phases[self.phase] = c.phases.get(self.phase,0.0)+own
        nbytes = int(self.meta.get('bytes') or 0)
        status = ('CANCELLED' if typ and typ.__name__=='Cancelled' else 'FAILED') if typ else 'OK'
        c.count += 1
        if self.operation=='copy': c.copy_bytes += nbytes
        if self.operation=='hash': c.hash_bytes += nbytes
        row=dict(phase=self.phase,operation=self.operation,file=self.file,target=self.target,
                 start_seconds=round(self.started-c.started,6),seconds=round(elapsed,6),
                 self_seconds=round(own,6),status=status,bytes=nbytes,
                 mib_per_second=round(nbytes/1048576.0/elapsed,3) if nbytes and elapsed else None)
        row.update(self.meta)
        if typ: row['error_type']=typ.__name__
        # Timings must remain bounded for unusually large Support folders.
        if len(c.operations)<100000: c.operations.append(row)
        else: c.omitted += 1
        return False


def span(phase, operation, file='', target=''):
    return Span(phase,operation,file,target)


def call(phase, operation, file, function, *args, **kwargs):
    with span(phase,operation,file):
        return function(*args,**kwargs)


def timed(phase, operation, file_index=0, target_index=None):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if not current(): return function(*args,**kwargs)
            file=args[file_index] if len(args)>file_index else ''
            target=args[target_index] if target_index is not None and len(args)>target_index else ''
            with span(phase,operation,file,target) as measurement:
                result=function(*args,**kwargs)
                if operation=='copy' and isinstance(result,dict):
                    measurement.meta.update(bytes=result.get('size',0),copy_method=result.get('copy_method','DIRECT'))
                return result
        return wrapped
    return decorate


def report_lines(data):
    if not data: return []
    lines=['','PERFORMANCE SUMMARY',
           'Processing elapsed: {0:.3f} seconds | Clock: {1}'.format(data['elapsed_seconds'],data['clock']),
           data['scope'], data['phase_basis']]
    initial=sum(x['timing']['elapsed_seconds'] for x in data.get('initial_discovery',[]))
    if data.get('initial_discovery'):
        lines += ['Initial reference discovery: {0:.3f} seconds'.format(initial),
                  'Measured total including initial discovery: {0:.3f} seconds'.format(data['measured_total_seconds']),
                  'timings.csv scope distinguishes discovery from package-relative start offsets.']
    for row in sorted(data['phases'],key=lambda r:-r['seconds']):
        lines.append('  {0}: {1:.3f} seconds'.format(row['phase'],row['seconds']))
    lines += ['  Other/coordination: {0:.3f} seconds'.format(data['other_seconds']),
              'Logical bytes copied: {0} | bytes hashed: {1}'.format(data['copy_bytes'],data['hash_bytes']),
              'Throughput includes copy verification; rename events move no file payload.',
              'See timings.csv for each operation, file, byte count and throughput.',
              'File integrity and host reference checks do not verify nested dependencies.']
    if data.get('omitted_operation_rows'):lines.append('Timing rows omitted at safety limit: '+str(data['omitted_operation_rows']))
    return lines
