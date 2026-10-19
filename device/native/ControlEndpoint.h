#pragma once
#include <QObject>
#include <QVariantMap>
#include <QHash>
#include <QSocketNotifier>
#include <QTimer>
#include <QUrl>
#include <QProcess>

class ControlEndpoint : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString activeRequestId READ activeRequestId NOTIFY activeRequestChanged)
    Q_PROPERTY(bool acceptsCommands READ acceptsCommands WRITE setAcceptsCommands NOTIFY acceptsCommandsChanged)
public:
    explicit ControlEndpoint(QObject *parent = nullptr);
    ~ControlEndpoint() override;
    QString activeRequestId() const { return activeId; }
    bool acceptsCommands() const { return commandsAccepted; }
    void setAcceptsCommands(bool accepted) {
        if (commandsAccepted == accepted) return;
        commandsAccepted = accepted; emit acceptsCommandsChanged();
    }
    Q_INVOKABLE bool isActive(const QString &id) const { return !id.isEmpty() && id == activeId; }
    Q_INVOKABLE QUrl imageUrl(const QString &sha256, const QString &format) const;
    Q_INVOKABLE QVariantList pageImages(const QString &documentId, const QString &pageId) const;
    Q_INVOKABLE bool itemFilesAbsent(const QString &id) const;
    Q_INVOKABLE QString pageSha256(const QString &documentId, const QString &pageId) const;
    Q_INVOKABLE QVariantMap savedState(const QString &id) const;
    Q_INVOKABLE void complete(const QString &requestId, const QVariantMap &receipt);
signals:
    void activeRequestChanged();
    void acceptsCommandsChanged();
    void requestReceived(const QVariantMap &request);
private:
    struct Client { QByteArray input, output; QSocketNotifier *reader, *writer; QTimer *timer; };
    void start();
    void startRelay();
    void acceptClients();
    void readClient(int fd);
    void writeClient(int fd);
    void closeClient(int fd);
    void dispatch(int fd, const QByteArray &line);
    void reply(int fd, const QVariantMap &receipt);
    bool persist(const QString &id, const QVariantMap &record);
    QString directory;
    QHash<int, Client *> clients;
    QSocketNotifier *acceptor = nullptr;
    QTimer deadline, relayRetry;
    QProcess relayProcess;
    int listener = -1, activeClient = -1;
    bool journalFailed = false, commandsAccepted = false;
    QString activeId;
    QVariantMap activeRecord;
};
