#include <QBuffer>
#include "InkPad.h"
#include "ControlEndpoint.h"
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QImage>
#include <QProcess>
#include <QProcessEnvironment>
#include <QQueue>
#include <QQmlExtensionPlugin>
#include <QSocketNotifier>
#include <QStringDecoder>
#include <QTemporaryDir>
#include <QTimer>
#include <QVariantMap>
#include <qqml.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <cerrno>
#include <cstdint>
#include <cstring>

struct Header { uint32_t type, size; };
static_assert(sizeof(Header) == 8);

namespace {
constexpr qsizetype PacketLimit = 64 * 1024;
constexpr qsizetype QueueLimit = 34 * 1024 * 1024;
constexpr qint64 ImageLimit = 4 * 1024 * 1024;
constexpr auto BackendPath = "/home/root/xovi/goodbot/backend/entry";

QProcessEnvironment backendEnvironment() {
    auto environment = QProcessEnvironment::systemEnvironment();
    environment.remove(QStringLiteral("LD_PRELOAD"));
    environment.remove(QStringLiteral("XOVI_ROOT"));
    return environment;
}

class ImageBuffer final : public QBuffer {
protected:
    qint64 writeData(const char *data, qint64 size) override {
        if (size < 0 || pos() + size > ImageLimit) return -1;
        return QBuffer::writeData(data, size);
    }
};
}

