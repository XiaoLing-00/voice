# UI/agent_panel.py
"""
AI 知识助手面板（复用原项目 ChatHistoryWidget 框架）
用于查询知识库、搜索资料、查看历史记录等辅助功能。
"""
from PySide6.QtWidgets import (
    QPushButton, QLineEdit, QVBoxLayout, QHBoxLayout,
    QLabel, QScrollArea, QWidget, QFrame, QTextBrowser, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from UI.base_panel import PanelFrame


# 建议在 UI 目录新建一个 style_const.py 统一管理样式，或者直接修改组件
class MessageBubble(QFrame):
    def __init__(self, role: str, content: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)

        # 容器：控制气泡的对齐
        container_layout = QHBoxLayout()
        container_layout.setSpacing(10)

        # 气泡主体
        self.bubble_frame = QFrame()
        bubble_layout = QVBoxLayout(self.bubble_frame)
        bubble_layout.setContentsMargins(12, 10, 12, 10)

        # 角色文本
        role_label = QLabel("🤖 AI Assistant" if role == "assistant" else "Me")
        role_label.setStyleSheet("font-size: 10px; color: #9CA3AF; font-weight: 700; text-transform: uppercase;")
        bubble_layout.addWidget(role_label)

        # 内容 (改用 QLabel 支持自动换行，或优化后的 QTextBrowser)
        text_content = QTextBrowser()
        text_content.setMarkdown(content)
        text_content.setOpenExternalLinks(True)
        text_content.setFrameShape(QFrame.NoFrame)
        text_content.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 隐藏内部滚动条

        # 动态计算高度 (这是一个痛点，建议根据文本长度微调)
        text_content.setStyleSheet("background: transparent; font-size: 13px; color: #374151;")
        bubble_layout.addWidget(text_content)

        # 根据角色设置样式
        if role == "assistant":
            self.bubble_frame.setStyleSheet("""
                QFrame {
                    background: #FFFFFF; border: 1px solid #E5E7EB;
                    border-top-left-radius: 4px; border-top-right-radius: 16px;
                    border-bottom-left-radius: 16px; border-bottom-right-radius: 16px;
                }
            """)
            container_layout.addWidget(self.bubble_frame)
            container_layout.addStretch()
        else:
            self.bubble_frame.setStyleSheet("""
                QFrame {
                    background: #2563EB; border: none;
                    border-top-left-radius: 16px; border-top-right-radius: 4px;
                    border-bottom-left-radius: 16px; border-bottom-right-radius: 16px;
                }
            """)
            # 用户侧文字设为黑色
            text_content.setStyleSheet("background: transparent; font-size: 13px; color: #000;")
            container_layout.addStretch()
            container_layout.addWidget(self.bubble_frame)

        layout.addLayout(container_layout)

class ChatHistoryWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(10)
        self._layout.addStretch()

    def add_message(self, role: str, content: str):
        bubble = MessageBubble(role, content)
        self._layout.insertWidget(self._layout.count() - 1, bubble)

    def clear_messages(self):
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()


class AgentPanel(PanelFrame):
    message_sent = Signal(str)

    def __init__(self, agent, parent=None):
        super().__init__("🤖 AI 知识助手", parent)
        self.agent = agent
        self._build_ui()

    def _build_ui(self):
        hint = QLabel(
            "你可以问我：\n"
            "  • 查看岗位技术栈信息\n"
            "  • 搜索某个技术概念的解释\n"
            "  • 查看你的历史面试得分\n"
            "  • 让我搜索最新技术资料"
        )
        hint.setStyleSheet("""
            QLabel {
                color: #374151; font-size: 11px;
                background: #FFFBEB; border: 1px solid #FDE68A;
                border-radius: 6px; padding: 8px;
            }
        """)
        hint.setWordWrap(True)
        self.layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: 1px solid #E5E7EB; border-radius: 6px; background: white; }
            QScrollBar:vertical { width: 6px; background: #F3F4F6; }
            QScrollBar::handle:vertical { background: #D1D5DB; border-radius: 3px; }
        """)
        self.chat = ChatHistoryWidget()
        scroll.setWidget(self.chat)
        self.layout.addWidget(scroll, stretch=1)

        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("输入问题…")
        self.input_box.setStyleSheet("""
            QLineEdit {
                border: 1px solid #D1D5DB; border-radius: 6px;
                padding: 8px; font-size: 13px;
            }
        """)
        self.input_box.returnPressed.connect(self._send)

        send_btn = QPushButton("发送")
        send_btn.setFixedWidth(60)
        send_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB; color: white; border: none;
                border-radius: 6px; padding: 8px; font-weight: bold;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        send_btn.clicked.connect(self._send)

        clear_btn = QPushButton("清空")
        clear_btn.setFixedWidth(52)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: #6B7280; color: white; border: none;
                border-radius: 6px; padding: 8px;
            }
            QPushButton:hover { background: #4B5563; }
        """)
        clear_btn.clicked.connect(self._clear)

        input_row.addWidget(self.input_box, stretch=1)
        input_row.addWidget(send_btn)
        input_row.addWidget(clear_btn)
        self.layout.addLayout(input_row)

    def _send(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self.chat.add_message("user", text)
        try:
            reply = self.agent.chat(text)
            self.chat.add_message("assistant", reply)
        except Exception as e:
            self.chat.add_message("assistant", f"❌ 出错了：{e}")
        self.message_sent.emit(text)

    def _clear(self):
        self.chat.clear_messages()
        self.agent.clear_conversation()
