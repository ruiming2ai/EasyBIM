# -*- coding: utf-8 -*-
"""Read-only spreadsheet associations exposed by third-party storage.

This is a conservative generic reader, not a private RFtools/Ideate format
adapter. It never runs Excel, interprets macros, or rewrites plugin storage.
Only path-shaped values in named source fields are candidates. Coverage is
reported separately because restricted/encoded vendor schemas may be unreadable.
"""
from __future__ import unicode_literals
import json
import ntpath
import re
import xml.etree.ElementTree as ET
from . import files as f

EXTENSIONS=('.xls','.xlsx','.xlsm','.xlsb','.xlt','.xltx','.xltm','.csv')
PATH_FIELDS=set(('excelfilepath','excelpath','workbookpath','workbookfile','workbookfilename',
                 'sourcefile','sourcefilepath','sourcepath','fullpath','filepath','filename',
                 'excelfile','workbook','file','path'))
STRONG_FIELDS=PATH_FIELDS-set(('file','path','filename','fullpath'))
VENDORS=('ideate','sticky','bimlink','rushforth','draftxl','rftools')
MAX_TEXT=2*1024*1024
MAX_NODES=20000
MAX_DEPTH=12


def _name(value):return re.sub('[^a-z0-9]','',f.text(value).lower())
def is_workbook(value):
    if not isinstance(value,f.string_types):return False
    value=value.strip()
    if not value or any(c in value for c in '\r\n\x00'):return False
    if value.startswith(('http://','https://')):
        # Signed/remote web links require a vendor/cloud adapter; do not put
        # query credentials in manifests or treat a web page as a file.
        return False
    return ntpath.splitext(value)[1].lower() in EXTENSIONS and not value.startswith(('=','+','@'))


def associations(data):
    """Extract (field trail, exact path) pairs from bounded JSON/XML/map fields."""
    found=[];seen=set();budget=[MAX_NODES]
    def visit(value,trail,field='',depth=0):
        budget[0]-=1
        if budget[0]<0 or depth>MAX_DEPTH:raise ValueError('Plugin storage exceeds safe inspection bounds.')
        if isinstance(value,dict):
            for key,item in value.items():visit(item,trail+[f.text(key)],f.text(key),depth+1)
        elif isinstance(value,(tuple,list)):
            for index,item in enumerate(value):visit(item,trail+[f.text(index)],field,depth+1)
        elif isinstance(value,f.string_types):
            if len(value)>MAX_TEXT:raise ValueError('Plugin storage string exceeds safe inspection bounds.')
            stripped=value.strip()
            if _name(field) in PATH_FIELDS and is_workbook(stripped):
                pair=('.'.join(trail),stripped)
                if pair not in seen:seen.add(pair);found.append(pair)
            elif stripped.startswith(('{','[')):
                try:parsed=json.loads(stripped)
                except (ValueError,TypeError):return
                visit(parsed,trail,field,depth+1)
            elif stripped.startswith('<'):
                if '<!DOCTYPE' in stripped.upper() or '<!ENTITY' in stripped.upper():
                    raise ValueError('XML entity/DTD declarations are not read.')
                try:root=ET.fromstring(stripped)
                except ET.ParseError:return
                def xml(node,path,level):
                    if level>MAX_DEPTH:raise ValueError('Plugin XML exceeds safe inspection depth.')
                    tag=node.tag.rsplit('}',1)[-1]
                    visit(node.text or '',path+[tag],tag,level)
                    for key,val in node.attrib.items():visit(val,path+[tag,key],key,level)
                    for child in node:xml(child,path+[tag],level+1)
                xml(root,trail,depth+1)
    visit(data,[])
    return found


def make_row(provider,schema,element,field,path,base=''):
    resolved=f.resolve_source(path,base) or path
    return dict(id='plugin:'+f.text(element)+':'+f.text(schema)+':'+field,
                element_id=f.text(element),source=resolved,configured_source=path,
                kind='PluginSpreadsheet',category='spreadsheets',special='plugin_spreadsheet',
                td=False,loaded=None,provider=provider,schema_guid=f.text(schema),source_field=field,
                source_evidence='READABLE_PLUGIN_STORAGE',
                note='Source workbook association only; copied bytes do not reconnect private plugin links.')


def _dispose(value):
    if value is not None:
        try:value.Dispose()
        except Exception:pass