// One connection survives panel hide/reopen. All socket and process work is asynchronous.
class Connection final : public QObject {
    Q_OBJECT
public:
    static Connection *instance() {
        static Connection *connection = new Connection(QCoreApplication::instance());
        return connection;
    }
    bool ready() const { return peer >= 0; }
    void sendMessage(uint type, const QString &contents) {
        const QByteArray bytes = contents.toUtf8();
        if (type > 100 || bytes.size() > PacketLimit || queuedBytes + 8 + bytes.size() > QueueLimit) {
            fail("Native bridge queue limit reached; saved data is retained");
            return;
        }
        if (failed) {
            emit messageReceived(12, QStringLiteral("Native backend is unavailable; reopen after checking installation"));
            return;
        }
        Header header{type, static_cast<uint32_t>(bytes.size())};
        outbound.enqueue(QByteArray(reinterpret_cast<const char *>(&header), sizeof header));
        outbound.enqueue(bytes); // Existing backend consumes an empty body datagram too.
        queuedBytes += sizeof header + bytes.size();
        flush();
    }
signals:
    void messageReceived(uint type, const QString &contents);
    void readyChanged();
private:
    explicit Connection(QObject *parent) : QObject(parent), directory(QStringLiteral("/tmp/goodbot-native-XXXXXX")) {
        // Backend errors are deliberately not forwarded to the note application's logs.
        process.setStandardErrorFile(QProcess::nullDevice());
        process.setStandardOutputFile(QProcess::nullDevice());
        connect(&process, &QProcess::errorOccurred, this, [this] { fail("Native backend could not start"); });
        connect(&process, &QProcess::finished, this, [this] { fail("Native backend stopped; saved data remains on disk"); });
        QTimer::singleShot(0, this, &Connection::start);
    }
    ~Connection() override {
        process.disconnect(this);
        closeSockets();
        if (process.state() != QProcess::NotRunning) {
            process.kill();
            process.waitForFinished(1000);
        }
    }
    void closeSockets() {
        if (reader) reader->setEnabled(false);
        if (writer) writer->setEnabled(false);
        if (acceptor) acceptor->setEnabled(false);
        if (peer >= 0) { ::close(peer); peer = -1; }
        if (listener >= 0) { ::close(listener); listener = -1; }
    }
    void fail(const char *reason) {
        if (failed) return;
        qWarning("GoodBot native: %s", reason);
        failed = true;
        closeSockets();
        outbound.clear();
        queuedBytes = 0;
        process.kill();
        emit readyChanged();
        emit messageReceived(12, QString::fromLatin1(reason));
    }
    void start() {
        if (failed) return;
        if (!directory.isValid()) { fail("Cannot create private native socket directory"); return; }
        const QByteArray dir = directory.path().toLocal8Bit();
        struct stat info{};
        if (::chmod(dir.constData(), 0700) != 0 || ::lstat(dir.constData(), &info) != 0 ||
            !S_ISDIR(info.st_mode) || info.st_uid != ::getuid() || (info.st_mode & 077)) {
            fail("Native socket directory is not private"); return;
        }
        const QByteArray socketPath = dir + "/endpoint";
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        if (socketPath.size() >= static_cast<qsizetype>(sizeof address.sun_path)) {
            fail("Native socket path is too long"); return;
        }
        std::memcpy(address.sun_path, socketPath.constData(), socketPath.size() + 1);
        listener = ::socket(AF_UNIX, SOCK_SEQPACKET | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        if (listener < 0 || ::bind(listener, reinterpret_cast<sockaddr *>(&address), sizeof address) != 0 ||
            ::chmod(socketPath.constData(), 0600) != 0 || ::listen(listener, 1) != 0) {
            fail("Cannot create private native listener"); return;
        }
        acceptor = new QSocketNotifier(listener, QSocketNotifier::Read, this);
        connect(acceptor, &QSocketNotifier::activated, this, &Connection::acceptPeer);
        process.setProcessEnvironment(backendEnvironment());
        process.setProgram(QString::fromLatin1(BackendPath));
        process.setArguments({QString::fromLocal8Bit(socketPath)});
        process.start();
        QTimer::singleShot(10000, this, [this] {
            if (peer < 0 && !failed) fail("Native backend did not connect");
        });
    }
    void acceptPeer() {
        int candidate = ::accept4(listener, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (candidate < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) fail("Native accept failed");
            return;
        }
        ucred credentials{};
        socklen_t size = sizeof credentials;
        if (::getsockopt(candidate, SOL_SOCKET, SO_PEERCRED, &credentials, &size) != 0 ||
            size != sizeof credentials || credentials.uid != ::getuid() ||
            credentials.pid != process.processId()) {
            ::close(candidate);
            return;
        }
        peer = candidate;
        acceptor->setEnabled(false);
        ::close(listener);
        listener = -1;
        reader = new QSocketNotifier(peer, QSocketNotifier::Read, this);
        writer = new QSocketNotifier(peer, QSocketNotifier::Write, this);
        writer->setEnabled(false);
        connect(reader, &QSocketNotifier::activated, this, &Connection::readPackets);
        connect(writer, &QSocketNotifier::activated, this, &Connection::flush);
        emit readyChanged();
        flush();
    }
    void flush() {
        if (peer < 0) return;
        // Bounded batches yield to pen/touch event handling during large-image transfers.
        for (int batch = 0; batch < 64 && !outbound.isEmpty(); ++batch) {
            const QByteArray &bytes = outbound.head();
            const ssize_t count = ::send(peer, bytes.constData(), bytes.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
                writer->setEnabled(true);
                return;
            }
            if (count != bytes.size()) { fail("Native socket write failed; saved data is retained"); return; }
            queuedBytes -= bytes.size();
            outbound.dequeue();
        }
        writer->setEnabled(!outbound.isEmpty());
    }
    void readPackets() {
        for (int batch = 0; batch < 64; ++batch) {
            QByteArray bytes(awaitingBody ? incoming.size : sizeof(Header), Qt::Uninitialized);
            const ssize_t count = ::recv(peer, bytes.data(), bytes.size(), MSG_DONTWAIT | MSG_TRUNC);
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
            if (count == 0) { fail("Native backend disconnected; saved data remains on disk"); return; }
            if (count != bytes.size()) { fail("Invalid native message framing"); return; }
            if (!awaitingBody) {
                std::memcpy(&incoming, bytes.constData(), sizeof incoming);
                if (incoming.size > PacketLimit || incoming.type > 100) { fail("Invalid native message header"); return; }
                awaitingBody = incoming.size != 0;
                // Backend intentionally omits body datagrams for zero-length replies.
                if (!awaitingBody) emit messageReceived(incoming.type, QString());
            } else {
                awaitingBody = false;
                QStringDecoder decoder(QStringDecoder::Utf8);
                const QString text = decoder(bytes);
                if (decoder.hasError()) { fail("Invalid native message text"); return; }
                emit messageReceived(incoming.type, text);
                if (failed) return;
            }
        }
    }
    QTemporaryDir directory;
    QProcess process;
    QSocketNotifier *acceptor = nullptr, *reader = nullptr, *writer = nullptr;
    QQueue<QByteArray> outbound;
    qsizetype queuedBytes = 0;
    int listener = -1, peer = -1;
    Header incoming{};
    bool awaitingBody = false, failed = false;
};

class Endpoint : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString applicationID MEMBER applicationID)
    Q_PROPERTY(bool ready READ ready NOTIFY readyChanged)
