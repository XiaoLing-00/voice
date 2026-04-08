"""
面试主界面（组件化重构版）。

架构说明：
  - UI 组件：AsrButton / ChatInputBar / ChatBubble / ScoreCardBubble / TypingIndicator
  - 业务流：仅负责信号路由、状态同步、面试会话编排
  - 零耦合：组件不感知 db/engine，面板不感知组件内部实现
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
# 👇 引入独立组件（按实际路径调整）
from UI.components.button.ASR_button import AsrButton
from UI.components.chat_input_bar import ChatInputBar
from service.voice_sdk.models import VoiceResult


# ══════════════════════════════════════════════════════════════════════════════
# 面试 Worker（LLM 流式，保持不变）
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
# 「↓ 新消息」浮动 Toast（保持不变）
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
        self.move(parent_rect.width() - self.width() - 18,
                  parent_rect.height() - self.height() - 14)
        self.raise_()


# ══════════════════════════════════════════════════════════════════════════════
# 主面板（重构核心）
# ══════════════════════════════════════════════════════════════════════════════

class InterviewPanel(QWidget):
    def __init__(self, db, engine, parent=None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self._session_id: int | None = None

        # 流式对话状态
        self._is_streaming         = False
        self._current_ai_bubble: ChatBubble | None = None
        self._typing_indicator: TypingIndicator | None = None
        self._stream_phase         = ""
        self._pending_is_finished  = False

        # 滚动状态
        self._user_scrolled_up = False
        self._has_new_content  = False

        # 👇 组件实例（纯 UI，零业务逻辑）
        self.asr_btn = AsrButton(self)
        self.input_bar = ChatInputBar(self)

        # 👇 面试 Worker（业务核心）
        self._worker = InterviewWorker(engine, db)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._bind_worker_signals()
        self._thread.start()

        self._build_ui()
        self._bind_component_signals()

    # ══════════════════════════════════════════════════════════════════════════
    # 信号绑定
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

    def _bind_component_signals(self) -> None:
        # ASR 组件联动
        self.asr_btn.status_changed.connect(lambda s: self.status_lbl.setText(s))
        self.asr_btn.recording_started.connect(lambda: self._set_input_enabled(False))
        self.asr_btn.recording_stopped.connect(lambda: self._set_input_enabled(True))
        self.asr_btn.play_requested.connect(self._play_audio_file)
        self.asr_btn.asr_finished.connect(self._on_asr_transcript_ready)
        self.asr_btn.asr_error.connect(lambda e: QMessageBox.critical(self, "转写失败", e))
        self.asr_btn.recording_error.connect(lambda e: QMessageBox.critical(self, "录音失败", e))

        # 输入框组件联动
        self.input_bar.send_requested.connect(self._submit_answer_request)

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
        root.addWidget(self._build_footer())

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet(f"QFrame {{ background: {T.SURFACE}; border-bottom: 1px solid {T.BORDER}; }}")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(12)

        title = QLabel("🎯  模拟面试")
        title.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {T.TEXT}; font-family: {T.FONT};")
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
        self._scroll.setStyleSheet(f"QScrollArea {{ background: {T.BG}; border: none; }}")

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

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setFixedHeight(190)  # 适配 AsrButton + ChatInputBar 高度
        footer.setStyleSheet(f"QFrame {{ background: {T.SURFACE}; border-top: 1px solid {T.BORDER}; }}")
        f_lay = QVBoxLayout(footer)
        f_lay.setContentsMargins(22, 12, 22, 12)
        f_lay.setSpacing(8)

        self.status_lbl = QLabel("准备就绪")
        self.status_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px; font-family: {T.FONT};")
        f_lay.addWidget(self.status_lbl)

        # 👇 嵌入两个独立组件
        f_lay.addWidget(self.asr_btn)
        f_lay.addWidget(self.input_bar)

        return footer

    # ══════════════════════════════════════════════════════════════════════════
    # 业务处理（组件信号 -> Worker）
    # ══════════════════════════════════════════════════════════════════════════

    def _on_asr_transcript_ready(self, transcript: str) -> None:
        """ASR 转写完成后的处理"""
        if self._is_streaming or not transcript:
            return
        # 填入输入框并自动提交（若需手动确认，注释下一行）
        self.input_bar.set_text(transcript)
        self._submit_answer_request(transcript)

    def _submit_answer_request(self, answer: str) -> None:
        """提交回答给 LLM"""
        if self._is_streaming:
            return
        self._pending_is_finished = False
        self._stream_phase = InterviewWorker.PHASE_ANSWER
        self._is_streaming = True
        self._add_typing_indicator()
        self._set_loading(True, "AI 正在思考...")
        self._set_input_enabled(False)
        self._worker.request_answer.emit(answer)

    # ══════════════════════════════════════════════════════════════════════════
    # Worker 信号处理（Worker -> 组件/状态）
    # ══════════════════════════════════════════════════════════════════════════

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

    def _on_session_started(self, session_id: int) -> None:
        self._session_id = session_id
        self._stream_phase = InterviewWorker.PHASE_FIRST_Q
        self._is_streaming = True
        self.asr_btn.setEnabled(False)
        self.input_bar.set_enabled(False)
        self._add_typing_indicator()
        self._set_loading(True, "AI 面试官正在出题...")

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
        self.status_lbl.setText("题目已完成，请点击「结束面试」生成报告")
        self._set_input_enabled(False)

    def _on_score_received(self, score: float) -> None:
        self._add_system_msg(f"━━  综合得分：{score}/10  ━━")

    def _on_stream_done(self, phase: str) -> None:
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts()
        self._current_ai_bubble = None
        self._is_streaming = False
        self.asr_btn.setEnabled(True)
        self.input_bar.set_enabled(True)

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
                self.status_lbl.setText("题目已完成，请点击「结束面试」生成报告")
            else:
                self._set_input_enabled(True)
        elif phase == InterviewWorker.PHASE_REPORT:
            self._set_loading(False)
            self._add_system_msg("面试完成 ✓")
            self.status_lbl.setText("面试完成 ✓")
            self.start_btn.setEnabled(True)
            self.name_input.setEnabled(True)
            self.job_combo.setEnabled(True)
            self._session_id = None

    def _on_error(self, msg: str) -> None:
        self._remove_typing_indicator()
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts(force=True)
        self._current_ai_bubble = None
        self._is_streaming = False
        self.asr_btn.setEnabled(True)
        self.input_bar.set_enabled(True)
        self._set_loading(False)
        self._set_input_enabled(True)
        self.start_btn.setEnabled(True)
        self.name_input.setEnabled(True)
        self.job_combo.setEnabled(True)
        QMessageBox.critical(self, "错误", f"发生错误：{msg}")

    # ══════════════════════════════════════════════════════════════════════════
    # 业务控制（Header 按钮 -> Worker）
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
    # 音频播放委托
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
    # 滚动 & Toast（保持不变）
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
    # UI 辅助（聊天气泡管理）
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

    def _add_bubble(self, role: str, text: str) -> None:
        bubble = ChatBubble(role, text)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._notify_new_content()

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

    def _set_loading(self, loading: bool, msg: str = "") -> None:
        if loading:
            self.status_lbl.setText(f"⏳  {msg}")
            self.status_lbl.setStyleSheet(
                f"color: {T.NEON}; font-size: 12px; font-weight: 600;"
            )
        else:
            self.status_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")

    def _set_input_enabled(self, enabled: bool) -> None:
        """统一控制输入区域启用状态"""
        self.asr_btn.setEnabled(enabled)
        self.input_bar.set_enabled(enabled)

    def _show_toast(self, msg: str) -> None:
        orig = self.status_lbl.text()
        self.status_lbl.setText(f"⚠️  {msg}")
        self.status_lbl.setStyleSheet(
            f"color: {T.ACCENT}; font-weight: bold; font-size: 12px;"
        )
        QTimer.singleShot(2000, lambda: (
            self.status_lbl.setText(orig),
            self.status_lbl.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;"),
        ))

    # ══════════════════════════════════════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        # 停止当前 AI 气泡 TTS
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts(force=True)

        # 👇 组件自行清理内部线程
        self.asr_btn.close()
        self.input_bar.close()

        # 清理 InterviewWorker 线程
        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                if not self._thread.wait(1500):
                    self._thread.terminate()
                    self._thread.wait(300)
        except Exception:
            pass

        super().closeEvent(event)