# service/interview_engine_sdk/rag_service.py
"""
RAG 检索服务 - 封装知识库调用细节
"""
from typing import Optional
from service.tools.registry import get_ds_course_kb


class RAGService:
    """
    统一处理面试场景下的 RAG 检索。
    负责构造查询语句、调用 KnowledgeCore、处理异常。
    """

    def __init__(self, kb_id: Optional[str] = None):
        # 懒加载或初始化时获取实例
        self._kb = get_ds_course_kb(kb_id)

    def retrieve_for_question(
            self,
            job_name: str,
            top_k: int = 2
    ) -> str:
        """
        场景 1: 面试开始前/第一题，基于岗位名称检索场景素材。
        """
        if not self._kb:
            return ""
        try:
            return self._kb.retrieve_as_context(job_name, top_k=top_k)
        except Exception as e:
            print(f"[RAG] 开场检索失败：{e}")
            return ""

    def retrieve_for_followup(
            self,
            question: str,
            answer: str,
            top_k: int = 2
    ) -> str:
        """
        场景 2: 追问/下一题，基于“题目 + 回答”检索知识点。
        策略：截取关键片段避免 token 浪费，混合查询提高命中率。
        """
        if not self._kb:
            return ""

        # 构造查询：题目主旨 + 回答关键词
        # 注意：这里可以根据需要接入关键词提取服务，目前简单截取
        query = f"{question[:100]} {answer[:150]}"

        try:
            return self._kb.retrieve_as_context(query, top_k=top_k)
        except Exception as e:
            print(f"[RAG] 追问检索失败：{e}")
            return ""

    def format_context(self, context: str, role: str = "reference") -> str:
        """
        将原始检索内容格式化为 Prompt 可用的段落。
        """
        if not context:
            return ""

        if role == "reference":
            return (
                f"\n【面试官参考资料】\n{context}\n"
                "注意：请自然融入场景，不要直接展示给候选人。\n"
            )
        elif role == "knowledge":
            return (
                f"\n【相关知识点】\n{context}\n"
                "注意：结合知识点进行引导，不要直接给答案。\n"
            )
        return context