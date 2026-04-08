"""
chat_input_bar.py
布局驱动自适应输入框组件
职责：根据父容器高度实时拉伸、支持滚轮、快捷键拦截、发送信号触发
"""

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QTextEdit, QSizePolicy

from UI.components import ButtonFactory, T


class _ResizableTextEdit(QTextEdit):
    """内部类：支持弹性拉伸与滚轮的文本编辑区"""
    send_requested = Signal()

    def __init__(self, min_h: int = 40, parent=None):
        super().__init__(parent)
        self.min_h = min_h
        self.setMinimumHeight(min_h)

        # 垂直滚动条按需显示，水平永远隐藏
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QTextEdit.NoFrame)

        # 关键：允许垂直方向弹性拉伸，填满父容器分配的空间
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def wheelEvent(self, event: QWheelEvent) -> None:
        # 拦截滚轮事件，优先作用于本输入框，不向上传递给聊天滚动区
        super().wheelEvent(event)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Ctrl+Enter 发送，Enter 换行
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatInputBar(QWidget):
    # ── 信号定义 ──────────────────────────────────────────────────────────────
    send_requested = Signal(str)
    text_changed   = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 允许垂直拉伸以填满 Footer 高度
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._build_ui()

    # ══════════════════════════════════════════════════════════════════════════
    # UI 构建
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        self.text_edit = _ResizableTextEdit(min_h=40)
        self.text_edit.setPlaceholderText("输入你的回答... (Ctrl+Enter 发送)")
        self.text_edit.textChanged.connect(lambda: self.text_changed.emit(self.text_edit.toPlainText()))
        self.text_edit.send_requested.connect(self._trigger_send)

        # 发送按钮：固定宽度，高度跟随输入框
        self.send_btn = ButtonFactory.solid("发送", T.NEON, height=44, width=80)
        self.send_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.send_btn.clicked.connect(self._trigger_send)

        lay.addWidget(self.text_edit, stretch=1)
        lay.addWidget(self.send_btn)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 实时同步按钮高度与文本框实际渲染高度，保持视觉对齐
        if self.text_edit:
            self.send_btn.setFixedHeight(self.text_edit.height())

    # ══════════════════════════════════════════════════════════════════════════
    # 公共接口
    # ══════════════════════════════════════════════════════════════════════════

    def _trigger_send(self) -> None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            return
        self.send_requested.emit(text)
        self.text_edit.clear()

    def set_enabled(self, enabled: bool) -> None:
        self.text_edit.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.text_edit.setFocus()

    def set_text(self, text: str) -> None:
        self.text_edit.setPlainText(text)
        self.text_edit.moveCursor(self.text_edit.textCursor().End)

    def clear(self) -> None:
        self.text_edit.clear()

    def set_placeholder(self, text: str) -> None:
        self.text_edit.setPlaceholderText(text)