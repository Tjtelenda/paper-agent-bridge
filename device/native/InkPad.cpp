#include "InkPad.h"
#include <QBuffer>
#include <QImageReader>
#include <QMouseEvent>
#include <QPainter>
#include <QTabletEvent>
#include <cmath>

namespace {
constexpr int MaxDimension = 4096, MaxPixels = 4 * 1024 * 1024, MaxBytes = 4 * 1024 * 1024;
bool bounded(const QSize &size) {
    return size.width() > 0 && size.height() > 0 && size.width() <= MaxDimension
        && size.height() <= MaxDimension && qint64(size.width()) * size.height() <= MaxPixels;
}
}

InkPad::InkPad(QQuickItem *parent) : QQuickPaintedItem(parent) {
    setAcceptedMouseButtons(Qt::LeftButton);
    setKeepMouseGrab(true);
    setAcceptTouchEvents(false);
    setAntialiasing(true);
    setFillColor(Qt::white);
}
bool InkPad::fail(const QString &message) {
    lastError = message; emit errorChanged(); return false;
}
bool InkPad::ensureSize() {
    if (!std::isfinite(width()) || !std::isfinite(height()) || width() <= 0 || height() <= 0
        || width() > MaxDimension || height() > MaxDimension) return false;
    const QSize size(qCeil(width()), qCeil(height()));
    if (!bounded(size)) return false;
    if (image.size() == size) return true;
    QImage next(size, QImage::Format_ARGB32_Premultiplied); next.fill(Qt::white);
    if (!image.isNull()) { QPainter painter(&next); painter.drawImage(QRect(QPoint(), size), image); }
    image = next; update(); return true;
}
void InkPad::geometryChange(const QRectF &now, const QRectF &before) {
    QQuickPaintedItem::geometryChange(now, before);
    if (now.size() != before.size() && ensureSize() && ink) emit changed();
}
void InkPad::point(QPointF position, qreal pressure, bool start) {
    if (!ensureSize() || !std::isfinite(position.x()) || !std::isfinite(position.y())) return;
    if (position.x() < 0 || position.y() < 0 || position.x() >= width() || position.y() >= height()) {
        finish(); return;
    }
    if (start) { previous = position; drawing = true; }
    if (!drawing) return;
    if (!std::isfinite(pressure)) pressure = 1;
    const qreal penWidth = 1.5 + 2 * qBound(qreal(0), pressure, qreal(1));
    QPainter painter(&image); painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(QPen(Qt::black, penWidth, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    if (start) painter.drawPoint(position); else painter.drawLine(previous, position);
    painter.end();
    // Paint only the new segment; the bitmap keeps all earlier strokes.
    const QRect dirty = QRectF(previous, position).normalized().adjusted(-5, -5, 5, 5).toAlignedRect();
    previous = position; ink = true; update(dirty);
}
void InkPad::finish() {
    if (drawing) { drawing = false; emit changed(); }
}
bool InkPad::event(QEvent *event) {
    if (event->type() == QEvent::TabletPress || event->type() == QEvent::TabletMove || event->type() == QEvent::TabletRelease) {
        auto *pen = static_cast<QTabletEvent *>(event);
        if (event->type() == QEvent::TabletPress) tablet = true;
        if (event->type() == QEvent::TabletRelease) { finish(); tablet = false; }
        else point(pen->position(), pen->pressure(), event->type() == QEvent::TabletPress);
        event->accept(); return true;
    }
    return QQuickPaintedItem::event(event);
}
void InkPad::mousePressEvent(QMouseEvent *event) {
    if (!tablet) point(event->position(), 1, true);
    event->accept();
}
void InkPad::mouseMoveEvent(QMouseEvent *event) {
    if (!tablet) point(event->position(), 1, false);
    event->accept();
}
void InkPad::mouseReleaseEvent(QMouseEvent *event) {
    if (!tablet) finish();
    event->accept();
}
void InkPad::mouseDoubleClickEvent(QMouseEvent *event) { mousePressEvent(event); }
void InkPad::mouseUngrabEvent() { finish(); tablet = false; }
void InkPad::paint(QPainter *painter) { painter->drawImage(QPoint(), image); }
QString InkPad::dataUrl() {
    if (!ink) return QString();
    QByteArray bytes; QBuffer buffer(&bytes); buffer.open(QIODevice::WriteOnly);
    if (!image.save(&buffer, "PNG") || bytes.size() > MaxBytes) { fail("Drawing exceeds the image limit; keep it open and shorten it"); return QString(); }
    return QStringLiteral("data:image/png;base64,") + QString::fromLatin1(bytes.toBase64());
}
bool InkPad::loadDataUrl(const QString &url) {
    const QString prefix = QStringLiteral("data:image/png;base64,");
    if (!url.startsWith(prefix) || url.size() > prefix.size() + (MaxBytes + 2) / 3 * 4)
        return fail("Saved drawing is not a bounded PNG");
    const auto decoded = QByteArray::fromBase64Encoding(url.mid(prefix.size()).toLatin1(), QByteArray::AbortOnBase64DecodingErrors);
    if (!decoded || decoded.decoded.size() > MaxBytes || !decoded.decoded.startsWith("\x89PNG\r\n\x1a\n"))
        return fail("Saved drawing is invalid; previous drawing was kept");
    QBuffer buffer; buffer.setData(decoded.decoded); buffer.open(QIODevice::ReadOnly);
    QImageReader reader(&buffer, "PNG");
    if (!bounded(reader.size())) return fail("Saved drawing dimensions exceed the limit");
    const QImage loaded = reader.read();
    if (loaded.isNull()) return fail("Saved drawing cannot be decoded; previous drawing was kept");
    image = loaded.convertToFormat(QImage::Format_ARGB32_Premultiplied); ink = true; drawing = false;
    ensureSize(); lastError.clear(); emit errorChanged(); update(); emit changed(); return true;
}
void InkPad::clear() {
    drawing = tablet = ink = false; ensureSize(); image.fill(Qt::white);
    lastError.clear(); emit errorChanged(); update(); emit changed();
}
