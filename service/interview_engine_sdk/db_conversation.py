# service/interview_engine_sdk/db_conversation.py
"""
数据库交互层 - 纯数据操作，无业务逻辑

职责：
  - Session 生命周期管理（创建/查询/更新/关闭）
  - Turn 管理（题目/答案/评分持久化）
  - 基础信息查询（学生/岗位）
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass, asdict


@dataclass
class InterviewSession:
    """面试会话数据模型"""
    id: int
    student_id: int
    job_position_id: int
    status: str  # ongoing/finished/cancelled
    started_at: str
    finished_at: Optional[str] = None
    overall_score: Optional[float] = None
    report: Optional[str] = None


@dataclass
class InterviewTurn:
    """面试轮次数据模型"""
    id: int
    session_id: int
    turn_index: int
    question_text: str
    student_answer: str
    scores: Optional[Dict[str, float]] = None  # {"tech": 8, "logic": 7, ...}
    created_at: str = ""


def _safe_json_loads(value: Optional[Union[str, list, dict]]) -> Optional[Any]:
    """
    安全解析 JSON：如果已经是 Python 对象则直接返回，如果是字符串则解析

    解决 SQLite + 某些 ORM 自动反序列化导致的类型不一致问题
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value  # 已经是 Python 对象，直接返回
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _safe_json_dumps(value: Optional[Any]) -> Optional[str]:
    """
    安全序列化 JSON：如果已经是字符串则直接返回，否则序列化

    避免重复序列化导致的错误
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value  # 已经是字符串，直接返回
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


class DBConversation:
    """数据库交互封装类"""

    def __init__(self, db):
        """
        Args:
            db: 数据库连接对象（需支持 execute/fetchone/fetchall 接口）
        """
        self.db = db

    # ── Session 管理 ──────────────────────────────────────────────────────

    def create_session(self, student_id: int, job_position_id: int) -> int:
        """创建新面试会话，返回 session_id"""
        now = datetime.now().isoformat()
        cur = self.db.execute(
            "INSERT INTO interview_session "
            "(student_id, job_position_id, status, started_at) VALUES (?,?,?,?)",
            (student_id, job_position_id, "ongoing", now),
        )
        return cur.lastrowid

    def get_session(self, session_id: int) -> Optional[InterviewSession]:
        """查询会话详情"""
        row = self.db.fetchone(
            "SELECT id, student_id, job_position_id, status, started_at, "
            "finished_at, overall_score, report FROM interview_session WHERE id=?",
            (session_id,),
        )
        if not row:
            return None
        return InterviewSession(*row)

    def update_session_status(
            self, session_id: int, status: str,
            finished_at: Optional[str] = None,
            overall_score: Optional[float] = None,
            report: Optional[str] = None
    ) -> bool:
        """更新会话状态（支持部分字段更新）"""
        updates = ["status = ?"]
        params = [status]

        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(finished_at)
        if overall_score is not None:
            updates.append("overall_score = ?")
            params.append(overall_score)
        if report is not None:
            updates.append("report = ?")
            params.append(report)

        params.append(session_id)
        self.db.execute(
            f"UPDATE interview_session SET {', '.join(updates)} WHERE id=?",
            tuple(params)
        )
        return True

    def close_session(self, session_id: int, overall_score: float, report: str) -> bool:
        """便捷方法：结束会话并写入最终结果"""
        return self.update_session_status(
            session_id, "finished",
            finished_at=datetime.now().isoformat(),
            overall_score=overall_score,
            report=report
        )

    # ── Turn 管理 ────────────────────────────────────────────────────────

    def save_turn(
            self,
            session_id: int,
            question_text: str,
            student_answer: str = "",
            scores: Optional[Dict[str, float]] = None
    ) -> int:
        """保存一轮问答记录"""
        idx = self.db.fetchone(
            "SELECT COALESCE(MAX(turn_index)+1, 0) FROM interview_turn WHERE session_id=?",
            (session_id,),
        )[0]

        # 🔧 修复：使用安全序列化
        scores_json = _safe_json_dumps(scores)

        cur = self.db.execute(
            "INSERT INTO interview_turn "
            "(session_id, turn_index, question_text, student_answer, scores, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                session_id, idx, question_text, student_answer,
                scores_json,
                datetime.now().isoformat()
            ),
        )
        return cur.lastrowid

    def update_turn_answer(self, turn_id: int, answer: str, scores: Dict[str, float]) -> bool:
        """更新学生答案及评分"""
        # 🔧 修复：使用安全序列化
        scores_json = _safe_json_dumps(scores)

        self.db.execute(
            "UPDATE interview_turn SET student_answer=?, scores=? WHERE id=?",
            (answer, scores_json, turn_id)
        )
        return True

    def get_unanswered_turn(self, session_id: int) -> Optional[InterviewTurn]:
        """获取最新未回答的题目"""
        row = self.db.fetchone(
            "SELECT id, session_id, turn_index, question_text, student_answer, scores, created_at "
            "FROM interview_turn WHERE session_id=? AND student_answer='' "
            "ORDER BY turn_index DESC LIMIT 1",
            (session_id,),
        )
        if not row:
            return None
        # 🔧 修复：使用安全解析
        scores = _safe_json_loads(row[5])
        return InterviewTurn(row[0], row[1], row[2], row[3], row[4], scores, row[6])

    def get_session_turns(self, session_id: int) -> List[InterviewTurn]:
        """获取会话所有轮次（按顺序）"""
        rows = self.db.fetchall(
            "SELECT id, session_id, turn_index, question_text, student_answer, scores, created_at "
            "FROM interview_turn WHERE session_id=? ORDER BY turn_index",
            (session_id,),
        )
        turns = []
        for row in rows:
            # 🔧 修复：使用安全解析
            scores = _safe_json_loads(row[5])
            turns.append(InterviewTurn(row[0], row[1], row[2], row[3], row[4], scores, row[6]))
        return turns

    def count_answered_turns(self, session_id: int) -> int:
        """统计已回答的轮次数"""
        return self.db.fetchone(
            "SELECT COUNT(*) FROM interview_turn WHERE session_id=? AND student_answer!=''",
            (session_id,),
        )[0]

    # ── 基础信息查询 ─────────────────────────────────────────────────────

    def get_job_position(self, job_position_id: int) -> Optional[Dict[str, Any]]:
        """查询岗位信息"""
        row = self.db.fetchone(
            "SELECT id, name, tech_stack FROM job_position WHERE id=?",
            (job_position_id,),
        )
        if not row:
            return None
        # 🔧 修复：使用安全解析
        return {
            "id": row[0],
            "name": row[1],
            "tech_stack": _safe_json_loads(row[2]) or []
        }

    def get_student(self, student_id: int) -> Optional[Dict[str, Any]]:
        """查询学生信息"""
        row = self.db.fetchone(
            "SELECT id, name FROM student WHERE id=?",
            (student_id,),
        )
        if not row:
            return None
        return {"id": row[0], "name": row[1]}

    def get_session_job(self, session_id: int) -> Optional[Dict[str, Any]]:
        """便捷方法：通过 session 查关联岗位"""
        row = self.db.fetchone(
            "SELECT jp.id, jp.name, jp.tech_stack FROM interview_session s "
            "JOIN job_position jp ON s.job_position_id=jp.id WHERE s.id=?",
            (session_id,),
        )
        if not row:
            return None
        # 🔧 修复：使用安全解析
        return {
            "id": row[0],
            "name": row[1],
            "tech_stack": _safe_json_loads(row[2]) or []
        }

    def get_session_student(self, session_id: int) -> Optional[Dict[str, Any]]:
        """便捷方法：通过 session 查关联学生"""
        row = self.db.fetchone(
            "SELECT st.id, st.name FROM interview_session s "
            "JOIN student st ON s.student_id=st.id WHERE s.id=?",
            (session_id,),
        )
        if not row:
            return None
        return {"id": row[0], "name": row[1]}