# main.py
import sys
import os

from dotenv import load_dotenv
load_dotenv()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QTabWidget,
)

from service.db import DatabaseManager
from service.schema import SchemaInitializer
from service.tools.knowledge.KnowledgeCore import KnowledgeCore
from service.interview_engine import InterviewEngine
from service.helper_engine import HelperEngine

from UI.interview_panel import InterviewPanel
from UI.agent_panel import AgentPanel
from UI.history_panel import HistoryPanel
from UI.quiz_panel import QuizPanel
from UI.components import Theme as T


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 基础服务 ──────────────────────────────────────────────────────────────
    db = DatabaseManager("interview.db")
    SchemaInitializer(db).initialize()

    # ── 知识库 ────────────────────────────────────────────────────────────────
    #
    # tech_kb      — 技术知识库，HelperEngine(AI 助手) 使用
    #                包含 Java/Spring/MySQL/Redis/前端/面试技巧等内容
    #                .env: TECH_KB_ID = "xxx"
    #
    # ds_course_kb — 数据结构课程知识库，InterviewEngine(面试引擎) 使用
    #                提供场景面试素材，拼入面试官 prompt，不暴露给模型做工具调用
    #                .env: DS_COURSE_KB_ID = "xxx"
    #
    tech_kb = KnowledgeCore(
        knowledge_base_id=os.getenv("TECH_KB_ID", ""),
        label="技术知识库",
    )
    ds_course_kb = KnowledgeCore(
        knowledge_base_id=os.getenv("DS_COURSE_KB_ID", ""),
        label="数据结构课程",
    )

    # ── 引擎层 ────────────────────────────────────────────────────────────────
    interview_engine = InterviewEngine(db=db, ds_course_kb=ds_course_kb)
    helper_engine    = HelperEngine(db=db, tech_kb=tech_kb)

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
    agent_panel     = AgentPanel(helper_engine)

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