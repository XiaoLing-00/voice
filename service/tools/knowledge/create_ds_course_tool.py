# service/tools/knowledge/create_ds_course_tool.py
"""
数据结构课程知识库检索工具（InterviewEngine 使用）

工具名：search_ds_course
知识库 ID 来源：环境变量 DS_COURSE_KB_ID
"""
import os
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from service.tools.knowledge.KnowledgeCore import KnowledgeCore


class DSCourseSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "检索关键词或面试话题，例如：'链表反转场景'、'二叉树遍历应用'、"
            "'栈解决括号匹配'、'动态规划背包问题'、'图的最短路径'。"
            "适合检索数据结构课程中的具体场景案例和题目素材。"
        ),
    )
    top_k: int = Field(default=3, description="返回结果数，1~5", ge=1, le=5)


def create_ds_course_tool(kb: KnowledgeCore = None):
    """
    工厂函数，返回 search_ds_course LangChain tool。

    参数：
        kb — KnowledgeCore 实例，不传则自动从环境变量 DS_COURSE_KB_ID 构造。

    用法（推荐，由 registry 调用）：
        tool = create_ds_course_tool()          # 自动读 env
        tool = create_ds_course_tool(course_kb) # 手动传入
    """
    if kb is None:
        kb_id = os.getenv("DS_COURSE_KB_ID", "")
        if not kb_id:
            raise ValueError(
                "create_ds_course_tool：未传入 kb 实例，"
                "且环境变量 DS_COURSE_KB_ID 未配置"
            )
        kb = KnowledgeCore(knowledge_base_id=kb_id, label="数据结构课程")

    @tool(args_schema=DSCourseSearchInput)
    def search_ds_course(query: str, top_k: int = 3) -> str:
        """
        从数据结构课程知识库检索场景素材、课程案例和题目背景。
        在出题或追问时，调用本工具获取课程相关场景，
        让面试题目更贴近真实课程内容，提高面试的针对性。
        """
        results = kb.retrieve(query, top_k=top_k)
        if not results or results[0].startswith(("📭", "⚠️")):
            return results[0] if results else "课程知识库未返回相关素材。"

        lines = [f"📖 数据结构课程素材（关键词：{query}，共 {len(results)} 条）：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r}\n")
        return "\n".join(lines)

    return search_ds_course