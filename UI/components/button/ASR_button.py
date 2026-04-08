import os
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QMessageBox, QSizePolicy
)
from service.voice_sdk.audio.recorder import VoiceRecorder
from service.voice_sdk.stt.client import STTClient
from service.voice_sdk.models import  RecordBundle
from ..ButtonFactory import ButtonFactory,T
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
# AsrButton 组件
# ══════════════════════════════════════════════════════════════════════════════

class AsrButton(QWidget):
    # ── 信号定义 ──────────────────────────────────────────────────────────────
    recording_started = Signal()
    recording_stopped = Signal()
    recording_error = Signal(str)

    audio_recorded = Signal(object)  # RecordBundle
    asr_started = Signal()
    asr_finished = Signal(str)  # transcript
    asr_error = Signal(str)

    play_requested = Signal(str)  # audio_path
    bundle_sent = Signal(object)  # RecordBundle
    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_recording = False
        self._is_asr_processing = False
        self._pending_bundle = None
        self._auto_transcribe = False

        # 线程与 Worker
        self._voice_thread: QThread | None = None
        self._voice_worker: VoiceWorker | None = None
        self._asr_thread: QThread | None = None
        self._asr_worker: ASRWorker | None = None

        self._build_ui()
        self._set_state("idle")

    # ══════════════════════════════════════════════════════════════════════════
    # UI 构建
    # ══════════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(8)

        # 主控制行
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        self.btn_record = ButtonFactory.solid("🎤 语音", T.PURPLE, height=48, width=90)
        self.btn_record.clicked.connect(self._on_main_btn_clicked)

        self.btn_cancel_rec = ButtonFactory.solid("取消", T.ACCENT, height=48, width=80)
        self.btn_cancel_rec.setVisible(False)
        self.btn_cancel_rec.clicked.connect(self._on_cancel_recording)

        ctrl_row.addWidget(self.btn_record)
        ctrl_row.addWidget(self.btn_cancel_rec)
        main_lay.addLayout(ctrl_row)

        # 预览控制栏（默认隐藏）
        self.preview_frame = QFrame()
        self.preview_frame.setStyleSheet(f"""
            QFrame {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; border-radius: 8px; }}
        """)
        self.preview_frame.setVisible(False)
        vp_lay = QHBoxLayout(self.preview_frame)
        vp_lay.setContentsMargins(8, 4, 8, 4)
        vp_lay.setSpacing(8)

        self.lbl_preview = QLabel("")
        self.lbl_preview.setStyleSheet(f"color: {T.TEXT}; font-size:12px;")
        self.lbl_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.btn_play = ButtonFactory.solid("▶ 播放", T.TEXT_DIM, height=30, width=80)
        self.btn_play.setVisible(False)
        self.btn_play.clicked.connect(self._on_play_clicked)

        self.btn_transcribe = ButtonFactory.solid("转文字", T.GREEN, height=30, width=80)
        self.btn_transcribe.clicked.connect(self._on_transcribe_clicked)

        self.btn_send = ButtonFactory.solid("发送", T.NEON, height=30, width=80)
        self.btn_send.clicked.connect(self._on_send_clicked)

        self.btn_cancel_preview = ButtonFactory.solid("清除", T.ACCENT, height=30, width=80)
        self.btn_cancel_preview.clicked.connect(self._on_clear_preview)

        vp_lay.addWidget(self.lbl_preview)
        vp_lay.addWidget(self.btn_play)
        vp_lay.addWidget(self.btn_transcribe)
        vp_lay.addWidget(self.btn_send)
        vp_lay.addWidget(self.btn_cancel_preview)
        main_lay.addWidget(self.preview_frame)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    # ══════════════════════════════════════════════════════════════════════════
    # 交互逻辑
    # ══════════════════════════════════════════════════════════════════════════

    def _on_main_btn_clicked(self) -> None:
        if self._is_asr_processing:
            return

        if self._is_recording:
            # 停止录音
            if self._voice_worker:
                self._voice_worker.stop()
            self.btn_record.setText("停止中...")
            self.btn_record.setEnabled(False)
            self.btn_cancel_rec.setEnabled(False)
            self.status_changed.emit("正在结束录音...")
            return

        # 开始录音
        self._clear_pending_bundle()
        self._is_recording = True
        self.btn_record.setText("⏹ 停止")
        self.btn_record.setEnabled(True)
        self.btn_cancel_rec.setVisible(True)
        self.btn_cancel_rec.setEnabled(True)
        self.status_changed.emit("录音中... 点击停止进入预发送")
        self.recording_started.emit()

        self._start_voice_thread()

    def _on_cancel_recording(self) -> None:
        if self._voice_worker:
            self._voice_worker.cancel()
        self._reset_record_btn()
        self._clear_pending_bundle()
        self.status_changed.emit("已取消录音")

    def _on_voice_result(self, bundle: object) -> None:
        self._reset_record_btn()
        self._pending_bundle = bundle
        self._is_recording = False
        self.recording_stopped.emit()

        self.lbl_preview.setText(f"语音条：{bundle.duration:.1f}s")
        self.lbl_preview.setToolTip(f"文件：{os.path.basename(bundle.audio_path)}")
        self.preview_frame.setVisible(True)
        self.btn_play.setVisible(True)
        self.btn_send.setEnabled(True)
        self.btn_transcribe.setEnabled(True)
        self.btn_cancel_preview.setEnabled(True)
        self.status_changed.emit("录音完成：可播放、转文字、发送或清除")
        self.audio_recorded.emit(bundle)

    def _on_voice_error(self, err: str) -> None:
        self._reset_record_btn()
        self._clear_pending_bundle()
        self.status_changed.emit("录音失败")
        self.recording_error.emit(err)
        QMessageBox.critical(self, "语音输入失败", err)

    def _on_play_clicked(self) -> None:
        if not self._pending_bundle or not os.path.exists(self._pending_bundle.audio_path):
            QMessageBox.warning(self, "播放失败", "未找到语音文件。")
            return
        self.play_requested.emit(self._pending_bundle.audio_path)

    def _on_transcribe_clicked(self) -> None:
        if not self._pending_bundle or self._is_asr_processing:
            return
        if not os.path.exists(self._pending_bundle.audio_path):
            QMessageBox.warning(self, "转文字失败", "录音文件不存在")
            return

        self._auto_transcribe = False
        self._start_asr_thread()

    def _on_send_clicked(self) -> None:
        if not self._pending_bundle or self._is_asr_processing:
            return
        self._auto_transcribe = True
        self.bundle_sent.emit(self._pending_bundle)
        self._start_asr_thread()

    def _on_clear_preview(self) -> None:
        self._clear_pending_bundle()
        self.status_changed.emit("已清除录音")

    def _on_asr_result(self, result: object) -> None:
        self._is_asr_processing = False
        self._set_buttons_enabled(True)

        transcript = (result.transcript or "").strip()
        if transcript and not transcript.startswith("[未检测到语音内容]"):
            self.status_changed.emit("转写完成" if not self._auto_transcribe else "语音已发送，AI 正在处理...")
            self.asr_finished.emit(transcript)
            if self._auto_transcribe:
                self._clear_pending_bundle()
        else:
            msg = "未识别到有效语音，请重试"
            self.status_changed.emit(msg)
            if self._auto_transcribe:
                QMessageBox.warning(self, "发送失败", msg)
            self.asr_finished.emit("")  # 空字符串表示失败

    def _on_asr_error(self, err: str) -> None:
        self._is_asr_processing = False
        self._set_buttons_enabled(True)
        self.status_changed.emit("转文字失败")
        self.asr_error.emit(err)
        QMessageBox.critical(self, "转文字失败", err)

    # ══════════════════════════════════════════════════════════════════════════
    # 状态与线程管理
    # ══════════════════════════════════════════════════════════════════════════

    def _set_state(self, state: str) -> None:
        if state == "idle":
            self._set_buttons_enabled(True)
        elif state == "recording":
            self.btn_record.setEnabled(False)
            self.btn_cancel_rec.setEnabled(True)
            self.preview_frame.setVisible(False)
        elif state == "processing":
            self.btn_transcribe.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.btn_cancel_preview.setEnabled(False)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self.btn_transcribe.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)
        self.btn_cancel_preview.setEnabled(enabled)

    def _start_voice_thread(self) -> None:
        self._stop_thread("_voice_thread", "_voice_worker", stop_worker=True)
        self._voice_thread = QThread(self)
        self._voice_worker = VoiceWorker()
        self._voice_worker.moveToThread(self._voice_thread)

        self._voice_thread.started.connect(self._voice_worker.run)
        self._voice_worker.finished.connect(self._on_voice_result)
        self._voice_worker.error.connect(self._on_voice_error)
        self._voice_worker.finished.connect(self._voice_thread.quit)
        self._voice_worker.error.connect(self._voice_thread.quit)
        self._voice_thread.finished.connect(self._voice_worker.deleteLater)
        self._voice_thread.finished.connect(self._voice_thread.deleteLater)
        self._voice_thread.finished.connect(self._cleanup_voice_thread)
        self._voice_thread.start()

    def _start_asr_thread(self) -> None:
        self._is_asr_processing = True
        self._set_state("processing")
        self.status_changed.emit("正在转文字，请稍候...")
        self.asr_started.emit()

        self._stop_thread("_asr_thread", "_asr_worker", stop_worker=False)
        self._asr_thread = QThread(self)
        self._asr_worker = ASRWorker(self._pending_bundle.audio_path)
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

    def _stop_thread(self, thread_attr: str, worker_attr: str, stop_worker: bool = False) -> None:
        thread = getattr(self, thread_attr, None)
        worker = getattr(self, worker_attr, None)
        if stop_worker and worker:
            for m in ("stop", "cancel"):
                fn = getattr(worker, m, None)
                if callable(fn): fn()
        if thread and thread.isRunning():
            thread.quit()
            if not thread.wait(1000):
                thread.terminate()
                thread.wait(300)
        setattr(self, worker_attr, None)
        setattr(self, thread_attr, None)

    def _cleanup_voice_thread(self) -> None:
        self._voice_thread = None; self._voice_worker = None

    def _cleanup_asr_thread(self) -> None:
        self._asr_thread = None; self._asr_worker = None

    def _reset_record_btn(self) -> None:
        self._is_recording = False
        self.btn_record.setText("🎤 语音")
        self.btn_record.setEnabled(True)
        self.btn_cancel_rec.setVisible(False)

    def _clear_pending_bundle(self) -> None:
        if self._pending_bundle:
            path = self._pending_bundle.audio_path
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        self._pending_bundle = None
        self.preview_frame.setVisible(False)
        self.lbl_preview.setText("")
        self.btn_play.setVisible(False)
        self._set_buttons_enabled(True)

    def closeEvent(self, event) -> None:
        self._stop_thread("_voice_thread", "_voice_worker", stop_worker=True)
        self._stop_thread("_asr_thread", "_asr_worker", stop_worker=False)
        super().closeEvent(event)