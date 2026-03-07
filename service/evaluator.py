# service/evaluator.py
"""
LLM 答案评估器
对学生的面试回答进行多维度打分，返回结构化评估结果。
"""
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


@dataclass
class EvalResult:
    tech_score: int = 0        # 技术正确性 0-10
    logic_score: int = 0       # 逻辑严谨性 0-10
    depth_score: int = 0       # 知识深度   0-10
    clarity_score: int = 0     # 表达清晰度 0-10
    overall_score: float = 0.0 # 综合得分（加权均值）
    strengths: str = ""        # 亮点
    weaknesses: str = ""       # 不足
    suggestion: str = ""       # 改进建议
    raw_json: str = ""         # 原始 JSON 字符串（调试用）

    def to_dict(self) -> dict:
        return {
            "tech": self.tech_score,
            "logic": self.logic_score,
            "depth": self.depth_score,
            "clarity": self.clarity_score,
            "overall": self.overall_score,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "suggestion": self.suggestion,
        }

    def to_display(self) -> str:
        return (
            f"📊 评分结果\n"
            f"  技术正确性: {self.tech_score}/10\n"
            f"  逻辑严谨性: {self.logic_score}/10\n"
            f"  知识深度:   {self.depth_score}/10\n"
            f"  表达清晰度: {self.clarity_score}/10\n"
            f"  综合得分:   {self.overall_score:.1f}/10\n\n"
            f"✅ 亮点：{self.strengths}\n"
            f"⚠️ 不足：{self.weaknesses}\n"
            f"💡 建议：{self.suggestion}"
        )


_EVAL_SYSTEM = """你是一位严格但公正的技术面试评估官。
请对候选人的回答进行多维度评分，必须严格按照以下 JSON 格式返回，不得包含任何其他内容：

{
  "tech_score": <0-10整数，技术答案的正确性和准确性>,
  "logic_score": <0-10整数，回答的逻辑结构和条理性>,
  "depth_score": <0-10整数，对知识点理解的深度和广度>,
  "clarity_score": <0-10整数，表达是否清晰易懂>,
  "strengths": "<简短描述回答的亮点，50字以内>",
  "weaknesses": "<简短描述主要不足，50字以内>",
  "suggestion": "<具体的改进建议，100字以内>"
}

评分标准：
- 0-3：完全错误或严重缺失
- 4-5：基本了解但有明显错误
- 6-7：正确但不够深入
- 8-9：掌握扎实，有自己的理解
- 10：完美，有深度洞察"""


class AnswerEvaluator:
    """
    面试回答评估器

    用法：
        evaluator = AnswerEvaluator()
        result = evaluator.evaluate(
            question="请解释 Java 中的 synchronized 关键字",
            answer="synchronized 用于线程同步...",
            job_name="Java 后端工程师",
            context="【参考知识库】..."   # 可选，来自 RAG
        )
    """

    # 各维度权重
    _WEIGHTS = {"tech": 0.35, "logic": 0.25, "depth": 0.25, "clarity": 0.15}

    def __init__(self, model: str = "qwen-plus"):
        self._llm = ChatOpenAI(
            model=model,
            temperature=0.1,
            max_tokens=512,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        )

    def evaluate(
        self,
        question: str,
        answer: str,
        job_name: str = "",
        context: str = "",
    ) -> EvalResult:
        """调用 LLM 评估并返回 EvalResult"""
        user_content = self._build_prompt(question, answer, job_name, context)
        try:
            response = self._llm.invoke([
                SystemMessage(content=_EVAL_SYSTEM),
                HumanMessage(content=user_content),
            ])
            return self._parse(response.content)
        except Exception as e:
            # 降级：返回默认评分，避免整个流程崩溃
            result = EvalResult()
            result.suggestion = f"评估服务暂时不可用: {e}"
            return result

    def _build_prompt(self, question: str, answer: str, job_name: str, context: str) -> str:
        parts = []
        if job_name:
            parts.append(f"岗位：{job_name}")
        parts.append(f"面试题：{question}")
        parts.append(f"候选人回答：{answer if answer.strip() else '（未作答）'}")
        if context:
            parts.append(context)
        return "\n\n".join(parts)

    def _parse(self, raw: str) -> EvalResult:
        # 去掉可能的 markdown 代码块
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            result = EvalResult(raw_json=raw)
            result.suggestion = "解析评估结果失败，请重试"
            return result

        t = int(data.get("tech_score", 5))
        l = int(data.get("logic_score", 5))
        d = int(data.get("depth_score", 5))
        c = int(data.get("clarity_score", 5))
        overall = round(
            t * self._WEIGHTS["tech"]
            + l * self._WEIGHTS["logic"]
            + d * self._WEIGHTS["depth"]
            + c * self._WEIGHTS["clarity"],
            2,
        )
        return EvalResult(
            tech_score=t,
            logic_score=l,
            depth_score=d,
            clarity_score=c,
            overall_score=overall,
            strengths=data.get("strengths", ""),
            weaknesses=data.get("weaknesses", ""),
            suggestion=data.get("suggestion", ""),
            raw_json=raw,
        )
