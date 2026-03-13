# service/tools/registry.py
"""
工具注册中心

职责：
  1. 构建所有可用工具实例（懒加载，失败时跳过并打印警告）
  2. 根据 SkillSet 筛选并返回工具列表
  3. 提供兼容旧接口的 get_tools() 快捷函数

知识库工具注入方式：
  registry 接受独立的 KnowledgeCore 实例（tech_kb / interview_kb），
  分别传给对应的工具工厂，做到每个知识库工具与其 KnowledgeCore 一一绑定。

  如果某个 KnowledgeCore 未传入（None），对应工具跳过加载并打印警告。
"""
from __future__ import annotations

from typing import Any, Optional

from .db_tools import (
    create_history_tool,
    create_student_lookup_tool,
    create_job_info_tool,
    create_quiz_draw_tool,
    create_quiz_search_tool,
    create_quiz_stats_tool,
)
from .knowledge import (
    KnowledgeCore,
    create_knowledge_search_tool,
    create_ds_course_tool,
)
from .search_tools import create_web_search_tool, create_wiki_tool
from .permissions import (
    SkillSet,
    INTERVIEW_SKILLS,
    READONLY_SKILLS,
    ASSISTANT_SKILLS,
    ADMIN_SKILLS,
)


def build_tools(
    db=None,
    tech_kb: Optional[KnowledgeCore] = None,
    ds_course_kb: Optional[KnowledgeCore] = None,
) -> dict[str, Any]:
    """
    构建所有可用工具，返回 {tool_name: tool_obj} 字典。

    参数：
        db           — DatabaseManager，DB 类工具需要
        tech_kb      — 技术知识库 KnowledgeCore → search_knowledge_base（HelperEngine 用）
        ds_course_kb — 数据结构课程库 KnowledgeCore → search_ds_course（InterviewEngine 用）
    """
    result: dict[str, Any] = {}

    # ── DB 类工具（都依赖 db）────────────────────────────────────────────────
    _db_factories = [
        ("get_job_position_info",         create_job_info_tool),
        ("draw_questions_from_bank",      create_quiz_draw_tool),
        ("get_question_bank_stats",       create_quiz_stats_tool),
        ("search_question_bank",          create_quiz_search_tool),
        ("get_student_interview_history", create_history_tool),
        ("get_student_id_by_name",        create_student_lookup_tool),
    ]
    for tool_name, factory in _db_factories:
        if db is None:
            print(f"[Registry] ⚠️  {tool_name} 跳过：db 未传入")
            continue
        try:
            result[tool_name] = factory(db)
            print(f"[Registry] ✅ {tool_name}")
        except Exception as e:
            print(f"[Registry] ⚠️  {tool_name} 加载失败：{e}")

    # ── 知识库类工具 ──────────────────────────────────────────────────────────
    _kb_factories = [
        ("search_knowledge_base", create_knowledge_search_tool, tech_kb,      "tech_kb"),
        ("search_ds_course",      create_ds_course_tool,        ds_course_kb, "ds_course_kb"),
    ]
    for tool_name, factory, kb_instance, kb_param in _kb_factories:
        if kb_instance is None:
            print(f"[Registry] ⚠️  {tool_name} 跳过：{kb_param} 未传入")
            continue
        try:
            result[tool_name] = factory(kb_instance)
            print(f"[Registry] ✅ {tool_name} (kb={kb_instance.label!r})")
        except Exception as e:
            print(f"[Registry] ⚠️  {tool_name} 加载失败：{e}")

    # ── 联网搜索类工具（从 env 读取 API Key，无额外参数）────────────────────
    _search_factories = [
        ("web_search",      create_web_search_tool),
        ("search_wikipedia",create_wiki_tool),
    ]
    for tool_name, factory in _search_factories:
        try:
            result[tool_name] = factory()
            print(f"[Registry] ✅ {tool_name}")
        except Exception as e:
            print(f"[Registry] ⚠️  {tool_name} 加载失败：{e}")

    return result


def get_tools_for(
    db=None,
    tech_kb: Optional[KnowledgeCore] = None,
    ds_course_kb: Optional[KnowledgeCore] = None,
    skill_set: SkillSet = ASSISTANT_SKILLS,
) -> list:
    """根据 SkillSet 返回对应的工具列表。"""
    all_tools = build_tools(db=db, tech_kb=tech_kb, ds_course_kb=ds_course_kb)
    selected  = [obj for name, obj in all_tools.items() if name in skill_set]
    print(f"[Registry] 集合「{skill_set.name}」加载 {len(selected)}/{len(skill_set)} 个工具")
    return selected


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def get_interview_tools(db, ds_course_kb: Optional[KnowledgeCore] = None) -> list:
    """面试引擎专用（COMMON_GROUP + DS_COURSE_GROUP）。"""
    return get_tools_for(db=db, ds_course_kb=ds_course_kb, skill_set=INTERVIEW_SKILLS)


def get_assistant_tools(db, tech_kb: Optional[KnowledgeCore] = None) -> list:
    """AI 助手全量工具（含 search_knowledge_base）。"""
    return get_tools_for(db=db, tech_kb=tech_kb, skill_set=ASSISTANT_SKILLS)


def get_readonly_tools(db, tech_kb: Optional[KnowledgeCore] = None) -> list:
    """只读工具集。"""
    return get_tools_for(db=db, tech_kb=tech_kb, skill_set=READONLY_SKILLS)


def get_tools(db, tech_kb=None) -> list:
    """兼容旧接口，等同于 get_assistant_tools。"""
    return get_assistant_tools(db, tech_kb=tech_kb)