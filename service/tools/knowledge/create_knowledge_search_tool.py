# service/tools/knowledge/create_knowledge_search_tool.py
"""
技术知识库检索工具（HelperEngine / AI助手 使用）

工具名：search_knowledge_base
知识库 ID 来源：环境变量 TECH_KB_ID
"""
import os
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


def create_knowledge_search_tool(kb: KnowledgeCore = None):
    """
    工厂函数，返回 search_knowledge_base LangChain tool。

    参数：
        kb — KnowledgeCore 实例，不传则自动从环境变量 TECH_KB_ID 构造。

    用法（推荐，由 registry 调用）：
        tool = create_knowledge_search_tool()          # 自动读 env
        tool = create_knowledge_search_tool(tech_kb)  # 手动传入
    """
    if kb is None:
        kb_id = os.getenv("TECH_KB_ID", "")
        if not kb_id:
            raise ValueError(
                "create_knowledge_search_tool：未传入 kb 实例，"
                "且环境变量 TECH_KB_ID 未配置"
            )
        kb = KnowledgeCore(knowledge_base_id=kb_id, label="技术知识库")

    # 闭包捕获 kb 实例
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