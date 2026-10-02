# -*- coding: utf-8 -*-
"""Read-only Autodesk Platform Services transport and documented endpoints.

Bearer credentials are sent only to developer.api.autodesk.com. Signed object
URLs are used without a bearer and are never included in exception messages.
"""
from __future__ import unicode_literals
import io
import json
import os
import sys
import time
import uuid
try:
    from urllib.parse import urlsplit, urljoin, urlencode, quote, unquote
    from urllib.request import Request, build_opener, HTTPRedirectHandler
    from urllib.error import HTTPError
except ImportError:
    from urlparse import urlsplit, urljoin
    from urllib import urlencode, quote, unquote
    from urllib2 import Request, build_opener, HTTPRedirectHandler, HTTPError
from . import files as f

API='https://developer.api.autodesk.com'
PREFIXES=('/project/v1/','/data/v1/','/construction/rcm/v1/')

class ApiError(IOError):
    pass

class Response(object):
    def __init__(self,status,headers,stream):
        self.status=int(status);self.headers=dict((k.lower(),v) for k,v in headers.items());self.stream=stream
    def close(self): self.stream.close()

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class _NetStream(object):
    def __init__(self,response): self.response=response;self.stream=response.GetResponseStream()
    def read(self,count=-1):
        from System import Array, Byte
        count=1024*1024 if count<0 else count
        buf=Array.CreateInstance(Byte,count);done=self.stream.Read(buf,0,count)
        return str(bytearray(buf)[:done])
    def close(self):
        self.stream.Close();self.response.Close()


def transport(method,url,headers,body=None):
    """One request, no automatic redirect, bounded network waits, default TLS validation."""
    if sys.platform=='cli':
        import System
        req=System.Net.WebRequest.Create(url)
        req.Method=method;req.AllowAutoRedirect=False;req.Timeout=45000;req.ReadWriteTimeout=45000
        for key,value in headers.items():
            if key.lower()=='content-type':req.ContentType=value
            elif key.lower()=='accept':req.Accept=value
            else:req.Headers[key]=value
        if body is not None:
            from System import Array, Byte
            data=Array[Byte](bytearray(body));req.ContentLength=len(data)
            stream=req.GetRequestStream()
            try:stream.Write(data,0,len(data))
            finally:stream.Close()
        try:response=req.GetResponse()
        except System.Net.WebException as exc:
            if exc.Response is None:raise ApiError('Autodesk network request failed before a response was received.')
            response=exc.Response
        return Response(int(response.StatusCode),dict((f.text(k),f.text(response.Headers[k]))
                        for k in response.Headers.AllKeys),_NetStream(response))
    request=Request(url,data=body,headers=headers)
    # Avoid relying on the Python 3-only Request(method=...) argument.
    request.get_method=lambda:method
    try:response=build_opener(_NoRedirect()).open(request,timeout=45)
    except HTTPError as exc:response=exc
    except Exception:raise ApiError('Autodesk network request failed before a response was received.')
    return Response(response.getcode(),dict(response.headers.items()),response)


def segment(value):return quote(f.text(value).encode('utf-8'),safe='')


def api_url(path):
    value=f.text(path)
    parsed=urlsplit(value)
    if parsed.fragment:raise ValueError('API fragments are not accepted.')
    decoded=parsed.path
    for _ in range(3):decoded=unquote(decoded)
    if '..' in decoded or '\\' in decoded:raise ValueError('API path traversal is not accepted.')
    if parsed.scheme:
        if parsed.scheme!='https' or parsed.netloc!='developer.api.autodesk.com':
            raise ValueError('API requests must use the Autodesk API HTTPS host.')
        path=parsed.path+('?' + parsed.query if parsed.query else '')
    elif parsed.netloc or not value.startswith('/'):
        raise ValueError('Invalid Autodesk API path.')
    if not path.startswith(PREFIXES) or '..' in path or '#' in path or '\\' in path:
        raise ValueError('Endpoint is outside the read-only API surface.')
    return API+path


def validate_download_url(url):
    p=urlsplit(url);host=(p.hostname or '').lower()
    if (p.scheme!='https' or p.username or p.password or p.fragment or p.port not in (None,443)
            or not (host=='s3.amazonaws.com' or host.endswith('.amazonaws.com')
                    or host.endswith('.amazonaws.com.cn'))):
        raise ValueError('Download must use an HTTPS Autodesk-issued S3 URL without embedded credentials.')
    return url


def read_json(response,limit=16*1024*1024):
    pieces=[];size=0
    while True:
        chunk=response.stream.read(min(65536,limit-size+1))
        if not chunk:break
        pieces.append(chunk);size+=len(chunk)
        if size>limit:raise ApiError('Autodesk metadata response exceeds the safety limit.')
    try:value=json.loads(b''.join(pieces).decode('utf-8'))
    except (ValueError,UnicodeError):raise ApiError('Autodesk returned invalid JSON metadata.')
    if not isinstance(value,dict):raise ApiError('Autodesk metadata root is not an object.')
    return value


