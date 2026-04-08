# UI/panel/interview_panel.py
"""
面试主界面。

TTS 职责已全部下移至 ChatBubble：
  - 创建 AI 气泡时传入 enable_tts=True
  - 流结束时调用 bubble.stop_tts()
  - 强制关闭时调用 bubble.stop_tts(force=True)
  - Panel 层不再持有任何 TTS 状态或线程
"""

import json
import os
import threading

from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextEdit, QScrollArea, QFrame,
    QMessageBox, QSizePolicy,
)
from PySide6.QtGui import QColor, QKeyEvent
from datetime import datetime

from UI.components import (
    T, ChatBubble, ScoreCardBubble, TypingIndicator,
    ButtonFactory, GLOBAL_QSS, input_qss, combo_qss,
)
from service.voice_sdk.audio.recorder import VoiceRecorder
from service.voice_sdk.stt.client import STTClient
from service.voice_sdk.models import (VoiceResult,RecordBundle)
from service.voice_sdk.audio.player import StreamingAudioPlayer


# ══════════════════════════════════════════════════════════════════════════════
# 语音 Worker（录音）
# ══════════════════════════════════════════════════════════════════════════════

class VoiceWorker(QObject):
    finished = Signal(object)   # RecordBundle
    error    = Signal(str)

    def __init__(self):
        super().__init__()
        self.recorder = VoiceRecorder()

    def stop(self):
        self.recorder.stop()

    def cancel(self):
        self.recorder.cancel()

    def run(self):
        try:
            audio_path, duration = self.recorder.record(60)
            if not audio_path or duration <= 0:
                raise RuntimeError("录音路径或时长无效")
            if not os.path.exists(audio_path):
                raise RuntimeError(f"录音文件不存在：{audio_path}")
            if os.path.getsize(audio_path) < 1000:
                raise RuntimeError("录音文件过小，可能未成功捕获音频")

            bundle = RecordBundle(
                transcript="",
                audio_path=audio_path,
                duration=duration,
                emotion="流畅",
                compressed_audio_file="",
                non_speech=False,
            )
            self.finished.emit(bundle)
        except Exception as e:
            self.error.emit(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ASR Worker（语音转文字）
# ══════════════════════════════════════════════════════════════════════════════

class ASRWorker(QObject):
    finished = Signal(object)   # VoiceResult
    error    = Signal(str)

    def __init__(self, audio_path: str):
        super().__init__()
        self.audio_path = audio_path

    def run(self):
        try:
            client = STTClient()
            result = client.analyze(self.audio_path)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 面试 Worker（LLM 流式）
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
            if row:
                student_id = row[0]
            else:
                cur = self.db.execute(
                    "INSERT INTO student (name, created_at) VALUES (?,?)",
                    (name, datetime.now().isoformat()),
                )
                student_id = cur.lastrowid

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
# 「↓ 新消息」浮动 Toast
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
# 主面板
# ══════════════════════════════════════════════════════════════════════════════

class InterviewPanel(QWidget):
    def __init__(self, db, engine, parent=None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self._session_id: int | None = None

        self._is_streaming         = False
        self._current_ai_bubble: ChatBubble | None = None
        self._typing_indicator: TypingIndicator | None = None
        self._stream_phase         = ""
        self._pending_is_finished  = False

        # 语音录制状态
        self._is_voice_recording    = False
        self._voice_thread: QThread | None = None
        self._voice_worker: VoiceWorker | None = None
        self._pending_voice_bundle: RecordBundle | None = None
        self._is_asr_processing     = False
        self._asr_thread: QThread | None = None
        self._asr_worker: ASRWorker | None = None
        self._pending_voice_auto_send = False
        self._voice_bubble_audio_map:   dict[QObject, str]       = {}
        self._voice_bubble_widget_map:  dict[QObject, ChatBubble]= {}
        self._voice_bubble_default_style: dict[ChatBubble, str]  = {}
        self._playing_bubble: ChatBubble | None = None

        # 滚动状态
        self._user_scrolled_up = False
        self._has_new_content  = False

        # ── 面试 Worker ────────────────────────────────────────────────────────
        self._worker = InterviewWorker(engine, db)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._worker.request_start.connect(self._worker.on_start_requested)
        self._worker.request_answer.connect(self._worker.on_answer_requested)
        self._worker.request_finish.connect(self._worker.on_finish_requested)

        self._worker.session_started.connect(self._on_session_started)
        self._worker.stream_chunk.connect(self._on_chunk)
        self._worker.eval_received.connect(self._on_eval_received)
        self._worker.is_finished_flag.connect(self._on_is_finished_flag)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.score_received.connect(self._on_score_received)
        self._worker.stream_done.connect(self._on_stream_done)
        self._worker.error_occurred.connect(self._on_error)

        self._thread.start()
        self._build_ui()

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
        header.setStyleSheet(f"""
            QFrame {{
                background: {T.SURFACE};
                border-bottom: 1px solid {T.BORDER};
            }}
        """)
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

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setFixedHeight(145)
        footer.setStyleSheet(f"""
            QFrame {{
                background: {T.SURFACE};
                border-top: 1px solid {T.BORDER};
            }}
        """)
        f_lay = QVBoxLayout(footer)
        f_lay.setContentsMargins(22, 12, 22, 12)
        f_lay.setSpacing(8)

        self.status_lbl = QLabel("准备就绪")
        self.status_lbl.setStyleSheet(
            f"color: {T.TEXT_DIM}; font-size: 12px; font-family: {T.FONT};"
        )
        f_lay.addWidget(self.status_lbl)

        # 语音预览条
        self.voice_preview_frame = QFrame()
        self.voice_preview_frame.setStyleSheet(
            f"background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 8px;"
        )
        self.voice_preview_frame.setVisible(False)
        vp_lay = QHBoxLayout(self.voice_preview_frame)
        vp_lay.setContentsMargins(8, 4, 8, 4)
        vp_lay.setSpacing(8)

        self.voice_preview_lbl = QLabel("")
        self.voice_preview_lbl.setStyleSheet(f"color: {T.TEXT}; font-size:12px;")

        self.voice_play_btn = ButtonFactory.solid("▶ 播放", T.TEXT_DIM, height=30)
        self.voice_play_btn.setFixedWidth(96)
        self.voice_play_btn.clicked.connect(self._on_voice_play)
        self.voice_play_btn.setVisible(False)

        self.voice_send_confirm_btn = ButtonFactory.solid("发送语音", T.NEON, height=30)
        self.voice_send_confirm_btn.setFixedWidth(96)
        self.voice_send_confirm_btn.clicked.connect(self._send_voice_bundle)

        self.voice_transcribe_btn = ButtonFactory.solid("转文字", T.GREEN, height=30)
        self.voice_transcribe_btn.setFixedWidth(96)
        self.voice_transcribe_btn.clicked.connect(self._start_asr_transcribe)

        self.voice_cancel_send_btn = ButtonFactory.solid("取消录音", T.ACCENT, height=30)
        self.voice_cancel_send_btn.setFixedWidth(96)
        self.voice_cancel_send_btn.clicked.connect(self._cancel_pending_voice)

        vp_lay.addWidget(self.voice_preview_lbl)
        vp_lay.addWidget(self.voice_play_btn)
        vp_lay.addWidget(self.voice_send_confirm_btn)
        vp_lay.addWidget(self.voice_transcribe_btn)
        vp_lay.addWidget(self.voice_cancel_send_btn)
        f_lay.addWidget(self.voice_preview_frame)

        # 输入行
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.answer_input = QTextEdit()
        self.answer_input.setPlaceholderText("输入你的回答... (Ctrl+Enter 发送)")
        self.answer_input.setFixedHeight(54)
        self.answer_input.setEnabled(False)
        self.answer_input.installEventFilter(self)

        self.voice_btn = ButtonFactory.solid("🎤 语音", T.PURPLE, height=54)
        self.voice_btn.setFixedWidth(90)
        self.voice_btn.setEnabled(False)
        self.voice_btn.clicked.connect(self._on_voice_btn_click)

        self.voice_cancel_btn = ButtonFactory.solid("取消", T.ACCENT, height=54)
        self.voice_cancel_btn.setFixedWidth(80)
        self.voice_cancel_btn.setVisible(False)
        self.voice_cancel_btn.clicked.connect(self._on_voice_cancel)

        self.send_btn = ButtonFactory.solid("发送", T.NEON, height=54)
        self.send_btn.setFixedWidth(80)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_answer)

        input_row.addWidget(self.answer_input)
        input_row.addWidget(self.voice_btn)
        input_row.addWidget(self.voice_cancel_btn)
        input_row.addWidget(self.send_btn)
        f_lay.addLayout(input_row)
        return footer

    # ══════════════════════════════════════════════════════════════════════════
    # 流式 chunk 处理
    # ══════════════════════════════════════════════════════════════════════════

    def _on_chunk(self, chunk: str) -> None:
        if self._typing_indicator is not None:
            self._remove_typing_indicator()

        if self._current_ai_bubble is None:
            # 面试官/助手问题阶段开启 TTS；报告阶段静默
            enable_tts = self._stream_phase in (
                InterviewWorker.PHASE_FIRST_Q,
                InterviewWorker.PHASE_ANSWER,
            )
            self._current_ai_bubble = ChatBubble("ai", enable_tts=enable_tts)
            self._current_ai_bubble.start_tts()  # 幂等：enable_tts=False 时无操作
            self._chat_layout.insertWidget(
                self._chat_layout.count() - 1, self._current_ai_bubble
            )

        self._current_ai_bubble.append_chunk(chunk)
        self._notify_new_content()

    # ══════════════════════════════════════════════════════════════════════════
    # 信号槽
    # ══════════════════════════════════════════════════════════════════════════

    def _on_session_started(self, session_id: int) -> None:
        self._session_id = session_id
        self._stream_phase = InterviewWorker.PHASE_FIRST_Q
        self._is_streaming = True
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
        # 通知当前气泡 TTS 流结束
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
        self._set_loading(False)
        self._set_input_enabled(True)
        self.start_btn.setEnabled(True)
        self.name_input.setEnabled(True)
        self.job_combo.setEnabled(True)
        QMessageBox.critical(self, "错误", f"发生错误：{msg}")

    # ══════════════════════════════════════════════════════════════════════════
    # 业务控制
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

    def _send_answer(self) -> None:
        if self._is_streaming:
            return
        answer = self.answer_input.toPlainText().strip()
        if not answer:
            return
        self.answer_input.clear()
        self._add_bubble("user", answer)
        self._submit_answer_request(answer)

    def _submit_answer_request(self, answer: str) -> None:
        if self._is_streaming:
            return
        self._pending_is_finished = False
        self._stream_phase = InterviewWorker.PHASE_ANSWER
        self._is_streaming = True
        self._add_typing_indicator()
        self._set_loading(True, "AI 正在思考...")
        self._set_input_enabled(False)
        self._worker.request_answer.emit(answer)

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
    # 语音录制
    # ══════════════════════════════════════════════════════════════════════════

    def _on_voice_btn_click(self) -> None:
        if self._is_streaming or self._is_asr_processing:
            return

        if self._is_voice_recording:
            if self._voice_worker:
                self._voice_worker.stop()
            self.voice_btn.setText("停止中...")
            self.voice_btn.setEnabled(False)
            self.voice_cancel_btn.setEnabled(False)
            self.status_lbl.setText("正在结束录音...")
            return

        self._cancel_pending_voice()
        self._is_voice_recording = True
        self.voice_btn.setText("停止录音")
        self.voice_btn.setEnabled(True)
        self.voice_cancel_btn.setVisible(True)
        self.voice_cancel_btn.setEnabled(True)
        self.answer_input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self.status_lbl.setText("录音中... 点击停止录音进入预发送")

        self._shutdown_thread("_voice_thread", "_voice_worker", stop_worker=True)

        self._voice_thread = QThread(self)
        self._voice_worker = VoiceWorker()
        self._voice_worker.moveToThread(self._voice_thread)

        self._voice_thread.started.connect(self._voice_worker.run)
        self._voice_worker.finished.connect(self._on_voice_result)
        self._voice_worker.error.connect(self._on_voice_error)
        self._voice_worker.finished.connect(self._reset_voice_btn)
        self._voice_worker.error.connect(self._reset_voice_btn)
        self._voice_worker.finished.connect(self._voice_thread.quit)
        self._voice_worker.error.connect(self._voice_thread.quit)
        self._voice_thread.finished.connect(self._voice_worker.deleteLater)
        self._voice_thread.finished.connect(self._voice_thread.deleteLater)
        self._voice_thread.finished.connect(self._cleanup_voice_thread)

        self._voice_thread.start()

    def _on_voice_result(self, bundle: RecordBundle) -> None:
        self._reset_voice_btn()
        self._pending_voice_bundle = bundle
        self.voice_preview_lbl.setText(f"语音条：{bundle.duration:.1f}s")
        self.voice_preview_lbl.setToolTip(f"文件：{os.path.basename(bundle.audio_path)}")
        self.voice_preview_frame.setVisible(True)
        self.voice_play_btn.setVisible(True)
        self.voice_send_confirm_btn.setEnabled(True)
        self.voice_transcribe_btn.setEnabled(True)
        self.voice_cancel_send_btn.setEnabled(True)
        self.answer_input.setPlainText("")
        self.answer_input.setPlaceholderText("点击转文字后可在此编辑识别结果")
        self._set_input_enabled(True)
        self.status_lbl.setText("录音完成：可发送语音、转文字，或取消")

    def _on_voice_error(self, error_msg: str) -> None:
        self._reset_voice_btn()
        self._cancel_pending_voice()
        QMessageBox.critical(self, "语音输入失败", error_msg)

    def _on_voice_cancel(self) -> None:
        if self._voice_worker:
            self._voice_worker.cancel()
        self._reset_voice_btn()
        self._cancel_pending_voice()

    def _cleanup_voice_thread(self) -> None:
        self._voice_thread = None
        self._voice_worker = None

    def _on_voice_play(self) -> None:
        if not self._pending_voice_bundle or not os.path.exists(
            self._pending_voice_bundle.audio_path
        ):
            QMessageBox.warning(self, "播放失败", "未找到语音文件。")
            return
        self._play_audio_file(self._pending_voice_bundle.audio_path)

    def _play_audio_file(self, audio_path: str, bubble: ChatBubble | None = None) -> None:
        if bubble is not None:
            self._set_voice_bubble_playing(bubble)
        try:
            if os.name == "nt":
                os.startfile(audio_path)  # type: ignore[attr-defined]
            elif os.name == "posix":
                import subprocess
                subprocess.Popen(["xdg-open", audio_path])
            else:
                QMessageBox.information(self, "播放", "当前系统不支持自动播放。")
        except Exception as e:
            self._clear_playing_bubble_highlight()
            QMessageBox.warning(self, "播放失败", f"无法播放音频文件：{e}")

    def _set_voice_bubble_playing(self, bubble: ChatBubble) -> None:
        self._clear_playing_bubble_highlight()
        self._playing_bubble = bubble
        bubble.bubble.setStyleSheet(f"""
            QFrame#bubble {{
                background: {T.USER_BUBBLE};
                border: 1px solid {T.GREEN};
                border-radius: 18px 18px 4px 18px;
            }}
        """)
        self.status_lbl.setText("正在播放语音...")
        QTimer.singleShot(1500, self._clear_playing_bubble_highlight)

    def _clear_playing_bubble_highlight(self) -> None:
        if self._playing_bubble is None:
            return
        default_style = self._voice_bubble_default_style.get(self._playing_bubble, "")
        self._playing_bubble.bubble.setStyleSheet(default_style)
        self._playing_bubble = None
        if not (self._is_streaming or self._is_voice_recording or self._is_asr_processing):
            self.status_lbl.setText("准备就绪")

    def _send_voice_bundle(self) -> None:
        if not self._pending_voice_bundle:
            return
        if self._is_streaming or self._is_asr_processing:
            return

        bundle = self._pending_voice_bundle
        self._append_voice_bubble(bundle)

        transcript = (bundle.transcript or "").strip()
        if transcript and not transcript.startswith("[未检测到语音内容]"):
            self._clear_pending_voice()
            self.status_lbl.setText("语音已发送，AI 正在思考...")
            self._submit_answer_request(transcript)
            return

        self._pending_voice_auto_send = True
        self.status_lbl.setText("语音已发送，正在自动转写...")
        self._start_asr_transcribe()

    def _start_asr_transcribe(self) -> None:
        if not self._pending_voice_bundle or self._is_asr_processing:
            return
        if not os.path.exists(self._pending_voice_bundle.audio_path):
            QMessageBox.warning(self, "转文字失败", "录音文件不存在")
            return

        self._is_asr_processing = True
        self.voice_transcribe_btn.setEnabled(False)
        self.voice_send_confirm_btn.setEnabled(False)
        self.voice_cancel_send_btn.setEnabled(False)
        self.status_lbl.setText("正在转文字，请稍候...")

        self._shutdown_thread("_asr_thread", "_asr_worker", stop_worker=False)

        self._asr_thread = QThread(self)
        self._asr_worker = ASRWorker(self._pending_voice_bundle.audio_path)
        self._asr_worker.moveToThread(self._asr_thread)

        self._asr_thread.started.connect(self._asr_worker.run)
        self._asr_worker.finished.connect(self._on_asr_result)
        self._asr_worker.error.connect(self._on_asr_error)
        self._asr_worker.finished.connect(self._asr_thread.quit)
        self._asr_worker.error.connect(self._asr_thread.quit)
        self._asr_thread.finished.connect(self._asr_worker.deleteLater)
        self._asr_thread.finished.connect(self._asr_thread.deleteLater)
        self._asr_thread.finished.connect(self._cleanup_asr_thread)

        self._asr_thread.start()

    def _on_asr_result(self, result: VoiceResult) -> None:
        self._is_asr_processing = False
        self.voice_transcribe_btn.setEnabled(True)
        self.voice_send_confirm_btn.setEnabled(True)
        self.voice_cancel_send_btn.setEnabled(True)

        transcript  = (result.transcript or "").strip()
        auto_send   = self._pending_voice_auto_send
        self._pending_voice_auto_send = False

        if transcript and not transcript.startswith("[未检测到语音内容]"):
            if auto_send:
                self._clear_pending_voice()
                self.status_lbl.setText("语音转写完成，AI 正在思考...")
                self._submit_answer_request(transcript)
            else:
                self.answer_input.setPlainText(transcript)
                self.status_lbl.setText("转文字完成：可编辑后点击发送")
        else:
            if auto_send:
                self.status_lbl.setText("语音发送失败：未识别到有效语音")
                QMessageBox.warning(self, "发送失败", "未识别到有效语音，请重试。")
            else:
                self.answer_input.setPlaceholderText("未识别到有效语音，请重试")
                self.status_lbl.setText("未识别到有效语音，可重试转文字或直接发送语音")

        self.answer_input.setFocus()

    def _on_asr_error(self, error_msg: str) -> None:
        self._is_asr_processing = False
        self._pending_voice_auto_send = False
        self.voice_transcribe_btn.setEnabled(True)
        self.voice_send_confirm_btn.setEnabled(True)
        self.voice_cancel_send_btn.setEnabled(True)
        self.status_lbl.setText("转文字失败")
        QMessageBox.critical(self, "转文字失败", error_msg)

    def _cleanup_asr_thread(self) -> None:
        self._asr_thread = None
        self._asr_worker = None

    def _append_voice_bubble(self, bundle: RecordBundle) -> None:
        msg    = f"▶ {bundle.duration:.1f}''"
        bubble = ChatBubble("user", msg)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        self._notify_new_content()
        self._voice_bubble_default_style[bubble] = bubble.bubble.styleSheet()

        targets = [bubble, bubble.bubble, bubble.text_view]
        for target in targets:
            self._voice_bubble_audio_map[target]  = bundle.audio_path
            self._voice_bubble_widget_map[target] = bubble
            target.installEventFilter(self)
            target.setCursor(Qt.PointingHandCursor)
            target.setToolTip("点击播放语音")

    def _cancel_pending_voice(self) -> None:
        if self._is_asr_processing:
            return
        if self._pending_voice_bundle:
            path = self._pending_voice_bundle.audio_path
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            self._pending_voice_bundle = None
        self.voice_preview_frame.setVisible(False)
        self.voice_preview_lbl.setText("")
        self.voice_play_btn.setVisible(False)
        self.answer_input.setPlaceholderText("输入你的回答... (Ctrl+Enter 发送)")
        self.status_lbl.setText("已取消录音")

    def _clear_pending_voice(self) -> None:
        self._pending_voice_bundle = None
        self.voice_preview_frame.setVisible(False)
        self.voice_preview_lbl.setText("")
        self.voice_play_btn.setVisible(False)

    def _reset_voice_btn(self) -> None:
        self._is_voice_recording = False
        self.voice_btn.setText("🎤 语音")
        self.voice_btn.setEnabled(True)
        self.voice_cancel_btn.setVisible(False)

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
        self._clear_playing_bubble_highlight()
        self._voice_bubble_audio_map.clear()
        self._voice_bubble_widget_map.clear()
        self._voice_bubble_default_style.clear()
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
            self.status_lbl.setStyleSheet(
                f"color: {T.TEXT_DIM}; font-size: 12px;"
            )

    def _set_input_enabled(self, enabled: bool) -> None:
        self.answer_input.setEnabled(enabled)
        self.voice_btn.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.answer_input.setFocus()

    def _show_toast(self, msg: str) -> None:
        orig = self.status_lbl.text()
        self.status_lbl.setText(f"⚠️  {msg}")
        self.status_lbl.setStyleSheet(
            f"color: {T.ACCENT}; font-weight: bold; font-size: 12px;"
        )
        QTimer.singleShot(2000, lambda: (
            self.status_lbl.setText(orig),
            self.status_lbl.setStyleSheet(
                f"color: {T.TEXT_DIM}; font-size: 12px;"
            ),
        ))

    def _shutdown_thread(
        self,
        thread_attr: str,
        worker_attr: str,
        stop_worker: bool = False,
    ) -> None:
        thread = getattr(self, thread_attr, None)
        worker = getattr(self, worker_attr, None)

        if stop_worker and worker is not None:
            for method_name in ("stop", "cancel"):
                method = getattr(worker, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass

        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    if not thread.wait(1200):
                        thread.terminate()
                        thread.wait(300)
            except Exception:
                pass

        setattr(self, worker_attr, None)
        setattr(self, thread_attr, None)

    def eventFilter(self, obj, event) -> bool:
        if obj in self._voice_bubble_audio_map and event.type() == QEvent.MouseButtonRelease:
            if hasattr(event, "button") and event.button() == Qt.LeftButton:
                audio_path = self._voice_bubble_audio_map.get(obj, "")
                bubble = self._voice_bubble_widget_map.get(obj)
                if audio_path and os.path.exists(audio_path):
                    self._play_audio_file(audio_path, bubble=bubble)
                else:
                    QMessageBox.warning(self, "播放失败", "语音文件不存在。")
                return True

        if obj is self.answer_input and event.type() == QEvent.KeyPress:
            ke: QKeyEvent = event
            if ke.key() == Qt.Key_Return and ke.modifiers() == Qt.ControlModifier:
                if self.send_btn.isEnabled():
                    self._send_answer()
                return True

        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        # 强制停止当前 AI 气泡的 TTS
        if self._current_ai_bubble is not None:
            self._current_ai_bubble.stop_tts(force=True)

        self._shutdown_thread("_voice_thread", "_voice_worker", stop_worker=True)
        self._shutdown_thread("_asr_thread", "_asr_worker", stop_worker=False)

        try:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                if not self._thread.wait(1500):
                    self._thread.terminate()
                    self._thread.wait(300)
        except Exception:
            pass

        super().closeEvent(event)