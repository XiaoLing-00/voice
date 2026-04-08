"""
AI 知识助手面板（组件化重构版）。

架构说明：
  - UI 组件：ChatInputBar / ChatBubble / TypingIndicator
  - 业务流：仅负责信号路由、流式对话编排、工具状态同步
  - TTS：完全由 ChatBubble 内部承接，面板零感知
  - Footer：顶部 10px 为拖拽热区，拖拽条严格跟随鼠标移动
"""

import threading

from PySide6.QtWidgets import (
    QPushButton, QVBoxLayout, QHBoxLayout,
    QLabel, QScrollArea, QWidget, QFrame,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QMouseEvent

from UI.components import (
    T, ChatBubble, TypingIndicator, StreamSignals,
    ButtonFactory, GLOBAL_QSS, input_qss,
)
from UI.components.chat_input_bar import ChatInputBar

# ── 快捷提示 ──────────────────────────────────────────────────────────────────
HINTS = [
    ("🎲", "随机抽题", "从题库随机抽5道题", T.NEON),
    ("🔍", "搜索题目", "搜索 Redis 相关题目", T.PURPLE),
    ("📊", "题库统计", "查看题库分类统计", T.YELLOW),
    ("🌐", "联网搜索", "搜索 Spring Boot 3.0 新特性", T.GREEN),
    ("📚", "知识检索", "什么是 MVCC？", T.NEON),
    ("🏆", "历史记录", "查看学生ID=1的面试记录", T.ACCENT),
]


class ResizableFooter(QFrame):
    """支持鼠标拖拽调整高度的底部区域（拖拽条严格跟随鼠标）"""

    def __init__(self, min_height: int = 80, max_height: int = 350, parent=None):
        super().__init__(parent)
        self.min_height = min_height
        self.max_height = max_height
        self.setFixedHeight(min_height)
        self._dragging = False
        self._start_y = 0
        self._start_height = 0
        self.setMouseTracking(True)
        self.setStyleSheet(f"""
            QFrame {{ background: {T.SURFACE}; border-top: 1px solid {T.BORDER}; }}
        """)

    def mousePressEvent(self, event) -> None:
        # 顶部 10px 为拖拽热区
        if event.button() == Qt.LeftButton and event.y() <= 10:
            self._dragging = True
            self._start_y = event.globalPosition().y()
            self._start_height = self.height()
            self.setCursor(Qt.SizeVerCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            delta = event.globalPosition().y() - self._start_y
            # 🔑 核心：鼠标下移(delta>0)时高度减小，使顶部边缘严格跟随光标
            new_h = self._start_height - delta
            new_h = max(self.min_height, min(new_h, self.max_height))

            if new_h != self.height():
                self.setFixedHeight(int(new_h))
            event.accept()
        else:
            self.setCursor(Qt.SizeVerCursor if event.y() <= 10 else Qt.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

class HelperPanel(QWidget):
    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent

        # 流式信号中枢
        self._stream_signals = StreamSignals()
        self._current_ai_bubble: ChatBubble | None = None
        self._typing_indicator: TypingIndicator | None = None
        self._is_streaming = False

        self._stream_signals.chunk_received.connect(self._on_chunk)
        self._stream_signals.stream_done.connect(self._on_stream_done)
        self._stream_signals.stream_error.connect(self._on_stream_error)

        self._build_ui()
        self._bind_input_signals()

    # ── 信号绑定 ──────────────────────────────────────────────────────────────
    def _bind_input_signals(self) -> None:
        self.input_bar.send_requested.connect(self._send)

    # ── UI 构建 ───────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.setStyleSheet(GLOBAL_QSS + input_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_hints())
        root.addWidget(self._build_chat_area(), stretch=1)
        root.addWidget(self._build_footer())

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(56)
        header.setStyleSheet(f"QFrame {{ background: {T.SURFACE}; border-bottom: 1px solid {T.BORDER}; }}")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(22, 0, 22, 0)

        title = QLabel("🤖  AI 知识助手")
        title.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {T.TEXT}; font-family: {T.FONT};")

        self._tool_status = QLabel()
        self._refresh_tool_status()
        self._tool_status.setStyleSheet(f"""
            font-size: 11px; color: {T.GREEN}; font-weight: 600;
            background: {T.GREEN}11; border: 1px solid {T.GREEN}33;
            border-radius: 10px; padding: 2px 10px;
        """)

        clear_btn = ButtonFactory.ghost("清空对话")
        clear_btn.clicked.connect(self._clear)

        lay.addWidget(title)
        lay.addStretch()
        lay.addWidget(self._tool_status)
        lay.addSpacing(12)
        lay.addWidget(clear_btn)
        return header

    def _build_hints(self) -> QFrame:
        frame = QFrame()
        frame.setFixedHeight(52)
        frame.setStyleSheet(f"QFrame {{ background: {T.SURFACE2}; border-bottom: 1px solid {T.BORDER}; }}")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(18, 10, 18, 10)
        lay.setSpacing(8)

        for icon, label, tooltip, color in HINTS:
            btn = ButtonFactory.tag(f"{icon} {label}", color)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda checked, t=tooltip: self._quick_send(t))
            lay.addWidget(btn)
        lay.addStretch()
        return frame

    def _build_chat_area(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea {{ background: {T.BG}; border: none; }}")

        self._chat_widget = QWidget()
        self._chat_widget.setStyleSheet(f"background: {T.BG};")
        self._chat_layout = QVBoxLayout(self._chat_widget)
        self._chat_layout.setContentsMargins(20, 18, 20, 10)
        self._chat_layout.setSpacing(10)
        self._chat_layout.addStretch()

        welcome = ChatBubble(
            "assistant",
            "你好！我是 **AI 知识助手** 🤖\n\n"
            "我可以帮你：\n"
            "- 🎲 随机抽题练习\n"
            "- 🔍 搜索题目和查看答案\n"
            "- 📊 题库统计与分析\n"
            "- 🌐 联网搜索最新技术资料\n"
            "- 📚 知识库技术概念检索\n"
            "- 🏆 查看历史面试记录\n\n"
            "点击上方快捷按钮，或直接输入问题开始！",
        )
        self._chat_layout.insertWidget(0, welcome)
        self._scroll.setWidget(self._chat_widget)
        return self._scroll

    def _build_footer(self) -> ResizableFooter:
        # 👇 替换为可拖拽高度的 Footer
        footer = ResizableFooter(min_height=80, max_height=350)

        lay = QHBoxLayout(footer)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(10)

        self.input_bar = ChatInputBar(self)
        self.input_bar.set_placeholder("输入问题，按 Ctrl+Enter 发送...")

        lay.addWidget(self.input_bar)
        return footer

    # ── 消息逻辑 ──────────────────────────────────────────────────────────────
    def _refresh_tool_status(self) -> None:
        count = (
            len(self.agent.get_registered_tools())
            if hasattr(self.agent, "get_registered_tools") else 8
        )
        self._tool_status.setText(f"● {count} 个工具就绪")

    def _quick_send(self, text: str) -> None:
        self.input_bar.set_text(text)
        self._send()

    def _send(self) -> None:
        if self._is_streaming:
            return
        text = self.input_bar.text_edit.toPlainText().strip()
        if not text:
            return
        self.input_bar.clear()
        self._add_user_bubble(text)
        self._start_stream(text)

    def _add_user_bubble(self, text: str) -> None:
        bubble = ChatBubble("user", text)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._scroll_bottom()

    def _start_stream(self, text: str) -> None:
        self._is_streaming = True
        self._set_input_enabled(False)

        self._typing_indicator = TypingIndicator()
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, self._typing_indicator)
        self._scroll_bottom()

        def _run() -> None:
            try:
                for chunk in self.agent.stream(text):
                    self._stream_signals.chunk_received.emit(chunk)
                self._stream_signals.stream_done.emit()
            except Exception as e:
                self._stream_signals.stream_error.emit(str(e))

        threading.Thread(target=_run, daemon=True).start()

    def _on_chunk(self, chunk: str) -> None:
        if self._typing_indicator is not None:
            self._chat_layout.removeWidget(self._typing_indicator)
            self._typing_indicator.stop()
            self._typing_indicator.deleteLater()
            self._typing_indicator = None

        if self._current_ai_bubble is None:
            self._current_ai_bubble = ChatBubble("assistant", enable_tts=True)
            self._current_ai_bubble.start_tts()
            self._chat_layout.insertWidget(self._chat_layout.count() - 1, self._current_ai_bubble)

        self._current_ai_bubble.append_chunk(chunk)
        self._scroll_bottom()

    def _on_stream_done(self) -> None:
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts()
        self._current_ai_bubble = None
        self._is_streaming = False
        self._set_input_enabled(True)
        self.input_bar.text_edit.setFocus()

    def _on_stream_error(self, msg: str) -> None:
        if self._typing_indicator is not None:
            self._chat_layout.removeWidget(self._typing_indicator)
            self._typing_indicator.stop()
            self._typing_indicator.deleteLater()
            self._typing_indicator = None

        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts(force=True)

        err_bubble = ChatBubble("assistant", f"❌ 出错了：{msg}")
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, err_bubble)
        self._current_ai_bubble = None
        self._is_streaming = False
        self._set_input_enabled(True)

    def _clear(self) -> None:
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.agent.clear_conversation()

    def _scroll_bottom(self) -> None:
        QTimer.singleShot(60, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _set_input_enabled(self, enabled: bool) -> None:
        self.input_bar.set_enabled(enabled)