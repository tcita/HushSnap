from PyQt6 import QtCore, QtGui


# 托盘图标：程序内绘制，避免依赖外部 ico 文件
def create_tray_icon():
    def draw_icon_pixmap(size):
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        padding = max(1, int(size * 0.08))
        rect = QtCore.QRectF(padding, padding, size - 2 * padding, size - 2 * padding)

        # 圆角底板渐变
        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QtGui.QColor(45, 174, 229))
        gradient.setColorAt(1.0, QtGui.QColor(20, 122, 191))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(gradient))
        painter.drawRoundedRect(rect, size * 0.22, size * 0.22)

        # 相机机身
        body_width = rect.width() * 0.70
        body_height = rect.height() * 0.48
        body_x = rect.center().x() - body_width / 2
        body_y = rect.center().y() - body_height / 2 + size * 0.02
        body_rect = QtCore.QRectF(body_x, body_y, body_width, body_height)
        painter.setBrush(QtGui.QColor(245, 250, 255))
        painter.drawRoundedRect(body_rect, size * 0.07, size * 0.07)

        # 顶部取景突起
        hump_rect = QtCore.QRectF(
            body_rect.left() + body_rect.width() * 0.10,
            body_rect.top() - body_rect.height() * 0.20,
            body_rect.width() * 0.26,
            body_rect.height() * 0.22,
        )
        painter.drawRoundedRect(hump_rect, size * 0.04, size * 0.04)

        # 镜头
        lens_radius = size * 0.14
        lens_center = QtCore.QPointF(body_rect.center().x(), body_rect.center().y())
        painter.setBrush(QtGui.QColor(28, 70, 110))
        painter.drawEllipse(lens_center, lens_radius, lens_radius)
        painter.setBrush(QtGui.QColor(120, 192, 245))
        painter.drawEllipse(lens_center, lens_radius * 0.55, lens_radius * 0.55)

        # 高光
        highlight_rect = QtCore.QRectF(
            rect.left() + size * 0.14,
            rect.top() + size * 0.12,
            size * 0.28,
            size * 0.13,
        )
        painter.setBrush(QtGui.QColor(255, 255, 255, 70))
        painter.drawRoundedRect(highlight_rect, size * 0.05, size * 0.05)

        painter.end()
        return pixmap

    tray_icon = QtGui.QIcon()
    for icon_size in (16, 20, 24, 32, 40, 48, 64, 128):
        tray_icon.addPixmap(draw_icon_pixmap(icon_size))
    return tray_icon