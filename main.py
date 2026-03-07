# main.py
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QTabWidget,
)
from PySide6.QtCore import Qt

from service.db import DatabaseManager
from service.schema import SchemaInitializer
from service.knowledge_store import KnowledgeStore
from service.interview_engine import InterviewEngine
from service.agent_core import Agent
from service.tools import get_tools

from UI.interview_panel import InterviewPanel
from UI.agent_panel import AgentPanel
from UI.history_panel import HistoryPanel


def _seed_knowledge(ks: KnowledgeStore, db):
    """首次运行时从 knowledge_base/ 目录导入知识文件"""
    base_dir = Path("knowledge_base")
    if not base_dir.exists():
        return

    # 岗位目录名 → job_position_id 映射
    pos_rows = db.fetchall("SELECT id, name FROM job_position")
    name_to_id = {name: jid for jid, name in pos_rows}

    dir_map = {
        "java_backend": name_to_id.get("Java 后端工程师", 1),
        "frontend":     name_to_id.get("前端开发工程师", 2),
        "common":       0,
    }

    for sub_dir, job_id in dir_map.items():
        folder = base_dir / sub_dir
        if not folder.exists():
            continue
        for fpath in folder.glob("*.txt"):
            # 检查是否已经导入过（按文件名判断）
            existing = db.fetchone(
                "SELECT id FROM knowledge_chunk WHERE source=? LIMIT 1",
                (fpath.name,),
            )
            if existing:
                continue
            try:
                count = ks.add_file(str(fpath), job_position_id=job_id)
                print(f"[KnowledgeStore] 导入 {fpath.name} → {count} 个分块 (job_id={job_id})")
            except Exception as e:
                print(f"[KnowledgeStore] 导入失败 {fpath.name}: {e}")

    # 把题库答案也导入知识库
    for job_id in [1, 2]:
        already = db.fetchone(
            "SELECT id FROM knowledge_chunk WHERE source='题库答案' AND job_position_id=? LIMIT 1",
            (job_id,),
        )
        if already:
            continue
        qa_rows = db.fetchall(
            "SELECT content, answer FROM question_bank WHERE job_position_id=?",
            (job_id,),
        )
        if qa_rows:
            qa_list = [{"question": q, "answer": a} for q, a in qa_rows]
            count = ks.add_qa_pairs(qa_list, job_position_id=job_id)
            print(f"[KnowledgeStore] 题库答案导入 job_id={job_id} → {count} 个分块")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 基础服务 ──────────────────────────────────────────────────────────────
    db = DatabaseManager("interview.db")
    SchemaInitializer(db).initialize()

    ks = KnowledgeStore(db)
    _seed_knowledge(ks, db)

    engine = InterviewEngine(db, ks)

    # ── Agent（知识助手） ─────────────────────────────────────────────────────
    agent = Agent(
        db=db,
        system_prompt="""你是一位专业的求职面试辅导助手。
你可以帮助用户：
1. 查询各岗位的技术要求和面试重点
2. 从知识库检索技术概念的解释
3. 搜索最新的技术资料和行业动态
4. 查看学生的历史面试表现

请用简洁、专业的中文回答，必要时调用工具获取准确信息。""",
    )
    tools = get_tools(db, ks)
    agent.register_tools(tools)

    # ── UI ───────────────────────────────────────────────────────────────────
    window = QMainWindow()
    window.setWindowTitle("AI 模拟面试与能力提升平台")
    window.resize(1280, 820)

    central = QWidget()
    window.setCentralWidget(central)
    root = QHBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    tabs = QTabWidget()
    tabs.setStyleSheet("""
        QTabWidget::pane {
            border: none;
            background: #F3F4F6;
        }
        QTabBar::tab {
            padding: 10px 24px;
            font-size: 13px;
            border-bottom: 2px solid transparent;
            background: #F9FAFB;
            color: #6B7280;
        }
        QTabBar::tab:selected {
            border-bottom: 2px solid #2563EB;
            color: #1D40AF;
            font-weight: bold;
            background: white;
        }
        QTabBar::tab:hover { background: #EFF6FF; }
    """)

    interview_panel = InterviewPanel(db, engine)
    history_panel   = HistoryPanel(db)
    agent_panel     = AgentPanel(agent)

    tabs.addTab(interview_panel, "🎯 模拟面试")
    tabs.addTab(history_panel,   "📊 历史分析")
    tabs.addTab(agent_panel,     "🤖 AI 助手")

    root.addWidget(tabs)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
