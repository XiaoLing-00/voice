# main.py
import sys

from dotenv import load_dotenv
load_dotenv()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QTabWidget,
)

from service.db import DatabaseManager
from service.schema import SchemaInitializer
from service.interview_engine_sdk.interview_engine import InterviewEngine
from service.helper_engine import HelperEngine

from UI.panel.interview_panel import InterviewPanel
from UI.panel.helper_panel import HelperPanel
from UI.panel.history_panel import HistoryPanel
from UI.panel.quiz_panel import QuizPanel
from UI.components.info import Theme as T


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 基础服务 ──────────────────────────────────────────────────────────────
    db = DatabaseManager("interview.db")
    SchemaInitializer(db).initialize()

    # ── 引擎层 ────────────────────────────────────────────────────────────────
    # KnowledgeCore 实例由各引擎内部通过 registry 自动从 env 构造，
    # 无需在 main.py 手动创建。
    # 相关环境变量（.env）：
    #   TECH_KB_ID      — 技术知识库，HelperEngine(AI 助手) 使用
    #   DS_COURSE_KB_ID — 数据结构课程库，InterviewEngine(面试引擎) 使用
    interview_engine = InterviewEngine(db=db)
    helper_engine    = HelperEngine(db=db)

    # ── 主窗口 ────────────────────────────────────────────────────────────────
    window = QMainWindow()
    window.setWindowTitle("AI 模拟面试与能力提升平台")
    window.resize(1340, 880)
    window.setStyleSheet(f"QMainWindow {{ background: {T.BG}; }}")

    central = QWidget()
    window.setCentralWidget(central)
    root = QHBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    tabs = QTabWidget()
    tabs.setStyleSheet(f"""
        QTabWidget::pane {{ border: none; background: {T.BG}; }}
        QTabBar {{ background: {T.SURFACE}; }}
        QTabBar::tab {{
            background: {T.SURFACE}; color: {T.TEXT_DIM};
            padding: 12px 26px; font-size: 13px; font-weight: 600;
            font-family: {T.FONT}; border: none;
            border-bottom: 2px solid transparent; min-width: 100px;
        }}
        QTabBar::tab:selected {{
            color: {T.NEON}; border-bottom: 2px solid {T.NEON}; background: {T.BG};
        }}
        QTabBar::tab:hover:!selected {{ color: {T.TEXT}; background: {T.SURFACE2}; }}
    """)

    interview_panel = InterviewPanel(db, interview_engine)
    history_panel   = HistoryPanel(db)
    quiz_panel      = QuizPanel(db)
    agent_panel     = HelperPanel(helper_engine)

    tabs.addTab(interview_panel, "🎯  模拟面试")
    tabs.addTab(quiz_panel,      "📚  题库练习")
    tabs.addTab(history_panel,   "📊  历史分析")
    tabs.addTab(agent_panel,     "🤖  AI 助手")

    root.addWidget(tabs)

    tabs.currentChanged.connect(
        lambda idx: history_panel._refresh() if tabs.widget(idx) is history_panel else None
    )

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()