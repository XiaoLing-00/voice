# service/interview_engine.py
"""
InterviewEngine — 模拟面试引擎，Agent 的使用方

内部持有 Agent(INTERVIEW_SKILLS) 做所有 LLM 调用。
知识库参考上下文通过注入的 tech_kb（KnowledgeCore）检索，
不经过工具调用，直接拼入 prompt。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Generator, Optional

from service.agent_core import Agent
from service.evaluator import AnswerEvaluator, EvalResult
from service.tools.knowledge.KnowledgeCore import KnowledgeCore
from service.tools.permissions import INTERVIEW_SKILLS


# ── 面试会话对话历史 ──────────────────────────────────────────────────────────

class InterviewHistory:
    def __init__(self, system_prompt: str = "", max_turns: int = 30):
        self.system_prompt = system_prompt
        self.max_turns     = max_turns
        self.messages: list[dict] = []

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str):
        self.messages.append({"role": "assistant", "content": content or ""})
        self._trim()

    def _trim(self):
        user_idx = [i for i, m in enumerate(self.messages) if m["role"] == "user"]
        if len(user_idx) > self.max_turns:
            self.messages = self.messages[user_idx[-self.max_turns]:]

    def get(self) -> list[dict]:
        result = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        result.extend(self.messages)
        return result

    def clear(self):
        self.messages.clear()


# ── System Prompts ────────────────────────────────────────────────────────────

_INTERVIEWER_SYSTEM = """你是一位专业、严谨的技术面试官，正在对"{job_name}"岗位的候选人进行模拟面试。

## 你的工作流程
1. 根据岗位技术栈，由浅入深地提问
2. 认真听取候选人的回答
3. 根据回答质量决定：追问细节 OR 切换下一个知识点

## 出题原则
- 覆盖岗位核心技术栈：{tech_stack}
- 难度循序渐进：先考察基础概念，再深入原理和实践
- 每次只问一个问题，等候选人回答后再追问或换题
- 回答正确且完整 → 追问更深层原理
- 回答有误 → 委婉指出并给提示

## 约束
- 每次回复只包含一个问题或追问，不得一次问多个
- 不要在候选人回答前就告知答案
"""

_REPORT_PROMPT = """请根据以下面试记录，生成一份结构化的面试评估报告。

岗位：{job_name}
候选人：{student_name}
面试题数：{turn_count} 题
各题得分：{scores_summary}

请用中文输出以下格式（直接输出内容，不要多余格式）：

【综合评价】
（2-3句话总体评价）

【技术能力】
（技术知识掌握情况，强项和薄弱点）

【表现亮点】
（2-3个具体亮点）

【待提升项】
（2-3个改进方向）

