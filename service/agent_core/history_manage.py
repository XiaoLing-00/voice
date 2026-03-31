from typing import  List
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