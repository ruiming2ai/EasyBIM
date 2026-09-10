"""Desktop-only monitoring decision benchmark; contains no Revit API calls."""
import copy
import datetime
import json
import pathlib
import platform
import statistics
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
from easybim import independent_placement as p
from easybim import copy_monitor_state as s

def snap(n):
    return dict(frame=p.frame((n, 0, 5)), params={"p"+str(i): float(i) for i in range(40)},
                type_revision="type-a", host="", independence="")

def timed(operation, repeats=3):
    samples=[]
    for _ in range(repeats):
        start=time.perf_counter()
        operation()
        samples.append(time.perf_counter()-start)
    return round(statistics.median(samples),6)

def benchmark(n):
    snapshots=[snap(i) for i in range(n)]
    def create():
        return [s.new_record("link-"+str(i%2),"linked-document","source-"+str(i),
                            "destination-"+str(i),value,value)
                for i,value in enumerate(snapshots)]
    records=create()
    moved=[copy.deepcopy(v) for v in snapshots]
    for value in moved[:min(50,n)]:
        value["frame"]["origin"][0]+=1
    def compare(values):
        return [s.compare(r,a,b) for r,a,b in zip(records,values,snapshots)]
    assert all(row["status"]=="unchanged" for row in compare(snapshots))
    assert sum(row["status"]=="source_changed" for row in compare(moved))==min(50,n)
    return dict(pairs=n, parameters_per_snapshot=40, repetitions=3,
                create_records_seconds=timed(create),
                unchanged_decisions_seconds=timed(lambda:compare(snapshots)),
                fifty_changed_decisions_seconds=timed(lambda:compare(moved)))

if __name__=="__main__":
    print(json.dumps(dict(measured_utc=datetime.datetime.utcnow().isoformat()+"Z",
         python=platform.python_version(),platform=platform.platform(),
         scope="Pure Python record construction/comparison only. Excludes Revit API, storage, geometry, loading, placement and WPF.",
         results=[benchmark(n) for n in (100,1000,10000)]),indent=2))
