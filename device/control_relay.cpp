// Separate worker: HTTPS and synchronous command execution never block notebook input.
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QRegularExpression>
#include <QTemporaryFile>
#include <QTemporaryDir>
#include <QThread>
#include <curl/curl.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cerrno>
#include <memory>
#include <stdexcept>

namespace {
const QString Root = QStringLiteral("/home/root/.local/share/goodbot-bridge");
const QString Control = Root + "/control";
constexpr qsizetype JsonLimit = 2 * 1024 * 1024, SpoolLimit = JsonLimit + 8192, ImageLimit = 4 * 1024 * 1024;
void require(bool condition) { if (!condition) throw std::runtime_error("Control relay unavailable"); }
QByteArray json(const QJsonObject &object) { return QJsonDocument(object).toJson(QJsonDocument::Compact); }
QJsonObject object(const QByteArray &bytes) {
    QJsonParseError error{}; const auto document = QJsonDocument::fromJson(bytes, &error);
    require(error.error == QJsonParseError::NoError && document.isObject()); return document.object();
}
QByteArray readPrivate(const QString &path, qsizetype limit, bool missing = false) {
    const int fd = ::open(QFile::encodeName(path).constData(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0 && missing && errno == ENOENT) return {};
    require(fd >= 0);
    struct stat st{};
    if (::fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != ::getuid() || (st.st_mode & 077) || st.st_nlink != 1 || st.st_size <= 0 || st.st_size > limit) {
        ::close(fd); require(false);
    }
    QFile file; if (!file.open(fd, QIODevice::ReadOnly, QFileDevice::AutoCloseHandle)) { ::close(fd); require(false); }
    const auto bytes = file.read(limit + 1);
    require(bytes.size() == st.st_size && file.error() == QFileDevice::NoError); return bytes;
}
void privateDirectory(const QString &path) {
    const auto name = QFile::encodeName(path);
    require(::mkdir(name.constData(), 0700) == 0 || errno == EEXIST);
    struct stat st{};
    require(::lstat(name.constData(), &st) == 0 && S_ISDIR(st.st_mode) && st.st_uid == ::getuid() && !(st.st_mode & 077));
}
bool syncDirectory(const QString &path) {
    const int fd = ::open(QFile::encodeName(path).constData(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    const bool okay = ::fsync(fd) == 0; ::close(fd); return okay;
}
void atomicWrite(const QString &name, const QByteArray &bytes, const QString &directory = Control) {
    QTemporaryFile temporary(directory + "/.relay-XXXXXX");
    require(temporary.open());
    require(temporary.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner));
    require(temporary.write(bytes) == bytes.size() && temporary.flush() && ::fsync(temporary.handle()) == 0);
    require(::rename(QFile::encodeName(temporary.fileName()).constData(), QFile::encodeName(name).constData()) == 0);
    temporary.setAutoRemove(false);
    require(syncDirectory(directory));
}
QMap<QString, QByteArray> parseConfig(const QByteArray &bytes) {
    QMap<QString, QByteArray> result;
    for (const auto &line : bytes.split('\n')) {
        if (line.isEmpty() || line.startsWith('#')) continue;
        const auto split = line.indexOf('=');
        require(split > 0 && !line.contains('\r') && !line.contains('\0'));
        const QString key = QString::fromLatin1(line.left(split));
        require(!result.contains(key)); result[key] = line.mid(split + 1);
    }
    require(QRegularExpression("^https://[A-Za-z0-9.-]+(:[0-9]+)?$").match(QString::fromLatin1(result["url"])).hasMatch());
    require(QRegularExpression("^[A-Za-z0-9_-]{32,128}$").match(QString::fromLatin1(result["token"])).hasMatch());
    require(result["cf_id"].isEmpty() == result["cf_secret"].isEmpty());
    for (const auto &key : {QStringLiteral("cf_id"), QStringLiteral("cf_secret")}) {
        for (const char c : result[key]) require(c >= 32 && c <= 126);
    }
    return result;
}
QMap<QString, QByteArray> config() { return parseConfig(readPrivate(Root + "/config", 8192)); }
QByteArray request(const QMap<QString, QByteArray> &settings, const QByteArray &path, const QByteArray &body = {}, qsizetype limit = JsonLimit) {
    std::unique_ptr<CURL, decltype(&curl_easy_cleanup)> curl(curl_easy_init(), curl_easy_cleanup); require(bool(curl));
    std::unique_ptr<curl_slist, decltype(&curl_slist_free_all)> headers(nullptr, curl_slist_free_all);
    auto header = [&](const QByteArray &value) {
        auto *next = curl_slist_append(headers.get(), value.constData()); require(next != nullptr);
        headers.release(); headers.reset(next);
    };
    header("Authorization: Bearer " + settings.value("token"));
    if (!settings.value("cf_id").isEmpty()) {
        header("CF-Access-Client-Id: " + settings.value("cf_id"));
        header("CF-Access-Client-Secret: " + settings.value("cf_secret"));
    }
    const auto url = settings.value("url") + path;
    auto option = [&](CURLoption key, auto value) { require(curl_easy_setopt(curl.get(), key, value) == CURLE_OK); };
    option(CURLOPT_URL, url.constData()); option(CURLOPT_PROTOCOLS_STR, "https");
    option(CURLOPT_FOLLOWLOCATION, 0L); option(CURLOPT_SSL_VERIFYPEER, 1L); option(CURLOPT_SSL_VERIFYHOST, 2L);
    option(CURLOPT_CONNECTTIMEOUT, 8L); option(CURLOPT_TIMEOUT, body.isEmpty() ? 25L : 15L); option(CURLOPT_NOSIGNAL, 1L);
    struct Response { QByteArray bytes; qsizetype limit; } response{{}, limit};
    option(CURLOPT_WRITEFUNCTION, +[](char *data, size_t size, size_t count, void *context) -> size_t {
        auto &out = *static_cast<Response *>(context);
        if (size && count > size_t(out.limit - out.bytes.size()) / size) return 0;
        const size_t total = size * count;
        try { out.bytes.append(data, qsizetype(total)); } catch (...) { return 0; }
        return total;
    });
    option(CURLOPT_WRITEDATA, &response);
    if (!body.isEmpty()) {
        header("Content-Type: application/json"); option(CURLOPT_POSTFIELDS, body.constData());
        option(CURLOPT_POSTFIELDSIZE_LARGE, curl_off_t(body.size()));
    }
    option(CURLOPT_HTTPHEADER, headers.get()); require(curl_easy_perform(curl.get()) == CURLE_OK);
    long status = 0; require(curl_easy_getinfo(curl.get(), CURLINFO_RESPONSE_CODE, &status) == CURLE_OK && status >= 200 && status < 300);
    return response.bytes;
}
bool validId(const QString &id) { return QRegularExpression("^[A-Za-z0-9][A-Za-z0-9_.-]{15,79}$").match(id).hasMatch(); }
bool validCommand(const QJsonObject &command) {
    return command.size() == 3 && command.contains("request_id") && command.contains("operation") && command.contains("arguments") &&
        validId(command.value("request_id").toString()) && command.value("operation").isString() && command.value("arguments").isObject() && json(command).size() < 128 * 1024;
}
bool validReceipt(const QJsonObject &receipt, const QJsonObject &command) {
    const QString status = receipt.value("status").toString();
    return receipt.size() == 4 && receipt.value("request_id") == command.value("request_id") && receipt.value("operation") == command.value("operation") &&
        receipt.value("result").isObject() && (status == "verified" || status == "needs_review" || status == "rejected");
}
void stageImage(const QMap<QString, QByteArray> &settings, const QJsonObject &command) {
    if (command.value("operation") != "insert_image") return;
    const auto args = command.value("arguments").toObject();
    const auto hash = args.value("image_sha256").toString(); const auto format = args.value("image_format").toString();
    require(QRegularExpression("^[a-f0-9]{64}$").match(hash).hasMatch() && (format == "png" || format == "jpeg"));
    privateDirectory(Control + "/assets");
    const auto name = hash.toLatin1() + '.' + format.toLatin1(); const QString path = Control + "/assets/" + QString::fromLatin1(name);
    auto bytes = readPrivate(path, ImageLimit, true);
    if (bytes.isEmpty()) {
        bytes = request(settings, "/v1/device/control/assets/" + name, {}, ImageLimit);
        require(!bytes.isEmpty() && QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex() == hash.toLatin1());
        QTemporaryFile temporary(Control + "/assets/.download-XXXXXX"); require(temporary.open());
        require(temporary.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner));
        require(temporary.write(bytes) == bytes.size() && temporary.flush() && ::fsync(temporary.handle()) == 0);
        const int linked = ::link(QFile::encodeName(temporary.fileName()).constData(), QFile::encodeName(path).constData());
        require(linked == 0 || errno == EEXIST);
        require(temporary.remove()); require(syncDirectory(Control + "/assets"));
        bytes = readPrivate(path, ImageLimit);
    }
    require(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex() == hash.toLatin1());
}
QJsonObject execute(const QJsonObject &command) {
    QProcess child;
    child.setProgram(QStringLiteral("/home/root/xovi/goodbot/backend/control-client"));
    child.setStandardErrorFile(QProcess::nullDevice());
    child.start(); require(child.waitForStarted(3000));
    const auto bytes = json(command) + '\n'; require(child.write(bytes) == bytes.size()); child.closeWriteChannel();
    QByteArray response;
    for (int elapsed = 0; elapsed < 32000 && child.state() != QProcess::NotRunning; elapsed += 100) {
        child.waitForReadyRead(100); response += child.readAllStandardOutput();
        if (response.size() > JsonLimit) { child.kill(); child.waitForFinished(1000); require(false); }
    }
    if (child.state() != QProcess::NotRunning) { child.kill(); child.waitForFinished(1000); require(false); }
    response += child.readAllStandardOutput(); require(response.size() <= JsonLimit);
    const auto receipt = object(response); require(validReceipt(receipt, command)); return receipt;
}
QJsonObject uncertain(const QJsonObject &command) {
    return {{"request_id", command.value("request_id")}, {"operation", command.value("operation")}, {"status", "needs_review"},
        {"result", QJsonObject{{"error", "Device delivery did not yield a verified receipt; reconcile the same request ID"}}}};
}
}
int main(int argc, char **argv) {
    QCoreApplication application(argc, argv); ::umask(0077);
    try {
        if (argc == 2 && QByteArray(argv[1]) == "--self-test") {
            const QJsonObject command{{"request_id", "relay-validation-0001"}, {"operation", "inspect"}, {"arguments", QJsonObject{}}};
            require(validCommand(command));
            auto invalid = command; invalid["request_id"] = "../../bad"; require(!validCommand(invalid));
            invalid = command; invalid["extra"] = true; require(!validCommand(invalid));
            const auto receipt = uncertain(command); require(validReceipt(receipt, command));
            auto badReceipt = receipt; badReceipt["request_id"] = "another-validation-0001"; require(!validReceipt(badReceipt, command));
            badReceipt = receipt; badReceipt["status"] = "in_progress"; require(!validReceipt(badReceipt, command));
            const QByteArray goodConfig = "url=https://example.com\ntoken=" + QByteArray(32, 'a') + '\n';
            require(parseConfig(goodConfig).value("url") == "https://example.com");
            for (const auto &badConfig : {goodConfig + "cf_id=unpaired\n", goodConfig + "url=https://another.example\n",
                    QByteArray("url=http://example.com\ntoken=") + QByteArray(32, 'a'), goodConfig + "cf_id=bad\rcarriage\n"}) {
                bool rejected = false; try { parseConfig(badConfig); } catch (...) { rejected = true; } require(rejected);
            }
            QTemporaryDir temporary; require(temporary.isValid());
            const QString spool = temporary.path() + "/receipt";
            atomicWrite(spool, json(receipt), temporary.path()); require(object(readPrivate(spool, JsonLimit)) == receipt);
            atomicWrite(spool, "replacement", temporary.path()); require(readPrivate(spool, JsonLimit) == "replacement");
            require(::symlink("receipt", QFile::encodeName(temporary.path() + "/link").constData()) == 0);
            bool refusedLink = false; try { readPrivate(temporary.path() + "/link", JsonLimit); } catch (...) { refusedLink = true; }
            require(refusedLink);
            return 0;
        }
        require(argc == 1 && curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK);
        privateDirectory(Control);
        const int lock = ::open(QFile::encodeName(Control + "/relay.lock").constData(), O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
        require(lock >= 0 && ::flock(lock, LOCK_EX | LOCK_NB) == 0);
        struct stat lockInfo{};
        require(::fstat(lock, &lockInfo) == 0 && S_ISREG(lockInfo.st_mode) && lockInfo.st_uid == ::getuid() &&
            lockInfo.st_nlink == 1 && !(lockInfo.st_mode & 077));
        const QString pending = Control + "/pending-receipt.json";
        for (;;) {
            try {
                const auto settings = config();
                const auto saved = readPrivate(pending, SpoolLimit, true);
                if (!saved.isEmpty()) {
                    const auto record = object(saved); const auto receipt = record.value("receipt").toObject();
                    require(record.value("origin").toString().toLatin1() == settings.value("url"));
                    require(validId(receipt.value("request_id").toString()));
                    const auto ack = object(request(settings, "/v1/device/control/receipts", json(receipt)));
                    require(ack.value("accepted").toBool() && ack.value("request_id") == receipt.value("request_id"));
                    require(::unlink(QFile::encodeName(pending).constData()) == 0 && syncDirectory(Control));
                    continue;
                }
                const auto next = object(request(settings, "/v1/device/control/next?wait=20"));
                if (next.value("command").isNull()) continue;
                require(next.size() == 1 && next.value("command").isObject());
                const auto command = next.value("command").toObject(); require(validCommand(command));
                QJsonObject receipt;
                try { stageImage(settings, command); receipt = execute(command); }
                catch (...) { receipt = uncertain(command); }
                if (receipt.value("status") == "rejected" && receipt.value("result").toObject().value("error") == "Device command busy; retry the same request ID") {
                    QThread::sleep(5); continue; // No journal or mutation occurred for the busy response.
                }
                const auto record = json({{"origin", QString::fromLatin1(settings.value("url"))}, {"receipt", receipt}});
                // Until this receipt is durable, retry storage only, never the native mutation.
                for (;;) {
                    try { atomicWrite(pending, record); break; } catch (...) { QThread::sleep(15); }
                }
            } catch (...) { QThread::sleep(15); }
        }
    } catch (...) { return argc == 2 ? 1 : 0; }
}
