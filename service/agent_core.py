# service/agent_core.py
"""
Agent 核心框架（原生 OpenAI SDK 真实流式输出）

重构要点：
  - 构造函数接受 skill_set: SkillSet 参数，自动从 registry 加载对应工具
  - 提供 setter 方法（set_system_prompt / set_skill_set / set_model）方便运行时调整
  - 保留原有 chat() / stream() / clear_conversation() 接口，完全向后兼容
  - register_tool / register_tools 仍可手动追加工具（优先级高于 skill_set 自动加载）
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Generator, List, Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


# ── 对话历史管理 ──────────────────────────────────────────────────────────────

class ConversationHistory:
    def __init__(self, system_prompt: str = "", max_turns: int = 30):
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.messages: List[dict] = []

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str, tool_calls: list | None = None):
        msg: dict = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        self._trim()

    def add_tool_result(self, tool_call_id: str, content: str):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._trim()

    def _trim(self):
        user_indices = [i for i, m in enumerate(self.messages) if m["role"] == "user"]
        if len(user_indices) <= self.max_turns:
            return
        cutoff = user_indices[-self.max_turns]
        self.messages = self.messages[cutoff:]

    def get(self) -> List[dict]:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend(self.messages)
        return result

    def clear(self):
        self.messages.clear()

    def update_system_prompt(self, prompt: str):
        """更新 system prompt，不影响已有对话历史。"""
        self.system_prompt = prompt


# ── LangChain 工具 → OpenAI tools 格式转换 ───────────────────────────────────

def _lc_tool_to_openai(tool_obj) -> dict:
    """把 LangChain @tool 对象转换为 OpenAI tools 格式。"""
    schema = (
        tool_obj.args_schema.schema()
        if tool_obj.args_schema
        else {"properties": {}, "type": "object"}
    )
    return {
        "type": "function",
        "function": {
            "name": tool_obj.name,
            "description": tool_obj.description or "",
            "parameters": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        },
    }


# ── Agent 主类 ────────────────────────────────────────────────────────────────

class Agent:
    """
    通用 Agent，支持：
      - SkillSet 注入：构造时传入 skill_set，自动从 registry 加载对应工具
      - system_prompt 注入：构造或运行时均可设置
      - setter 方法：set_system_prompt / set_skill_set / set_model
      - 手动工具注册：register_tool / register_tools（可与 skill_set 叠加使用）
      - 真实流式输出：stream() 生成器，chat() 同步兼容

    示例——通过 skill_set 构造：
        from service.tools import ASSISTANT_SKILLS
        agent = Agent(db=db, knowledge_store=ks, skill_set=ASSISTANT_SKILLS,
                      system_prompt="你是AI助手...")

    示例——手动注册工具（旧用法，完全兼容）：
        agent = Agent(db=db, system_prompt="...")
        agent.register_tools(get_tools(db, ks))
    """

    def __init__(
        self,
        db=None,
        knowledge_store=None,
        skill_set=None,                         # SkillSet | None
        system_prompt: Optional[str] = None,
        model: str = "qwen3-omni-flash",
        temperature: float = 0.1,
        max_tokens: int = 2048,
        max_turns: int = 30,
    ):
        self.db = db
        self.knowledge_store = knowledge_store
        self.system_prompt = system_prompt or ""
        self.conversation = ConversationHistory(
            system_prompt=self.system_prompt,
            max_turns=max_turns,
        )
        self._tools_lc: Dict[str, Any] = {}
        self._tools_openai: List[dict] = []

        self._client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

        # 如果传入了 skill_set，自动加载对应工具
        if skill_set is not None:
            self._load_skill_set(skill_set)

    # ── SkillSet 自动加载 ──────────────────────────────────────────────────────

    def _load_skill_set(self, skill_set) -> None:
        """
        根据 SkillSet 从 registry 加载工具并注册。
        延迟导入避免循环依赖。
        """
        try:
            from service.tools.registry import get_tools_for
            tools = get_tools_for(
                db=self.db,
                knowledge_store=self.knowledge_store,
                skill_set=skill_set,
            )
            self.register_tools(tools)
            print(f"[Agent] ✅ SkillSet「{skill_set.name}」加载 {len(tools)} 个工具")
        except Exception as e:
            print(f"[Agent] ⚠️  SkillSet 加载失败：{e}")

    # ── Setter 方法 ────────────────────────────────────────────────────────────

    def set_system_prompt(self, prompt: str) -> "Agent":
        """
        运行时更新 system prompt。
        不清空对话历史，新 prompt 在下次请求时生效。
        返回 self 支持链式调用。
        """
        self.system_prompt = prompt
        self.conversation.update_system_prompt(prompt)
        return self

    def set_skill_set(self, skill_set, clear_existing: bool = True) -> "Agent":
        """
        切换工具集合。
        clear_existing=True 时先清空已注册工具，再加载新集合。
        返回 self 支持链式调用。
        """
        if clear_existing:
            self._tools_lc.clear()
            self._tools_openai.clear()
        self._load_skill_set(skill_set)
        return self

    def set_model(self, model: str, temperature: float | None = None) -> "Agent":
        """切换底层模型，可选更新温度参数。返回 self 支持链式调用。"""
        self._model = model
        if temperature is not None:
            self._temperature = temperature
        return self

    def set_temperature(self, temperature: float) -> "Agent":
        self._temperature = temperature
        return self

    def set_max_tokens(self, max_tokens: int) -> "Agent":
        self._max_tokens = max_tokens
        return self

    # ── 工具注册（手动，向后兼容） ─────────────────────────────────────────────

    def register_tool(self, tool_obj) -> "Agent":
        self._tools_lc[tool_obj.name] = tool_obj
        self._tools_openai = [_lc_tool_to_openai(t) for t in self._tools_lc.values()]
        return self

    def register_tools(self, tools: list) -> "Agent":
        for t in tools:
            self.register_tool(t)
        return self

    def unregister_tool(self, tool_name: str) -> "Agent":
        """移除指定工具。"""
        self._tools_lc.pop(tool_name, None)
        self._tools_openai = [_lc_tool_to_openai(t) for t in self._tools_lc.values()]
        return self

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def chat(self, user_input: str) -> str:
        """同步完整输出（兼容旧调用）。"""
        return "".join(self.stream(user_input))

    def stream(self, user_input: str) -> Generator[str, None, None]:
        """
        真实流式生成器。
        工具调用阶段整体收取后执行，纯文本阶段逐 token yield。
        """
        self.conversation.add_user(user_input)

        for _round in range(12):
            messages = self.conversation.get()

            stream_kwargs: dict = dict(
                model=self._model,
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
                stream_options={"include_usage": False},
            )
            if self._tools_openai:
                stream_kwargs["tools"] = self._tools_openai
                stream_kwargs["tool_choice"] = "auto"

            try:
                response_stream = self._client.chat.completions.create(**stream_kwargs)
            except Exception as e:
                yield f"\n\n[⚠️ 调用失败: {e}]\n"
                yield "[💡 请确认：1. 模型名正确 2. 账户有相应权限 3. base_url 无误]\n"
                return

            # ── 流式收取 ─────────────────────────────────────────────────────
            content_parts: list[str] = []
            tool_calls_map: dict[int, dict] = {}
            finish_reason = None

            for chunk in response_stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason or finish_reason

                if delta.content:
                    content_parts.append(delta.content)
                    if not tool_calls_map:
                        yield delta.content

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": tc.id or "", "name": "", "args": ""}
                        existing = tool_calls_map[idx]
                        if tc.id:
                            existing["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                existing["name"] += tc.function.name
                            if tc.function.arguments:
                                existing["args"] += tc.function.arguments

            full_content = "".join(content_parts)

            # ── 判断结果 ──────────────────────────────────────────────────────
            if tool_calls_map:
                openai_tool_calls = []
                for idx in sorted(tool_calls_map.keys()):
                    tc = tool_calls_map[idx]
                    openai_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["args"]},
                    })

                self.conversation.add_assistant(full_content, tool_calls=openai_tool_calls)

                for tc_info in openai_tool_calls:
                    tool_name = tc_info["function"]["name"]
                    yield f"\n\n⚙️ **正在调用** `{tool_name}`...\n\n"
                    result = self._execute_tool(tool_name, tc_info["function"]["arguments"])
                    self.conversation.add_tool_result(tc_info["id"], result)
            else:
                self.conversation.add_assistant(full_content)
                return

        yield "\n\n[⚠️ 已达到最大工具调用轮数]"

    # ── 工具执行 ──────────────────────────────────────────────────────────────

    def _execute_tool(self, tool_name: str, args_str: str) -> str:
        tool_obj = self._tools_lc.get(tool_name)
        if not tool_obj:
            return f"❌ 未找到工具: {tool_name}"
        try:
            args = json.loads(args_str) if args_str else {}
            result = tool_obj.invoke(args)
            return str(result)
        except Exception as e:
            return f"❌ 工具执行失败 ({tool_name}): {e}"

    # ── 工具函数 ──────────────────────────────────────────────────────────────

    def clear_conversation(self) -> "Agent":
        self.conversation.clear()
        return self

    def get_registered_tools(self) -> List[str]:
        return list(self._tools_lc.keys())

    def get_tool_count(self) -> int:
        return len(self._tools_lc)

    def __repr__(self) -> str:
        return (
            f"Agent(model={self._model!r}, tools={self.get_registered_tools()}, "
            f"system_prompt_len={len(self.system_prompt)})"
        )