#include <sys/socket.h>
#include <sys/un.h>
#include <poll.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>

int main() {
    constexpr size_t RequestLimit = 128 * 1024, ResponseLimit = 2 * 1024 * 1024;
    std::string request;
    char buffer[8192];
    while (const size_t n = std::fread(buffer, 1, sizeof buffer, stdin)) {
        request.append(buffer, n);
        if (request.size() > RequestLimit - 1) { std::fputs("Request exceeds limit\n", stderr); return 1; }
    }
    if (std::ferror(stdin)) return 1;
    while (!request.empty() && (request.back() == '\n' || request.back() == '\r')) request.pop_back();
    if (request.empty() || request.find('\n') != std::string::npos) return 1;
    request.push_back('\n');
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return 1;
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    std::strcpy(address.sun_path, "/home/root/.local/share/goodbot-bridge/control/socket");
    if (::connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof address) != 0) {
        std::fputs("Native notebook control unavailable\n", stderr); ::close(fd); return 1;
    }
    ucred credentials{}; socklen_t size = sizeof credentials;
    if (::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &size) != 0 || size != sizeof credentials || credentials.uid != ::getuid()) {
        ::close(fd); return 1;
    }
    const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    size_t sent = 0;
    std::string response;
    while (std::chrono::steady_clock::now() < end) {
        pollfd item{fd, short(sent < request.size() ? POLLOUT : POLLIN), 0};
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(end - std::chrono::steady_clock::now()).count();
        const int ready = ::poll(&item, 1, int(remaining > 0 ? remaining : 1));
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0 || (item.revents & (POLLERR | POLLNVAL)) ||
            (sent < request.size() && (item.revents & POLLHUP))) break;
        if (sent < request.size() && (item.revents & POLLOUT)) {
            const auto n = ::send(fd, request.data() + sent, request.size() - sent, MSG_NOSIGNAL);
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n <= 0) break;
            sent += size_t(n);
        } else if (sent == request.size() && (item.revents & (POLLIN | POLLHUP))) {
            const auto n = ::recv(fd, buffer, sizeof buffer, 0);
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n <= 0) break;
            response.append(buffer, size_t(n));
            if (response.size() > ResponseLimit) break;
            if (response.back() == '\n') {
                std::fwrite(response.data(), 1, response.size(), stdout);
                ::close(fd);
                // The endpoint produces compact canonical JSON with one trusted top-level status.
                for (const auto &entry : {std::pair<const char *, int>{"\"status\":\"verified\"}\n", 0}, {"\"status\":\"needs_review\"}\n", 2}}) {
                    const size_t length = std::strlen(entry.first);
                    if (response.size() >= length && response.compare(response.size() - length, length, entry.first) == 0) return entry.second;
                }
                return 1;
            }
        }
    }
    ::close(fd);
    std::fputs("No receipt; outcome uncertain. Retry only with the same request ID.\n", stderr);
    return 2;
}
