#pragma once
#include <QImage>
#include <QQuickPaintedItem>

class InkPad : public QQuickPaintedItem {
    Q_OBJECT
    Q_PROPERTY(bool hasInk READ hasInk NOTIFY changed)
    Q_PROPERTY(QString error READ error NOTIFY errorChanged)
public:
    explicit InkPad(QQuickItem *parent = nullptr);
    bool hasInk() const { return ink; }
    QString error() const { return lastError; }
    Q_INVOKABLE QString dataUrl();
    Q_INVOKABLE bool loadDataUrl(const QString &url);
    Q_INVOKABLE void clear();
    void paint(QPainter *painter) override;
signals:
    void changed();
    void errorChanged();
protected:
    void geometryChange(const QRectF &now, const QRectF &before) override;
    bool event(QEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void mouseReleaseEvent(QMouseEvent *event) override;
    void mouseDoubleClickEvent(QMouseEvent *event) override;
    void mouseUngrabEvent() override;
private:
    bool ensureSize();
    bool fail(const QString &message);
    void point(QPointF position, qreal pressure, bool start);
    void finish();
    QImage image;
    QPointF previous;
    bool ink = false, drawing = false, tablet = false;
    QString lastError;
};