【学习建议】
（具体的学习资源方向或练习建议）
"""


# ── InterviewEngine ───────────────────────────────────────────────────────────

class InterviewEngine:
    """
    模拟面试引擎，Agent 的使用方。

    参数：
        ds_course_kb — 数据结构课程知识库 KnowledgeCore
                       加载为 search_ds_course tool，面试官（LLM）可主动调用，
                       检索课程场景素材后出更贴合课程的题目。

    使用示例：
        ds_kb  = KnowledgeCore(knowledge_base_id=os.getenv("DS_COURSE_KB_ID"), label="数据结构课程")
        engine = InterviewEngine(db=db, ds_course_kb=ds_kb)
        panel  = InterviewPanel(db, engine)
    """

    MAX_TURNS = 8

    def __init__(
        self,
        db,
        ds_course_kb: Optional[KnowledgeCore] = None,
        model: str = "qwen3-omni-flash",
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ):
        self.db        = db
        self.evaluator = AnswerEvaluator()

        from service.tools.registry import get_interview_tools
        self._agent = Agent(
            db=db,
            system_prompt="",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        tools = get_interview_tools(db, ds_course_kb=ds_course_kb)
        self._agent.register_tools(tools)

        self._histories: dict[int, InterviewHistory] = {}

    # ── 内部：借用 Agent._client 做纯文本流式调用 ─────────────────────────────

    def _stream_messages(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Generator[str, None, None]:
        """面试场景：纯文本流式，不需要 tool_calling loop。"""
        try:
            stream = self._agent._client.chat.completions.create(
                model=self._agent._model,
                messages=messages,
                temperature=temperature if temperature is not None else self._agent._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._agent._max_tokens,
                stream=True,
                stream_options={"include_usage": False},
            )
        except Exception as e:
            yield f"\n\n[⚠️ 调用失败: {e}]\n"
            return

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ── 开始面试 ──────────────────────────────────────────────────────────────

    def start_session(self, student_id: int, job_position_id: int) -> int:
        now = datetime.now().isoformat()
        cur = self.db.execute(
            "INSERT INTO interview_session "
            "(student_id, job_position_id, status, started_at) VALUES (?,?,?,?)",
            (student_id, job_position_id, "ongoing", now),
        )
        session_id = cur.lastrowid

        job = self._get_job_by_id(session_id)
        tech_stack_str = "、".join(json.loads(job["tech_stack"]))
        system_content = _INTERVIEWER_SYSTEM.format(
            job_name=job["name"], tech_stack=tech_stack_str
        )
        history = InterviewHistory(system_prompt=system_content)
        history.add_user("你好，我准备好了，请开始面试。")
        self._histories[session_id] = history
        return session_id

    # ── 第一问 ────────────────────────────────────────────────────────────────

    def get_first_question_stream(self, session_id: int) -> Generator[str, None, None]:
        history = self._histories.get(session_id)
        if history is None:
            yield "❌ 会话不存在，请重新开始面试。"
            return
        yield from self._stream_messages(history.get())

    def confirm_first_question(self, session_id: int, full_text: str):
        history = self._histories.get(session_id)
        if history is None:
            return
        history.add_assistant(full_text)
        self._save_turn(session_id, question_text=full_text, student_answer="")

    def get_first_question(self, session_id: int) -> str:
        full = "".join(self.get_first_question_stream(session_id))
        self.confirm_first_question(session_id, full)
        return full

    # ── 提交回答 ──────────────────────────────────────────────────────────────

    def submit_answer_stream(
        self, session_id: int, answer: str
    ) -> Generator[str, None, None]:
        turn = self._get_latest_unanswered_turn(session_id)
        if not turn:
            yield "__FINISHED__\n"
            return

        turn_id, question_text = turn
        job = self._get_job_by_id(session_id)

        # 同步评估
        eval_result: EvalResult = self.evaluator.evaluate(
            question=question_text,
            answer=answer,
            job_name=job["name"],
        )
        self.db.execute(
            "UPDATE interview_turn SET student_answer=?, scores=? WHERE id=?",
            (answer, json.dumps(eval_result.to_dict()), turn_id),
        )
        yield f"__EVAL__:{json.dumps(eval_result.to_dict(), ensure_ascii=False)}\n"

        # 判断是否达到最大轮数
        finished_count = self.db.fetchone(
            "SELECT COUNT(*) FROM interview_turn "
            "WHERE session_id=? AND student_answer!=''",
            (session_id,),
        )[0]
        is_finished = finished_count >= self.MAX_TURNS

        history = self._histories.get(session_id)
        if history is None:
            yield "__ERROR__:会话历史丢失\n"
            return

        history.add_user(answer)
        if is_finished:
            history.add_user("（面试轮数已到，请给候选人一个简短收尾语）")
            yield "__IS_FINISHED__\n"

        yield from self._stream_messages(history.get())

    def confirm_answer(self, session_id: int, ai_full_text: str, is_finished: bool):
        history = self._histories.get(session_id)
        if history is None:
            return
        history.add_assistant(ai_full_text)
        if not is_finished:
            self._save_turn(session_id, question_text=ai_full_text, student_answer="")

    def submit_answer(self, session_id: int, answer: str) -> dict:
        eval_result = None
        ai_parts: list[str] = []
        is_finished = False
        for token in self.submit_answer_stream(session_id, answer):
            if token.startswith("__EVAL__:"):
                eval_result = _DictEvalResult(json.loads(token[len("__EVAL__:"):].strip()))
            elif token == "__IS_FINISHED__\n":
                is_finished = True
            elif token == "__FINISHED__\n":
                return {"ai_reply": "面试已结束，请点击「结束面试」查看报告。", "is_finished": True}
            elif token.startswith("__ERROR__:"):
                raise RuntimeError(token[len("__ERROR__:"):].strip())
            else:
                ai_parts.append(token)
        ai_reply = "".join(ai_parts)
        self.confirm_answer(session_id, ai_reply, is_finished)
        return {"eval": eval_result, "ai_reply": ai_reply, "is_finished": is_finished}

    # ── 结束面试 ──────────────────────────────────────────────────────────────

    def finish_session_stream(self, session_id: int) -> Generator[str, None, None]:
        turns = self.db.fetchall(
            "SELECT question_text, student_answer, scores FROM interview_turn "
            "WHERE session_id=? AND student_answer!='' ORDER BY turn_index",
            (session_id,),
        )
        if not turns:
            yield "__SCORE__:0\n"
            yield "本次面试未完成任何题目，无法生成报告。"
            return

        all_scores, lines = [], []
        for i, (q, a, sc_json) in enumerate(turns, 1):
            if sc_json:
                sc = json.loads(sc_json)
                ov = sc.get("overall", 0)
                all_scores.append(ov)
                lines.append(
                    f"第{i}题（{q[:20]}…）: 综合 {ov}/10  "
                    f"技术{sc.get('tech',0)} 逻辑{sc.get('logic',0)} "
                    f"深度{sc.get('depth',0)} 表达{sc.get('clarity',0)}"
                )

        overall_score = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0
        yield f"__SCORE__:{overall_score}\n"

        job     = self._get_job_by_id(session_id)
        student = self._get_student(session_id)
        prompt  = _REPORT_PROMPT.format(
            job_name=job["name"],
            student_name=student["name"],
            turn_count=len(turns),
            scores_summary="\n".join(lines),
        )
        yield from self._stream_messages(
            [{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=1500,
        )

    def confirm_finish(self, session_id: int, overall_score: float, report_text: str):
        self._close_session(session_id, overall_score=overall_score, report=report_text)

    def finish_session(self, session_id: int) -> str:
        overall_score, report_parts = 0.0, []
        for token in self.finish_session_stream(session_id):
            if token.startswith("__SCORE__:"):
                overall_score = float(token[len("__SCORE__:"):].strip())
            else:
                report_parts.append(token)
        report_text = "".join(report_parts)
        self.confirm_finish(session_id, overall_score, report_text)
        return report_text

    # ── 运行时调整 ────────────────────────────────────────────────────────────

    def set_model(self, model: str, temperature: float | None = None) -> "InterviewEngine":
        self._agent.set_model(model, temperature)
        return self

    @property
    def agent(self) -> Agent:
        return self._agent

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _save_turn(self, session_id: int, question_text: str, student_answer: str):
        idx = self.db.fetchone(
            "SELECT COALESCE(MAX(turn_index)+1, 0) FROM interview_turn WHERE session_id=?",
            (session_id,),
        )[0]
        self.db.execute(
            "INSERT INTO interview_turn "
            "(session_id, turn_index, question_text, student_answer, created_at) "
            "VALUES (?,?,?,?,?)",
            (session_id, idx, question_text, student_answer, datetime.now().isoformat()),
        )

    def _get_latest_unanswered_turn(self, session_id: int):
        return self.db.fetchone(
            "SELECT id, question_text FROM interview_turn "
            "WHERE session_id=? AND student_answer='' "
            "ORDER BY turn_index DESC LIMIT 1",
            (session_id,),
        )

    def _close_session(self, session_id: int, overall_score: float, report: str):
        self.db.execute(
            "UPDATE interview_session "
            "SET status='finished', finished_at=?, overall_score=?, report=? WHERE id=?",
            (datetime.now().isoformat(), overall_score, report, session_id),
        )
        self._histories.pop(session_id, None)

    def _get_job_by_id(self, session_id: int) -> dict:
        row = self.db.fetchone(
            "SELECT jp.id, jp.name, jp.tech_stack FROM interview_session s "
            "JOIN job_position jp ON s.job_position_id=jp.id WHERE s.id=?",
            (session_id,),
        )
        return {"id": row[0], "name": row[1], "tech_stack": row[2]}

    def _get_student(self, session_id: int) -> dict:
        row = self.db.fetchone(
            "SELECT st.id, st.name FROM interview_session s "
            "JOIN student st ON s.student_id=st.id WHERE s.id=?",
            (session_id,),
        )
        return {"id": row[0], "name": row[1]}

    def get_session_turns(self, session_id: int) -> list:
        return self.db.fetchall(
            "SELECT turn_index, question_text, student_answer, scores "
            "FROM interview_turn WHERE session_id=? ORDER BY turn_index",
            (session_id,),
        )


class _DictEvalResult:
    def __init__(self, data: dict):
        self._data   = data
        self.overall = data.get("overall", 0)
        self.tech    = data.get("tech", 0)
        self.logic   = data.get("logic", 0)
        self.depth   = data.get("depth", 0)
        self.clarity = data.get("clarity", 0)
        self.comment = data.get("comment", "")

    def to_dict(self) -> dict:
        return self._data