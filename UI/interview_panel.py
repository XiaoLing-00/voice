# UI/interview_panel.py

"""
面试主界面 (Apple Aesthetic Refactor)

学生选择岗位 → 开始面试 → 聊天式问答 → 查看报告
优化点：
1. 修复主线程阻塞导致的“假死”问题（将 session 初始化移至子线程）
2. 使用 Signal 替代 invokeMethod，避免 PySide6 线程通信死锁
3. 全面升级 UI 为 Apple 现代风格（圆角、阴影、系统字体、流畅动效）
"""

import json
import sys
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextEdit, QScrollArea, QFrame,
    QMessageBox, QSizePolicy, QApplication, QGraphicsDropShadowEffect, QTextBrowser
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QColor, QPainter, QPainterPath, QBrush

from UI.base_panel import PanelFrame

# ── 全局样式配置 (Apple Design System) ───────────────────────────────────────

COLORS = {
    "bg": "#F5F5F7",  # Apple Light Gray
    "surface": "#FFFFFF",  # White Card
    "primary": "#007AFF",  # Apple Blue
    "primary_hover": "#0056CC",
    "success": "#34C759",  # Apple Green
    "text_main": "#1D1D1F",  # Almost Black
    "text_sec": "#86868B",  # Gray Text
    "bubble_ai": "#E9E9EB",  # iMessage Gray
    "bubble_user": "#007AFF",  # iMessage Blue
    "bubble_user_text": "#FFFFFF",
    "border": "#D2D2D7",
    "input_bg": "#FFFFFF",
}
APPLE_COLORS = {
    "bg": "#F2F2F7",  # 系统浅灰背景
    "card": "#FFFFFF",  # 纯白卡片
    "blue": "#007AFF",  # 经典苹果蓝
    "green": "#34C759",  # 苹果绿
    "border": "#D1D1D6",  # 极淡边框
    "text_main": "#1C1C1E",  # 标题黑
    "text_sec": "#8E8E93",  # 次要灰
    "ai_bubble": "#E9E9EB",  # iMessage 灰色
}

# 优化后的 CSS 模板
GLOBAL_STYLE = f"""
    QWidget {{
        font-family: -apple-system, "Helvetica Neue", "PingFang SC", sans-serif;
        color: {APPLE_COLORS['text_main']};
    }}

    /* 滚动条美化 - 极简苹果风 */
    QScrollBar:vertical {{
        width: 6px; background: transparent; margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(0, 0, 0, 0.1); border-radius: 3px; min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(0, 0, 0, 0.2);
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
"""
FONT_STACK = """
    -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif
"""


# ── 后台线程：安全通信 ───────────────────────────────────────────────────────

class InterviewWorker(QObject):
    """
    在子线程中执行 LLM 调用及数据库会话初始化
    修复：将 start_session 也移入此处，避免主线程 IO 阻塞
    """
    # 定义触发信号，替代 invokeMethod
    request_start = Signal(str, int)  # name, job_id
    request_answer = Signal(str)  # answer
    request_finish = Signal()  # no args

    first_question_ready = Signal(str)
    answer_result_ready = Signal(dict)
    report_ready = Signal(str)
    error_occurred = Signal(str)
    session_started = Signal(int)  # 通知主线程 session_id

    def __init__(self, engine, db):
        super().__init__()
        self.engine = engine
        self.db = db  # 注意：确保 db 连接是线程安全的，SQLite 需 check_same_thread=False
        self.session_id = None

    def on_start_requested(self, name: str, job_id: int):
        try:
            # 1. 创建或复用学生 (DB IO)
            row = self.db.fetchone("SELECT id FROM student WHERE name=?", (name,))
            if row:
                student_id = row[0]
            else:
                cur = self.db.execute(
                    "INSERT INTO student (name, created_at) VALUES (?,?)",
                    (name, datetime.now().isoformat()),
                )
                student_id = cur.lastrowid

            # 2. 创建会话 (Engine IO - 可能涉及网络)
            self.session_id = self.engine.start_session(student_id, job_id)
            self.session_started.emit(self.session_id)

            # 3. 获取第一题 (LLM IO - 耗时)
            q = self.engine.get_first_question(self.session_id)
            self.first_question_ready.emit(q)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def on_answer_requested(self, answer: str):
        try:
            if self.session_id is None:
                raise RuntimeError("Session not initialized")
            result = self.engine.submit_answer(self.session_id, answer)
            self.answer_result_ready.emit(result)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def on_finish_requested(self):
        try:
            if self.session_id is None:
                raise RuntimeError("Session not initialized")
            report = self.engine.finish_session(self.session_id)
            self.report_ready.emit(report)
        except Exception as e:
            self.error_occurred.emit(str(e))


