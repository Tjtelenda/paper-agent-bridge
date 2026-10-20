#include "ControlEndpoint.h"
#include <QCryptographicHash>
#include <QBuffer>
#include <QProcessEnvironment>
#include <QImageReader>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSet>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>

namespace {
constexpr int RequestLimit = 128 * 1024, ResponseLimit = 2 * 1024 * 1024;
const QSet<QString> Operations = {"inspect", "list_templates", "create_notebook", "create_folder", "rename",
    "append_page", "set_template", "copy_pages", "move_pages", "delete_pages", "quick_sheet", "insert_text",
    "trash", "restore", "delete_item", "list_folder", "move_item", "open_page", "insert_image", "create_from_pages",
    "copy_item", "list_tags", "add_tags", "remove_tags", "request_status"};
QByteArray json(const QVariantMap &value) { return QJsonDocument::fromVariant(value).toJson(QJsonDocument::Compact); }
QVariantMap receipt(const QString &id, const QString &operation, const QString &status, const QString &error) {
    return {{"request_id", id}, {"operation", operation}, {"status", status}, {"result", QVariantMap{{"error", error}}}};
}
bool privateDirectory(const QString &path) {
    const QByteArray name = QFile::encodeName(path);
    const bool created = ::mkdir(name.constData(), 0700) == 0;
    if (!created && errno != EEXIST) return false;
    if (created) {
        const auto parent = QFile::encodeName(QFileInfo(path).absolutePath());
        const int fd = ::open(parent.constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (fd < 0) return false;
        const bool synced = ::fsync(fd) == 0; ::close(fd);
        if (!synced) return false;
    }
    struct stat st{};
    return ::lstat(name.constData(), &st) == 0 && S_ISDIR(st.st_mode) && st.st_uid == ::getuid() && !(st.st_mode & 077);
}
bool writeAll(int fd, const QByteArray &bytes) {
    qsizetype offset = 0;
    while (offset < bytes.size()) {
        const ssize_t n = ::write(fd, bytes.constData() + offset, bytes.size() - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        offset += n;
    }
    return true;
}
}

ControlEndpoint::ControlEndpoint(QObject *parent) : QObject(parent),
    directory(QStringLiteral("/home/root/.local/share/goodbot-bridge/control")) {
#ifdef GOODBOT_CONTROL_PROBE
    directory = qEnvironmentVariable("GOODBOT_CONTROL_TEST_DIR");
#endif
    relayRetry.setSingleShot(true); relayRetry.setInterval(30000);
    connect(&relayRetry, &QTimer::timeout, this, &ControlEndpoint::startRelay);
    connect(&relayProcess, &QProcess::finished, this, [this] { relayRetry.start(); });
    connect(&relayProcess, &QProcess::errorOccurred, this, [this] { relayRetry.start(); });
    deadline.setSingleShot(true);
    connect(&deadline, &QTimer::timeout, this, [this] {
        complete(activeId, {{"status", "needs_review"}, {"result", QVariantMap{{"error", "Device operation timed out; inspect before retrying with a new ID"}}}});
    });
    QTimer::singleShot(0, this, &ControlEndpoint::start);
}
ControlEndpoint::~ControlEndpoint() {
    relayRetry.stop(); relayProcess.disconnect(this);
    if (relayProcess.state() != QProcess::NotRunning) {
        relayProcess.terminate();
        if (!relayProcess.waitForFinished(1000)) { relayProcess.kill(); relayProcess.waitForFinished(1000); }
    }
    for (int fd : clients.keys()) closeClient(fd);
    if (listener >= 0) { ::close(listener); ::unlink(QFile::encodeName(directory + "/socket").constData()); }
}
void ControlEndpoint::start() {
    // Every existing ancestor must be a real directory; the writable private root is never followed through a link.
    const QString applicationDirectory = QFileInfo(directory).absolutePath();
    QString prefix;
    for (const QString &part : directory.split('/', Qt::SkipEmptyParts)) {
        prefix += '/' + part;
        struct stat st{};
        const auto path = QFile::encodeName(prefix);
        if (::lstat(path.constData(), &st) != 0) {
            if ((prefix != directory && prefix != applicationDirectory) || !privateDirectory(prefix)) return;
        } else {
            if (!S_ISDIR(st.st_mode)) return;
#ifdef GOODBOT_CONTROL_PROBE
            const bool testTemporaryRoot = prefix == "/tmp" && st.st_uid == 0 && (st.st_mode & S_ISVTX);
#else
            const bool testTemporaryRoot = false;
#endif
            if ((st.st_mode & 022) && !testTemporaryRoot) return;
        }
    }
    if (!privateDirectory(applicationDirectory) || !privateDirectory(directory) || !privateDirectory(directory + "/requests")) return;
    const auto path = QFile::encodeName(directory + "/socket");
    struct stat st{};
    if (::lstat(path.constData(), &st) == 0) {
        if (!S_ISSOCK(st.st_mode) || st.st_uid != ::getuid() || (st.st_mode & 077)) return;
        int probe = ::socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        sockaddr_un old{}; old.sun_family = AF_UNIX;
        std::memcpy(old.sun_path, path.constData(), path.size() + 1);
        const int connected = probe < 0 ? 0 : ::connect(probe, reinterpret_cast<sockaddr *>(&old), sizeof old);
        const int connectError = errno;
        if (probe >= 0) ::close(probe);
        if (connected == 0 || (connectError != ECONNREFUSED && connectError != ENOENT) || ::unlink(path.constData()) != 0) return;
    } else if (errno != ENOENT) return;
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    if (path.size() >= qsizetype(sizeof address.sun_path)) return;
    std::memcpy(address.sun_path, path.constData(), path.size() + 1);
    listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (listener < 0) return;
    if (::bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof address) != 0 ||
        ::chmod(path.constData(), 0600) != 0 || ::listen(listener, 8) != 0) {
        ::close(listener); listener = -1; return;
    }
    acceptor = new QSocketNotifier(listener, QSocketNotifier::Read, this);
    connect(acceptor, &QSocketNotifier::activated, this, &ControlEndpoint::acceptClients);
#ifndef GOODBOT_CONTROL_PROBE
    startRelay();
#endif
}
void ControlEndpoint::startRelay() {
    if (listener < 0 || relayProcess.state() != QProcess::NotRunning) return;
    auto environment = QProcessEnvironment::systemEnvironment();
    environment.remove(QStringLiteral("LD_PRELOAD")); environment.remove(QStringLiteral("XOVI_ROOT"));
    relayProcess.setProcessEnvironment(environment);
    relayProcess.setProgram(QStringLiteral("/home/root/xovi/goodbot/backend/control-relay"));
    relayProcess.setStandardOutputFile(QProcess::nullDevice()); relayProcess.setStandardErrorFile(QProcess::nullDevice());
    relayProcess.start();
}
void ControlEndpoint::acceptClients() {
    for (int batch = 0; batch < 8; ++batch) {
        const int fd = ::accept4(listener, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (fd < 0) return;
        ucred credentials{}; socklen_t size = sizeof credentials;
        if (clients.size() >= 8 || ::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &credentials, &size) != 0 ||
            size != sizeof credentials || credentials.uid != ::getuid()) { ::close(fd); continue; }
        auto *client = new Client{{}, {}, new QSocketNotifier(fd, QSocketNotifier::Read, this),
            new QSocketNotifier(fd, QSocketNotifier::Write, this), new QTimer(this)};
        clients.insert(fd, client);
        client->writer->setEnabled(false);
        client->timer->setSingleShot(true);
        connect(client->reader, &QSocketNotifier::activated, this, [this, fd] { readClient(fd); });
        connect(client->writer, &QSocketNotifier::activated, this, [this, fd] { writeClient(fd); });
        connect(client->timer, &QTimer::timeout, this, [this, fd] { closeClient(fd); });
        client->timer->start(30000);
    }
}
void ControlEndpoint::closeClient(int fd) {
    auto *client = clients.take(fd);
    if (!client) return;
    client->reader->setEnabled(false); client->writer->setEnabled(false); client->timer->stop();
    client->reader->deleteLater(); client->writer->deleteLater(); client->timer->deleteLater();
    ::close(fd); delete client;
    if (activeClient == fd) activeClient = -1; // The durable operation continues after a disconnect.
}
void ControlEndpoint::readClient(int fd) {
    auto *client = clients.value(fd);
    char buffer[8192];
    for (int batch = 0; client && batch < 17; ++batch) {
        const auto n = ::recv(fd, buffer, sizeof buffer, MSG_DONTWAIT);
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
        if (n <= 0) { closeClient(fd); return; }
        client->input.append(buffer, n);
        if (client->input.size() > RequestLimit) { reply(fd, receipt({}, {}, "rejected", "Request exceeds limit")); return; }
        const auto end = client->input.indexOf('\n');
        if (end >= 0) {
            if (end != client->input.size() - 1) { reply(fd, receipt({}, {}, "rejected", "One request per connection")); return; }
            client->reader->setEnabled(false);
            dispatch(fd, client->input.left(end)); return;
        }
    }
}
void ControlEndpoint::reply(int fd, const QVariantMap &value) {
    auto *client = clients.value(fd);
    if (!client) return;
    client->reader->setEnabled(false);
    client->output = json(value) + '\n';
    if (client->output.size() > ResponseLimit) { closeClient(fd); return; }
    writeClient(fd);
}
void ControlEndpoint::writeClient(int fd) {
    auto *client = clients.value(fd);
    if (!client) return;
    while (!client->output.isEmpty()) {
        const auto n = ::send(fd, client->output.constData(), client->output.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) { client->writer->setEnabled(true); return; }
        if (n <= 0) { closeClient(fd); return; }
        client->output.remove(0, n);
    }
    closeClient(fd);
}
bool ControlEndpoint::persist(const QString &id, const QVariantMap &record) {
    const auto bytes = json(record);
    if (bytes.size() > ResponseLimit) return false;
    const auto path = QFile::encodeName(directory + "/requests/" + id + ".json");
    const auto temporary = path + ".tmp";
    int fd = ::open(temporary.constData(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return false;
    const bool written = writeAll(fd, bytes) && ::fsync(fd) == 0;
    const bool closed = ::close(fd) == 0;
    if (!written || !closed || ::rename(temporary.constData(), path.constData()) != 0) {
        ::unlink(temporary.constData()); return false;
    }
    fd = ::open(QFile::encodeName(directory + "/requests").constData(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return false;
    const bool synced = ::fsync(fd) == 0; ::close(fd); return synced;
}
void ControlEndpoint::dispatch(int fd, const QByteArray &line) {
    QJsonParseError error{};
    const auto doc = QJsonDocument::fromJson(line, &error);
    const auto object = doc.object();
    const QString id = object.value("request_id").toString(), op = object.value("operation").toString();
    const QSet<QString> keys = {"request_id", "operation", "arguments"};
    const auto objectKeys = object.keys();
    if (error.error != QJsonParseError::NoError || !doc.isObject() || QSet<QString>(objectKeys.begin(), objectKeys.end()) != keys ||
        !QRegularExpression("^[A-Za-z0-9][A-Za-z0-9_.-]{15,79}$").match(id).hasMatch() ||
        !Operations.contains(op) || !object.value("arguments").isObject()) {
        reply(fd, receipt(id, op, "rejected", "Invalid request envelope or unsupported operation")); return;
    }
    auto readRecord = [this](const QString &target, bool &found, bool &valid) {
        found = false; valid = false;
        const auto path = QFile::encodeName(directory + "/requests/" + target + ".json");
        const int previous = ::open(path.constData(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
        if (previous < 0) { valid = errno == ENOENT; return QVariantMap{}; }
        found = true;
        struct stat st{};
        if (::fstat(previous, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != ::getuid() ||
            (st.st_mode & 077) || st.st_nlink != 1 || st.st_size <= 0 || st.st_size > ResponseLimit) {
            ::close(previous); return QVariantMap{};
        }
        QFile file;
        if (!file.open(previous, QIODevice::ReadOnly, QFileDevice::AutoCloseHandle)) { ::close(previous); return QVariantMap{}; }
        const auto bytes = file.read(ResponseLimit + 1);
        QJsonParseError parseError{};
        const auto document = QJsonDocument::fromJson(bytes, &parseError);
        if (bytes.size() != st.st_size || file.error() != QFileDevice::NoError || parseError.error != QJsonParseError::NoError || !document.isObject()) return QVariantMap{};
        const auto record = document.object().toVariantMap();
        const auto request = record.value("request").toMap();
        const auto savedReceipt = record.value("receipt").toMap();
        valid = request.value("request_id").toString() == target &&
            QRegularExpression("^[a-f0-9]{64}$").match(record.value("hash").toString()).hasMatch() &&
            (record.value("state") == "dispatching" || (record.value("state") == "completed" &&
                savedReceipt.value("request_id").toString() == target && savedReceipt.value("operation") == request.value("operation") &&
                (savedReceipt.value("status") == "verified" || savedReceipt.value("status") == "needs_review" || savedReceipt.value("status") == "rejected")));
        return record;
    };
    if (op == "request_status") {
        const auto args = object.value("arguments").toObject();
        const QString target = args.value("target_request_id").toString();
        if (args.size() != 1 || !args.contains("target_request_id") ||
            !QRegularExpression("^[A-Za-z0-9][A-Za-z0-9_.-]{15,79}$").match(target).hasMatch()) {
            reply(fd, receipt(id, op, "rejected", "Status requires one valid target_request_id")); return;
        }
        bool found, valid;
        const auto record = readRecord(target, found, valid);
        if (!valid) { reply(fd, receipt(id, op, "needs_review", "Target journal cannot be safely read")); return; }
        QVariantMap result{{"found", found}};
        if (found) {
            if (record.contains("receipt") && !journalFailed) result["receipt"] = record.value("receipt");
            else result["uncertain"] = true;
        }
        reply(fd, {{"request_id", id}, {"operation", op}, {"status", "verified"}, {"result", result}});
        return;
    }
    if (journalFailed) { reply(fd, receipt(id, op, "needs_review", "Journal persistence failed; inspect device storage")); return; }
    const auto hash = QString::fromLatin1(QCryptographicHash::hash(json(object.toVariantMap()), QCryptographicHash::Sha256).toHex());
    bool found, valid;
    const auto previous = readRecord(id, found, valid);
    if (!valid) { reply(fd, receipt(id, op, "rejected", "Journal cannot be safely read")); return; }
    if (found) {
        if (previous.value("hash").toString() != hash) reply(fd, receipt(id, op, "rejected", "Request ID conflicts with journal"));
        else if (previous.contains("receipt")) reply(fd, previous.value("receipt").toMap());
        else reply(fd, receipt(id, op, "needs_review", "Previous dispatch outcome uncertain; inspect before using a new ID"));
        return;
    }
    if (!commandsAccepted || !activeId.isEmpty()) { reply(fd, receipt(id, op, "rejected", "Device command busy; retry the same request ID")); return; }
    QVariantMap record{{"hash", hash}, {"request", object.toVariantMap()}, {"state", "dispatching"}};
    if (!persist(id, record)) { reply(fd, receipt(id, op, "rejected", "Cannot durably journal command")); return; }
    activeId = id; activeClient = fd; activeRecord = record;
    deadline.start(25000);
    emit activeRequestChanged();
    emit requestReceived(object.toVariantMap());
}
void ControlEndpoint::complete(const QString &id, const QVariantMap &completion) {
    if (id.isEmpty() || id != activeId) return;
    const auto op = activeRecord.value("request").toMap().value("operation").toString();
    const QString status = completion.value("status").toString();
    if (status != "verified" && status != "needs_review" && status != "rejected") return;
    QVariantMap answer{{"request_id", id}, {"operation", op}, {"status", status}, {"result", completion.value("result").toMap()}};
    activeRecord["state"] = "completed"; activeRecord["receipt"] = answer;
    if (!persist(id, activeRecord)) {
        journalFailed = true;
        answer = receipt(id, op, "needs_review", "Operation may have run; receipt persistence failed");
    }
    deadline.stop();
    const int fd = activeClient;
    activeId.clear(); activeRecord.clear(); activeClient = -1;
    emit activeRequestChanged();
    if (fd >= 0) reply(fd, answer);
}


namespace {
QByteArray privateFile(const QString &path, qint64 limit) {
    QString prefix;
    const auto parts = QFileInfo(path).absolutePath().split('/', Qt::SkipEmptyParts);
    for (const auto &part : parts) {
        prefix += '/' + part;
        struct stat parent{};
        if (::lstat(QFile::encodeName(prefix).constData(), &parent) != 0 || !S_ISDIR(parent.st_mode)) return {};
    }
    const int fd = ::open(QFile::encodeName(path).constData(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return {};
    struct stat st{};
    if (::fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != ::getuid() || st.st_nlink != 1 ||
        st.st_size <= 0 || st.st_size > limit || (st.st_mode & 022)) { ::close(fd); return {}; }
    QFile file;
    if (!file.open(fd, QIODevice::ReadOnly, QFileDevice::AutoCloseHandle)) { ::close(fd); return {}; }
    const auto bytes = file.read(limit + 1);
    if (bytes.size() != st.st_size || file.error() != QFileDevice::NoError) return {};
    return bytes;
}
QString digest(const QByteArray &bytes) {
    return QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex());
}
}
QUrl ControlEndpoint::imageUrl(const QString &sha256, const QString &format) const {
    if (!QRegularExpression("^[a-f0-9]{64}$").match(sha256).hasMatch() || (format != "png" && format != "jpeg")) return {};
    struct stat assets{};
    if (::lstat(QFile::encodeName(directory + "/assets").constData(), &assets) != 0 || !S_ISDIR(assets.st_mode) ||
        assets.st_uid != ::getuid() || (assets.st_mode & 077)) return {};
    const QString path = directory + "/assets/" + sha256 + '.' + format;
    const auto bytes = privateFile(path, 4 * 1024 * 1024);
    if (bytes.isEmpty() || digest(bytes) != sha256) return {};
    QBuffer buffer; buffer.setData(bytes); buffer.open(QIODevice::ReadOnly);
    QImageReader reader(&buffer, format.toLatin1());
    const QSize size = reader.size();
    if (!size.isValid() || size.width() > 8192 || size.height() > 8192 || qint64(size.width()) * size.height() > 16 * 1024 * 1024) return {};
    if (reader.read().isNull()) return {};
    return QUrl::fromLocalFile(path);
}
QVariantMap ControlEndpoint::savedState(const QString &id) const {
    if (!QRegularExpression("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$").match(id).hasMatch()) return {};
    QString base = QStringLiteral("/home/root/.local/share/remarkable/xochitl/");
#ifdef GOODBOT_CONTROL_PROBE
    base = directory + "/xochitl/";
#endif
    QVariantMap result;
    for (const QString &kind : {QStringLiteral("metadata"), QStringLiteral("content")}) {
        const auto bytes = privateFile(base + id + '.' + kind, 8 * 1024 * 1024);
        QJsonParseError error{};
        const auto document = QJsonDocument::fromJson(bytes, &error);
        if (bytes.isEmpty() && kind == "content" && result.value("metadata").toMap().value("type").toString() == "CollectionType") {
            struct stat info{};
            const auto path = QFile::encodeName(base + id + ".content");
            if (::lstat(path.constData(), &info) != 0 && errno == ENOENT) return result;
        }
        if (bytes.isEmpty() || error.error != QJsonParseError::NoError || !document.isObject()) return {};
        result[kind] = document.object().toVariantMap();
        result[kind + "_sha256"] = digest(bytes);
    }
    return result;
}


QString ControlEndpoint::pageSha256(const QString &documentId, const QString &pageId) const {
    const QRegularExpression uuid("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$");
    if (!uuid.match(documentId).hasMatch() || !uuid.match(pageId).hasMatch()) return {};
    QString base = QStringLiteral("/home/root/.local/share/remarkable/xochitl/");
#ifdef GOODBOT_CONTROL_PROBE
    base = directory + "/xochitl/";
#endif
    const auto bytes = privateFile(base + documentId + '/' + pageId + ".rm", 32 * 1024 * 1024);
    return bytes.isEmpty() ? QString() : digest(bytes);
}


bool ControlEndpoint::itemFilesAbsent(const QString &id) const {
    if (!QRegularExpression("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$").match(id).hasMatch()) return false;
    QString base = QStringLiteral("/home/root/.local/share/remarkable/xochitl");
#ifdef GOODBOT_CONTROL_PROBE
    base = directory + "/xochitl";
#endif
    QString prefix;
    for (const QString &part : base.split('/', Qt::SkipEmptyParts)) {
        prefix += '/' + part;
        struct stat parent{};
        if (::lstat(QFile::encodeName(prefix).constData(), &parent) != 0 || !S_ISDIR(parent.st_mode)) return false;
    }
    struct stat library{};
    if (::lstat(QFile::encodeName(base).constData(), &library) != 0 || library.st_uid != ::getuid() || (library.st_mode & 022)) return false;
    for (const QString &suffix : {QStringLiteral(".metadata"), QStringLiteral(".content"), QString()}) {
        struct stat st{};
        if (::lstat(QFile::encodeName(base + '/' + id + suffix).constData(), &st) == 0 || errno != ENOENT) return false;
    }
    return true;
}


QVariantList ControlEndpoint::pageImages(const QString &documentId, const QString &pageId) const {
    const QRegularExpression uuid("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$");
    if (!uuid.match(documentId).hasMatch() || !uuid.match(pageId).hasMatch()) return {};
    QString base = QStringLiteral("/home/root/.local/share/remarkable/xochitl/");
#ifdef GOODBOT_CONTROL_PROBE
    base = directory + "/xochitl/";
#endif
    const QString folder = base + documentId + '/' + pageId;
    struct stat st{};
    if (::lstat(QFile::encodeName(folder).constData(), &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != ::getuid() || (st.st_mode & 022)) return {};
    QDirIterator files(folder, QDir::Files | QDir::NoDotAndDotDot | QDir::NoSymLinks);
    const QRegularExpression imageName("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\\.(png|jpg|jpeg)$");
    QVariantList result; qint64 total = 0;
    while (files.hasNext()) {
        const QString path = files.next();
        if (!imageName.match(files.fileName()).hasMatch()) continue;
        if (result.size() >= 128) return {};
        const auto bytes = privateFile(path, 16 * 1024 * 1024);
        total += bytes.size(); if (bytes.isEmpty() || total > 64 * 1024 * 1024) return {};
        QBuffer buffer; buffer.setData(bytes); if (!buffer.open(QIODevice::ReadOnly)) return {};
        QImageReader reader(&buffer); const auto size = reader.size();
        if (!reader.canRead() || !size.isValid() || size.width() > 8192 || size.height() > 8192 || qint64(size.width()) * size.height() > 16 * 1024 * 1024) return {};
        result.append(QVariantMap{{"filename", files.fileName()}, {"sha256", digest(bytes)},
            {"width", size.width()}, {"height", size.height()}, {"size", bytes.size()}});
    }
    return result;
}

#ifdef GOODBOT_CONTROL_PROBE
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QTemporaryDir>
#include <QThread>
#include <cassert>
int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    QTemporaryDir temporary;
    assert(temporary.isValid());
    const QString directory = temporary.path() + "/application/control";
    qputenv("GOODBOT_CONTROL_TEST_DIR", directory.toUtf8());
    auto pump = [] { QCoreApplication::processEvents(); QThread::msleep(1); };
    auto connectClient = [&] {
        const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        sockaddr_un address{}; address.sun_family = AF_UNIX;
        const auto path = QFile::encodeName(directory + "/socket");
        assert(path.size() < qsizetype(sizeof address.sun_path));
        std::memcpy(address.sun_path, path.constData(), path.size() + 1);
        assert(fd >= 0 && ::connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof address) == 0);
        return fd;
    };
    auto exchange = [&](QVariantMap request, bool partial = false) {
        const int fd = connectClient();
        const auto bytes = json(request) + '\n';
        const int split = partial ? 7 : int(bytes.size());
        assert(::send(fd, bytes.constData(), split, MSG_NOSIGNAL) == split);
        if (partial) { pump(); assert(::send(fd, bytes.constData() + split, bytes.size() - split, MSG_NOSIGNAL) == bytes.size() - split); }
        QByteArray response; QElapsedTimer timer; timer.start();
        while (!response.endsWith('\n') && timer.elapsed() < 2000) {
            pump(); char buffer[8192]; const auto n = ::recv(fd, buffer, sizeof buffer, MSG_DONTWAIT);
            if (n > 0) response.append(buffer, n);
            else if (n == 0) break;
        }
        ::close(fd);
        assert(response.endsWith('\n'));
        return QJsonDocument::fromJson(response).object().toVariantMap();
    };
    QVariantMap request{{"request_id", "probe-command-00001"}, {"operation", "inspect"}, {"arguments", QVariantMap{}}};
    int dispatched = 0;
    {
        ControlEndpoint endpoint; endpoint.setAcceptsCommands(true); pump();
        QObject::connect(&endpoint, &ControlEndpoint::requestReceived, [&](const QVariantMap &r) {
            ++dispatched;
            assert(endpoint.isActive(r.value("request_id").toString()));
            endpoint.complete(r.value("request_id").toString(), {{"status", "verified"}, {"result", QVariantMap{{"ok", true}}}});
        });
        endpoint.setAcceptsCommands(false);
        const auto locked = exchange(request);
        assert(locked.value("status") == "rejected" && locked.value("result").toMap().value("error") == "Device command busy; retry the same request ID");
        assert(dispatched == 0 && !QFile::exists(directory + "/requests/probe-command-00001.json"));
        endpoint.setAcceptsCommands(true);
        const auto first = exchange(request, true);
        assert(first.value("status") == "verified" && dispatched == 1);
        assert(endpoint.activeRequestId().isEmpty() && !endpoint.isActive("probe-command-00001"));
        assert(endpoint.imageUrl(QString(64, 'a'), "png").isEmpty());
        assert(endpoint.imageUrl("../bad", "png").isEmpty());
        assert(endpoint.savedState("../../bad").isEmpty());
        assert(privateDirectory(directory + "/assets"));
        auto storeImage = [&](int width) {
            QImage image(width, 16, QImage::Format_ARGB32); image.fill(Qt::white);
            QBuffer buffer; assert(buffer.open(QIODevice::WriteOnly)); assert(image.save(&buffer, "PNG"));
            const auto hash = digest(buffer.data());
            QFile file(directory + "/assets/" + hash + ".png");
            assert(file.open(QIODevice::WriteOnly)); assert(file.write(buffer.data()) == buffer.size()); assert(file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner)); file.close();
            return hash;
        };
        const auto imageHash = storeImage(16);
        assert(endpoint.imageUrl(imageHash, "png").isLocalFile());
        assert(endpoint.imageUrl(imageHash, "jpeg").isEmpty());
        assert(endpoint.imageUrl(storeImage(8193), "png").isEmpty());
        assert(privateDirectory(directory + "/xochitl"));
        const QString documentId = "12345678-1234-1234-1234-123456789abc";
        for (const QString &kind : {QStringLiteral("metadata"), QStringLiteral("content")}) {
            QFile file(directory + "/xochitl/" + documentId + '.' + kind);
            assert(file.open(QIODevice::WriteOnly)); assert(file.write("{\"test\":true}") == 13); assert(file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner)); file.close();
        }
        const auto saved = endpoint.savedState(documentId);
        assert(saved.value("metadata").toMap().value("test").toBool());
        assert(saved.value("content_sha256").toString().size() == 64);
        QFile broken(directory + "/xochitl/" + documentId + ".metadata");
        assert(broken.open(QIODevice::WriteOnly | QIODevice::Truncate)); assert(broken.write("bad") == 3); broken.close();
        assert(endpoint.savedState(documentId).isEmpty());
        const QString folderId = "abcdef12-1234-1234-1234-123456789abc";
        QFile folder(directory + "/xochitl/" + folderId + ".metadata");
        assert(folder.open(QIODevice::WriteOnly));
        const QByteArray folderMetadata("{\"type\":\"CollectionType\"}");
        assert(folder.write(folderMetadata) == folderMetadata.size());
        assert(folder.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner)); folder.close();
        const auto folderState = endpoint.savedState(folderId);
        assert(folderState.value("metadata").toMap().value("type") == "CollectionType" && !folderState.contains("content"));
        const QString missingContentId = "abcdef34-1234-1234-1234-123456789abc";
        QFile documentOnly(directory + "/xochitl/" + missingContentId + ".metadata");
        assert(documentOnly.open(QIODevice::WriteOnly));
        assert(documentOnly.write("{}") == 2);
        assert(documentOnly.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner)); documentOnly.close();
        assert(endpoint.savedState(missingContentId).isEmpty());
        QVariantMap statusRequest{{"request_id", "probe-status-query-0001"}, {"operation", "request_status"},
            {"arguments", QVariantMap{{"target_request_id", request.value("request_id")}}}};
        endpoint.setAcceptsCommands(false);
        assert(exchange(request) == first && dispatched == 1);
        const auto status = exchange(statusRequest).value("result").toMap();
        endpoint.setAcceptsCommands(true);
        assert(status.value("found").toBool() && status.value("receipt").toMap() == first && dispatched == 1);
        assert(!QFile::exists(directory + "/requests/probe-status-query-0001.json"));
        auto invalidStatus = statusRequest;
        invalidStatus["arguments"] = QVariantMap{{"target_request_id", request.value("request_id")}, {"extra", true}};
        assert(exchange(invalidStatus).value("status") == "rejected" && dispatched == 1);
        statusRequest["arguments"] = QVariantMap{{"target_request_id", "probe-never-seen-0001"}};
        assert(!exchange(statusRequest).value("result").toMap().value("found").toBool() && dispatched == 1);
        assert(privateDirectory(directory + "/xochitl/" + documentId));
        const QString pageId = "abcdef56-1234-1234-1234-123456789abc";
        QFile page(directory + "/xochitl/" + documentId + '/' + pageId + ".rm");
        assert(page.open(QIODevice::WriteOnly)); assert(page.write("native-page") == 11);
        assert(page.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner)); page.close();
        assert(endpoint.pageSha256(documentId, pageId) == digest("native-page"));
        assert(privateDirectory(directory + "/xochitl/" + documentId + '/' + pageId));
        assert(QFile::copy(directory + "/assets/" + imageHash + ".png", directory + "/xochitl/" + documentId + '/' + pageId + "/12345678-1234-1234-1234-123456789abc.png"));
        const auto images = endpoint.pageImages(documentId, pageId);
        assert(images.size() == 1 && images.first().toMap().value("sha256") == imageHash);
        assert(images.first().toMap().value("width").toInt() == 16 && images.first().toMap().value("height").toInt() == 16);
        assert(endpoint.pageImages("../bad", pageId).isEmpty());
        assert(endpoint.pageSha256("../bad", pageId).isEmpty());
        assert(!endpoint.itemFilesAbsent("../bad"));
        assert(!endpoint.itemFilesAbsent(documentId));
        assert(endpoint.itemFilesAbsent("ffffffff-1234-1234-1234-123456789abc"));
        assert(exchange(request) == first && dispatched == 1);
        auto conflict = request; conflict["arguments"] = QVariantMap{{"different", true}};
        assert(exchange(conflict).value("status") == "rejected" && dispatched == 1);
        auto invalid = request; invalid["operation"] = "system.run";
        assert(exchange(invalid).value("status") == "rejected" && dispatched == 1);
        invalid = request; invalid["extra"] = true;
        assert(exchange(invalid).value("status") == "rejected" && dispatched == 1);
        struct stat st{}; assert(::stat(QFile::encodeName(directory + "/socket").constData(), &st) == 0 && !(st.st_mode & 077));
        // Disconnecting does not cancel a dispatched command or invalidate its receipt.
        auto disconnected = request; disconnected["request_id"] = "probe-disconnected-0001";
        const int fd = connectClient(); const auto bytes = json(disconnected) + '\n';
        assert(::send(fd, bytes.constData(), bytes.size(), MSG_NOSIGNAL) == bytes.size());
        ::close(fd); pump(); pump();
        assert(exchange(disconnected).value("status") == "verified" && dispatched == 2);
    }
    request["request_id"] = "probe-crash-unknown-0001";
    {
        ControlEndpoint endpoint; endpoint.setAcceptsCommands(true); pump();
        QObject::connect(&endpoint, &ControlEndpoint::requestReceived, [&](const QVariantMap &) { ++dispatched; });
        const int fd = connectClient(); const auto bytes = json(request) + '\n';
        assert(::send(fd, bytes.constData(), bytes.size(), MSG_NOSIGNAL) == bytes.size());
        pump(); pump(); assert(dispatched == 3);
        QVariantMap query{{"request_id", "probe-pending-query-0001"}, {"operation", "request_status"},
            {"arguments", QVariantMap{{"target_request_id", request.value("request_id")}}}};
        const auto pending = exchange(query).value("result").toMap();
        assert(pending.value("found").toBool() && pending.value("uncertain").toBool() && dispatched == 3);
        ::close(fd);
    }
    {
        ControlEndpoint endpoint; endpoint.setAcceptsCommands(true); pump();
        QObject::connect(&endpoint, &ControlEndpoint::requestReceived, [&](const QVariantMap &) { ++dispatched; });
        assert(exchange(request).value("status") == "needs_review" && dispatched == 3);
        auto blocked = request; blocked["request_id"] = "probe-blocked-00001";
        QFile blocker(directory + "/requests/probe-blocked-00001.json.tmp");
        assert(blocker.open(QIODevice::WriteOnly)); blocker.close();
        assert(exchange(blocked).value("status") == "rejected" && dispatched == 3);
        auto unsafe = request; unsafe["request_id"] = "probe-symlink-00001";
        assert(::symlink("/dev/null", QFile::encodeName(directory + "/requests/probe-symlink-00001.json").constData()) == 0);
        assert(exchange(unsafe).value("status") == "rejected" && dispatched == 3);
        auto oversized = request; oversized["request_id"] = "probe-oversized-00001";
        oversized["arguments"] = QVariantMap{{"text", QString(130 * 1024, 'x')}};
        assert(exchange(oversized).value("status") == "rejected" && dispatched == 3);
    }
    return 0;
}
#endif
