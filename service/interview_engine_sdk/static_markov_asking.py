# service/interview_engine_sdk/static_markov_asking.py
"""
静态马尔可夫决策引擎 - 面试流程控制逻辑

职责：
  - 意图分类（deepen/correct/clarify/next）
  - 难度动态调整策略
  - 追问次数控制
  - 结束条件判断

设计原则：
  - 纯函数优先，无状态，便于测试
  - 配置项集中管理，支持热更新
  - 决策结果结构化输出
"""
from __future__ import annotations

from dataclasses import dataclass, field , asdict
from typing import Literal, Optional, Dict
from enum import Enum


class IntentType(str, Enum):
    """追问意图类型"""
    DEEPEN = "deepen"  # 答对但浅，追问原理
    CORRECT = "correct"  # 答错，引导纠正
    CLARIFY = "clarify"  # 逻辑乱，重新提问
    NEXT = "next"  # 回答良好，换新题
    WRAPUP = "wrapup"  # 结束面试


@dataclass
class EvalScores:
    """评分数据模型（解耦 evaluator 依赖）"""
    tech: float = 0.0  # 技术准确性 [0-10]
    logic: float = 0.0  # 逻辑清晰度 [0-10]
    depth: float = 0.0  # 回答深度 [0-10]
    clarity: float = 0.0  # 表达清晰度 [0-10]

    @property
    def overall(self) -> float:
        """加权综合分（可配置权重）"""
        return round(
            self.tech * 0.4 + self.logic * 0.3 + self.depth * 0.2 + self.clarity * 0.1,
            2
        )


@dataclass
class DecisionConfig:
    """决策参数配置（支持外部注入）"""
    max_turns: int = 8  # 最大轮数
    max_followups: int = 2  # 单题最大追问次数
    depth_threshold: float = 7.0  # 深度分阈值（低于则追问）
    tech_error_threshold: float = 5.0  # 技术分阈值（低于则纠正）
    logic_confusion_threshold: float = 5.0  # 逻辑分阈值

    # 难度调整策略
    difficulty_steps: Dict[str, str] = field(default_factory=lambda: {
        "easy": "medium",
        "medium": "hard",
        "hard": "hard"  # 保持最高难度
    })
    promote_threshold: float = 8.0  # 综合分≥此值则升难度
    demote_threshold: float = 4.0  # 综合分≤此值则降难度


@dataclass
class DecisionResult:
    """决策输出结果"""
    intent: IntentType
    next_difficulty: Optional[str] = None  # 下一题难度
    should_followup: bool = False  # 是否追问
    followup_reason: str = ""  # 追问原因说明
    should_finish: bool = False  # 是否结束面试
    metadata: Dict = field(default_factory=dict)  # 调试信息


class MarkovDecisionEngine:
    """静态马尔可夫决策引擎"""

    def __init__(self, config: Optional[DecisionConfig] = None):
        self.config = config or DecisionConfig()
        # 内部状态：session_id → 追问计数
        self._followup_counts: Dict[int, int] = {}

    # ── 核心决策方法 ──────────────────────────────────────────────────────

    def classify_intent(self, scores: EvalScores) -> IntentType:
        """
        根据评分分类追问意图（纯函数，无副作用）

        决策树：
          1. 技术分过低 → correct（先纠正错误）
          2. 逻辑分过低 → clarify（先理清思路）
          3. 深度分不足但技术合格 → deepen（追问原理）
          4. 其他 → next（正常推进）
        """
        if scores.tech < self.config.tech_error_threshold:
            return IntentType.CORRECT
        if scores.logic < self.config.logic_confusion_threshold:
            return IntentType.CLARIFY
        if scores.depth < self.config.depth_threshold and scores.tech >= 6.0:
            return IntentType.DEEPEN
        return IntentType.NEXT

    def decide_next_action(
            self,
            session_id: int,
            scores: EvalScores,
            current_difficulty: str,
            answered_count: int,
            followup_count: int = 0
    ) -> DecisionResult:
        """
           综合决策：意图 + 难度 + 是否追问 + 是否结束
           Args:
               session_id: 会话ID（用于追踪追问次数）
               scores: 当前回答评分
               current_difficulty: 当前题目难度
               answered_count: 已回答轮数
               followup_count: 当前题已追问次数
           Returns:
               DecisionResult: 结构化决策结果

           """
        # 1. 基础意图分类
        intent = self.classify_intent(scores)

        # 2. 检查是否达到最大轮数 → 强制结束
        if answered_count >= self.config.max_turns:
            return DecisionResult(
                intent=IntentType.WRAPUP,
                should_finish=True,
                metadata={"reason": "max_turns_reached"}
            )

        # 3. 追问决策
        # 修改逻辑：只要是 DEEPEN 意图，就判断次数
        if intent == IntentType.DEEPEN:
            if followup_count < self.config.max_followups:
                # 更新追问计数
                self._followup_counts[session_id] = followup_count + 1
                return DecisionResult(
                    intent=IntentType.DEEPEN,
                    next_difficulty=current_difficulty,  # 追问不升难度
                    should_followup=True,
                    followup_reason=f"深度分{scores.depth}<{self.config.depth_threshold}",
                    metadata={"followup_count": followup_count + 1}
                )
            else:
                # 【核心修复】：追问超限，强制将意图扭转为 NEXT
                intent = IntentType.NEXT

        # 4. 难度调整策略（仅换新题或追问结束时生效）
        next_difficulty = self._adjust_difficulty(
            scores.overall, current_difficulty
        )

        return DecisionResult(
            intent=intent,
            next_difficulty=next_difficulty,
            should_followup=False,
            metadata={
                "score_breakdown": asdict(scores),  # 现在可以正常调用 asdict 了
                "difficulty_transition": f"{current_difficulty}→{next_difficulty}"
            }
        )

    def _adjust_difficulty(self, overall_score: float, current: str) -> str:
        """根据综合分动态调整难度"""
        if overall_score >= self.config.promote_threshold:
            return self.config.difficulty_steps.get(current, current)
        if overall_score <= self.config.demote_threshold:
            # 降级逻辑：hard→medium→easy
            reverse_steps = {v: k for k, v in self.config.difficulty_steps.items()}
            return reverse_steps.get(current, "easy")
        return current  # 保持当前难度

    # ── 状态管理 ─────────────────────────────────────────────────────────

    def reset_session(self, session_id: int):
        """重置 session 的追问计数"""
        self._followup_counts.pop(session_id, None)

    def get_followup_count(self, session_id: int) -> int:
        """获取当前追问次数"""
        return self._followup_counts.get(session_id, 0)

    # ── 配置管理 ─────────────────────────────────────────────────────────

    def update_config(self, **kwargs):
        """热更新配置参数"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def get_config(self) -> DecisionConfig:
        """获取当前配置（用于调试/日志）"""
        return self.config


# 工具函数：从 dict 创建 EvalScores（兼容旧接口）
def scores_from_dict(data: Dict[str, float]) -> EvalScores:
    """兼容旧版 evaluator 输出格式"""
    return EvalScores(
        tech=data.get("tech", 0),
        logic=data.get("logic", 0),
        depth=data.get("depth", 0),
        clarity=data.get("clarity", 0)
    )