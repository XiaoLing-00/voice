# service/tools/knowledge/create_combined_kb_tool.py
"""
双知识库混合检索工具

工具名：search_combined_knowledge
同时检索技术知识库和教学知识库，合并结果。
"""
import os
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from service.tools.knowledge.KnowledgeCore import KnowledgeCore, retrieve_combined


class CombinedKBSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "检索关键词，将同时查询面试要点库（技术知识）和教学资料库（课程内容）。"
            "适合在课程答辩模式下，基于老师讲义和面试要点双重参考出题。"
        ),
    )
    top_k: int = Field(default=3, description="每个知识库返回的条数", ge=1, le=5)


def create_combined_kb_tool(tech_kb=None, course_kb=None):
    """
    工厂函数，返回 search_combined_knowledge LangChain tool。

    参数：
        tech_kb — 技术知识库实例，不传则自动从环境变量 TECH_KB_ID 构造
        course_kb — 课程知识库实例，不传则自动从环境变量 DS_COURSE_KB_ID 构造
    """
    if tech_kb is None:
        tech_kb_id = os.getenv("TECH_KB_ID", "")
        if not tech_kb_id:
            raise ValueError(
                "create_combined_kb_tool：未传入 kb 实例，"
                "且环境变量 TECH_KB_ID 未配置"
            )
        tech_kb = KnowledgeCore(knowledge_base_id=tech_kb_id, label="技术知识库")

    if course_kb is None:
        course_kb_id = os.getenv("DS_COURSE_KB_ID", "")
        if not course_kb_id:
            raise ValueError(
                "create_combined_kb_tool：未传入 kb 实例，"
                "且环境变量 DS_COURSE_KB_ID 未配置"
            )
        course_kb = KnowledgeCore(knowledge_base_id=course_kb_id, label="课程知识库")

    @tool(args_schema=CombinedKBSearchInput)
    def search_combined_knowledge(query: str, top_k: int = 3) -> str:
        """
        同时检索面试要点库（库A）和教学资料库（库B），合并返回结果。
        专用于课程答辩模式，结合面试知识点和老师课程内容进行出题和评分参考。
        """
        combined = retrieve_combined(tech_kb, course_kb, query, top_k=top_k)
        if not combined:
            return "两个知识库均未找到相关内容。"
        
        return f"📚 混合检索结果（{query}）：\n\n{combined}"

    return search_combined_knowledge
