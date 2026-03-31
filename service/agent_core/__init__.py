"""
Agent Core Module
~~~~~~~~~~~~~~~~~

提供通用 Agent 核心框架，支持：
  - SkillSet 动态注入与工具自动注册
  - System Prompt 运行时动态更新
  - 真实流式输出 (Streaming) 与工具调用 (Function Calling)
  - 完全兼容 OpenAI / DashScope 等兼容接口

核心类:
    Agent: 主 Agent 类，负责对话管理、工具调度、流式响应

示例:
    >>> from service.agent_core import Agent
    >>> from service.tools import ASSISTANT_SKILLS
    >>>
    >>> agent = Agent(
    ...     db=db,
    ...     knowledge_store=ks,
    ...     skill_set=ASSISTANT_SKILLS,
    ...     system_prompt="你是一个专业的面试助手",
    ...     model="qwen3-omni-flash"
    ... )
    >>>
    >>> # 流式对话
    >>> for chunk in agent.stream("请介绍一下 Python 的装饰器"):
    ...     print(chunk, end="", flush=True)
    >>>
    >>> # 运行时切换技能集
    >>> agent.set_skill_set(CODING_SKILLS).set_system_prompt("你现在是代码评审专家")
    >>>
    >>> # 同步调用（兼容旧接口）
    >>> response = agent.chat("帮我写一个快速排序")
"""

from .agent_core import Agent

# 明确导出内容，避免 * 导入污染命名空间
__all__ = ["Agent"]

# 模块版本信息（便于追踪和调试）
__version__ = "1.0.0"
__author__ = "Your Team"