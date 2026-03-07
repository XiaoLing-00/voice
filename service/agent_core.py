# service/agent_core.py
"""
Agent 核心框架（从原项目复用）
ConversationHistory + Agent + agentic loop
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()


class ConversationHistory:
    def __init__(self, system_prompt: str = "", max_turns: int = 30):
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.messages: List[BaseMessage] = []

    def add_user(self, content: str):
        self.messages.append(HumanMessage(content=content, id=f"user_{datetime.now().isoformat()}"))
        self._trim()

    def add_assistant(self, message: AIMessage):
        self.messages.append(message)
        self._trim()

    def add_tool_result(self, tool_call_id: str, content: str):
        self.messages.append(ToolMessage(content=content, tool_call_id=tool_call_id))
        self._trim()

    def _trim(self):
        human_indices = [i for i, m in enumerate(self.messages) if isinstance(m, HumanMessage)]
        if len(human_indices) <= self.max_turns:
            return
        cutoff_index = human_indices[-self.max_turns]
        system_msgs = [m for m in self.messages if isinstance(m, SystemMessage)]
        self.messages = system_msgs + self.messages[cutoff_index:]

    def get(self) -> List[BaseMessage]:
        if self.system_prompt:
            if not any(isinstance(m, SystemMessage) for m in self.messages):
                return [SystemMessage(content=self.system_prompt)] + self.messages
        return self.messages

    def clear(self):
        self.messages.clear()

    def to_dict(self) -> List[Dict]:
        return [self._msg_to_dict(m) for m in self.messages]

    @staticmethod
    def _msg_to_dict(msg: BaseMessage) -> Dict:
        base = {"role": msg.type, "content": msg.content}
        if isinstance(msg, AIMessage) and msg.tool_calls:
            base["tool_calls"] = msg.tool_calls
        elif isinstance(msg, ToolMessage):
            base["tool_call_id"] = msg.tool_call_id
        return base


class Agent:
    def __init__(
        self,
        db,
        system_prompt: Optional[str] = None,
        model: str = "qwen-plus",
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        self.db = db
        self._tools: Dict[str, Any] = {}
        self.system_prompt = system_prompt or ""
        self.conversation = ConversationHistory(system_prompt=self.system_prompt)
        self._bound_model = None

        self._llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        )

    def register_tool(self, tool_obj):
        self._tools[tool_obj.name] = tool_obj
        self._bound_model = None

    def register_tools(self, tools: list):
        for t in tools:
            self.register_tool(t)

    def _get_bound_model(self):
        if self._bound_model is None:
            self._bound_model = self._llm.bind_tools(list(self._tools.values()))
        return self._bound_model

    def chat(self, user_input: str, config: Optional[RunnableConfig] = None) -> str:
        self.conversation.add_user(user_input)
        for _ in range(10):
            model = self._get_bound_model()
            response: AIMessage = model.invoke(self.conversation.get(), config=config)
            if response.tool_calls:
                self.conversation.add_assistant(response)
                for tc in response.tool_calls:
                    result = self._execute_tool(tc)
                    self.conversation.add_tool_result(tc["id"], result)
            else:
                self.conversation.add_assistant(response)
                return response.content
        return "[Agent] 达到最大工具调用次数"

    def _execute_tool(self, tool_call: Dict[str, Any]) -> str:
        obj = self._tools.get(tool_call["name"])
        if not obj:
            return f"❌ 未找到工具: {tool_call['name']}"
        try:
            result = obj.invoke(tool_call.get("args", {}))
            return str(result)
        except Exception as e:
            return f"❌ 工具执行失败: {e}"

    def clear_conversation(self):
        self.conversation.clear()

    def get_tools(self) -> list:
        return list(self._tools.values())

    def get_registered_tools(self) -> List[str]:
        return list(self._tools.keys())