def token_request(form,request=None):
    """The only POST supported here is a local OAuth token exchange/refresh."""
    if form.get('grant_type') not in ('authorization_code','refresh_token'):
        raise ValueError('Unsupported OAuth grant.')
    req=request or transport
    response=req('POST',API+'/authentication/v2/token',
                 {'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},
                 urlencode(form).encode('ascii'))
    try:
        if response.status!=200:raise ApiError('Autodesk sign-in/token exchange failed (HTTP '+str(response.status)+').')
        return read_json(response,1024*1024)
    finally:response.close()


class Client(object):
    def __init__(self,token,transport=None,cancelled=None):
        self._token=token;self._transport=transport or globals()['transport'];self.cancelled=cancelled
    def __repr__(self):return '<APS read-only client>'
    def get_json(self,path):
        url=api_url(path)
        for attempt in range(3):
            f.check(self.cancelled)
            token=self._token() if callable(self._token) else self._token
            response=self._transport('GET',url,{'Authorization':'Bearer '+token,'Accept':'application/json'},None)
            try:
                if response.status==200:return read_json(response)
                if response.status in (429,500,502,503,504) and attempt<2:
                    try:delay=min(15.0,max(0.2,float(response.headers.get('retry-after',2**attempt))))
                    except (TypeError,ValueError):delay=1.0
                else:raise ApiError('Autodesk metadata request failed (HTTP '+str(response.status)+'). Check sign-in, app provisioning and download permissions.')
            finally:response.close()
            until=time.time()+delay
            while time.time()<until:f.check(self.cancelled);time.sleep(0.1)
        raise ApiError('Autodesk metadata request failed after retries.')
    def listing(self,path):
        rows=[];seen=set()
        while path:
            url=api_url(path)
            if url in seen or len(seen)>=1000:raise ApiError('Autodesk pagination repeated or exceeded its safety limit.')
            seen.add(url);data=self.get_json(url)
            entries=data.get('data',[])
            if not isinstance(entries,list):raise ApiError('Unexpected Autodesk listing shape.')
            rows.extend(entries)
            nxt=data.get('links',{}).get('next')
            path=nxt.get('href') if isinstance(nxt,dict) else nxt
        return rows
    def hubs(self):return self.listing('/project/v1/hubs')
    def projects(self,hub):return self.listing('/project/v1/hubs/'+segment(hub)+'/projects')
    def top_folders(self,hub,project):
        return self.listing('/project/v1/hubs/'+segment(hub)+'/projects/'+segment(project)+'/topFolders')
    def contents(self,project,folder):
        return self.listing('/data/v1/projects/'+segment(project)+'/folders/'+segment(folder)+'/contents')
    def versions(self,project,item):
        return self.listing('/data/v1/projects/'+segment(project)+'/items/'+segment(item)+'/versions')
    def version(self,project,version):
        return self.get_json('/data/v1/projects/'+segment(project)+'/versions/'+segment(version)).get('data',{})
    def item_tip(self,project,item):
        return self.get_json('/data/v1/projects/'+segment(project)+'/items/'+segment(item)+'/tip').get('data',{})
    def linked_files(self,project,version):
        if not f.text(version).startswith('urn:') or '?version=' not in version:
            raise ValueError('Select an exact published version URN, not an item or latest-version alias.')
        base='/construction/rcm/v1/projects/'+segment(project)+'/published-versions/'+segment(version)+'/linked-files'
        offset=0;results=[];host=None;seen=set()
        while True:
            page=self.get_json(base+'?includeHost=true&limit=600&offset='+str(offset))
            if page.get('hostFile'):
                if page['hostFile'].get('versionId')!=version:raise ApiError('Autodesk returned a different host version.')
                host=page['hostFile']
            linked=page.get('linkedFiles',{});entries=linked.get('results',[])
            pagination=linked.get('pagination',{})
            if not isinstance(entries,list):raise ApiError('Unexpected linked-files response shape.')
            actual=int(pagination.get('offset',offset));total=int(pagination.get('totalResults',len(entries)))
            if actual!=offset or offset in seen:raise ApiError('Linked-files pagination repeated or changed offset.')
            seen.add(offset);results.extend(entries)
            if len(results)>=total:break
            if not entries or len(seen)>=1000:raise ApiError('Linked-files inventory was truncated.')
            offset+=len(entries)
        if not host:raise ApiError('Host file was omitted. Check host download permission.')
        return dict(hostFile=host,linkedFiles=dict(results=results))
    def download(self,url,target,expected_size=None,pulse=None):
        validate_download_url(url);f.check(self.cancelled)
        if os.path.exists(target):raise IOError('Refusing to overwrite a downloaded snapshot.')
        response=None;temp=target+'.part-'+uuid.uuid4().hex[:8]
        try:
            for redirect in range(4):
                response=self._transport('GET',url,{},None)  # deliberately NO bearer
                if response.status in (301,302,303,307,308):
                    next_url=urljoin(url,response.headers.get('location',''))
                    response.close();response=None;validate_download_url(next_url);url=next_url
                    continue
                break
            if response is None or response.status!=200:
                status=response.status if response is not None else 'redirect-limit'
                raise ApiError('Signed model download failed (HTTP '+str(status)+'). Re-select the same published version to refresh expired URLs.')
            folder=os.path.dirname(target)
            if not os.path.isdir(folder):os.makedirs(folder)
            total=0
            with open(temp,'wb') as out:
                while True:
                    f.check(self.cancelled);chunk=response.stream.read(1024*1024)
                    if not chunk:break
                    out.write(chunk);total+=len(chunk)
                    if expected_size is not None and total>int(expected_size):raise ApiError('Downloaded model exceeds its declared size.')
                    if pulse:pulse('Downloading selected cloud model',total,int(expected_size or total))
            if expected_size is not None and total!=int(expected_size):raise ApiError('Downloaded model is incomplete (size mismatch).')
            sha=f.digest(temp,self.cancelled);f.publish(temp,target)
            return dict(size=total,sha256=sha,source_stability='AUTHENTICATED_SNAPSHOT',copy_method='APS_SIGNED_DOWNLOAD')
        finally:
            if response is not None:response.close()
            if os.path.exists(temp):os.remove(temp)