public:
    explicit Endpoint(QObject *parent = nullptr) : QObject(parent), connection(Connection::instance()) {
        connect(connection, &Connection::messageReceived, this, &Endpoint::messageReceived);
        connect(connection, &Connection::readyChanged, this, &Endpoint::readyChanged);
    }
    QString applicationID;
    bool ready() const { return connection->ready(); }
    Q_INVOKABLE void sendMessage(uint type, const QString &contents) { connection->sendMessage(type, contents); }
    Q_INVOKABLE QVariantMap encodeImage(const QImage &image) const {
        if (image.isNull() || image.width() > 8192 || image.height() > 8192 ||
            qint64(image.width()) * image.height() > 16 * 1024 * 1024) {
            return {{QStringLiteral("error"), QStringLiteral("Selection image is empty or too large")}};
        }
        ImageBuffer buffer;
        buffer.open(QIODevice::WriteOnly);
        if (!image.save(&buffer, "PNG") || buffer.size() > ImageLimit) {
            return {{QStringLiteral("error"), QStringLiteral("Selection PNG exceeds the 4 MiB limit")}};
        }
        const QByteArray bytes = buffer.data();
        const QVariantMap encoded{{QStringLiteral("media_type"), QStringLiteral("image/png")},
                                  {QStringLiteral("data_base64"), QString::fromLatin1(bytes.toBase64())}};
        return {{QStringLiteral("image"), encoded},
                {QStringLiteral("sha256"), QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex())},
                {QStringLiteral("width"), image.width()},
                {QStringLiteral("height"), image.height()}};
    }
signals:
    void messageReceived(uint type, const QString &contents);
    void readyChanged();
private:
    Connection *connection;
};

class BridgePlugin final : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override {
        Q_ASSERT(QByteArray(uri) == "GoodBot.Bridge");
        qmlRegisterType<Endpoint>(uri, 1, 0, "Endpoint");
        qmlRegisterType<InkPad>(uri, 1, 0, "InkPad");
        qmlRegisterType<ControlEndpoint>(uri, 1, 0, "ControlEndpoint");
    }
};
#include "BridgePlugin.moc"

