# service/helper_engine.py
"""
HelperEngine — AI 学习助手引擎，Agent 的直接使用方

接受独立的 tech_kb / interview_kb（KnowledgeCore 实例），
通过 registry 加载 ASSISTANT_SKILLS 全量工具集。

对外接口与旧 Agent 完全兼容，AgentPanel 无需改动：
  stream(user_input) / chat(user_input) / clear_conversation() / get_registered_tools()
"""
from __future__ import annotations

from typing import Generator, List, Optional

from service.agent_core import Agent
from service.tools.knowledge.KnowledgeCore import KnowledgeCore
from service.tools.permissions import ASSISTANT_SKILLS


_HELPER_SYSTEM_PROMPT = """你是一位专业的求职面试辅导助手，结合技术深度与面试经验为候选人提供全方位支持。

## 知识库覆盖范围（使用 search_knowledge_base 检索）
- Java 后端：JVM、GC、多线程、Spring Boot、Spring AOP/事务、MyBatis
- 数据库：MySQL 索引/MVCC/锁、慢 SQL 优化
- 缓存：Redis 数据结构、持久化、分布式锁、缓存三问
- 前端：JavaScript 事件循环/原型链/闭包、Vue3 响应式、React Hooks、Webpack
- 面试技巧：STAR 法则、结构化回答、自我介绍、压力面试、反问环节

## 工具使用优先级
1. **search_knowledge_base** — 技术概念/原理/面试技巧，优先于联网
2. **draw_questions_from_bank** — 随机抽题练习
3. **search_question_bank** — 关键词搜索题目
4. **get_question_bank_stats** — 题库统计
5. **get_job_position_info** — 岗位技术栈查询
6. **get_student_id_by_name** + **get_student_interview_history** — 历史记录查询
7. **web_search** — 知识库无结果时查最新资料

## 回答原则
- 简洁、专业的中文，善用 Markdown 格式
- 遇到技术题或面试技巧问题：先调 search_knowledge_base，再补充自己的理解
"""


class HelperEngine:
    """
    AI 学习助手引擎。

    参数：
        tech_kb — 技术知识库 KnowledgeCore（对应 search_knowledge_base tool）
                  包含技术知识 + 面试技巧，是同一个知识库。

    AgentPanel 使用示例：
        tech_kb = KnowledgeCore(knowledge_base_id=os.getenv("TECH_KB_ID"), label="技术知识库")
        helper  = HelperEngine(db=db, tech_kb=tech_kb)
        panel   = AgentPanel(helper)
    """

    def __init__(
        self,
        db,
        tech_kb: Optional[KnowledgeCore] = None,
        model: str = "qwen3-omni-flash",
        temperature: float = 0.1,
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
    ):
        self.db      = db
        self.tech_kb = tech_kb

        self._agent = Agent(
            db=db,
            system_prompt=system_prompt or _HELPER_SYSTEM_PROMPT,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        from service.tools.registry import get_tools_for
        tools = get_tools_for(db=db, tech_kb=tech_kb, skill_set=ASSISTANT_SKILLS)
        self._agent.register_tools(tools)

    # ── 对外接口（与旧 Agent 完全兼容）──────────────────────────────────────

    def stream(self, user_input: str) -> Generator[str, None, None]:
        yield from self._agent.stream(user_input)

    def chat(self, user_input: str) -> str:
        return self._agent.chat(user_input)

    def clear_conversation(self) -> None:
        self._agent.clear_conversation()

    def get_registered_tools(self) -> List[str]:
        return self._agent.get_registered_tools()

    def set_system_prompt(self, prompt: str) -> "HelperEngine":
        self._agent.set_system_prompt(prompt)
        return self

    def set_model(self, model: str, temperature: float | None = None) -> "HelperEngine":
        self._agent.set_model(model, temperature)
        return self

    @property
    def agent(self) -> Agent:
        return self._agent