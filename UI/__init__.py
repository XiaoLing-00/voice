# UI/__init__.py
"""
UI 包根入口。

为兼容旧代码（如 `from UI.components import Theme`），
将新组件库与面板层全部重新导出。
"""

from UI.components import (
    Theme, T,
    StreamSignals,
    ButtonFactory,
    ChatBubble,
    ScoreCardBubble,
    StatBadge,
    TypingIndicator,
    ChartCard,
    GrowthChart,
    RadarChart,
    md_to_html,
    GLOBAL_QSS,
    header_qss,
    input_qss,
    combo_qss,
)

from UI.panel import (
    helper_panel,
    HistoryPanel,
    InterviewPanel,
    QuizPanel,
)
from UI.panel.base_panel import PanelFrame

__all__ = [
    # Theme & signals
    "Theme", "T", "StreamSignals",
    # Widgets
    "ButtonFactory", "ChatBubble", "ScoreCardBubble",
    "StatBadge", "TypingIndicator",
    # Charts
    "ChartCard", "GrowthChart", "RadarChart",
    # Utils
    "md_to_html",
    # QSS helpers
    "GLOBAL_QSS", "header_qss", "input_qss", "combo_qss",
    # Panels
    "helper_panel", "HistoryPanel", "InterviewPanel", "QuizPanel",
    "PanelFrame",
]