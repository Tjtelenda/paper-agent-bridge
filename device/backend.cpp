// Native socket transport + private durable storage; UI owns JSON, libcurl owns TLS.
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <curl/curl.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <sstream>
#include <iostream>
#include <map>
#include <memory>
#include <regex>
#include <stdexcept>
#include <string>
#include <vector>
using std::string;
constexpr size_t MAX = 32 * 1024 * 1024, CHUNK = 32000;
struct Header  {
    uint32_t type, size;
};
static_assert(sizeof(Header) == 8, "AppLoad ABI");
void require(bool ok, const char *why)  {
    if (!ok) throw std::runtime_error(why);
}
void writeAll(int fd, const string &s)  {
    size_t at = 0;
    while (at < s.size())  {
        ssize_t n = write(fd, s.data()+at, s.size()-at);
        if (n < 0 && errno == EINTR) continue;
        require(n > 0, "write failed");
        at += n;
    }
}
string readFile(const string &path, size_t limit = MAX)  {
    int fd = open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0 && errno == ENOENT) return "";
    require(fd >= 0, "cannot read local file");
    string out;
    char b[8192];
    ssize_t n;
    while ((n = read(fd, b, sizeof b)) > 0)  {
        out.append(b, n);
        if (out.size() > limit)  {
            close(fd);
            throw std::runtime_error("local file too large");
        }
    }
    close(fd);
    require(n == 0, "read failed");
    return out;
}
void atomicWrite(const string &dir, const string &name, const string &body)  {
    string tmp = dir + "/.write-XXXXXX";
    std::vector<char> p(tmp.begin(),tmp.end());
    p.push_back(0);
    int fd = mkstemp(p.data());
    require(fd >= 0, "cannot create durable snapshot");
    try  {
        writeAll(fd,body);
        require(fsync(fd)==0,"snapshot sync failed");
        close(fd);
        fd=-1;
        require(rename(p.data(),(dir+"/"+name).c_str())==0,"snapshot rename failed");
        int d = open(dir.c_str(),O_RDONLY|O_DIRECTORY);
        require(d >= 0,"directory open failed");
        int result=fsync(d);
        close(d);
        require(result==0,"directory sync failed");
    }
    catch (...)  {
        if(fd>=0)close(fd);
        unlink(p.data());
        throw;
    }
}
void packet(int fd, uint32_t type, const string &body)  {
    Header h {
        type,static_cast<uint32_t>(body.size())
    };
    require(send(fd,&h,sizeof h,MSG_NOSIGNAL)==sizeof h,"IPC header send failed");
    if (!body.empty()) require(send(fd,body.data(),body.size(),MSG_NOSIGNAL)==static_cast<ssize_t>(body.size()),"IPC body send failed");
}
string base64(const string &s)  {
    static const char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    string out;
    unsigned value=0;
    int bits=-6;
    for(unsigned char c:s) {
        value=(value<<8)|c;
        bits+=8;
        while(bits>=0) {
            out+=alphabet[(value>>bits)&63];
            bits-=6;
        }
    }
    if(bits>-6)out+=alphabet[((value<<8)>>(bits+8))&63];
    while(out.size()%4)out+='=';
    return out;
}
string unbase64(const string &s)  {
    static const string alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    string out;
    unsigned value=0;
    int bits=-8;
    bool padding=false;
    for(char c:s) {
        if(c=='=') {
            padding=true;
            continue;
        }
        auto p=alphabet.find(c);
        require(!padding&&p!=string::npos,"invalid base64");
        value=(value<<6)|p;
        bits+=6;
        if(bits>=0) {
            out+=char((value>>bits)&255);
            bits-=8;
        }
    }
    return out;
}
void message(int fd, uint32_t type, const string &raw)  {
    string body=base64(raw);
    // Small datagrams avoid the kernel's AF_UNIX packet-size limit for images.
    for(size_t at=0; at<body.size() || at==0; at+=CHUNK) packet(fd,100,std::to_string(type)+"\n"+std::to_string(at)+"\n"+std::to_string(body.size())+"\n"+body.substr(at,CHUNK));
}
std::map<string,string> config(const string &dir)  {
    struct stat st {
    };
    require(lstat((dir+"/config").c_str(),&st)==0 && S_ISREG(st.st_mode) && st.st_uid==getuid() && (st.st_mode&077)==0,"config must be owned by you with mode 600");
    std::map<string,string> c;
    std::istringstream in(readFile(dir+"/config",8192));
    string line;
    while(std::getline(in,line))  {
        if(line.empty()||line[0]=='#')continue;
        auto p=line.find('=');
        require(p!=string::npos,"invalid config line");
        require(line.find_first_of("\r\0",0,2)==string::npos,"invalid configuration value");
        c[line.substr(0,p)]=line.substr(p+1);
    }
    require(std::regex_match(c["url"],std::regex("https://[A-Za-z0-9.-]+(:[0-9]+)?")),"url must be an HTTPS origin");
    require(std::regex_match(c["token"],std::regex("[A-Za-z0-9_-]{32,128}")),"invalid bridge token");
    require(c["cf_id"].empty()==c["cf_secret"].empty(),"Cloudflare credentials must be a pair");
    return c;
}
string request(const string &dir,uint32_t type,const string &body,int &status)  {
    auto c=config(dir);
    string path;
    if(type==3||type==4||type==10)  {
        require(body.size()<=8*1024*1024,"request too large");
        path=type==3?"/v1/captures":type==4?"/v1/replies":"/v1/conversations";
    }
    else if(type==7) {
        require(std::regex_match(body,std::regex("[A-Za-z0-9_-]{1,128}")),"invalid source ID");
        path="/v1/sources/"+body;
    }
    else  {
        require(std::regex_match(body,std::regex("[0-9]{1,15}")),"invalid inbox cursor");
        path="/v1/events?after="+body+"&wait=20";
    }
    std::unique_ptr<CURL,decltype(&curl_easy_cleanup)> curl(curl_easy_init(),curl_easy_cleanup);
    require(bool(curl),"HTTPS initialization failed");
    std::unique_ptr<curl_slist,decltype(&curl_slist_free_all)> headers(nullptr,curl_slist_free_all);
    auto header=[&](const string &value) {
        auto *next=curl_slist_append(headers.get(),value.c_str());
        require(next!=nullptr,"HTTPS header allocation failed");
        headers.release();
        headers.reset(next);
    };
    header("Authorization: Bearer "+c["token"]);
    if(!c["cf_id"].empty()) {
        header("CF-Access-Client-Id: "+c["cf_id"]);
        header("CF-Access-Client-Secret: "+c["cf_secret"]);
    }
    string response,url=c["url"]+path;
    auto option=[&](CURLoption name,auto value) {
        require(curl_easy_setopt(curl.get(),name,value)==CURLE_OK,"HTTPS configuration failed");
    };
    option(CURLOPT_URL,url.c_str());
    option(CURLOPT_PROTOCOLS_STR,"https");
    option(CURLOPT_FOLLOWLOCATION,0L);
    option(CURLOPT_SSL_VERIFYPEER,1L);
    option(CURLOPT_SSL_VERIFYHOST,2L);
    option(CURLOPT_CONNECTTIMEOUT,8L);
    option(CURLOPT_TIMEOUT,28L);
    option(CURLOPT_NOSIGNAL,1L);
    option(CURLOPT_MAXFILESIZE_LARGE,static_cast<curl_off_t>(8*1024*1024));
    option(CURLOPT_WRITEFUNCTION,+[](char *data,size_t size,size_t count,void *context)->size_t {
        auto &out=*static_cast<string*>(context);
        constexpr size_t limit=8*1024*1024;
        if(size && count>(limit-out.size())/size)return 0;
        size_t bytes=size*count;
        try { out.append(data,bytes); }
        catch(...) { return 0; } // Never unwind a C callback.
        return bytes;
    });
    option(CURLOPT_WRITEDATA,&response);
    if(type==3||type==4||type==10) {
        header("Content-Type: application/json");
        option(CURLOPT_POSTFIELDS,body.data());
        option(CURLOPT_POSTFIELDSIZE_LARGE,static_cast<curl_off_t>(body.size()));
    }
    option(CURLOPT_HTTPHEADER,headers.get());
    require(curl_easy_perform(curl.get())==CURLE_OK,"Offline or HTTPS request failed; saved items remain queued");
    long code=0;
    require(curl_easy_getinfo(curl.get(),CURLINFO_RESPONSE_CODE,&code)==CURLE_OK && code>=100 && code<=599,"invalid HTTP status");
    status=static_cast<int>(code);
    return response;
}
int main(int argc,char **argv)  {
    umask(0077);
    signal(SIGPIPE,SIG_IGN);
    try  {
        require(argc==2,"usage: entry APPLOAD_SOCKET (GOODBOT_STATE required)");
        struct CurlRuntime {
            CurlRuntime() { require(curl_global_init(CURL_GLOBAL_DEFAULT)==CURLE_OK,"HTTPS initialization failed"); }
            ~CurlRuntime() { curl_global_cleanup(); }
        } curlRuntime;
        const char *env=getenv("GOODBOT_STATE");
        string dir=env?env:"/home/root/.local/share/goodbot-bridge";
        require(mkdir(dir.c_str(),0700)==0||errno==EEXIST,"create state directory first");
        struct stat st {
        }
        ;
        require(lstat(dir.c_str(),&st)==0&&S_ISDIR(st.st_mode)&&st.st_uid==getuid()&&(st.st_mode&077)==0,"state directory must be private and owned by you");
        int lock=open((dir+"/lock").c_str(),O_CREAT|O_RDWR|O_NOFOLLOW|O_CLOEXEC,0600);
        require(lock>=0&&flock(lock,LOCK_EX|LOCK_NB)==0,"another backend owns state");
        int fd=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0);
        require(fd>=0,"socket failed");
        sockaddr_un addr {
        }
        ;
        addr.sun_family=AF_UNIX;
        require(strlen(argv[1])<sizeof addr.sun_path,"socket path too long");
        strcpy(addr.sun_path,argv[1]);
        require(connect(fd,reinterpret_cast<sockaddr*>(&addr),sizeof addr)==0,"AppLoad connect failed");
        string assembled;
        uint32_t target=0;
        size_t total=0;
        while(true)  {
            Header h {
            }
            ;
            ssize_t n=recv(fd,&h,sizeof h,MSG_TRUNC);
            if(n==0)break;
            require(n==sizeof h&&h.size<=MAX,"invalid IPC header");
            string body(h.size,'\0');
            // management.cpp always sends a second datagram, including zero-length bodies.
            char empty;
            require(recv(fd,h.size?&body[0]:&empty,body.size(),MSG_TRUNC)==static_cast<ssize_t>(h.size),"invalid IPC body");
            if(h.type==0xffffffff)break;
            try  {
                if(h.type==100)  {
                    size_t a=body.find('\n'),b=body.find('\n',a+1),c=body.find('\n',b+1);
                    require(a!=string::npos&&b!=string::npos&&c!=string::npos,"invalid chunk");
                    auto number=[](const string&s) {
                        require(std::regex_match(s,std::regex("[0-9]{1,9}")),"invalid chunk number");
                        return std::stoul(s);
                    }
                    ;
                    uint32_t t=number(body.substr(0,a));
                    size_t at=number(body.substr(a+1,b-a-1)),len=number(body.substr(b+1,c-b-1));
                    require(len<=MAX,"snapshot too large");
                    if(at==0) {
                        assembled.clear();
                        target=t;
                        total=len;
                    }
                    require(t==target&&len==total&&at==assembled.size()&&body.size()-c-1<=total-at,"out-of-order chunk");
                    assembled+=body.substr(c+1);
                    if(assembled.size()!=total)continue;
                    h.type=target;
                    body=unbase64(assembled);
                    assembled.clear();
                }
                if(h.type==0||h.type==0xfffffffe)  {
                    auto saved=readFile(dir+"/state.json");
                    message(fd,0,saved.empty()?"{}":saved);
                }
                else if(h.type==1)  {
                    require(!body.empty(),"empty state");
                    atomicWrite(dir,"state.json",body);
                    message(fd,1,"saved");
                }
                else if(h.type==3||h.type==4||h.type==5||h.type==7||h.type==10)  {
                    int status=0;
                    auto response=request(dir,h.type,body,status);
                    if(h.type==7&&status==200) {
                        require(response.size()<=4*1024*1024,"source image too large");
                        bool png=response.size()>8&&response.substr(0,8)==string("\x89PNG\r\n\x1a\n",8);
                        bool jpg=response.size()>3&&static_cast<unsigned char>(response[0])==255&&static_cast<unsigned char>(response[1])==216;
                        require(png||jpg,"source is not PNG/JPEG");
                        response=string("data:image/")+(png?"png":"jpeg")+";base64,"+base64(response);
                    }
                    bool permanent=status==400||status==409||status==413||status==422;
                    message(fd,status>=200&&status<300?h.type:(permanent?8:9),status>=200&&status<300?response:"HTTP "+std::to_string(status)+": request retained; check credentials or payload");
                }
                else if(h.type==0xfffffffd)continue;
                else if(h.type==6)message(fd,9,"Exact notebook/page opening is unavailable until the firmware adapter is verified");
                else throw std::runtime_error("unsupported command");
            }
            catch(const std::exception &e)  {
                message(fd,h.type==1?11:9,e.what());
            }
        }
        close(fd);
        close(lock);
    }
    catch(const std::exception&e)  {
        std::cerr<<e.what()<<'\n';
        return 1;
    }
}
