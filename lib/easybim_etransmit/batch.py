# -*- coding: utf-8 -*-
"""Batch orchestration. Revit API work is sequential; each package is isolated."""
from __future__ import unicode_literals
import io
import json
import os
from . import VERSION, files as f, engine, jobnames


def run_batch(models, root, backend_factory, options=None, extras=None,
              cancelled=None, pulse=None, source_names=None):
    """Transmit every selected source, without one failed model losing the batch.

    backend_factory(package_root, first_source) runs on the caller/Revit thread.
    It must never start Revit API work in a worker thread.
    """
    models=list(models); opts=dict(options or f.defaults())
    if opts.get('zip_per_model'): opts['per_model']=True
    groups=[[m] for m in models] if opts.get('per_model') else [models]
    planned=jobnames.plan(models,root,opts.get('per_model'),source_names)
    if not os.path.isdir(root): os.makedirs(root)
    jobs=[]; results=[]
    for index, group in enumerate(groups):
        package=planned[index]
        job=dict(index=index+1,models=list(group),root=package,status='NOT_STARTED',
                 zip_status='NOT_REQUESTED',name=os.path.basename(package))
        jobs.append(job)
    try:
        for job in jobs:
            if cancelled and cancelled(): break
            group=job['models']; package=job['root']
            try:
                backend=backend_factory(package,group[0])
                result=engine.transmit(group,package,backend,opts,extras,cancelled,pulse)
            except f.Cancelled:
                job['status']='CANCELLED'; break
            except Exception as exc:
                if not os.path.isdir(package): os.makedirs(package)
                result=dict(version=VERSION,root=package,status='FAILED',
                            requested_models=list(group),models=[],files=[],references=[],aliases=[],
                            options=opts,issues=[engine.issue('BATCH_MODEL_FAILED',group[0],exc,'error')])
                engine.write_reports(result)
            results.append(result);job['status']=result['status']
            job.update(engine.package_counts(result))
            if opts.get('zip_per_model') and result['status']!='CANCELLED':
                path=job['root']+'.zip'
                job['zip_path']=path
                try:
                    engine.zip_package(package,path,cancelled)
                    job['zip_status']='VERIFIED'
                    job['zip_sha256']=f.digest(path,cancelled)
                except f.Cancelled:
                    job['zip_status']='CANCELLED';break
                except Exception as exc:
                    job['zip_status']='FAILED'
                    result['issues'].append(engine.issue('MODEL_ZIP_FAILED',path,exc,'error'))
                    result['status']='NEEDS_REVIEW' if result['status']=='COLLECTED' else result['status']
                    job['status']=result['status'];engine.write_reports(result)
        if opts.get('zip') and results and not (cancelled and cancelled()):
            # Whole-batch archive remains optional; it is outside the source tree.
            write_index(root,jobs)
            try:engine.zip_package(root,root+'.zip',cancelled)
            except f.Cancelled:raise
            except Exception as exc:
                for result,job in zip(results,jobs):
                    result['issues'].append(engine.issue('BATCH_ZIP_FAILED',root+'.zip',exc,'error'))
                    if result['status']=='COLLECTED':result['status']='NEEDS_REVIEW'
                    job['status']=result['status'];job['batch_zip_status']='FAILED'
                    engine.write_reports(result)
    except f.Cancelled:
        pass
    finally:
        for job in jobs:
            if job['status']=='NOT_STARTED': job['status']='NOT_STARTED_CANCELLED'
        write_index(root,jobs)
    return results


def write_index(root,jobs):
    with io.open(os.path.join(root,'batch.json'),'w',encoding='utf-8') as out:
        out.write(f.text(json.dumps(dict(version=VERSION,jobs=jobs),ensure_ascii=False,indent=2)))
    lines=['EasyBIM e-transmit '+VERSION,'Batch jobs: '+str(len(jobs)),
           'Revit operations run sequentially. Each model-named folder is a separate transmittal.',
           'A ZIP marked VERIFIED passed archive CRC checks, not a new Revit opening test.',
           'Read each package START_HERE.txt before sending any model.','']
    for job in jobs:
        lines.append('{0:02d}: {1} | ZIP: {2} | {3}'.format(
            job['index'],job['status'],job['zip_status'],job['name']+' | '+'; '.join(job['models'])))
    # In combined mode the package's own START_HERE must not be overwritten.
    name='BATCH_SUMMARY.txt' if any(j['root']==root for j in jobs) else 'START_HERE.txt'
    with io.open(os.path.join(root,name),'w',encoding='utf-8') as out:out.write('\n'.join(lines))
