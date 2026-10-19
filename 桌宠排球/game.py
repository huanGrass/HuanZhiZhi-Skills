"""The skill owns its court, input, physics and scoring; the host draws its pet."""
import math
import time
import winsound
from collections import deque
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from physics import (Match, WIDTH, HEIGHT, LEFT, RIGHT, CEILING, FLOOR,
                     NET_X, NET_TOP, BALL_RADIUS, PADDLE_RADIUS)


class Court(QtWidgets.QWidget):
    closed = QtCore.Signal()

    def __init__(self, session, geometry, *, settings=None):
        super().__init__()
        self.session = session
        self.pet_geometry = geometry
        self.error = None
        self._settings = settings
        self._drag = None
        self._hover_border = False
        self.setWindowTitle('桌面排球')
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint |
                            QtCore.Qt.WindowType.WindowStaysOnTopHint |
                            QtCore.Qt.WindowType.Tool)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        screen = QtGui.QGuiApplication.screenAt(QtCore.QPoint(*map(round, geometry['pivot'])))
        screen = screen or QtGui.QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self._area = area
        self.unit = min(1.0, (area.width()-48)/WIDTH, (area.height()-64)/HEIGHT)
        if self.unit < .65:
            raise RuntimeError('当前屏幕可用区域太小，无法展开球场')
        self.setFixedSize(round(WIDTH*self.unit), round(HEIGHT*self.unit))
        self.move(area.x()+(area.width()-self.width())//2, area.bottom()-self.height()-24)
        if settings is not None:
            saved = settings.value('court_position')
            if isinstance(saved, QtCore.QPoint):
                self.move(self._clamp_position(saved))

        # The host exposes anatomy, while collision rules stay in this skill.
        self.head_pose = geometry['poses']['left']['idle']
        hx, hy = self.head_pose['head']
        x, y, w, h = self.head_pose['bounds']
        head_radius_x = self.head_pose.get('head_radius_x', self.head_pose['head_radius'])/self.unit
        # Only the head must stay clear of the net; the body may extend across it.
        limits = (NET_X+14+head_radius_x,
                  min(845, RIGHT-(x+w-hx)/self.unit),
                  max(245, CEILING-(y-hy)/self.unit),
                  min(450, FLOOR-(y+h-hy)/self.unit))
        if limits[1]-limits[0] < 80 or limits[3]-limits[2] < 55:
            raise RuntimeError('之之现在的体型放不进半场，请调小桌宠后再开始比赛')
        self.match = Match(ai_limits=limits, head_radius=self.head_pose['head_radius']/self.unit,
                           head_radius_x=head_radius_x)
        self.trail = deque(maxlen=9)
        self._entry_progress = 0.0
        self._last = time.monotonic()
        self._last_status = None
        self._closed = False
        self._host_paused = False
        self._score_flash = 0.0
        self._scoring_side = None
        self._sound_enabled = True
        self._sound_active = False
        self._sound_until = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def serve(self):
        if self._entry_progress < 1 or self._host_paused or self._drag is not None:
            return
        if self.match.state == 'ready' and self.match.server == 1:
            return
        self.match.serve()
        self._feedback(0)
        self.trail.clear()
        self._last = time.monotonic()

    def toggle_pause(self):
        if self._entry_progress < 1 or self._host_paused or self._drag is not None:
            return
        self.match.pause()
        if self.match.state == 'paused':
            self._stop_sound()
        self._last = time.monotonic()

    def _play_sound(self, name):
        now = time.monotonic()
        if not self._sound_enabled or (now < self._sound_until and name != 'score'):
            return
        try:
            path = Path(__file__).with_name('sounds')/f'{name}.wav'
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self._sound_active = True
            self._sound_until = now+(.38 if name == 'score' else .08)
        except (RuntimeError, OSError):
            self._sound_enabled = False
            self.session.emit_log('音效暂不可用，比赛继续')

    def _stop_sound(self):
        if self._sound_active:
            try:
                winsound.PlaySound(None, 0)
            except (RuntimeError, OSError):
                pass
            self._sound_active = False
        self._sound_until = 0.0

    def _feedback(self, elapsed):
        quiet = self._host_paused or self._drag is not None or self.match.state == 'paused'
        if not quiet:
            self._score_flash = max(0.0, self._score_flash-min(elapsed, .1))
        sound = None
        priority = {'serve': 0, 'net': 1, 'paddle': 2, 'head': 2, 'score': 3}
        # Consume actual simulation events once, including events between paints
        # and automatic serves. Only the most significant sound in a frame plays.
        while self.match.events:
            kind, side = self.match.events.popleft()
            if kind == 'score':
                self._scoring_side, self._score_flash = side, .65
            name = ('paddle' if side == 0 else 'head') if kind == 'hit' else kind
            if sound is None or priority[name] > priority[sound]:
                sound = name
        if quiet:
            self._stop_sound()
        elif sound is not None:
            self._play_sound(sound)

    def tick(self):
        if self._closed:
            return
        try:
            if self.session.stop_requested:
                self.close()
                return
            now = time.monotonic()
            dt, self._last = now-self._last, now
            self._host_paused = self.session.poll_pause()
            point = self.mapFromGlobal(QtGui.QCursor.pos())
            if self._drag is None and self.rect().contains(point) and not self._on_border(point):
                self.match.aim(point.x()/self.unit, point.y()/self.unit)
            if not self._host_paused and self._drag is None:
                if self._entry_progress < 1:
                    self._entry_progress = min(1.0, self._entry_progress+max(0, min(dt, .1))/.8)
                else:
                    self.match.advance(dt)
            self._feedback(dt)
            if self.match.state == 'ready':
                self.trail.clear()
            if self.match.state == 'playing' and not self._host_paused and self._drag is None:
                self.trail.append((self.match.ball.x, self.match.ball.y))
            self._publish_pet()
            state = ('paused' if self._host_paused else 'entering' if self._entry_progress < 1
                     else self.match.state, tuple(self.match.score))
            if state != self._last_status:
                self._last_status = state
                title = {'entering': '之之入场中', 'ready': '等待发球', 'playing': '对打中', 'paused': '已暂停'}[state[0]]
                self.session.emit_status(title, '桌宠排球',
                                         f'你 {self.match.score[0]} : {self.match.score[1]} 之之')
            self.update()
        except Exception as error:
            self.error = error
            self.close()

    def mousePressEvent(self, event):
        if self._entry_progress < 1:
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self._on_border(event.position()):
                self._drag = (event.globalPosition().toPoint(), self.pos())
                self._stop_sound()
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
                event.accept()
            elif self.match.state == 'ready':
                self.serve()
            elif self.match.state == 'paused':
                self.toggle_pause()

    def _on_border(self, point):
        x, y = point.x()/self.unit, point.y()/self.unit
        margin = 8/self.unit
        return (LEFT-margin <= x <= RIGHT+margin and CEILING-margin <= y <= FLOOR+margin
                and min(abs(x-LEFT), abs(x-RIGHT), abs(y-CEILING), abs(y-FLOOR)) <= margin)

    def _clamp_position(self, position):
        return QtCore.QPoint(max(self._area.left(), min(position.x(), self._area.right()-self.width()+1)),
                             max(self._area.top(), min(position.y(), self._area.bottom()-self.height()+1)))

    def _publish_pet(self):
        # Head motion lives in the skill's physics and is used for both drawing
        # and collision. No independent decorative offset can desync the hitbox.
        ai = self.match.opponent
        hx, hy = self.head_pose['head']
        origin = self.mapToGlobal(QtCore.QPoint(0, 0))
        x, y = origin.x()+ai.x*self.unit-hx, origin.y()+ai.y*self.unit-hy
        if self._entry_progress < 1:
            # Interpolate the same pivot used by the host; gameplay waits for arrival.
            start_x, start_y = self.pet_geometry['pivot']
            t = self._entry_progress
            eased = t*t*(3-2*t)
            x, y = start_x+(x-start_x)*eased, start_y+(y-start_y)*eased
        self.session.update_pet(x, y, facing='left', pose='idle')

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            start, origin = self._drag
            self.move(self._clamp_position(origin+event.globalPosition().toPoint()-start))
            self.tick()
        else:
            self._hover_border = self._on_border(event.position())
            self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor if self._hover_border else QtCore.Qt.CursorShape.ArrowCursor)
            self.setToolTip('拖动边框移动整个球场' if self._hover_border else '')
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            self._last = time.monotonic()
            self._hover_border = self._on_border(event.position())
            self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor if self._hover_border else QtCore.Qt.CursorShape.ArrowCursor)
            if self._settings is not None:
                self._settings.setValue('court_position', self.pos())
                self._settings.sync()
                if self._settings.status() != QtCore.QSettings.Status.NoError:
                    self.session.emit_log('球场位置保存失败，下次启动将使用默认位置')
            self.update()

    def leaveEvent(self, event):
        self._hover_border = False
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close()
        elif event.key() == QtCore.Qt.Key.Key_Space:
            if self.match.state == 'ready' and self.match.server == 0:
                self.serve()
            else:
                self.toggle_pause()
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.Type.ActivationChange and hasattr(self, 'match'):
            if not self.isActiveWindow() and (self.match.state == 'playing' or
                                             (self.match.state == 'ready' and self.match.server == 1)):
                self._drag = None
                self.match.pause()
                self._stop_sound()
        super().changeEvent(event)

    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            self._drag = None
            self.timer.stop()
            self._stop_sound()
            self.closed.emit()
        event.accept()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.scale(self.unit, self.unit)
        # Nearly invisible input surface: only the game objects are visible.
        # Alpha=1 keeps desktop clicks inside the court from reaching other apps.
        margin = 10/self.unit
        p.fillRect(QtCore.QRectF(LEFT-margin, CEILING-margin, RIGHT-LEFT+2*margin, FLOOR-CEILING+48),
                   QtGui.QColor(0, 0, 0, 1))

        def text(x, y, w, h, value, size, color):
            p.setFont(QtGui.QFont('Microsoft YaHei UI', size, QtGui.QFont.Weight.DemiBold))
            rect = QtCore.QRectF(x, y, w, h)
            align = QtCore.Qt.AlignmentFlag.AlignCenter
            p.setPen(QtGui.QColor(12, 24, 30, 230))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 2)):
                p.drawText(rect.translated(dx, dy), align, value)
            p.setPen(QtGui.QColor(color))
            p.drawText(rect, align, value)

        # The same four edges are used by the physics and the drag hit testing.
        edges = {'left': (LEFT, CEILING, LEFT, FLOOR), 'right': (RIGHT, CEILING, RIGHT, FLOOR),
                 'top': (LEFT, CEILING, RIGHT, CEILING), 'bottom': (LEFT, FLOOR, RIGHT, FLOOR)}
        for name, line in edges.items():
            alpha = 200 if self._hover_border or self._drag is not None else 135 if name == 'bottom' else 90
            alpha = min(255, alpha+int(self.match.boundary_flash.get(name, 0)*400))
            pen = QtGui.QPen(QtGui.QColor(225, 239, 209, alpha), 1.6)
            pen.setDashPattern([5, 5])
            p.setPen(pen)
            p.drawLine(QtCore.QLineF(*line))
        # The net floats directly over the desktop; there is no panel.
        p.setPen(QtGui.QPen(QtGui.QColor(8, 28, 30, 210), 5))
        p.drawLine(QtCore.QLineF(NET_X+1, NET_TOP, NET_X+1, FLOOR))
        p.setPen(QtGui.QPen(QtGui.QColor('#e1efd1'), 2))
        p.drawLine(QtCore.QLineF(NET_X, NET_TOP, NET_X, FLOOR))
        p.setPen(QtGui.QPen(QtGui.QColor(235, 247, 224, 145), 1))
        for y in range(NET_TOP+10, FLOOR, 12):
            p.drawLine(QtCore.QLineF(NET_X-7, y, NET_X+7, y))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor('#e1efd1'))
        p.drawEllipse(QtCore.QPointF(NET_X, NET_TOP), 4, 4)
        text(365, NET_TOP-50, 55, 36, '你', 14, '#f6ffe9')
        text(472, NET_TOP-50, 16, 36, ':', 14, '#f6ffe9')
        text(537, NET_TOP-50, 60, 36, '之之', 14, '#f6ffe9')
        for side, x in enumerate((425, 490)):
            color = '#f6ffe9'
            p.save()
            if self._score_flash > 0 and self._scoring_side == side:
                scale = 1+.55*math.sin(math.pi*(1-self._score_flash/.65))
                p.translate(x+21, NET_TOP-32)
                p.scale(scale, scale)
                p.translate(-x-21, -NET_TOP+32)
                color = '#c9f593' if side == 0 else '#ffd49c'
            text(x, NET_TOP-50, 42, 36, str(self.match.score[side]), 14, color)
            p.restore()

        player = self.match.player
        p.setBrush(QtGui.QColor(169, 240, 140, 40))
        p.setPen(QtGui.QPen(QtGui.QColor(12, 35, 25, 190), 5))
        p.drawEllipse(QtCore.QPointF(player.x, player.y), PADDLE_RADIUS, PADDLE_RADIUS)
        p.setPen(QtGui.QPen(QtGui.QColor('#c9f593'), 2))
        p.drawEllipse(QtCore.QPointF(player.x, player.y), PADDLE_RADIUS, PADDLE_RADIUS)
        p.drawArc(QtCore.QRectF(player.x-18, player.y-18, 36, 36), 20*16, 140*16)
        p.drawLine(QtCore.QLineF(player.x-7, player.y+4, player.x+7, player.y+4))

        if self.match.state == 'playing' and not self._host_paused:
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            for index, (x, y) in enumerate(self.trail):
                p.setBrush(QtGui.QColor(255, 228, 173, 8+index*8))
                p.drawEllipse(QtCore.QPointF(x, y), 3+index*.65, 3+index*.65)
        b = self.match.ball
        ball_gradient = QtGui.QRadialGradient(b.x-4, b.y-5, 22)
        ball_gradient.setColorAt(0, QtGui.QColor('#fffbdf'))
        ball_gradient.setColorAt(1, QtGui.QColor('#efb96c'))
        p.setBrush(ball_gradient)
        p.setPen(QtGui.QPen(QtGui.QColor('#7b694a'), 2))
        p.drawEllipse(QtCore.QPointF(b.x, b.y), BALL_RADIUS, BALL_RADIUS)
        p.setPen(QtGui.QPen(QtGui.QColor('#bc9459'), 1.3))
        p.drawArc(QtCore.QRectF(b.x-7, b.y-11, 14, 22), -65*16, 135*16)

        if self.match.contact_flash and self.match.last_contact:
            x, y, side = self.match.last_contact
            t = self.match.contact_flash/.28
            color = QtGui.QColor('#c9f593' if side == 0 else '#ffd49c')
            color.setAlpha(round(200*t))
            p.setPen(QtGui.QPen(color, 2*t))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            radius = 5+(1-t)*19
            p.drawEllipse(QtCore.QPointF(x, y), radius, radius)

        if self._drag is not None:
            message = '移动球场中'
        elif self._host_paused:
            message = '已暂停 · 在桌宠状态条继续'
        elif self._entry_progress < 1:
            message = '之之正在飞入场地…'
        elif self.match.state == 'paused':
            message = '已暂停 · 点击场地继续'
        elif self.match.state == 'ready':
            message = self.match.message + (' · 准备中…' if self.match.server == 1 else ' · 点击发球')
        else:
            message = f'连续回合 {self.match.rally}' if self.match.rally else ''
        if message:
            text(200, 170, 560, 34, message, 13, '#f6ffe9')
        help_text = ('拖动虚线边框移动整个球场' if self._hover_border else
                     '鼠标控制左侧 · 拖动边框移动 · 空格暂停 · Esc 退出')
        text(220, FLOOR+12, 520, 26, help_text, 10, '#f1f7e7')
        p.end()


def run(session):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    geometry = session.acquire_pet()
    window = None
    try:
        window = Court(session, geometry, settings=QtCore.QSettings('HuanZhiZhi', 'Volleyball'))
        # Tool overlays do not count as primary windows for quitOnLastWindowClosed.
        window.closed.connect(app.quit)
        window.show()
        window.activateWindow()
        app.exec()
        if window.error is not None:
            raise window.error
        match = window.match
        return f'你 {match.score[0]} : {match.score[1]} 之之，最长连续回合 {match.best_rally}'
    finally:
        if window is not None:
            window.close()
        session.release_pet()