def _entity_data(entity,schema,depth=0,budget=None):
    if budget is None:budget=[MAX_NODES]
    if depth>MAX_DEPTH:raise ValueError('Nested storage entities exceed safe inspection depth.')
    output={}
    import clr
    from System import String
    from System.Collections.Generic import IList,IDictionary
    for field in schema.ListFields():
        budget[0]-=1
        if budget[0]<0:raise ValueError('Plugin entity count exceeds safe inspection bound.')
        kind=f.text(field.ContainerType)
        value_type=clr.GetPythonType(field.ValueType)
        # Only strings or nested entities can carry an association. Numeric
        # geometry and opaque binary fields are neither decoded nor exported.
        type_name=f.text(field.ValueType.FullName)
        nested=type_name=='Autodesk.Revit.DB.ExtensibleStorage.Entity'
        if type_name!='System.String' and not nested:continue
        if kind=='Simple':value=entity.Get[value_type](field)
        elif kind=='Array':value=entity.Get[IList[value_type]](field)
        elif kind=='Map':
            key_type=clr.GetPythonType(field.KeyType)
            value=entity.Get[IDictionary[key_type,value_type]](field)
        else:continue
        def convert(item):
            if not nested:return f.text(item) if item is not None else ''
            try:
                if not item.IsValid():return {}
                child_schema=item.Schema
                if not child_schema.ReadAccessGranted():raise ValueError('Nested plugin schema denies read access.')
                return _entity_data(item,child_schema,depth+1,budget)
            finally:_dispose(item)
        if kind=='Simple':output[field.FieldName]=convert(value)
        elif kind=='Array':
            if value.Count>MAX_NODES:raise ValueError('Plugin array exceeds safe inspection bound.')
            output[field.FieldName]=[convert(x) for x in value]
        else:
            if value.Count>MAX_NODES:raise ValueError('Plugin map exceeds safe inspection bound.')
            output[field.FieldName]=dict((f.text(k),convert(value[k])) for k in value.Keys)
    return output


def discover(DB,doc,cancelled=None,base=''):
    """Read relevant public schemas; return references and explicit coverage."""
    f.check(cancelled);rows=[];coverage=[]
    ns=getattr(DB,'ExtensibleStorage',None)
    if ns is None:
        return [],[dict(provider='Extensible Storage',status='UNSUPPORTED',message='Storage API is not exposed.')]
    try:schemas=list(ns.Schema.ListSchemas())
    except Exception:return [],[dict(provider='Extensible Storage',status='ERROR',message='Schema inventory failed.')]
    for schema in schemas:
        f.check(cancelled)
        provider='Unavailable schema';entry=None
        try:
            provider=f.text(schema.SchemaName);vendor=f.text(getattr(schema,'VendorId',''))
            recognized=any(x in (provider+' '+vendor).lower() for x in VENDORS)
            entry=dict(provider=provider,schema_guid=f.text(schema.GUID),status='SCANNED',elements=0,associations=0)
            if not bool(getattr(schema,'IsValidObject',True)):
                entry.update(status='UNAVAILABLE_SCHEMA',message='Schema is no longer valid; no fields or entities were read.')
                coverage.append(entry);continue
            # Revit can deny access before ListFields is safe to call. Do not
            # attempt field enumeration to determine relevance through a denial.
            if not schema.ReadAccessGranted():
                entry.update(status='READ_DENIED',message='Schema read access denied by Revit; no bypass attempted.')
                coverage.append(entry);continue
            fields=list(schema.ListFields())
            if not recognized and not any(_name(x.FieldName) in STRONG_FIELDS for x in fields):continue
            coverage.append(entry)
            filt=collector=None
            try:
                filt=ns.ExtensibleStorageFilter(schema.GUID)
                collector=DB.FilteredElementCollector(doc).WherePasses(filt)
                for element in collector:
                    f.check(cancelled)
                    entry['elements']+=1
                    if entry['elements']>MAX_NODES:raise ValueError('Plugin element count exceeds safe inspection bound.')
                    entity=None
                    try:
                        entity=element.GetEntity(schema)
                        if not entity.IsValid():continue
                        data=_entity_data(entity,schema)
                        ident=getattr(element.Id,'Value',None)
                        if ident is None:ident=element.Id.IntegerValue
                        for field,path in associations(data):
                            rows.append(make_row(provider,schema.GUID,ident,field,path,base))
                            entry['associations']+=1
                    finally:_dispose(entity)
            finally:_dispose(collector);_dispose(filt)
            if not entry['associations']:
                entry.update(status='NO_SUPPORTED_ASSOCIATION_FOUND',message='No readable named workbook-path field found; encoded/vendor-private associations are not ruled out.')
        except f.Cancelled:raise
        except Exception as exc:
            # Never dump raw storage, private settings or tokens into reports.
            if coverage and coverage[-1].get('provider')==provider:
                coverage[-1].update(status='UNSUPPORTED',message='Storage layout could not be safely decoded ('+type(exc).__name__+').')
            else:coverage.append(dict(provider=provider,status='ERROR',message='Schema inspection failed.'))
    if not coverage:coverage.append(dict(provider='Plugin spreadsheet sources',status='NO_SUPPORTED_ASSOCIATION_FOUND',message='No supported readable schema detected. This does not prove there are no plugin spreadsheets.'))
    return rows,coverage
