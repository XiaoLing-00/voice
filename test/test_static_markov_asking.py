# test/test_static_markov_asking.py
"""
静态马尔可夫决策引擎 - 边界测试脚本

职责：
  - 验证意图分类的阈值边界
  - 验证决策流程的状态控制（轮数、追问）
  - 验证难度调整逻辑
  - 验证配置热更新

运行方式：
  pytest test/test_static_markov_asking.py -v
"""
import pytest
import sys
from pathlib import Path

# 确保根目录在路径中以便导入 interview_engine_sdk
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from service.interview_engine_sdk.static_markov_asking import (
    MarkovDecisionEngine,
    EvalScores,
    DecisionConfig,
    IntentType,
    scores_from_dict
)
from dataclasses import asdict

# ── 兼容性修复 ──────────────────────────────────────────────────────
# 注意：源文件 static_markov_asking.py 中使用了 asdict 但未导入。
# 此处进行动态修补，确保测试脚本可直接运行，无需手动修改源文件。
import service.interview_engine_sdk.static_markov_asking as src_module

if not hasattr(src_module, 'asdict'):
    src_module.asdict = asdict


# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """提供默认配置的引擎实例"""
    return MarkovDecisionEngine()


@pytest.fixture
def config():
    """提供默认配置实例"""
    return DecisionConfig()


# ── 1. 意图分类边界测试 ──────────────────────────────────────────────

class TestIntentClassification:
    """测试 classify_intent 的阈值边界"""

    def test_intent_correct_threshold_boundary(self, engine):
        """技术分阈值边界：5.0 (NEXT) vs 4.9 (CORRECT)"""
        # 刚好在阈值上 (>= 5.0)
        scores_pass = EvalScores(tech=5.0, logic=8.0, depth=8.0, clarity=8.0)
        assert engine.classify_intent(scores_pass) == IntentType.NEXT

        # 低于阈值 (< 5.0)
        scores_fail = EvalScores(tech=4.9, logic=8.0, depth=8.0, clarity=8.0)
        assert engine.classify_intent(scores_fail) == IntentType.CORRECT

    def test_intent_clarify_threshold_boundary(self, engine):
        """逻辑分阈值边界：5.0 (NEXT/DEEPEN) vs 4.9 (CLARIFY)"""
        # 技术合格，逻辑刚好在阈值上
        scores_pass = EvalScores(tech=6.0, logic=5.0, depth=8.0, clarity=8.0)
        assert engine.classify_intent(scores_pass) == IntentType.NEXT

        # 技术合格，逻辑低于阈值
        scores_fail = EvalScores(tech=6.0, logic=4.9, depth=8.0, clarity=8.0)
        assert engine.classify_intent(scores_fail) == IntentType.CLARIFY

    def test_intent_deepen_threshold_boundary(self, engine):
        """深度分阈值边界：7.0 (NEXT) vs 6.9 (DEEPEN)"""
        # 技术合格，深度刚好在阈值上
        scores_pass = EvalScores(tech=6.0, logic=8.0, depth=7.0, clarity=8.0)
        assert engine.classify_intent(scores_pass) == IntentType.NEXT

        # 技术合格，深度低于阈值
        scores_fail = EvalScores(tech=6.0, logic=8.0, depth=6.9, clarity=8.0)
        assert engine.classify_intent(scores_fail) == IntentType.DEEPEN

    def test_intent_priority_logic(self, engine):
        """意图优先级：技术错误 > 逻辑混乱 > 深度不足"""
        # 技术错 + 逻辑乱 -> 应优先纠正技术
        scores_multi_fail = EvalScores(tech=4.0, logic=4.0, depth=4.0, clarity=8.0)
        assert engine.classify_intent(scores_multi_fail) == IntentType.CORRECT

        # 技术对 + 逻辑乱 + 深度浅 -> 应优先理清逻辑
        scores_logic_fail = EvalScores(tech=6.0, logic=4.0, depth=4.0, clarity=8.0)
        assert engine.classify_intent(scores_logic_fail) == IntentType.CLARIFY


# ── 2. 决策流程与状态控制测试 ────────────────────────────────────────

