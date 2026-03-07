# UI/history_panel.py
"""
历史记录与成长曲线面板
展示学生历次面试得分趋势 + 各维度雷达图
"""
import json
import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTextEdit, QSplitter, QFrame, QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPolygonF, QLinearGradient, QPainterPath
from PySide6.QtCore import QPointF

from UI.base_panel import PanelFrame

APPLE_COLORS = {
    "bg": "#F2F2F7",
    "surface": "#FFFFFF",
    "blue": "#007AFF",
    "blue_trans": QColor(0, 122, 255, 40),
    "green": "#34C759",
    "text_main": "#1C1C1E",
    "text_sec": "#8E8E93",
    "border": "#D1D1D6",
    "shadow": QColor(0, 0, 0, 25)
}

FONT_STACK = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif'
# ── 辅助组件：阴影卡片 ────────────────────────────────────────────────────────
class ShadowCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: white; 
                color: {APPLE_COLORS['text_main']};
                border-radius: 16px;
                border: 1px solid {APPLE_COLORS['border']};
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(APPLE_COLORS['shadow'])
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)


# ── 现代成长曲线图 (Area Chart) ────────────────────────────────────────────────

class GrowthChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scores: list[float] = []
        self.setMinimumSize(400, 250)

    def set_scores(self, scores: list[float]):
        self.scores = scores
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W, H = self.width(), self.height()
        PAD_L, PAD_R, PAD_T, PAD_B = 40, 20, 40, 30
        cw, ch = W - PAD_L - PAD_R, H - PAD_T - PAD_B

        if not self.scores:
            p.setPen(QColor(APPLE_COLORS['text_sec']))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无面试记录")
            return

        # 1. 绘制网格线
        p.setPen(QPen(QColor("#E5E5EA"), 1))
        for i in range(6):
            y = PAD_T + ch * (1 - i / 5)
            p.drawLine(PAD_L, y, W - PAD_R, y)
            p.drawText(5, y + 5, str(i * 2))

        # 2. 计算点位
        points = []
        step = cw / (len(self.scores) - 1) if len(self.scores) > 1 else cw / 2
        for i, s in enumerate(self.scores):
            x = PAD_L + i * step if len(self.scores) > 1 else PAD_L + cw / 2
            y = PAD_T + ch * (1 - s / 10)
            points.append(QPointF(x, y))

        # 3. 绘制填充渐变 (Area)
        if len(points) > 1:
            path_fill = QPainterPath()
            path_fill.moveTo(points[0].x(), PAD_T + ch)
            for pt in points: path_fill.lineTo(pt)
            path_fill.lineTo(points[-1].x(), PAD_T + ch)

            grad = QLinearGradient(0, PAD_T, 0, PAD_T + ch)
            grad.setColorAt(0, APPLE_COLORS['blue_trans'])
            grad.setColorAt(1, QColor(0, 122, 255, 0))
            p.fillPath(path_fill, QBrush(grad))

        # 4. 绘制折线
        p.setPen(QPen(QColor(APPLE_COLORS['blue']), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for i in range(len(points) - 1):
            p.drawLine(points[i], points[i + 1])

        # 5. 绘制圆点
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(APPLE_COLORS['blue']))
        for pt in points:
            p.drawEllipse(pt, 5, 5)
            p.setBrush(Qt.white)
            p.drawEllipse(pt, 2, 2)
            p.setBrush(QColor(APPLE_COLORS['blue']))


# ── 现代雷达图 ──────────────────────────────────────────────────────────────

class RadarChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = {}
        self.setMinimumSize(300, 300)

    def set_data(self, data: dict):
        self.data = data
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        r = min(cx, cy) - 50

        if not self.data:
            p.drawText(self.rect(), Qt.AlignCenter, "等待数据...")
            return

        cats = list(self.data.keys())
        n = len(cats)
        angle_step = 2 * math.pi / n

        # 1. 绘制背景多边形 (蜘蛛网)
        p.setPen(QPen(QColor("#E5E5EA"), 1))
        for level in range(1, 6):
            cur_r = r * (level / 5)
            pts = [QPointF(cx + cur_r * math.cos(i * angle_step - math.pi / 2),
                           cy + cur_r * math.sin(i * angle_step - math.pi / 2)) for i in range(n)]
            p.drawPolygon(QPolygonF(pts))

        # 2. 绘制轴线与文字
        p.setFont(QFont(FONT_STACK, 10, QFont.Bold))
        for i, cat in enumerate(cats):
            angle = i * angle_step - math.pi / 2
            end_x, end_y = cx + r * math.cos(angle), cy + r * math.sin(angle)
            p.drawLine(cx, cy, end_x, end_y)

            # 文字坐标微调
            tx, ty = cx + (r + 25) * math.cos(angle), cy + (r + 25) * math.sin(angle)
            rect = p.fontMetrics().boundingRect(cat)
            p.drawText(tx - rect.width() / 2, ty + rect.height() / 4, cat)

        # 3. 绘制数据区域
        data_pts = [QPointF(cx + r * (self.data[cat] / 10) * math.cos(i * angle_step - math.pi / 2),
                            cy + r * (self.data[cat] / 10) * math.sin(i * angle_step - math.pi / 2))
                    for i, cat in enumerate(cats)]

        poly = QPolygonF(data_pts)
        p.setPen(QPen(QColor(APPLE_COLORS['blue']), 2))
        p.setBrush(APPLE_COLORS['blue_trans'])
        p.drawPolygon(poly)

        # 4. 绘制数据点
        p.setBrush(QColor(APPLE_COLORS['blue']))
        for pt in data_pts: p.drawEllipse(pt, 4, 4)


# ── 主面板 ────────────────────────────────────────────────────────────────────

class HistoryPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet(f"background-color: {APPLE_COLORS['bg']}; font-family: {FONT_STACK};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(25)

        # ── 顶部筛选栏 ──
        header = QHBoxLayout()
        title_lbl = QLabel("成长实验室")
        title_lbl.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {APPLE_COLORS['text_main']};")

        self.student_combo = QComboBox()
        self.student_combo.setFixedWidth(180)
        self.student_combo.setStyleSheet(f"""
            QComboBox {{
                background: green; border-radius: 10px; padding: 6px 12px;
                border: 1px solid {APPLE_COLORS['border']}; font-size: 14px;
            }}
        """)

        refresh_btn = QPushButton("同步数据")
        refresh_btn.setFixedSize(90, 32)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: {APPLE_COLORS['blue']}; color: white; border-radius: 8px; font-weight: 600;
            }}
            QPushButton:hover {{ opacity: 0.8; }}
        """)
        refresh_btn.clicked.connect(self._refresh)

        header.addWidget(title_lbl)
        header.addStretch()
        header.addWidget(QLabel("选择成员:"))
        header.addWidget(self.student_combo)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # ── 图表区域 ──
        charts_layout = QHBoxLayout()

        # 左侧卡片：折线图
        self.growth_card = ShadowCard()
        growth_vbox = QVBoxLayout(self.growth_card)
        growth_vbox.addWidget(QLabel("📊 综合得分趋势"), alignment=Qt.AlignTop)
        self.growth_chart = GrowthChart()
        growth_vbox.addWidget(self.growth_chart)

        # 右侧卡片：雷达图
        self.radar_card = ShadowCard()
        radar_vbox = QVBoxLayout(self.radar_card)
        radar_vbox.addWidget(QLabel("🎯 最近能力维度"), alignment=Qt.AlignTop)
        self.radar_chart = RadarChart()
        radar_vbox.addWidget(self.radar_chart)

        charts_layout.addWidget(self.growth_card, stretch=6)
        charts_layout.addWidget(self.radar_card, stretch=4)
        layout.addLayout(charts_layout)

        # ── 底部报告卡片 ──
        report_card = ShadowCard()
        report_vbox = QVBoxLayout(report_card)
        report_vbox.setContentsMargins(20, 20, 20, 20)

        report_title = QLabel("📝 最近面试表现回顾")
        report_title.setStyleSheet(f"font-weight: 700; color: {APPLE_COLORS['text_main']};")

        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setFrameShape(QFrame.NoFrame)
        self.report_view.setPlaceholderText("选择学生查看详细历史反馈...")
        self.report_view.setStyleSheet(f"background: transparent; font-size: 14px; line-height: 1.6;")

        report_vbox.addWidget(report_title)
        report_vbox.addWidget(self.report_view)
        layout.addWidget(report_card, stretch=1)

        self.student_combo.currentIndexChanged.connect(self._load_student_data)
        self._refresh()

    def _refresh(self):
        self.student_combo.blockSignals(True)
        self.student_combo.clear()
        rows = self.db.fetchall("SELECT id, name FROM student ORDER BY id DESC")
        for sid, name in rows:
            self.student_combo.addItem(name, sid)
        self.student_combo.blockSignals(False)
        if self.student_combo.count() > 0:
            self._load_student_data()

    def _load_student_data(self):
        sid = self.student_combo.currentData()
        if not sid: return

        sessions = self.db.fetchall(
            "SELECT id, overall_score, report, started_at FROM interview_session "
            "WHERE student_id=? AND status='finished' ORDER BY started_at", (sid,)
        )

        if not sessions:
            self.growth_chart.set_scores([]);
            self.radar_chart.set_data({})
            self.report_view.setPlainText("暂无已完成的面试记录。")
            return

        # 更新成长曲线
        scores = [s[1] for s in sessions if s[1] is not None]
        self.growth_chart.set_scores(scores)

        # 更新雷达图与报告内容
        latest = sessions[-1]
        self.report_view.setMarkdown(latest[2] or "无报告内容")

        turns = self.db.fetchall(
            "SELECT scores FROM interview_turn WHERE session_id=? AND scores IS NOT NULL", (latest[0],)
        )
        if turns:
            dim_totals = {"技术": [], "逻辑": [], "深度": [], "表达": []}
            key_map = {"tech": "技术", "logic": "逻辑", "depth": "深度", "clarity": "表达"}
            for (sc_json,) in turns:
                sc = json.loads(sc_json)
                for k, cn in key_map.items():
                    if k in sc: dim_totals[cn].append(sc[k])

            radar_data = {cn: round(sum(vals) / len(vals), 1) if vals else 0 for cn, vals in dim_totals.items()}
            self.radar_chart.set_data(radar_data)