# UI/components/__init__.py
"""
统一组件库入口。

提供：
  - 所有原子组件的直接导入
  - 全局 QSS 字符串辅助函数
"""

from .info.Theme import Theme, T
from .info.StreamSignals import StreamSignals

from .ButtonFactory import ButtonFactory
from UI.components.Bubble.ChatBubble import ChatBubble
from UI.components.Bubble.ScoreCardBubble import ScoreCardBubble
from .StatBadge import StatBadge
from .TypingIndicator import TypingIndicator

from .chart import ChartCard, GrowthChart, RadarChart

from .util.md_to_html import md_to_html

__all__ = [
    # Theme
    "Theme", "T",
    # Signals
    "StreamSignals",
    # Widgets
    "ButtonFactory",
    "ChatBubble",
    "ScoreCardBubble",
    "StatBadge",
    "TypingIndicator",
    # Charts
    "ChartCard",
    "GrowthChart",
    "RadarChart",
    # Utils
    "md_to_html",
    # QSS helpers (see below)
    "GLOBAL_QSS",
    "header_qss",
    "input_qss",
    "combo_qss",
]


# ══════════════════════════════════════════════════════════════════════════════
# 全局 QSS 字符串工具函数
# ══════════════════════════════════════════════════════════════════════════════

GLOBAL_QSS = f"""
    QWidget {{
        background: {T.BG};
        color: {T.TEXT};
        font-family: {T.FONT};
    }}
    QScrollBar:vertical {{
        width: 5px; background: transparent; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {T.BORDER2}; border-radius: 2px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {T.NEON}66; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 5px; background: transparent; }}
    QScrollBar::handle:horizontal {{ background: {T.BORDER2}; border-radius: 2px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QComboBox QAbstractItemView {{
        background: {T.SURFACE2}; color: {T.TEXT};
        selection-background-color: {T.NEON}22;
        border: 1px solid {T.BORDER2}; outline: none;
    }}
"""


def header_qss(border_color: str = T.BORDER) -> str:
    return f"""
        QFrame {{
            background: {T.SURFACE};
            border-bottom: 1px solid {border_color};
        }}
    """


def input_qss(focus_color: str = T.NEON) -> str:
    return f"""
        QLineEdit, QTextEdit {{
            background: {T.BG}; border: 1px solid {T.BORDER2};
            border-radius: 10px; padding: 8px 14px;
            color: {T.TEXT}; font-size: 14px; font-family: {T.FONT};
        }}
        QLineEdit:focus, QTextEdit:focus {{ border-color: {focus_color}; }}
        QLineEdit:disabled, QTextEdit:disabled {{
            background: {T.SURFACE}; color: {T.TEXT_MUTE};
        }}
    """


def combo_qss(focus_color: str = T.NEON) -> str:
    return f"""
        QComboBox {{
            background: {T.BG}; border: 1px solid {T.BORDER2};
            border-radius: 8px; padding: 6px 12px;
            color: {T.TEXT}; font-size: 13px; font-family: {T.FONT};
        }}
        QComboBox:focus {{ border-color: {focus_color}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {T.TEXT_DIM};
            margin: 4px;
        }}
    """