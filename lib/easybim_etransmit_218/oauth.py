# -*- coding: utf-8 -*-
"""Native-app PKCE sign-in. Tokens remain in memory for this command only."""
from __future__ import unicode_literals
import base64,hashlib,os,sys,time
try:
    from urllib.parse import urlencode,urlsplit,parse_qs
    from http.server import HTTPServer,BaseHTTPRequestHandler
except ImportError:
    from urllib import urlencode
    from urlparse import urlsplit,parse_qs
    from BaseHTTPServer import HTTPServer,BaseHTTPRequestHandler
from . import aps,files as f


def random_text():return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('ascii')
def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
def equal(a,b):
    if len(a)!=len(b):return False
    different=0
    for x,y in zip(bytearray(a.encode('utf-8')),bytearray(b.encode('utf-8'))):different|=x^y
    return different==0


class AuthorizationDeclined(ValueError):
    pass


class Flow(object):
    def __init__(self,client_id,redirect_uri):
        p=urlsplit(redirect_uri)
        if (p.scheme!='http' or p.hostname!='127.0.0.1' or not p.port or p.port<1024
                or p.username or p.password or p.query or p.fragment or not p.path.startswith('/')):
            raise ValueError('Register an http://127.0.0.1:<port>/callback URI for this native app.')
        if not client_id.strip():raise ValueError('An APS native-app client ID is required (never a client secret).')
        self.client_id=client_id.strip();self.redirect_uri=redirect_uri;self.path=p.path;self.port=p.port
        self.state=random_text();self.verifier=random_text();self.used=False
    def __repr__(self):return '<APS PKCE flow>'
    def authorize_url(self):
        return aps.API+'/authentication/v2/authorize?'+urlencode(dict(
            response_type='code',client_id=self.client_id,redirect_uri=self.redirect_uri,
            scope='data:read',state=self.state,code_challenge=challenge(self.verifier),code_challenge_method='S256'))
    def accept_callback(self,path):
        parsed=urlsplit(path)
        if self.used or parsed.path!=self.path or parsed.scheme or parsed.netloc or len(path)>8192:
            raise ValueError('Invalid or repeated OAuth callback.')
        query=parse_qs(parsed.query)
        if len(query.get('state',[]))!=1 or not equal(query['state'][0],self.state):
            raise ValueError('OAuth callback state did not match.')
        if query.get('error'):
            self.used=True
            raise AuthorizationDeclined('Autodesk sign-in was declined or failed.')
        if len(query.get('code',[]))!=1 or not query['code'][0]:raise ValueError('Missing or ambiguous OAuth code.')
        self.used=True;return query['code'][0]
    def token_form(self,code):
        return dict(grant_type='authorization_code',client_id=self.client_id,
                    redirect_uri=self.redirect_uri,code=code,code_verifier=self.verifier)


class Tokens(object):
    def __init__(self,client_id,response):self.client_id=client_id;self.set(response)
    def __repr__(self):return '<APS session credentials (memory only)>'
    def set(self,response):
        if not response.get('access_token'):raise aps.ApiError('Autodesk did not issue an access token.')
        self.access=response['access_token'];self.refresh=response.get('refresh_token','')
        self.expires=time.time()+int(response.get('expires_in',3600))-60
    def __call__(self):
        if time.time()>=self.expires:
            if not self.refresh:raise aps.ApiError('Autodesk sign-in expired. Sign in again.')
            self.set(aps.token_request(dict(grant_type='refresh_token',client_id=self.client_id,
                                           refresh_token=self.refresh,scope='data:read')))
        return self.access
    def close(self):self.access='';self.refresh='';self.expires=0


def sign_in(client_id,redirect_uri='http://127.0.0.1:8767/callback',cancelled=None,pulse=None):
    flow=Flow(client_id,redirect_uri);codes=[];declined=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # Never log the OAuth code or state.
        def do_GET(self):
            try:
                code=flow.accept_callback(self.path);codes.append(code)
                status=200;message=b'Sign-in received. Return to Revit.'
            except AuthorizationDeclined:
                declined.append(True);status=200;message=b'Sign-in cancelled. Return to Revit.'
            except ValueError:
                status=400;message=b'Invalid sign-in callback. Return to Revit to retry.'
            self.send_response(status);self.send_header('Content-Type','text/plain')
            self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(message)
    class Server(HTTPServer):
        allow_reuse_address=False
        def get_request(self):
            sock,address=HTTPServer.get_request(self);sock.settimeout(3);return sock,address
    server=Server(('127.0.0.1',flow.port),Handler);server.timeout=0.2
    try:
        url=flow.authorize_url()
        if sys.platform=='cli':
            from System.Diagnostics import Process,ProcessStartInfo
            start=ProcessStartInfo(url);start.UseShellExecute=True;Process.Start(start)
        else:
            import webbrowser
            webbrowser.open(url)
        deadline=time.time()+180
        while not codes:
            f.check(cancelled)
            if time.time()>deadline:raise aps.ApiError('Autodesk sign-in timed out after three minutes.')
            if pulse:pulse('Waiting for Autodesk sign-in',0,1)
            server.handle_request()
            if declined:raise AuthorizationDeclined("Autodesk sign-in was declined.")
        return Tokens(client_id,aps.token_request(flow.token_form(codes[0])))
    finally:
        server.server_close();flow.verifier='';flow.state='';codes[:]=[]
