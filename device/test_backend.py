"""Host IPC/transport proof with a linked libcurl mock; no remote requests or tablet."""
import base64,json,os,pathlib,shlex,socket,struct,subprocess,tempfile,unittest
HERE=pathlib.Path(__file__).resolve().parent
MOCK_CURL = r"""
#include <curl/curl.h>
#include <cstdarg>
#include <fstream>
#include <iterator>
#include <limits>
#include <string>
#undef curl_easy_setopt
#undef curl_easy_getinfo
struct Mock {
    std::string url,protocols;
    long redirects=-1,peer=-1,host=-1,connect=-1,timeout=-1,nosignal=-1;
    curl_off_t maximum=-1,length=-1;
    curl_write_callback write=nullptr;
    void *context=nullptr;
    const char *body=nullptr;
    curl_slist *headers=nullptr;
    long status=200;
};
std::string state(const char *name) { return std::string(getenv("GOODBOT_STATE"))+"/"+name; }
extern "C" CURL *curl_easy_init() { return reinterpret_cast<CURL*>(new Mock); }
extern "C" void curl_easy_cleanup(CURL *handle) { delete reinterpret_cast<Mock*>(handle); }
extern "C" CURLcode curl_easy_setopt(CURL *handle,CURLoption option,...) {
    auto &m=*reinterpret_cast<Mock*>(handle);
    va_list args;va_start(args,option);
    switch(option) {
    case CURLOPT_URL:m.url=va_arg(args,const char*);break;
    case CURLOPT_PROTOCOLS_STR:m.protocols=va_arg(args,const char*);break;
    case CURLOPT_FOLLOWLOCATION:m.redirects=va_arg(args,long);break;
    case CURLOPT_SSL_VERIFYPEER:m.peer=va_arg(args,long);break;
    case CURLOPT_SSL_VERIFYHOST:m.host=va_arg(args,long);break;
    case CURLOPT_CONNECTTIMEOUT:m.connect=va_arg(args,long);break;
    case CURLOPT_TIMEOUT:m.timeout=va_arg(args,long);break;
    case CURLOPT_NOSIGNAL:m.nosignal=va_arg(args,long);break;
    case CURLOPT_MAXFILESIZE_LARGE:m.maximum=va_arg(args,curl_off_t);break;
    case CURLOPT_POSTFIELDSIZE_LARGE:m.length=va_arg(args,curl_off_t);break;
    case CURLOPT_POSTFIELDS:m.body=va_arg(args,const char*);break;
    case CURLOPT_WRITEFUNCTION:m.write=va_arg(args,curl_write_callback);break;
    case CURLOPT_WRITEDATA:m.context=va_arg(args,void*);break;
    case CURLOPT_HTTPHEADER:m.headers=va_arg(args,curl_slist*);break;
    default:va_end(args);return CURLE_UNKNOWN_OPTION;
    }
    va_end(args);return CURLE_OK;
}
extern "C" CURLcode curl_easy_getinfo(CURL *handle,CURLINFO info,...) {
    if(info!=CURLINFO_RESPONSE_CODE)return CURLE_UNKNOWN_OPTION;
    va_list args;va_start(args,info);*va_arg(args,long*)=reinterpret_cast<Mock*>(handle)->status;
    va_end(args);return CURLE_OK;
}
extern "C" CURLcode curl_easy_perform(CURL *handle) {
    auto &m=*reinterpret_cast<Mock*>(handle);
    if(m.protocols!="https"||m.redirects!=0||m.peer!=1||m.host!=2||m.connect!=8||m.timeout!=28||
       m.nosignal!=1||m.maximum!=8388608||!m.write||!m.context)return CURLE_BAD_FUNCTION_ARGUMENT;
    std::ofstream record(state("request-record"),std::ios::binary);
    record<<m.url<<'\n';
    for(auto *h=m.headers;h;h=h->next)record<<h->data<<'\n';
    if(m.body)record.write(m.body,m.length);
    record.close();
    std::string mode;std::ifstream(state("mock-mode"))>>mode;
    if(mode=="tls-failure")return CURLE_PEER_FAILED_VERIFICATION;
    if(mode=="overflow")return m.write(const_cast<char*>("x"),std::numeric_limits<size_t>::max(),2,m.context)==0?CURLE_WRITE_ERROR:CURLE_OK;
    if(!mode.empty()&&mode[0]>='1'&&mode[0]<='5')m.status=std::stol(mode);
    std::string output="{\"cursor\":0,\"events\":[],\"has_more\":false}";
    if(mode=="exact-limit"||mode=="oversized")output.assign(8388608+(mode=="oversized"), 'x');
    if(mode=="image"||mode=="large-image") {
        output=std::string("\x89PNG\r\n\x1a\n",8);
        output.resize(mode=="large-image"?4194305:16,'x');
    }
    for(size_t at=0;at<output.size();at+=16384) {
        size_t count=std::min<size_t>(16384,output.size()-at);
        if(m.write(output.data()+at,1,count,m.context)!=count)return CURLE_WRITE_ERROR;
    }
    return CURLE_OK;
}
"""
class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory();cls.binary=pathlib.Path(cls.build.name)/'entry'
        flags=shlex.split(os.environ.get('CPPFLAGS',''))+shlex.split(os.environ.get('CXXFLAGS',''))
        compiler=shlex.split(os.environ.get('CXX','g++'))
        mock=pathlib.Path(cls.build.name)/'mock_curl.cpp'
        mock.write_text(MOCK_CURL)
        subprocess.run(compiler+flags+['-std=c++17','-Wall','-Wextra','-Werror','-O2',str(HERE/'backend.cpp'),str(mock),
                                     '-o',str(cls.binary)]+shlex.split(os.environ.get('LDFLAGS',''))+
                       shlex.split(os.environ.get('LIBS','-lcurl')),check=True)
    @classmethod
    def tearDownClass(cls):cls.build.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.server=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.server.bind(str(self.root/'sock'));self.server.listen(1)
        self.env=dict(os.environ,GOODBOT_STATE=str(self.root))
        self.start()
    def start(self):
        self.proc=subprocess.Popen([str(self.binary),str(self.root/'sock')],env=self.env,stderr=subprocess.PIPE)
        self.peer,_=self.server.accept();self.peer.settimeout(5)
    def stop(self):
        self.peer.close();self.proc.wait(timeout=5)
        self.assertEqual(self.proc.returncode,0,self.proc.stderr.read().decode());self.proc.stderr.close()
    def tearDown(self):
        if self.proc.poll() is None:self.stop()
        self.server.close();self.tmp.cleanup()
    def raw(self,t,b=b''):
        self.peer.send(struct.pack('=II',t,len(b)));self.peer.send(b)
    def send(self,t,s):
        data=base64.b64encode(s.encode())
        for at in range(0,max(1,len(data)),32000):self.raw(100,f'{t}\n{at}\n{len(data)}\n'.encode()+data[at:at+32000])
    def receive(self):
        result=b''
        while True:
            t,n=struct.unpack('=II',self.peer.recv(8));self.assertEqual(t,100)
            t,at,total,piece=self.peer.recv(n).split(b'\n',3)
            self.assertEqual(int(at),len(result));result+=piece
            if len(result)==int(total):return int(t),base64.b64decode(result).decode()
    def test_empty_datagram_chunked_unicode_and_restart(self):
        self.raw(0);self.assertEqual(self.receive(),(0,'{}'))
        value=json.dumps({'draft':'café ✍'*70000},ensure_ascii=False)
        self.send(1,value);self.assertEqual(self.receive(),(1,'saved'))
        self.assertEqual((self.root/'state.json').stat().st_mode&0o777,0o600)
        self.stop();self.start();self.raw(0xfffffffe,b'1');self.assertEqual(self.receive(),(0,value))
        self.raw(0xfffffffd,b'0');self.raw(0);self.assertEqual(self.receive(),(0,value))
    def test_capture_boundary_and_unsupported_native_open(self):
        self.send(2,'');self.assertEqual(self.receive()[0],9)
        self.send(6,'{}');self.assertIn('unavailable',self.receive()[1])
    def test_snapshot_failure_has_a_distinct_code_and_can_recover(self):
        (self.root/'state.json').mkdir()
        self.send(1,'{"draft":"keep in memory"}')
        self.assertEqual(self.receive()[0],11)
        self.assertEqual(list(self.root.glob('.write-*')),[])
        (self.root/'state.json').rmdir()
        self.send(1,'{"draft":"saved after repair"}')
        self.assertEqual(self.receive(),(1,'saved'))
    def configure(self):
        (self.root/'config').write_text('url=https://example.invalid\ntoken='+'a'*32+'\ncf_id=client\ncf_secret=secret\n')
        (self.root/'config').chmod(0o600)
    def test_https_config_private_credentials_and_http_failures(self):
        self.configure()
        self.send(5,'0');self.assertEqual(self.receive(),(5,'{"cursor":0,"events":[],"has_more":false}'))
        request=(self.root/'request-record').read_text()
        self.assertIn('CF-Access-Client-Secret: secret',request)
        self.assertIn('Authorization: Bearer '+'a'*32,request)
        self.assertIn('/v1/events?after=0&wait=20',request)
        payload=json.dumps({'request_id':'chat_000000000001','destination':'muse','content':{'mode':'raw_text','text':'Hello café'}})
        for route,path in ((3,'captures'),(4,'replies'),(10,'conversations')):
            self.send(route,payload);self.assertEqual(self.receive()[0],route)
            request=(self.root/'request-record').read_text()
            self.assertIn('/v1/'+path,request)
            self.assertIn('Content-Type: application/json',request)
            self.assertTrue(request.endswith(payload))
        self.assertFalse((self.root/'request.tmp').exists())
        self.assertFalse((self.root/'response.tmp').exists())
        argv=pathlib.Path(f'/proc/{self.proc.pid}/cmdline').read_bytes()
        self.assertNotIn(b'secret',argv);self.assertNotIn(b'a'*32,argv)
        for code,kind in ((302,9),(401,9),(413,8),(503,9)):
            (self.root/'mock-mode').write_text(str(code))
            self.send(10,payload);reply=self.receive()
            self.assertEqual(reply[0],kind);self.assertIn('HTTP '+str(code),reply[1])
            self.assertNotIn('secret',reply[1]);self.assertNotIn('a'*32,reply[1])
        (self.root/'mock-mode').write_text('tls-failure')
        self.send(5,'0');self.assertIn('HTTPS request failed',self.receive()[1])
        (self.root/'config').chmod(0o644);self.send(5,'0');self.assertIn('mode 600',self.receive()[1])
    def test_response_and_request_bounds_without_content_length(self):
        self.configure()
        for mode,expected in (('exact-limit',5),('oversized',9),('overflow',9)):
            (self.root/'mock-mode').write_text(mode)
            self.send(5,'0');kind,value=self.receive();self.assertEqual(kind,expected)
            if mode=='exact-limit':self.assertEqual(len(value),8*1024*1024)
        (self.root/'mock-mode').write_text('image')
        self.send(7,'fixture_source_001');self.assertTrue(self.receive()[1].startswith('data:image/png;base64,'))
        (self.root/'mock-mode').write_text('large-image')
        self.send(7,'fixture_source_001');self.assertEqual(self.receive(),(9,'source image too large'))
        before=(self.root/'request-record').read_bytes()
        self.send(10,'x'*(8*1024*1024+1));self.assertEqual(self.receive(),(9,'request too large'))
        self.assertEqual((self.root/'request-record').read_bytes(),before)
    def test_config_rejects_header_injection_and_non_https(self):
        self.configure()
        original=(self.root/'config').read_bytes()
        for config in (original.replace(b'https:',b'http:'),original.replace(b'secret',b'secret\rInjected: x'),
                       original.replace(b'secret',b'secret\0Injected: x')):
            (self.root/'config').write_bytes(config)
            self.send(5,'0');self.assertEqual(self.receive()[0],9)
            self.assertFalse((self.root/'request-record').exists())
    def test_invalid_chunk_does_not_replace_snapshot(self):
        self.send(1,'{"safe":true}');self.receive()
        self.raw(100,b'1\n9\n10\nx');self.assertEqual(self.receive()[0],9)
        self.raw(0);self.assertEqual(self.receive(),(0,'{"safe":true}'))
if __name__=='__main__':unittest.main()
