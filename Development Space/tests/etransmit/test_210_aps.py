# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os,sys,unittest,io,json,tempfile,shutil
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
sys.path.insert(0,os.path.join(ROOT,'lib'))

def module(case,name):
    try:return __import__('easybim_etransmit.'+name,fromlist=[name])
    except ImportError:case.fail(name+' implementation is missing')

class OAuthTests(unittest.TestCase):
    def test_rfc7636_challenge(self):
        m=module(self,'oauth')
        self.assertEqual(m.challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'),
                         'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')
    def test_callback_validates_state_path_and_duplicates(self):
        m=module(self,'oauth');f=m.Flow('client','http://127.0.0.1:8767/callback')
        for path in ['/wrong?state='+f.state+'&code=c','/callback?state=wrong&code=c',
                     '/callback?state='+f.state+'&code=a&code=b']:
            with self.assertRaises(ValueError):f.accept_callback(path)
        self.assertEqual(f.accept_callback('/callback?state='+f.state+'&code=good'),'good')
        with self.assertRaises(ValueError):f.accept_callback('/callback?state='+f.state+'&code=good')
    def test_requires_loopback_native_callback(self):
        m=module(self,'oauth')
        for uri in ['http://0.0.0.0:8767/callback','https://evil.test/cb','http://localhost.evil/cb']:
            with self.assertRaises(ValueError):m.Flow('client',uri)
    def test_token_body_no_client_secret_and_read_scope(self):
        m=module(self,'oauth');f=m.Flow('public-client','http://127.0.0.1:8767/callback')
        self.assertIn('scope=data%3Aread',f.authorize_url())
        body=f.token_form('code');self.assertNotIn('client_secret',body)
        self.assertEqual(body['code_verifier'],f.verifier)
        self.assertNotIn(f.verifier,repr(f))

class APSTests(unittest.TestCase):
    def test_bearer_never_sent_to_signed_download(self):
        m=module(self,'aps');calls=[]
        def transport(method,url,headers,body):
            calls.append((url,headers));return m.Response(200,{},io.BytesIO(b'rvt'))
        c=m.Client('secret-token',transport=transport)
        root=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,root)
        c.download('https://bucket.s3.amazonaws.com/m.rvt?sig=private',os.path.join(root,'m.rvt'),3)
        self.assertNotIn('Authorization',calls[0][1])
        self.assertNotIn('secret-token',repr(c))
    def test_api_url_and_signed_url_are_strict(self):
        m=module(self,'aps');c=m.Client('secret',transport=lambda *a: self.fail('network should not be reached'))
        for path in ['https://evil.test/x','//evil.test/x','/data/v1/../admin','/authentication/v2/token']:
            with self.assertRaises(ValueError):c.get_json(path)
        for url in ['http://s3.amazonaws.com/a','https://localhost/a','https://user:pass@s3.amazonaws.com/a']:
            with self.assertRaises(ValueError):m.validate_download_url(url)
    def test_rcm_pagination_and_pinned_version(self):
        m=module(self,'aps');calls=[];v='urn:adsk.wipprod:fs.file:vf.Host?version=3'
        host=dict(modelName='Host.rvt',itemId='host-item',versionId=v,signedUrl='https://s3.amazonaws.com/host',size=1)
        def transport(method,url,headers,body):
            calls.append(url);offset=1 if 'offset=1' in url else 0
            data=dict(hostFile=host,linkedFiles=dict(pagination=dict(limit=1,offset=offset,totalResults=2),
                results=[dict(modelName='Link%d.rvt'%offset,itemId='link'+str(offset),signedUrl='https://s3.amazonaws.com/link',size=1)]))
            return m.Response(200,{},io.BytesIO(json.dumps(data).encode('utf-8')))
        result=m.Client('token',transport=transport).linked_files('b.project',v)
        self.assertEqual(len(result['linkedFiles']['results']),2)
        self.assertIn('Host%3Fversion%3D3',calls[0]);self.assertEqual(len(calls),2)
    def test_repeated_pagination_is_error_not_silent_truncation(self):
        m=module(self,'aps');v='urn:adsk.wipprod:fs.file:vf.H?version=2'
        data=dict(hostFile=dict(versionId=v),linkedFiles=dict(pagination=dict(offset=0,limit=1,totalResults=2),results=[]))
        c=m.Client('token',transport=lambda *a:m.Response(200,{},io.BytesIO(json.dumps(data).encode('utf-8'))))
        with self.assertRaises(m.ApiError):c.linked_files('b.p',v)
    def test_unauthorized_error_does_not_include_url_or_token(self):
        m=module(self,'aps')
        c=m.Client('secret-token',transport=lambda *a:m.Response(403,{},io.BytesIO(b'secret-token signedUrl=private')))
        with self.assertRaises(m.ApiError) as ctx:c.get_json('/project/v1/hubs')
        self.assertNotIn('secret-token',str(ctx.exception));self.assertNotIn('private',str(ctx.exception))