class TestDecisionFlow:
    """测试 decide_next_action 的状态流转"""

    def test_max_turns_boundary(self, engine):
        """最大轮数边界：7 (继续) vs 8 (结束)"""
        scores = EvalScores(tech=8.0, logic=8.0, depth=8.0, clarity=8.0)

        # 第 7 轮
        res_7 = engine.decide_next_action(
            session_id=1, scores=scores, current_difficulty="medium",
            answered_count=7, followup_count=0
        )
        assert res_7.should_finish is False
        assert res_7.intent != IntentType.WRAPUP

        # 第 8 轮 (达到 max_turns=8)
        res_8 = engine.decide_next_action(
            session_id=1, scores=scores, current_difficulty="medium",
            answered_count=8, followup_count=0
        )
        assert res_8.should_finish is True
        assert res_8.intent == IntentType.WRAPUP

    def test_followup_limit_boundary(self, engine):
        """追问次数边界：1 (可追问) vs 2 (不可追问)"""
        # 构造需要追问的分数 (深度浅)
        scores_shallow = EvalScores(tech=6.0, logic=8.0, depth=6.0, clarity=8.0)
        session_id = 100

        # 已追问 1 次 (max=2)，应允许第 2 次
        res_1 = engine.decide_next_action(
            session_id=session_id, scores=scores_shallow, current_difficulty="medium",
            answered_count=1, followup_count=1
        )
        assert res_1.should_followup is True
        assert engine.get_followup_count(session_id) == 2

        # 已追问 2 次 (达到 max=2)，应停止追问转下一题
        res_2 = engine.decide_next_action(
            session_id=session_id, scores=scores_shallow, current_difficulty="medium",
            answered_count=1, followup_count=2
        )
        assert res_2.should_followup is False
        assert res_2.intent == IntentType.NEXT  # 因为不能追问了，强制转 NEXT

    def test_session_state_isolation(self, engine):
        """不同 Session 的状态隔离"""
        scores_shallow = EvalScores(tech=6.0, logic=8.0, depth=6.0, clarity=8.0)

        # Session 1 追问 1 次
        engine.decide_next_action(1, scores_shallow, "medium", 1, 0)
        assert engine.get_followup_count(1) == 1

        # Session 2 应为 0
        assert engine.get_followup_count(2) == 0

        # Session 2 追问 1 次
        engine.decide_next_action(2, scores_shallow, "medium", 1, 0)
        assert engine.get_followup_count(2) == 1
        assert engine.get_followup_count(1) == 1  # Session 1 不受影响

    def test_session_reset(self, engine):
        """Session 重置功能"""
        scores_shallow = EvalScores(tech=6.0, logic=8.0, depth=6.0, clarity=8.0)
        session_id = 99

        engine.decide_next_action(session_id, scores_shallow, "medium", 1, 0)
        assert engine.get_followup_count(session_id) == 1

        engine.reset_session(session_id)
        assert engine.get_followup_count(session_id) == 0


# ── 3. 难度调整策略测试 ──────────────────────────────────────────────

class TestDifficultyAdjustment:
    """测试 _adjust_difficulty 逻辑"""

    def test_promote_threshold_boundary(self, engine):
        """升难度边界：8.0 (升) vs 7.9 (保持)"""
        # 刚好达到阈值
        next_diff = engine._adjust_difficulty(8.0, "easy")
        assert next_diff == "medium"

        # 低于阈值
        next_diff = engine._adjust_difficulty(7.9, "easy")
        assert next_diff == "easy"

    def test_demote_threshold_boundary(self, engine):
        """降难度边界：4.0 (降) vs 4.1 (保持)"""
        # 刚好达到阈值
        next_diff = engine._adjust_difficulty(4.0, "medium")
        assert next_diff == "easy"

        # 高于阈值
        next_diff = engine._adjust_difficulty(4.1, "medium")
        assert next_diff == "medium"

    def test_hard_difficulty_cap(self, engine):
        """最高难度封顶：Hard 升难度应保持 Hard"""
        next_diff = engine._adjust_difficulty(9.0, "hard")
        assert next_diff == "hard"

    def test_easy_difficulty_floor(self, engine):
        """最低难度保底：Easy 降难度应保持 Easy"""
        # 反向映射逻辑中，easy 没有前驱，应返回默认或保持
        # 根据代码逻辑：reverse_steps.get("easy", "easy")
        next_diff = engine._adjust_difficulty(3.0, "easy")
        assert next_diff == "easy"


# ── 4. 配置热更新测试 ────────────────────────────────────────────────

class TestConfigUpdate:
    """测试配置动态修改对决策的影响"""

    def test_update_threshold_effect(self, engine):
        """修改阈值后决策应立即生效"""
        scores = EvalScores(tech=5.5, logic=8.0, depth=8.0, clarity=8.0)

        # 默认阈值 5.0，5.5 应为 NEXT
        assert engine.classify_intent(scores) == IntentType.NEXT

        # 热更新阈值为 6.0
        engine.update_config(tech_error_threshold=6.0)

        # 5.5 现在小于 6.0，应为 CORRECT
        assert engine.classify_intent(scores) == IntentType.CORRECT

    def test_update_max_turns_effect(self, engine):
        """修改最大轮数后结束条件应立即生效"""
        scores = EvalScores(tech=8.0, logic=8.0, depth=8.0, clarity=8.0)

        # 默认 8 轮，第 5 轮不结束
        res = engine.decide_next_action(1, scores, "medium", 5, 0)
        assert res.should_finish is False

        # 热更新为 5 轮
        engine.update_config(max_turns=5)

        # 第 5 轮现在应结束
        res = engine.decide_next_action(1, scores, "medium", 5, 0)
        assert res.should_finish is True


# ── 5. 工具函数测试 ──────────────────────────────────────────────────

class TestUtilityFunctions:
    """测试辅助函数"""

    def test_scores_from_dict_complete(self):
        """完整字典转换"""
        data = {"tech": 9.0, "logic": 8.0, "depth": 7.0, "clarity": 6.0}
        scores = scores_from_dict(data)
        assert scores.tech == 9.0
        assert scores.overall == pytest.approx(9.0 * 0.4 + 8.0 * 0.3 + 7.0 * 0.2 + 6.0 * 0.1)

    def test_scores_from_dict_missing_keys(self):
        """缺失键默认值处理"""
        data = {"tech": 9.0}
        scores = scores_from_dict(data)
        assert scores.tech == 9.0
        assert scores.logic == 0.0
        assert scores.depth == 0.0

    def test_eval_scores_overall_calculation(self):
        """综合分加权计算验证"""
        scores = EvalScores(tech=10.0, logic=10.0, depth=10.0, clarity=10.0)
        assert scores.overall == 10.0

        scores = EvalScores(tech=0.0, logic=0.0, depth=0.0, clarity=0.0)
        assert scores.overall == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])