# ── 组件：现代化气泡 ──────────────────────────────────────────────────────────
class Bubble(QFrame):
    def __init__(self, role: str, text: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(0)

        # 内容容器
        content = QTextBrowser()
        content.setMarkdown(text)
        content.setOpenExternalLinks(True)
        content.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 自动计算高度
        content.document().setTextWidth(420)
        h = int(content.document().size().height()) + 20
        content.setFixedSize(440, max(45, h))

        if role == "ai":
            content.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: {APPLE_COLORS['ai_bubble']};
                    color: #000000; border: none;
                    border-radius: 20px; border-bottom-left-radius: 5px;
                    padding: 10px 15px; font-size: 14px;
                }}
            """)
            layout.addWidget(content)
            layout.addStretch()
        else:
            content.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: {APPLE_COLORS['blue']};
                    color: #FFFFFF; border: none;
                    border-radius: 20px; border-bottom-right-radius: 5px;
                    padding: 10px 15px; font-size: 14px;
                }}
            """)
            layout.addStretch()
            layout.addWidget(content)

class ScoreBubble(QFrame):
    """评分结果气泡 - 卡片式"""

    def __init__(self, eval_result, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # 使用垂直布局，并设置整体居中对齐
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setAlignment(Qt.AlignCenter)  # ✅ 正确：对 Layout 设置对齐

        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #FFFFFF; border: 1px solid #E5E5EA;
                border-radius: 12px; padding: 12px;
            }
        """)
        card_layout = QVBoxLayout(card)

        title = QLabel("📊 本题评估报告")
        title.setStyleSheet("font-weight: 600; font-size: 13px; color: #1D1D1F; margin-bottom: 8px;")
        card_layout.addWidget(title)

        grid = QHBoxLayout()
        scores = [
            ("技术", eval_result.tech_score),
            ("逻辑", eval_result.logic_score),
            ("深度", eval_result.depth_score),
            ("表达", eval_result.clarity_score),
        ]
        for label, score in scores:
            item = QVBoxLayout()
            lbl_name = QLabel(label)
            lbl_name.setStyleSheet("font-size: 11px; color: #86868B;")
            lbl_val = QLabel(str(score))
            lbl_val.setStyleSheet("font-size: 16px; font-weight: 700; color: #007AFF;")
            item.addWidget(lbl_name)
            item.addWidget(lbl_val)
            item.setAlignment(Qt.AlignCenter)
            grid.addLayout(item)

        overall = QLabel(f"综合 {eval_result.overall_score:.1f}")
        overall.setStyleSheet("font-size: 14px; font-weight: 700; color: #34C759; padding-left: 10px;")
        grid.addWidget(overall)

        card_layout.addLayout(grid)

        if eval_result.suggestion:
            tip = QLabel(f"💡 {eval_result.suggestion}")
            tip.setWordWrap(True)
            tip.setStyleSheet(
                "font-size: 12px; color: #86868B; margin-top: 8px; background: #F5F5F7; padding: 8px; border-radius: 8px;")
            card_layout.addWidget(tip)

        layout.addWidget(card)

# ── 主面板 ────────────────────────────────────────────────────────────────────

class InterviewPanel(QWidget):
    def __init__(self, db, engine, parent=None):
        super().__init__(parent)
        self.db = db
        self.engine = engine

        self._session_id: int | None = None
        self._student_id: int | None = None

        # 线程初始化
        self._worker = InterviewWorker(engine, db)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        # 连接信号 (修复死锁的关键：使用 Signal 而非 invokeMethod)
        self._worker.request_start.connect(self._worker.on_start_requested)
        self._worker.request_answer.connect(self._worker.on_answer_requested)
        self._worker.request_finish.connect(self._worker.on_finish_requested)

        self._worker.first_question_ready.connect(self._on_first_question)
        self._worker.answer_result_ready.connect(self._on_answer_result)
        self._worker.report_ready.connect(self._on_report)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.session_started.connect(self._on_session_started)

        self._thread.start()

        self._build_ui()
        self._apply_mac_style()

    def _apply_mac_style(self):
        """应用全局 Apple 风格样式"""
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {COLORS['bg']};
                font-family: {FONT_STACK};
                color: {COLORS['text_main']};
            }}
        """)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 顶部导航栏 (仿 macOS 窗口头) ─────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['surface']};
                border-bottom: 1px solid {COLORS['border']};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        # 左侧：输入区
        input_group = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("姓名")
        self.name_input.setFixedWidth(120)
        self.name_input.setStyleSheet(self._input_css())

        self.job_combo = QComboBox()
        self.job_combo.setFixedWidth(160)
        self.job_combo.setStyleSheet(self._input_css())
        self._load_jobs()

        input_group.addWidget(QLabel("姓名："))
        input_group.addWidget(self.name_input)
        input_group.addSpacing(10)
        input_group.addWidget(QLabel("岗位："))
        input_group.addWidget(self.job_combo)

        # 右侧：操作区
        btn_group = QHBoxLayout()
        self.start_btn = QPushButton("开始面试")
        self.start_btn.setFixedHeight(32)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet(self._btn_css(COLORS['primary']))
        self.start_btn.clicked.connect(self._start_interview)

        self.finish_btn = QPushButton("结束面试")
        self.finish_btn.setFixedHeight(32)
        self.finish_btn.setCursor(Qt.PointingHandCursor)
        self.finish_btn.setEnabled(False)
        self.finish_btn.setStyleSheet(self._btn_css(COLORS['success']))
        self.finish_btn.clicked.connect(self._finish_interview)

        btn_group.addWidget(self.start_btn)
        btn_group.addWidget(self.finish_btn)

        header_layout.addLayout(input_group)
        header_layout.addStretch()
        header_layout.addLayout(btn_group)

        root.addWidget(header)

        # ── 聊天区域 (仿 iMessage) ───────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                width: 8px; background: transparent; border-radius: 4px;
                margin: 4px;
            }
            QScrollBar::handle:vertical {
                background: #D1D1D6; border-radius: 4px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: #AEAEB2; }
        """)

        self._chat_container = QWidget()
        self._chat_container.setStyleSheet(f"background: {COLORS['bg']};")
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(24, 24, 24, 24)
        self._chat_layout.setSpacing(16)
        self._chat_layout.addStretch()

        self._scroll.setWidget(self._chat_container)
        root.addWidget(self._scroll, stretch=1)

        # ── 底部输入区 (悬浮感) ──────────────────────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(100)
        footer.setStyleSheet(f"""
            QFrame {{
                background: {COLORS['surface']};
                border-top: 1px solid {COLORS['border']};
            }}
        """)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(24, 16, 24, 16)
        footer_layout.setSpacing(10)

        # 状态栏
        self.status_lbl = QLabel("准备就绪")
        self.status_lbl.setStyleSheet(f"color: {COLORS['text_sec']}; font-size: 12px; padding-left: 4px;")
        footer_layout.addWidget(self.status_lbl)

        # 输入框 + 发送
        input_row = QHBoxLayout()
        self.answer_input = QTextEdit()
        self.answer_input.setPlaceholderText("输入回答... (Ctrl+Enter 发送)")
        self.answer_input.setFixedHeight(56)
        self.answer_input.setEnabled(False)
        self.answer_input.setStyleSheet(f"""
            QTextEdit {{
                background: {COLORS['input_bg']};
                border: 1px solid {COLORS['border']};
                border-radius: 12px; padding: 10px 16px;
                font-size: 14px; font-family: {FONT_STACK};
            }}
            QTextEdit:focus {{ border-color: {COLORS['primary']}; }}
            QTextEdit:disabled {{ background: #F5F5F7; color: #AEAEB2; }}
        """)
        self.answer_input.installEventFilter(self)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(80, 56)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setEnabled(False)
        self.send_btn.setStyleSheet(self._btn_css(COLORS['primary']))
        self.send_btn.clicked.connect(self._send_answer)

        input_row.addWidget(self.answer_input)
        input_row.addWidget(self.send_btn)
        footer_layout.addLayout(input_row)

        root.addWidget(footer)

    # ── 样式辅助 ──────────────────────────────────────────────────────────────

    def _input_css(self):
        return f"""
            QLineEdit, QComboBox {{
                background: #FFFFFF; border: 1px solid {COLORS['border']};
                border-radius: 8px; padding: 6px 12px; font-size: 13px;
                outline: none;
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {COLORS['primary']}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #86868B; margin: 4px; }}
        """

    def _btn_css(self, color):
        return f"""
            QPushButton {{
                background-color: {color}; color: white;
                border: none; border-radius: 8px;
                font-size: 13px; font-weight: 600; padding: 0 20px;
            }}
            QPushButton:hover {{ background-color: {color}DD; }}
            QPushButton:pressed {{ background-color: {color}BB; }}
            QPushButton:disabled {{ background-color: #D1D1D6; color: #86868B; }}
        """

    # ── 逻辑控制 ──────────────────────────────────────────────────────────────

    def _load_jobs(self):
        self.job_combo.clear()
        try:
            rows = self.db.fetchall("SELECT id, name FROM job_position")
            for jid, name in rows:
                self.job_combo.addItem(name, jid)
        except Exception:
            self.job_combo.addItem("暂无岗位", 0)

    def _start_interview(self):
        name = self.name_input.text().strip()
        if not name:
            self._showToast("请输入姓名")
            return
        if self.job_combo.count() == 0 or self.job_combo.currentData() is None:
            self._showToast("请选择岗位")
            return

        job_id = self.job_combo.currentData()

        # UI 状态更新 (立即反馈，不等待)
        self._set_loading_state(True, "正在初始化面试会话...")
        self.start_btn.setEnabled(False)
        self.name_input.setEnabled(False)
        self.job_combo.setEnabled(False)

        # 清空聊天区
        self._clear_chat()

        # 发送信号到工作线程 (非阻塞)
        # 修复：不再在主线程调用 engine.start_session
        self._worker.request_start.emit(name, job_id)

    def _on_session_started(self, session_id: int):
        """子线程通知会话已创建"""
        self._session_id = session_id
        # 这里不需要做太多 UI 操作，等待 first_question_ready

    def _send_answer(self):
        answer = self.answer_input.toPlainText().strip()
        if not answer:
            return

        self.answer_input.clear()
        self._add_bubble("user", answer)
        self._set_loading_state(True, "AI 正在思考...")
        self._set_input_enabled(False)

        # 发送信号到工作线程
        self._worker.request_answer.emit(answer)

    def _finish_interview(self):
        self._set_loading_state(True, "正在生成最终报告...")
        self._set_input_enabled(False)
        self.finish_btn.setEnabled(False)

        self._worker.request_finish.emit()

    # ── 信号槽回调 ────────────────────────────────────────────────────────────

    def _on_first_question(self, question: str):
        self._set_loading_state(False)
        self._add_bubble("ai", question)
        self._set_input_enabled(True)
        self.finish_btn.setEnabled(True)
        self.status_lbl.setText("面试进行中")
        self._add_system_msg("面试已开始")

    def _on_answer_result(self, result: dict):
        self._set_loading_state(False)
        eval_r = result.get("eval")
        ai_reply = result.get("ai_reply", "")
        is_finished = result.get("is_finished", False)

        if eval_r:
            self._add_score_bubble(eval_r)

        if ai_reply:
            self._add_bubble("ai", ai_reply)

        if is_finished:
            self._set_input_enabled(False)
            self.status_lbl.setText("题目已完成，请生成报告")
            self.send_btn.setEnabled(False)
        else:
            self._set_input_enabled(True)

    def _on_report(self, report: str):
        self._set_loading_state(False)
        self._add_system_msg("━━━━━━ 面试结束 ━━━━━━")
        self._add_bubble("ai", report)
        self.status_lbl.setText("面试完成")
        self.start_btn.setEnabled(True)
        self.name_input.setEnabled(True)
        self.job_combo.setEnabled(True)
        self._session_id = None

    def _on_error(self, msg: str):
        self._set_loading_state(False)
        self._set_input_enabled(True)
        self.start_btn.setEnabled(True)
        self.name_input.setEnabled(True)
        self.job_combo.setEnabled(True)
        QMessageBox.critical(self, "错误", f"发生错误：{msg}")

    # ── UI 辅助 ───────────────────────────────────────────────────────────────

    def _add_bubble(self, role: str, text: str):
        bubble = Bubble(role, text)
        # 插入到 stretch 之前 (保持气泡在底部上方)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _add_score_bubble(self, eval_result):
        bubble = ScoreBubble(eval_result)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _add_system_msg(self, text: str):
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"color: {COLORS['text_sec']}; font-size: 11px; padding: 8px; font-weight: 500;"
        )
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, lbl)

    def _clear_chat(self):
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _scroll_to_bottom(self):
        # 使用 QTimer 确保布局计算完成后再滚动
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _set_loading_state(self, loading: bool, msg: str = ""):
        if loading:
            self.status_lbl.setText(f"⏳ {msg}")
            self.status_lbl.setStyleSheet(f"color: {COLORS['primary']}; font-weight: 600;")
        else:
            self.status_lbl.setStyleSheet(f"color: {COLORS['text_sec']};")

    def _set_input_enabled(self, enabled: bool):
        self.answer_input.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.answer_input.setFocus()

    def _showToast(self, msg):
        # 简单的状态栏闪烁提示
        original = self.status_lbl.text()
        self.status_lbl.setText(f"⚠️ {msg}")
        self.status_lbl.setStyleSheet("color: #FF3B30; font-weight: bold;")
        QTimer.singleShot(2000, lambda: (
            self.status_lbl.setText(original),
            self.status_lbl.setStyleSheet(f"color: {COLORS['text_sec']};")
        ))

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if obj is self.answer_input and event.type() == QEvent.KeyPress:
            ke: QKeyEvent = event
            if ke.key() == Qt.Key_Return and ke.modifiers() == Qt.ControlModifier:
                if self.send_btn.isEnabled():
                    self._send_answer()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        # 清理线程
        self._thread.quit()
        self._thread.wait()
        super().closeEvent(event)