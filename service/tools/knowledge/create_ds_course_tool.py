# service/tools/knowledge/ds_course_skills.py
"""
数据结构课程知识库检索工具（InterviewEngine 使用）

工具名：search_ds_course
对应知识库：数据结构课程（场景面试素材、课程案例、题目背景等）
面试官（LLM）可主动调用，检索与当前面试话题相关的课程素材，
让题目更贴近真实课程场景。
"""
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


def create_ds_course_tool(ds_course_kb: KnowledgeCore):
    """
    工厂函数，接受数据结构课程知识库的 KnowledgeCore 实例，返回 LangChain tool。

    用法（InterviewEngine 内部）：
        ds_kb = KnowledgeCore(knowledge_base_id=os.getenv("DS_COURSE_KB_ID"), label="数据结构课程")
        tool  = create_ds_course_tool(ds_kb)
    """

    @tool(args_schema=DSCourseSearchInput)
    def search_ds_course(query: str, top_k: int = 3) -> str:
        """
        从数据结构课程知识库检索场景素材、课程案例和题目背景。
        在出题或追问时，调用本工具获取课程相关场景，
        让面试题目更贴近真实课程内容，提高面试的针对性。
        """
        results = ds_course_kb.retrieve(query, top_k=top_k)
        if not results or results[0].startswith(("📭", "⚠️")):
            return results[0] if results else "课程知识库未返回相关素材。"

        lines = [f"📖 数据结构课程素材（关键词：{query}，共 {len(results)} 条）：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r}\n")
        return "\n".join(lines)

    return search_ds_course