#ifdef GOODBOT_BRIDGE_PROBE
#include <cassert>
#include <QMouseEvent>
#include <memory>
#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
class InkProbe : public InkPad {
public:
    using InkPad::mousePressEvent;
    using InkPad::mouseMoveEvent;
    using InkPad::mouseReleaseEvent;
};
int main(int argc,char **argv) {
    QCoreApplication app(argc,argv);
    InkProbe pad; pad.setSize(QSizeF(64,64));
    QMouseEvent press(QEvent::MouseButtonPress,QPointF(10,10),QPointF(10,10),Qt::LeftButton,Qt::LeftButton,Qt::NoModifier);
    QMouseEvent move(QEvent::MouseMove,QPointF(50,50),QPointF(50,50),Qt::NoButton,Qt::LeftButton,Qt::NoModifier);
    QMouseEvent release(QEvent::MouseButtonRelease,QPointF(50,50),QPointF(50,50),Qt::LeftButton,Qt::NoButton,Qt::NoModifier);
    pad.mousePressEvent(&press);pad.mouseMoveEvent(&move);pad.mouseReleaseEvent(&release);
    QMouseEvent secondPress(QEvent::MouseButtonPress,QPointF(10,50),QPointF(10,50),Qt::LeftButton,Qt::LeftButton,Qt::NoModifier);
    QMouseEvent secondMove(QEvent::MouseMove,QPointF(50,10),QPointF(50,10),Qt::NoButton,Qt::LeftButton,Qt::NoModifier);
    pad.mousePressEvent(&secondPress);pad.mouseMoveEvent(&secondMove);pad.mouseReleaseEvent(&release);
    assert(pad.hasInk());
    const QString snapshot=pad.dataUrl();
    const QImage ink=QImage::fromData(QByteArray::fromBase64(snapshot.section(',',1).toLatin1()));
    assert(ink.size()==QSize(64,64)&&qGray(ink.pixel(10,10))<50&&qGray(ink.pixel(50,50))<50);
    assert(qGray(ink.pixel(10,50))<50&&qGray(ink.pixel(50,10))<50);
    InkProbe restored;restored.setSize(QSizeF(64,64));assert(restored.loadDataUrl(snapshot));
    const QString beforeBadLoad=restored.dataUrl();
    assert(!restored.loadDataUrl("data:image/png;base64,broken"));
    assert(restored.hasInk()&&restored.dataUrl()==beforeBadLoad);
    restored.clear();assert(!restored.hasInk()&&restored.dataUrl().isEmpty());
    qputenv("LD_PRELOAD", "/probe/xovi.so");
    qputenv("XOVI_ROOT", "/probe/xovi");
    qputenv("GOODBOT_STATE", "/probe/private-state");
    const auto childEnvironment = backendEnvironment();
    assert(!childEnvironment.contains("LD_PRELOAD") && !childEnvironment.contains("XOVI_ROOT"));
    assert(childEnvironment.value("GOODBOT_STATE") == "/probe/private-state");
    assert(qEnvironmentVariable("XOVI_ROOT") == "/probe/xovi");
    Endpoint endpoint;
    assert(!endpoint.ready());
    QVariantMap result;
    QImage image(32,48,QImage::Format_ARGB32); image.fill(Qt::white);
    bool invoked=QMetaObject::invokeMethod(&endpoint,"encodeImage",Qt::DirectConnection,Q_RETURN_ARG(QVariantMap,result),Q_ARG(QImage,image));
    assert(invoked&&!result.contains("error"));
    assert(result.value("width").toInt()==32&&result.value("height").toInt()==48);
    const auto encoded=result.value("image").toMap();
    const auto png=QByteArray::fromBase64(encoded.value("data_base64").toString().toLatin1());
    assert(QImage::fromData(png).size()==image.size());
    assert(result.value("sha256").toString()==QCryptographicHash::hash(png,QCryptographicHash::Sha256).toHex());
    assert(endpoint.encodeImage(QImage()).contains("error"));
    assert(endpoint.encodeImage(QImage(8193,1,QImage::Format_ARGB32)).contains("error"));
    QImage large(1400,1400,QImage::Format_ARGB32);uint32_t seed=1;
    for(int y=0;y<large.height();++y)for(int x=0;x<large.width();++x){seed=1664525*seed+1013904223;large.setPixel(x,y,seed);}
    assert(endpoint.encodeImage(large).contains("error"));
    qmlRegisterType<Endpoint>("GoodBot.Bridge",1,0,"Endpoint");
    QQmlEngine engine;
    engine.setImportPathList({}); // Probe the registered type, not the separately packaged QML UI.
    engine.rootContext()->setContextProperty("probeImage",image);
    QQmlComponent component(&engine);
    component.setData("import GoodBot.Bridge 1.0; Endpoint { property var encoded: encodeImage(probeImage) }",QUrl());
    std::unique_ptr<QObject> object(component.create());
    assert(object && !component.isError());
    assert(object->property("encoded").toMap().value("sha256")==result.value("sha256"));
    bool rejected=false;
    endpoint.sendMessage(0,QString()); // Queued safely before backend connection.
    QObject::connect(&endpoint,&Endpoint::messageReceived,[&](uint t,const QString&){if(t==12)rejected=true;});
    endpoint.sendMessage(101,QString());assert(rejected);
    return 0;
}

#endif
