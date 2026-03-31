# service/interview_engine_sdk/interview_engine.py
"""
InterviewEngine — 模拟面试引擎（重构版）

架构原则：
  - 编排层 (Orchestrator): 本类只负责流程调度，不包含业务逻辑
  - 决策层：MarkovDecisionEngine 负责意图判断/难度调整
  - 知识层：RAGService 负责检索/格式化
  - 数据层：DBConversation 负责持久化
  - 执行层：Agent 负责流式生成/工具调用

特殊 token 协议（UI 层消费）：
  __EVAL__:{json}    — 评分结果
  __IS_FINISHED__    — 本轮是最后一题
  __FINISHED__       — 已无未答题目（异常兜底）
  __ERROR__:{msg}    — 内部错误
  __SCORE__:{float}  — 总分
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Generator, Optional, Dict, Any

# 内部模块导入
from service.agent_core.agent_core import Agent
from service.agent_core.load_prompt import load_prompt
from service.evaluator import AnswerEvaluator, EvalResult

# 新架构模块导入
from .rag_service import RAGService
from .static_markov_asking import (
    MarkovDecisionEngine,
    EvalScores,
    scores_from_dict,
    IntentType,
    DecisionResult
)
from .db_conversation import DBConversation, InterviewTurn

# 工具注册
from service.tools.registry import get_interview_tools

# ── 系统提示词 ──────────────────────────────────────────────────────────────
_INTERVIEWER_SYSTEM = load_prompt("prompt/interview/interview_system.md")
_REPORT_PROMPT = load_prompt("prompt/interview/interview_report.md")


class InterviewEngine:
    """
    面试引擎编排器（Orchestrator Pattern）

    职责：
      1. 组装各服务组件（RAG/Markov/DB/Agent）
      2. 协调面试流程状态机
      3. 处理特殊 token 协议输出
      4. 管理 session 级缓存（History/Level/FollowupCount）
    """

    MAX_TURNS = 8  # 默认最大轮数，可由外部配置覆盖

    def __init__(
            self,
            db,
            model: str = "qwen3-omni-flash",
            temperature: float = 0.7,
            max_tokens: int = 1024,
            # 依赖注入（支持测试时 Mock）
            rag_service: Optional[RAGService] = None,
            decision_engine: Optional[MarkovDecisionEngine] = None,
            db_conv: Optional[DBConversation] = None,
            evaluator: Optional[AnswerEvaluator] = None,
    ):
        # ── 依赖注入 / 懒加载 ──────────────────────────────────────────────
        self.db_conv = db_conv or DBConversation(db)
        self.rag = rag_service or RAGService()
        self.decider = decision_engine or MarkovDecisionEngine()
        self.evaluator = evaluator or AnswerEvaluator()

        # ── Agent 初始化（执行层） ─────────────────────────────────────────
        self._agent = Agent(
            db=db,
            system_prompt="",  # 按 session 动态设置
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # 注册面试专用工具（如 search_ds_course）
        self._agent.register_tools(get_interview_tools(db))

        # ── Session 级缓存（内存态，请求结束后释放） ───────────────────────
        # session_id → InterviewHistory (对话历史)
        self._histories: dict[int, InterviewHistory] = {}
        # session_id → 当前难度
        self._session_levels: dict[int, str] = {}
        # session_id → 当前题的追问次数（注意：持久化计数应查 DB，此为缓存加速）
        self._followup_cache: dict[int, int] = {}

    # ── 生命周期：开始面试 ─────────────────────────────────────────────────

    def start_session(self, student_id: int, job_position_id: int) -> int:
        """创建面试会话，初始化上下文"""
        session_id = self.db_conv.create_session(student_id, job_position_id)

        # 加载岗位信息，构建 system prompt
        job = self.db_conv.get_session_job(session_id)

        # 🔧 修复：job["tech_stack"] 已经是 list，不需要 json.loads
        tech_stack_list = job["tech_stack"] if isinstance(job["tech_stack"], list) else []
        tech_stack = "、".join(tech_stack_list)

        system_content = _INTERVIEWER_SYSTEM.format(
            job_name=job["name"],
            tech_stack=tech_stack
        )

        # 初始化该 session 的对话历史
        history = InterviewHistory(system_prompt=system_content)
        self._histories[session_id] = history
        self._session_levels[session_id] = "easy"  # 默认起始难度

        return session_id

    # ── 第一题：流式生成 ───────────────────────────────────────────────────

    def get_first_question_stream(self, session_id: int) -> Generator[str, None, None]:
        """生成开场第一题（带 RAG 场景增强）"""
        history = self._histories.get(session_id)
        if not history:
            yield "__ERROR__:会话不存在\n"
            return

        # 1. RAG: 基于岗位名称检索开场场景
        job = self.db_conv.get_session_job(session_id)
        rag_ctx = self.rag.retrieve_for_question(job["name"], top_k=2)

        # 2. 构建首问 Prompt
        user_prompt = "你好，我准备好了，请开始面试。"
        if rag_ctx:
            user_prompt += self.rag.format_context(rag_ctx, role="reference")

        # 3. 更新 Agent 上下文并流式生成
        self._sync_history_to_agent(history)

        parts = []
        for chunk in self._agent_stream(user_prompt):
            parts.append(chunk)
            yield chunk

        ai_text = "".join(parts)

        # 4. 落库 & 更新历史
        history.add_user("你好，我准备好了，请开始面试。")
        history.add_assistant(ai_text)
        self.db_conv.save_turn(session_id, question_text=ai_text, student_answer="")

    # ── 核心：提交回答并生成追问（流式） ─────────────────────────────────────

    def submit_answer_stream(
            self,
            session_id: int,
            answer: str,
            emotion: str = ""  # 预留情感分析扩展
    ) -> Generator[str, None, None]:
        """
        主流程：评分 → 决策 → RAG → Prompt 构建 → Agent 流式生成

        状态流转：
          User Answer → Eval → Markov Decision → (RAG) → Prompt → Agent Stream → Next Question
        """
        # ── Step 1: 获取当前状态 ─────────────────────────────────────────
        turn: Optional[InterviewTurn] = self.db_conv.get_unanswered_turn(session_id)
        if not turn:
            yield "__FINISHED__\n"
            return

        turn_id, question_text = turn.id, turn.question_text
        job = self.db_conv.get_session_job(session_id)

        # 获取计数类状态（优先查缓存，兜底查 DB）
        answered_count = self.db_conv.count_answered_turns(session_id)
        followup_count = self._followup_cache.get(session_id, 0)
        current_difficulty = self._session_levels.get(session_id, "easy")

        history = self._histories.get(session_id)
        if not history:
            yield "__ERROR__:会话历史丢失\n"
            return

        # ── Step 2: 同步评分（阻塞） ──────────────────────────────────────
        eval_result: EvalResult = self.evaluator.evaluate(
            question=question_text,
            answer=answer,
            job_name=job["name"],
        )

        # 🔧 修复：确保 to_dict() 返回 dict，不重复序列化
        scores_dict = eval_result.to_dict()
        if isinstance(scores_dict, str):
            scores_dict = json.loads(scores_dict)

        # 落库评分
        self.db_conv.update_turn_answer(turn_id, answer, scores_dict)

        # 推送评分给 UI（确保是字符串）
        yield f"__EVAL__:{json.dumps(scores_dict, ensure_ascii=False)}\n"

        # ── Step 3: Markov 决策 ──────────────────────────────────────────
        # 转换评分格式
        scores = EvalScores(
            tech=eval_result.tech_score,
            logic=eval_result.logic_score,
            depth=eval_result.depth_score,
            clarity=eval_result.clarity_score
        )

        decision: DecisionResult = self.decider.decide_next_action(
            session_id=session_id,  # 仅用于日志追踪
            scores=scores,
            current_difficulty=current_difficulty,
            answered_count=answered_count,
            followup_count=followup_count
        )

        # 处理结束信号
        if decision.should_finish or decision.intent == IntentType.WRAPUP:
            yield "__IS_FINISHED__\n"
            # 收尾逻辑：生成结束语
            prompt = self._build_prompt_by_intent(
                intent=IntentType.WRAPUP,
                answer=answer,
                rag_ctx="",  # 收尾不需要 RAG
                difficulty=current_difficulty
            )
            yield from self._generate_and_save(session_id, history, prompt, is_final=True)
            return

        # ── Step 4: RAG 检索（按需） ──────────────────────────────────────
        rag_ctx = ""
        if decision.intent in [IntentType.DEEPEN, IntentType.NEXT, IntentType.CORRECT, IntentType.CLARIFY]:
            rag_ctx = self.rag.retrieve_for_followup(question_text, answer, top_k=2)

        # ── Step 5: 构建 Prompt ──────────────────────────────────────────
        followup_prompt = self._build_prompt_by_intent(
            intent=decision.intent,
            answer=answer,
            rag_ctx=self.rag.format_context(rag_ctx,
                                            role="knowledge" if decision.intent == IntentType.CORRECT else "reference"),
            difficulty=decision.next_difficulty
        )

        # ── Step 6: 更新状态（追问计数/难度） ─────────────────────────────
        if decision.should_followup:
            # 追问：不换新题，难度不变，计数 +1
            new_followup_count = followup_count + 1
            self._followup_cache[session_id] = new_followup_count
            # 追问作为当前 turn 的补充，或单独存为 followup_turn（按业务需求）
            # 这里按原逻辑：追问算作新 turn
            self.db_conv.save_turn(session_id, question_text="", student_answer="")  # 占位
        else:
            # 换新题：重置追问计数，更新难度
            self._followup_cache.pop(session_id, None)
            if decision.next_difficulty:
                self._session_levels[session_id] = decision.next_difficulty
            # 创建新 turn（question_text 稍后由 Agent 填充）
            self.db_conv.save_turn(session_id, question_text="", student_answer="")

        # ── Step 7: Agent 流式生成 ───────────────────────────────────────
        yield from self._generate_and_save(session_id, history, followup_prompt, is_final=False)

    def _generate_and_save(
            self,
            session_id: int,
            history: InterviewHistory,
            prompt: str,
            is_final: bool
    ) -> Generator[str, None, None]:
        """统一处理 Agent 流式生成 + 历史记录同步 + 落库"""
        self._sync_history_to_agent(history)

        parts = []
        for chunk in self._agent_stream(prompt):
            parts.append(chunk)
            yield chunk

        ai_text = "".join(parts)

        # 同步回 History（保持 Agent 上下文连贯）
        history.add_assistant(ai_text)

        # 落库：如果是追问/新题，更新最新未回答 turn 的 question_text
        if not is_final:
            latest_turn = self.db_conv.get_unanswered_turn(session_id)
            if latest_turn:
                # 简单起见，直接更新最新未回答的 turn
                # 生产环境建议用 turn_id 精确更新
                self.db_conv.db.execute(
                    "UPDATE interview_turn SET question_text=? WHERE id=?",
                    (ai_text, latest_turn.id)
                )

    # ── 结束面试：生成报告 ─────────────────────────────────────────────────

    def finish_session_stream(self, session_id: int) -> Generator[str, None, None]:
        """流式生成面试报告"""
        turns = self.db_conv.get_session_turns(session_id)
        answered_turns = [t for t in turns if t.student_answer]

        if not answered_turns:
            yield "__SCORE__:0\n"
            yield "本次面试未完成任何题目，无法生成报告。"
            return

        def _get_score(turn: InterviewTurn, key: str, default: float = 0) -> float:
            if turn.scores is None:
                return default
            if isinstance(turn.scores, dict):
                return turn.scores.get(key, default)
            return default

        scores = [_get_score(t, "overall") for t in answered_turns]
        overall_score = round(sum(scores) / len(scores), 2) if scores else 0.0
        yield f"__SCORE__:{overall_score}\n"

        # 构建报告 Prompt
        job = self.db_conv.get_session_job(session_id)
        student = self.db_conv.get_session_student(session_id)

        score_lines = []
        for i, t in enumerate(answered_turns, 1):
            sc = t.scores if isinstance(t.scores, dict) else {}
            score_lines.append(
                f"第{i}题：综合{_get_score(t, 'overall')}/10 "
                f"[技{_get_score(t, 'tech')} 逻{_get_score(t, 'logic')} 深{_get_score(t, 'depth')} 表{_get_score(t, 'clarity')}]"
            )

        report_prompt = _REPORT_PROMPT.format(
            job_name=job["name"],
            student_name=student["name"],
            turn_count=len(answered_turns),
            scores_summary="\n".join(score_lines),
        )

        # 报告生成 + 收集完整内容
        report_content = []
        try:
            stream = self._agent._client.chat.completions.create(
                model=self._agent._model,
                messages=[{"role": "user", "content": report_prompt}],
                temperature=0.3,
                max_tokens=2048,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    report_content.append(content)
                    yield content
        except Exception as e:
            yield f"\n\n[报告生成失败：{e}]\n"

        # 🔧 修复：使用正确的参数名 report
        full_report = "".join(report_content)
        self.db_conv.close_session(session_id, overall_score, report=full_report)

    def confirm_finish(self, session_id: int, overall_score: float, report: str):
        """
        确认结束面试并保存报告（向后兼容接口）

        Args:
            session_id: 会话 ID
            overall_score: 综合评分
            report: 面试报告内容
        """
        self.db_conv.close_session(session_id, overall_score, report=report)
        self.reset_session(session_id)  # 清理内存缓存

    # 可选：保留 confirm_answer 等兼容方法
    def confirm_answer(self, session_id: int, ai_full_text: str, is_finished: bool):
        """
        确认答案（流式版已内置，此方法供兼容保留）
        流式版本已在 submit_answer_stream 中完成历史同步和落库
        """
        pass

    def confirm_first_question(self, session_id: int, full_text: str):
        """
        确认第一题（流式版已内置，此方法供兼容保留）
        """
        pass
    # ── 内部工具方法 ───────────────────────────────────────────────────────

    def _sync_history_to_agent(self, history: InterviewHistory):
        """将 InterviewHistory 同步到 Agent.conversation"""
        self._agent.conversation.clear()
        self._agent.conversation.update_system_prompt(history.system_prompt)
        for msg in history.messages:
            if msg["role"] == "user":
                self._agent.conversation.add_user(msg["content"])
            elif msg["role"] == "assistant":
                self._agent.conversation.add_assistant(msg["content"])

    def _agent_stream(self, user_msg: str) -> Generator[str, None, None]:
        """
        封装 Agent.stream，过滤工具调用提示，支持临时参数覆盖
        """
        for chunk in self._agent.stream(user_msg):
            # 过滤内部工具调用提示，不展示给用户
            if chunk.startswith("\n\n⚙️ **正在调用**"):
                continue
            yield chunk

    def _build_prompt_by_intent(
            self,
            intent: IntentType,
            answer: str,
            rag_ctx: str,
            difficulty: str
    ) -> str:
        """根据决策意图构建 Prompt（模板集中管理）"""
        intent_templates = {
            IntentType.DEEPEN: (
                "候选人回答正确但较浅，请结合参考资料追问底层原理或边界场景。\n"
                "要求：不要换新题，语气自然，像真实面试官。"
            ),
            IntentType.CORRECT: (
                "候选人回答存在技术错误，请结合知识点用引导式追问帮他发现错误。\n"
                "要求：不要直接纠正，用'你确定吗？'等启发式提问。"
            ),
            IntentType.CLARIFY: (
                "候选人回答逻辑混乱，请结合参考资料换角度重新提问。\n"
                "要求：帮他理清思路，问题要更具体。"
            ),
            IntentType.NEXT: (
                f"候选人回答良好，请结合场景素材出一道新题（难度：{difficulty}）。\n"
                "要求：自然过渡，不要生硬切换。"
            ),
            IntentType.WRAPUP: (
                "面试轮数已到，请自然地结束面试对话，给出简短鼓励。"
            ),
        }

        instruction = intent_templates.get(intent, "")

        parts = [
            f"候选人回答：{answer}\n",
            f"【面试官指令】{instruction}\n",
        ]
        if rag_ctx:
            parts.append(rag_ctx)

        return "\n".join(parts)

    # ── 管理接口（供外部调用） ─────────────────────────────────────────────

    def set_model(self, model: str, temperature: Optional[float] = None) -> "InterviewEngine":
        self._agent.set_model(model, temperature)
        return self

    def reset_session(self, session_id: int):
        """清理 session 缓存"""
        self._histories.pop(session_id, None)
        self._session_levels.pop(session_id, None)
        self._followup_cache.pop(session_id, None)
        self.decider.reset_session(session_id)

    @property
    def agent(self) -> Agent:
        """暴露 Agent 供高级用法（谨慎使用）"""
        return self._agent

# ── 对话历史类（轻量版，与 DBConversation 解耦） ───────────────────────────

class InterviewHistory:
    """Session 级对话历史（内存态）"""

    def __init__(self, system_prompt: str = "", max_turns: int = 30):
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.messages: list[dict] = []

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str):
        self.messages.append({"role": "assistant", "content": content or ""})
        self._trim()

    def _trim(self):
        """保持最近 N 轮对话，避免 context 超长"""
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