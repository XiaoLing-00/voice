# service/evaluator.py
"""
LLM 答案评估器
对学生的面试回答进行多维度打分，返回结构化评估结果。
使用原生 OpenAI SDK（避免 langchain_openai → transformers → torch 依赖链）
"""
import json
import os
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


@dataclass
class EvalResult:
    tech_score: int = 0
    logic_score: int = 0
    depth_score: int = 0
    clarity_score: int = 0
    overall_score: float = 0.0
    strengths: str = ""
    weaknesses: str = ""
    suggestion: str = ""
    raw_json: str = ""

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
    _WEIGHTS = {"tech": 0.35, "logic": 0.25, "depth": 0.25, "clarity": 0.15}

    def __init__(self, model: str = "qwen-plus"):
        self._client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self._model = model

    def evaluate(
        self,
        question: str,
        answer: str,
        job_name: str = "",
        context: str = "",
    ) -> EvalResult:
        user_content = self._build_prompt(question, answer, job_name, context)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0.1,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": _EVAL_SYSTEM},
                    {"role": "user",   "content": user_content},
                ],
            )
            return self._parse(response.choices[0].message.content or "")
        except Exception as e:
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


def evaluate_voice_answer(voice_result):
    """基于语音情绪和转写结果做简单评分与追问决策。"""
    emotion = getattr(voice_result, "emotion", "")
    score = 80 if emotion in ["自信", "流畅"] else 60
    if score >= 90:
        followup_decision = "harder"
    elif score >= 60:
        followup_decision = "easier"
    elif score >= 30:
        followup_decision = "no_followup"
    else:
        followup_decision = "end"

    return {
        "score": score,
        "followup_decision": followup_decision,
    }
