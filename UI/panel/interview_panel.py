"""
interview_panel.py
面试主界面（组件化重构版）。

架构说明：
  - Footer 组件统一管理 AsrButton + ChatInputBar，面板不直接操作 asr_btn
  - _set_input_enabled 只调用 footer.set_enabled()，彻底杜绝父链 disabled 问题
  - 业务流：信号路由、状态同步、面试会话编排，零耦合组件内部
"""

import json
import os

from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QScrollArea, QFrame,
    QMessageBox, QSizePolicy,
)
from PySide6.QtGui import QKeyEvent
from datetime import datetime

from UI.components import (
    T, ChatBubble, ScoreCardBubble, TypingIndicator,
    ButtonFactory, GLOBAL_QSS, input_qss, combo_qss,
)
from UI.components.footer import Footer
from service.voice_sdk.models import VoiceResult


# ══════════════════════════════════════════════════════════════════════════════
# 面试 Worker（不变）
# ══════════════════════════════════════════════════════════════════════════════

class InterviewWorker(QObject):
    request_start  = Signal(str, int)
    request_answer = Signal(str)
    request_finish = Signal()

    session_started  = Signal(int)
    stream_chunk     = Signal(str)
    eval_received    = Signal(dict)
    is_finished_flag = Signal()
    all_finished     = Signal()
    score_received   = Signal(float)
    stream_done      = Signal(str)
    error_occurred   = Signal(str)

    PHASE_FIRST_Q = "first_q"
    PHASE_ANSWER  = "answer"
    PHASE_REPORT  = "report"

    def __init__(self, engine, db):
        super().__init__()
        self.engine = engine
        self.db = db
        self.session_id: int | None = None
        self._is_finished = False

    def on_start_requested(self, name: str, job_id: int):
        try:
            row = self.db.fetchone("SELECT id FROM student WHERE name=?", (name,))
            student_id = row[0] if row else self.db.execute(
                "INSERT INTO student (name, created_at) VALUES (?,?)",
                (name, datetime.now().isoformat()),
            ).lastrowid

            self.session_id = self.engine.start_session(student_id, job_id)
            self.session_started.emit(self.session_id)

            for token in self.engine.get_first_question_stream(self.session_id):
                self.stream_chunk.emit(token)
            self.stream_done.emit(self.PHASE_FIRST_Q)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def on_answer_requested(self, answer: str):
        if self.session_id is None:
            self.error_occurred.emit("Session not initialized")
            return
        try:
            self._is_finished = False
            for token in self.engine.submit_answer_stream(self.session_id, answer):
                if token.startswith("__EVAL__:"):
                    self.eval_received.emit(json.loads(token[len("__EVAL__:"):].strip()))
                elif token == "__IS_FINISHED__\n":
                    self._is_finished = True
                    self.is_finished_flag.emit()
                elif token == "__FINISHED__\n":
                    self.all_finished.emit()
                    self.stream_done.emit(self.PHASE_ANSWER)
                    return
                elif token.startswith("__ERROR__:"):
                    self.error_occurred.emit(token[len("__ERROR__:"):].strip())
                    return
                else:
                    self.stream_chunk.emit(token)
            self.stream_done.emit(self.PHASE_ANSWER)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def on_finish_requested(self):
        if self.session_id is None:
            self.error_occurred.emit("Session not initialized")
            return
        try:
            overall_score = 0.0
            report_parts: list[str] = []
            for token in self.engine.finish_session_stream(self.session_id):
                if token.startswith("__SCORE__:"):
                    overall_score = float(token[len("__SCORE__:"):].strip())
                    self.score_received.emit(overall_score)
                else:
                    report_parts.append(token)
                    self.stream_chunk.emit(token)
            report_text = "".join(report_parts)
            self.engine.confirm_finish(self.session_id, overall_score, report_text)
            self.stream_done.emit(self.PHASE_REPORT)
        except Exception as e:
            self.error_occurred.emit(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 「↓ 新消息」浮动 Toast（不变）
# ══════════════════════════════════════════════════════════════════════════════

class NewMessageToast(QPushButton):
    def __init__(self, parent: QWidget):
        super().__init__("↓  新消息", parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(110, 34)
        self.setStyleSheet(f"""
            QPushButton {{
                background: {T.NEON}; color: #0a0a0f;
                border: none; border-radius: 17px;
                font-size: 12px; font-weight: 700;
                font-family: {T.FONT}; padding: 0 12px;
            }}
            QPushButton:hover {{ background: {T.PURPLE}; color: #ffffff; }}
        """)
        self.hide()

    def update_position(self, parent_rect) -> None:
        self.move(
            parent_rect.width() - self.width() - 18,
            parent_rect.height() - self.height() - 14,
        )
        self.raise_()


# ══════════════════════════════════════════════════════════════════════════════
# 主面板
# ══════════════════════════════════════════════════════════════════════════════

class InterviewPanel(QWidget):
    def __init__(self, db, engine, parent=None):
        super().__init__(parent)
        self.db     = db
        self.engine = engine
        self._session_id: int | None = None

        # 流式对话状态
        self._is_streaming        = False
        self._current_ai_bubble: ChatBubble | None = None
        self._typing_indicator: TypingIndicator | None = None
        self._stream_phase        = ""
        self._pending_is_finished = False

        # 滚动状态
        self._user_scrolled_up = False
        self._has_new_content  = False

        # 面试 Worker
        self._worker = InterviewWorker(engine, db)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._bind_worker_signals()
        self._thread.start()

        self._build_ui()
        self._bind_footer_signals()

    # ══════════════════════════════════════════════════════════════════════════
    # Worker 信号绑定
    # ══════════════════════════════════════════════════════════════════════════

    def _bind_worker_signals(self) -> None:
        w = self._worker
        w.request_start.connect(w.on_start_requested)
        w.request_answer.connect(w.on_answer_requested)
        w.request_finish.connect(w.on_finish_requested)

        w.session_started.connect(self._on_session_started)
        w.stream_chunk.connect(self._on_chunk)
        w.eval_received.connect(self._on_eval_received)
        w.is_finished_flag.connect(self._on_is_finished_flag)
        w.all_finished.connect(self._on_all_finished)
        w.score_received.connect(self._on_score_received)
        w.stream_done.connect(self._on_stream_done)
        w.error_occurred.connect(self._on_error)

    # ══════════════════════════════════════════════════════════════════════════
    # Footer 信号绑定（录音/输入 → 业务，不直接碰 asr_btn）
    # ══════════════════════════════════════════════════════════════════════════

    def _bind_footer_signals(self) -> None:
        # 文字发送
        self.footer.send_requested.connect(self._on_text_send)

        # ASR 转写完成 → 自动填入并提交
        self.footer.asr_finished.connect(self._on_asr_transcript_ready)

        # 状态栏同步
        self.footer.status_changed.connect(self._update_status)

        # 音频播放委托（Footer 透传 AsrButton 的 play_requested）
        self.footer.play_requested.connect(self._play_audio_file)

        # 错误提示
        self.footer.asr_error.connect(
            lambda e: QMessageBox.critical(self, "转写失败", e)
        )

        # recording_started / recording_stopped 已在 Footer 内部联动 input_bar
        # 面板只需转发给状态栏即可
        self.footer.recording_started.connect(
            lambda: self._update_status("🎙 录音中...")
        )
        self.footer.recording_stopped.connect(
            lambda: self._update_status("录音完成")
        )

    # ══════════════════════════════════════════════════════════════════════════
    # UI 构建
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        self.setStyleSheet(GLOBAL_QSS + input_qss() + combo_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_chat_area(), stretch=1)

        # ✅ 用 Footer 组件替换原手工拼装的 QFrame footer
        self.footer = Footer(min_height=190, max_height=420)
        root.addWidget(self.footer)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet(
            f"QFrame {{ background: {T.SURFACE}; border-bottom: 1px solid {T.BORDER}; }}"
        )
        lay = QHBoxLayout(header)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(12)

        title = QLabel("🎯  模拟面试")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: 800; color: {T.TEXT}; font-family: {T.FONT};"
        )
        lay.addWidget(title)
        lay.addSpacing(20)

        name_lbl = QLabel("姓名")
        name_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("请输入姓名")
        self.name_input.setFixedSize(130, 34)

        job_lbl = QLabel("岗位")
        job_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        self.job_combo = QComboBox()
        self.job_combo.setFixedSize(170, 34)
        self._load_jobs()

        lay.addWidget(name_lbl)
        lay.addWidget(self.name_input)
        lay.addSpacing(8)
        lay.addWidget(job_lbl)
        lay.addWidget(self.job_combo)
        lay.addStretch()

        self.status_lbl = QLabel("准备就绪")
        self.status_lbl.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: 12px; font-family: {T.FONT};"
        )
        lay.addWidget(self.status_lbl)
        lay.addSpacing(12)

        self.start_btn = ButtonFactory.solid("开始面试", T.NEON, height=34)
        self.start_btn.setFixedWidth(90)
        self.start_btn.clicked.connect(self._start_interview)

        self.finish_btn = ButtonFactory.solid("结束面试", T.GREEN, height=34)
        self.finish_btn.setFixedWidth(90)
        self.finish_btn.setEnabled(False)
        self.finish_btn.clicked.connect(self._finish_interview)

        lay.addWidget(self.start_btn)
        lay.addWidget(self.finish_btn)
        return header

    def _build_chat_area(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {T.BG}; border: none; }}"
        )

        self._chat_container = QWidget()
        self._chat_container.setStyleSheet(f"background: {T.BG};")
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(22, 20, 22, 20)
        self._chat_layout.setSpacing(12)
        self._chat_layout.addStretch()

        welcome = ChatBubble("system", "请输入姓名、选择岗位，然后点击「开始面试」")
        self._chat_layout.insertWidget(0, welcome)

        self._scroll.setWidget(self._chat_container)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self._toast = NewMessageToast(self._scroll)
        self._toast.clicked.connect(self._jump_to_bottom)
        self._scroll.resizeEvent = self._on_scroll_resize  # type: ignore[method-assign]

        return self._scroll

    # ══════════════════════════════════════════════════════════════════════════
    # Footer 事件处理（输入/录音 → Worker）
    # ══════════════════════════════════════════════════════════════════════════

    def _on_text_send(self, text: str) -> None:
        """文字输入框发送"""
        self._submit_answer(text)

    def _on_asr_transcript_ready(self, transcript: str) -> None:
        """ASR 转写完成 → 自动填入并提交"""
        if not transcript or self._is_streaming:
            return
        self.footer.set_input_text(transcript)
        self._submit_answer(transcript)

    def _submit_answer(self, answer: str) -> None:
        """统一提交回答入口"""
        answer = answer.strip()
        if not answer or self._is_streaming:
            return
        self.footer.clear_input()
        self._pending_is_finished = False
        self._stream_phase = InterviewWorker.PHASE_ANSWER
        self._is_streaming = True
        self._add_typing_indicator()
        self._set_loading(True, "AI 正在思考...")
        self._set_input_enabled(False)
        self._worker.request_answer.emit(answer)

    # ══════════════════════════════════════════════════════════════════════════
    # Worker 信号处理
    # ══════════════════════════════════════════════════════════════════════════

    def _on_session_started(self, session_id: int) -> None:
        self._session_id = session_id
        self._stream_phase = InterviewWorker.PHASE_FIRST_Q
        self._is_streaming = True
        # ✅ 只禁用 input_bar，不禁用整个 footer
        self._set_input_enabled(False)
        self._add_typing_indicator()
        self._set_loading(True, "AI 面试官正在出题...")

    def _on_chunk(self, chunk: str) -> None:
        if self._typing_indicator is not None:
            self._remove_typing_indicator()

        if self._current_ai_bubble is None:
            enable_tts = self._stream_phase in (
                InterviewWorker.PHASE_FIRST_Q,
                InterviewWorker.PHASE_ANSWER,
            )
            self._current_ai_bubble = ChatBubble("ai", enable_tts=enable_tts)
            self._current_ai_bubble.start_tts()
            self._chat_layout.insertWidget(
                self._chat_layout.count() - 1, self._current_ai_bubble
            )

        self._current_ai_bubble.append_chunk(chunk)
        self._notify_new_content()

    def _on_eval_received(self, data: dict) -> None:
        class _FakeEval:
            def __init__(self, d):
                self.overall_score  = d.get("overall_score", d.get("overall",  0))
                self.tech_score     = d.get("tech_score",    d.get("tech",     0))
                self.logic_score    = d.get("logic_score",   d.get("logic",    0))
                self.depth_score    = d.get("depth_score",   d.get("depth",    0))
                self.clarity_score  = d.get("clarity_score", d.get("clarity",  0))
                self.suggestion     = d.get("suggestion",    d.get("comment",  ""))

        if self._typing_indicator is not None:
            self._chat_layout.removeWidget(self._typing_indicator)
        self._add_score_bubble(_FakeEval(data))
        if self._typing_indicator is not None:
            self._chat_layout.insertWidget(
                self._chat_layout.count() - 1, self._typing_indicator
            )
            self._notify_new_content()

    def _on_is_finished_flag(self) -> None:
        self._pending_is_finished = True

    def _on_all_finished(self) -> None:
        self._add_system_msg("面试已结束，请点击「结束面试」查看报告。")
        self._update_status("题目已完成，请点击「结束面试」生成报告")
        self._set_input_enabled(False)

    def _on_score_received(self, score: float) -> None:
        self._add_system_msg(f"━━  综合得分：{score}/10  ━━")

    def _on_stream_done(self, phase: str) -> None:
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts()
        self._current_ai_bubble = None
        self._is_streaming = False

        if phase == InterviewWorker.PHASE_FIRST_Q:
            self._set_loading(False)
            self._set_input_enabled(True)
            self.finish_btn.setEnabled(True)
            self._add_system_msg("面试已开始，加油！🚀")

        elif phase == InterviewWorker.PHASE_ANSWER:
            self._set_loading(False)
            if self._pending_is_finished:
                self._pending_is_finished = False
                self._set_input_enabled(False)
                self._update_status("题目已完成，请点击「结束面试」生成报告")
            else:
                self._set_input_enabled(True)

        elif phase == InterviewWorker.PHASE_REPORT:
            self._set_loading(False)
            self._add_system_msg("面试完成 ✓")
            self._update_status("面试完成 ✓")
            self.start_btn.setEnabled(True)
            self.name_input.setEnabled(True)
            self.job_combo.setEnabled(True)
            self._session_id = None
            # 报告阶段结束后恢复输入区，方便用户继续交互
            self._set_input_enabled(True)

    def _on_error(self, msg: str) -> None:
        self._remove_typing_indicator()
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts(force=True)
        self._current_ai_bubble = None
        self._is_streaming = False
        self._set_loading(False)
        self._set_input_enabled(True)
        self.start_btn.setEnabled(True)
        self.name_input.setEnabled(True)
        self.job_combo.setEnabled(True)
        QMessageBox.critical(self, "错误", f"发生错误：{msg}")

    # ══════════════════════════════════════════════════════════════════════════
    # 业务控制（Header 按钮）
    # ══════════════════════════════════════════════════════════════════════════

    def _load_jobs(self) -> None:
        self.job_combo.clear()
        try:
            rows = self.db.fetchall("SELECT id, name FROM job_position")
            for jid, name in rows:
                self.job_combo.addItem(name, jid)
        except Exception:
            self.job_combo.addItem("暂无岗位", 0)

    def _start_interview(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            self._show_toast("请输入姓名")
            return
        if self.job_combo.count() == 0 or self.job_combo.currentData() is None:
            self._show_toast("请选择岗位")
            return

        job_id = self.job_combo.currentData()
        self.start_btn.setEnabled(False)
        self.name_input.setEnabled(False)
        self.job_combo.setEnabled(False)
        self._clear_chat()
        self._user_scrolled_up = False
        self._has_new_content  = False
        self._toast.hide()
        self._worker.request_start.emit(name, job_id)

    def _finish_interview(self) -> None:
        self._set_loading(True, "正在生成最终报告...")
        self._set_input_enabled(False)
        self.finish_btn.setEnabled(False)
        self._stream_phase = InterviewWorker.PHASE_REPORT
        self._is_streaming = True
        self._add_system_msg("━━━━━━  面试结束，正在生成报告  ━━━━━━")
        self._add_typing_indicator()
        self._worker.request_finish.emit()

    # ══════════════════════════════════════════════════════════════════════════
    # 音频播放
    # ══════════════════════════════════════════════════════════════════════════

    def _play_audio_file(self, audio_path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(audio_path)  # type: ignore[attr-defined]
            elif os.name == "posix":
                import subprocess
                subprocess.Popen(["xdg-open", audio_path])
            else:
                QMessageBox.information(self, "播放", "当前系统不支持自动播放。")
        except Exception as e:
            QMessageBox.warning(self, "播放失败", f"无法播放音频文件：{e}")

    # ══════════════════════════════════════════════════════════════════════════
    # 滚动 & Toast
    # ══════════════════════════════════════════════════════════════════════════

    def _on_scroll_changed(self, value: int) -> None:
        sb = self._scroll.verticalScrollBar()
        if value >= sb.maximum() - 10:
            self._user_scrolled_up = False
            self._has_new_content  = False
            self._toast.hide()
        else:
            self._user_scrolled_up = True

    def _notify_new_content(self) -> None:
        if self._user_scrolled_up:
            self._has_new_content = True
            self._toast.update_position(self._scroll.rect())
            self._toast.show()
            self._toast.raise_()
        else:
            self._scroll_to_bottom()

    def _jump_to_bottom(self) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._user_scrolled_up = False
        self._has_new_content  = False
        self._toast.hide()

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _on_scroll_resize(self, event) -> None:
        QScrollArea.resizeEvent(self._scroll, event)
        if self._toast.isVisible():
            self._toast.update_position(self._scroll.rect())

    # ══════════════════════════════════════════════════════════════════════════
    # UI 辅助
    # ══════════════════════════════════════════════════════════════════════════

    def _add_typing_indicator(self) -> None:
        if self._typing_indicator is not None:
            return
        self._typing_indicator = TypingIndicator()
        self._chat_layout.insertWidget(
            self._chat_layout.count() - 1, self._typing_indicator
        )
        self._scroll_to_bottom()

    def _remove_typing_indicator(self) -> None:
        if self._typing_indicator is None:
            return
        self._chat_layout.removeWidget(self._typing_indicator)
        self._typing_indicator.stop()
        self._typing_indicator.deleteLater()
        self._typing_indicator = None

    def _add_score_bubble(self, eval_result) -> None:
        bubble = ScoreCardBubble(eval_result)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._notify_new_content()

    def _add_system_msg(self, text: str) -> None:
        bubble = ChatBubble("system", text)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._notify_new_content()

    def _clear_chat(self) -> None:
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _update_status(self, msg: str) -> None:
        self.status_lbl.setText(msg)

    def _set_loading(self, loading: bool, msg: str = "") -> None:
        if loading:
            self.status_lbl.setText(f"⏳  {msg}")
            self.status_lbl.setStyleSheet(
                f"color: {T.NEON}; font-size: 12px; font-weight: 600;"
                f"font-family: {T.FONT};"
            )
        else:
            self.status_lbl.setStyleSheet(
                f"color: {T.TEXT_DIM}; font-size: 12px; font-family: {T.FONT};"
            )

    def _set_input_enabled(self, enabled: bool) -> None:
        """
        ✅ 只调用 footer.set_enabled()，由 Footer 内部决定如何控制子组件。
        Footer.set_enabled() 只操作 input_bar，不碰 asr_btn，
        彻底杜绝父链 disabled 导致按钮无法交互的问题。
        """
        self.footer.set_enabled(enabled)

    def _show_toast(self, msg: str) -> None:
        orig_text  = self.status_lbl.text()
        orig_style = self.status_lbl.styleSheet()
        self.status_lbl.setText(f"⚠️  {msg}")
        self.status_lbl.setStyleSheet(
            f"color: {T.ACCENT}; font-weight: bold; font-size: 12px;"
            f"font-family: {T.FONT};"
        )
        QTimer.singleShot(2000, lambda: (
            self.status_lbl.setText(orig_text),
            self.status_lbl.setStyleSheet(orig_style),
        ))

    # ══════════════════════════════════════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts(force=True)

        # Footer 内部的 AsrButton 会在自己的 closeEvent 里清理线程
        self.footer.close()

        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                if not self._thread.wait(1500):
                    self._thread.terminate()
                    self._thread.wait(300)
        except Exception:
            pass

        super().closeEvent(event)