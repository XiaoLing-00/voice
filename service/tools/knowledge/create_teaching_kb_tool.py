# service/tools/knowledge/create_teaching_kb_tool.py
"""
技术知识库检索工具

工具名：search_teaching_knowledge
知识库 ID 来源：环境变量 TECH_KB_ID
"""
import os
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from service.tools.knowledge.KnowledgeCore import KnowledgeCore


class TeachingKBSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "检索技术知识库：Java/Spring/MySQL/Redis/前端等面试知识，AI 助手使用"
        ),
    )
    top_k: int = Field(default=3, description="返回结果数，1~5", ge=1, le=5)


def create_teaching_kb_tool(kb: KnowledgeCore = None):
    """
    工厂函数，返回 search_teaching_knowledge LangChain tool。

    参数：
        kb — KnowledgeCore 实例，不传则自动从环境变量 TECH_KB_ID 构造。
    """
    kb = get_ds_teaching_kb(kb)

    @tool(args_schema=TeachingKBSearchInput)
    def search_teaching_knowledge(query: str, top_k: int = 3) -> str:
        """
        从教学知识库检索老师上传的课程资料、PPT、讲义、项目文档等。
        用于课程答辩模式，基于老师的教学材料出题和评分。
        有教学资料时优先于通用面试知识库使用。
        """
        results = kb.retrieve(query, top_k=top_k)
        if not results or results[0].startswith(("📭", "⚠️")):
            return results[0] if results else "教学知识库未返回结果。"

        lines = [f" 教学资料检索结果（关键词：{query}，共 {len(results)} 条）：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r}\n")
        return "\n".join(lines)

    return search_teaching_knowledge

def get_ds_teaching_kb(kb: KnowledgeCore = None) -> KnowledgeCore:
    if kb is None:
        kb_id = os.getenv("TECH_KB_ID", "")
        if not kb_id:
            raise ValueError(
                "create_teaching_kb_tool：未传入 kb 实例，"
                "且环境变量 TECH_KB_ID 未配置"
            )
        print(f"[Registry] OK: 创建技术知识库实例：{kb_id}")
        kb = KnowledgeCore(knowledge_base_id=kb_id, label="技术知识库")
    return kb