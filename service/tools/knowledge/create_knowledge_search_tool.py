# service/tools/knowledge/knowledge_skills.py
"""
通用技术知识库检索工具（HelperEngine 使用）

工具名：search_knowledge_base
对应知识库：原有技术知识库（Java/Spring/MySQL/Redis/前端等）
通过注入的 KnowledgeCore 实例调用，保持工具名不变以兼容现有 system_prompt。
"""
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from service.tools.knowledge.KnowledgeCore import KnowledgeCore


class KnowledgeSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "检索关键词或技术问题，例如：'Java 垃圾回收算法'、'MySQL MVCC 原理'、"
            "'Vue3 响应式实现'、'Redis 分布式锁'、'Spring AOP 原理'。"
            "适合查询技术概念、底层原理、最佳实践。"
        ),
    )
    top_k: int = Field(default=3, description="返回结果数，1~5", ge=1, le=5)


def create_knowledge_search_tool(kb: KnowledgeCore):
    """
    工厂函数，接受技术知识库的 KnowledgeCore 实例，返回 LangChain tool。

    用法（HelperEngine 内部）：
        kb   = KnowledgeCore(knowledge_base_id=os.getenv("TECH_KB_ID"), label="技术知识库")
        tool = create_knowledge_search_tool(kb)
    """

    @tool(args_schema=KnowledgeSearchInput)
    def search_knowledge_base(query: str, top_k: int = 3) -> str:
        """
        从技术知识库检索 Java、Spring、MySQL、Redis、前端等核心技术内容。
        适合回答技术概念、底层原理、面试高频技术题。
        优先于联网搜索使用，结果更权威、更贴合面试场景。
        """
        results = kb.retrieve(query, top_k=top_k)
        if not results or results[0].startswith(("📭", "⚠️")):
            return results[0] if results else "知识库未返回结果，建议使用联网搜索。"

        lines = [f"📚 知识库检索结果（关键词：{query}，共 {len(results)} 条）：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r}\n")
        return "\n".join(lines)

    return search_knowledge_base