class GraphTests(unittest.TestCase):
    def graph(self):
        v='urn:adsk.wipprod:fs.file:vf.Host?version=3'
        return v,dict(hostFile=dict(modelName='Host.rvt',itemId='host-item',versionId=v,size=1,signedUrl='https://s3.amazonaws.com/host'),
                      linkedFiles=dict(results=[dict(modelName='Arch.rvt',itemId='arch-item',size=1,publishStatus='NotPublished',signedUrl='https://s3.amazonaws.com/arch')]))
    def test_graph_maps_only_its_declared_members(self):
        m=module(self,'cloud_sources');v,d=self.graph();g=m.Graph('b.project',v,d)
        self.assertEqual(g.match_staged_name('Arch.rvt')['itemId'],'arch-item')
        self.assertIsNone(g.match_staged_name('Missing.rvt'))
        self.assertNotIn('signedUrl',json.dumps(g.public_manifest()))
        self.assertNotIn('https://s3',json.dumps(g.public_manifest()))
    def test_graph_rejects_different_host_version(self):
        m=module(self,'cloud_sources');v,d=self.graph();d['hostFile']['versionId']=v+'0'
        with self.assertRaises(ValueError):m.Graph('b.project',v,d)
    def test_graph_rejects_unsafe_name_and_ambiguous_names(self):
        m=module(self,'cloud_sources');v,d=self.graph();d['linkedFiles']['results'][0]['modelName']='../escape.rvt'
        with self.assertRaises(ValueError):m.Graph('b.project',v,d)
        v,d=self.graph();d['linkedFiles']['results'].append(dict(d['linkedFiles']['results'][0],itemId='different'))
        g=m.Graph('b.project',v,d)
        with self.assertRaises(ValueError):g.match_staged_name('Arch.rvt')

class AdditionalSecurity(unittest.TestCase):
    def test_absolute_api_fragment_and_encoded_traversal_rejected(self):
        m=module(self,'aps')
        for path in ['https://developer.api.autodesk.com/data/v1/projects#secret','/data/v1/%2e%2e/secrets','/data/v1/%252e%252e/secrets']:
            with self.assertRaises(ValueError):m.api_url(path)
    def test_denied_callback_is_consumed_and_distinguishable(self):
        m=module(self,'oauth');flow=m.Flow('c','http://127.0.0.1:8767/callback')
        kind=getattr(m,'AuthorizationDeclined',None)
        self.assertIsNotNone(kind,'Declined sign-in should stop rather than wait for timeout')
        with self.assertRaises(kind):flow.accept_callback('/callback?state='+flow.state+'&error=access_denied')
        self.assertTrue(flow.used)


class GraphRefresh(unittest.TestCase):
    def test_public_manifest_does_not_export_unknown_server_fields(self):
        m=module(self,'cloud_sources');v='urn:x?version=1'
        host=dict(modelName='H.rvt',itemId='h',versionId=v,size=1,signedUrl='https://s3.amazonaws.com/h',futureCredential='private')
        graph=m.Graph('b.p',v,dict(hostFile=host,linkedFiles=dict(results=[])))
        self.assertNotIn('futureCredential',graph.public_manifest()['files'][0])
    def test_refresh_pins_same_graph_and_rejects_changed_members(self):
        m=module(self,'cloud_sources');v='urn:x?version=1'
        host=dict(modelName='H.rvt',itemId='h',versionId=v,size=1,signedUrl='https://s3.amazonaws.com/h')
        graph=m.Graph('b.p',v,dict(hostFile=host,linkedFiles=dict(results=[])))
        self.assertTrue(callable(getattr(graph,'refresh_urls',None)))
        fresh=dict(host);fresh['signedUrl']='https://s3.amazonaws.com/new'
        graph.refresh_urls(dict(hostFile=fresh,linkedFiles=dict(results=[])))
        self.assertEqual(graph.host['signedUrl'],fresh['signedUrl'])
        fresh['size']=2
        with self.assertRaises(ValueError):graph.refresh_urls(dict(hostFile=fresh,linkedFiles=dict(results=[])))

if __name__=='__main__':unittest.main(verbosity=2)
