# UI/components/ChatBubble.py
"""
通用聊天气泡组件。

Role 决定气泡样式，**同时决定 TTS 能力**：
  - "assistant" / "ai"  → 面试官/助手角色，支持语音播报（如环境变量齐全）
  - "user"              → 用户角色，无 TTS
  - "system"            → 系统提示，无 TTS，居中纯文本显示

TTS 设计原则
------------
- TTS 线程完全内聚在 ChatBubble 内，外部（Panel）只需调用
  `bubble.start_tts()` / `bubble.stop_tts()`，或在流式阶段让
  `append_chunk()` 自动驱动。
- 通过构造参数 `enable_tts: bool` 显式开关，默认 False，需要 TTS
  的调用方传 True；仅当 `DASHSCOPE_API_KEY` 存在时才实际启动。
- TTS 线程为 daemon 线程，不阻塞主窗口关闭。
- `stop_tts(force=False)` 可在流结束/窗口关闭时调用，非强制模式
  会等待当前句子播完再停；强制模式立即终止。
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Callable

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QTextBrowser, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from UI.components.info.Theme import T
from UI.components.util.md_to_html import md_to_html


# ── Role 配置表 ───────────────────────────────────────────────────────────────

_ROLE_CFG: dict[str, dict] = {
    "user": {
        "label":       "👤  你",
        "label_color": T.YELLOW,
        "bg":          T.USER_BUBBLE,
        "border":      f"{T.NEON}33",
        "radius":      "18px 18px 4px 18px",
        "align":       "right",
        "tts":         False,   # 用户侧不播报
    },
    "assistant": {
        "label":       "🤖  AI 助手",
        "label_color": T.NEON,
        "bg":          T.AI_BUBBLE,
        "border":      T.BORDER2,
        "radius":      "4px 18px 18px 18px",
        "align":       "left",
        "tts":         True,    # 助手侧可播报
    },
    "ai": {
        "label":       "🤖  AI 面试官",
        "label_color": T.NEON,
        "bg":          T.AI_BUBBLE,
        "border":      T.BORDER2,
        "radius":      "4px 18px 18px 18px",
        "align":       "left",
        "tts":         True,    # 面试官侧可播报
    },
    "system": {
        "label":       "",
        "label_color": T.TEXT_DIM,
        "bg":          "transparent",
        "border":      "transparent",
        "radius":      "8px",
        "align":       "center",
        "tts":         False,
    },
}


# ── ChatBubble ────────────────────────────────────────────────────────────────

class ChatBubble(QFrame):
    """
    参数
    ----
    role        : "user" | "assistant" | "ai" | "system"
    content     : 初始 Markdown 文本（可为空，稍后通过 append_chunk 流式填充）
    max_width   : 气泡最大宽度（px）
    enable_tts  : 是否启用 TTS 语音播报（仅对支持 TTS 的 role 生效）
    tts_model   : TTS 模型名
    tts_voice   : 发音人
    """

    def __init__(
        self,
        role: str,
        content: str = "",
        max_width: int = 580,
        enable_tts: bool = False,
        tts_model: str = "qwen3-tts-instruct-flash",
        tts_voice: str = "Elias",
        parent=None,
    ):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)

        self._role      = role
        self._content   = content
        self._max_width = max_width

        # TTS 状态
        cfg = _ROLE_CFG.get(role, _ROLE_CFG["assistant"])
        self._tts_capable: bool = cfg["tts"] and enable_tts
        self._tts_model   = tts_model
        self._tts_voice   = tts_voice
        self._tts_started = False

        self._tts_queue:   queue.Queue[str | None] | None = None
        self._tts_thread:  threading.Thread | None        = None
        self._tts_player                                  = None  # StreamingAudioPlayer

        # 去重缓存
        self._tts_last_token: str             = ""
        self._tts_sentence_cache: list[str]   = []
        self._tts_last_sentence: str          = ""

        # ── 布局 ──────────────────────────────────────────────────────────────
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 3, 6, 3)
        outer.setSpacing(0)

        # system 消息：居中纯文本，提前返回
        if role == "system":
            lbl = QLabel(content)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"color: {T.TEXT_DIM}; font-size: 11px;"
                f"padding: 4px 12px; background: transparent;"
                f"font-family: {T.FONT};"
            )
            outer.addWidget(lbl)
            return

        # 气泡主体
        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        self.bubble.setMaximumWidth(max_width)
        self.bubble.setStyleSheet(f"""
            QFrame#bubble {{
                background: {cfg['bg']};
                border: 1px solid {cfg['border']};
                border-radius: {cfg['radius']};
            }}
        """)

        inner = QVBoxLayout(self.bubble)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(5)

        # 角色标签
        if cfg["label"]:
            role_lbl = QLabel(cfg["label"])
            role_lbl.setStyleSheet(
                f"font-size: 10px; color: {cfg['label_color']};"
                f"font-weight: 700; letter-spacing: 1px;"
                f"background: transparent; font-family: {T.FONT};"
            )
            inner.addWidget(role_lbl)

        # 内容视图
        self.text_view = QTextBrowser()
        self.text_view.setOpenExternalLinks(True)
        self.text_view.setFrameShape(QFrame.NoFrame)
        self.text_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.text_view.setStyleSheet(f"""
            QTextBrowser {{
                background: transparent;
                color: {T.TEXT};
                font-size: 14px;
                border: none;
                font-family: {T.FONT};
                line-height: 1.7;
            }}
        """)
        inner.addWidget(self.text_view)

        if cfg["align"] == "right":
            outer.addStretch(2)
            outer.addWidget(self.bubble, stretch=8)
        else:
            outer.addWidget(self.bubble, stretch=8)
            outer.addStretch(2)

        if content:
            self._render()
            self._adjust_height()

    # ══════════════════════════════════════════════════════════════════════════
    # 内容渲染
    # ══════════════════════════════════════════════════════════════════════════

    def _render(self) -> None:
        """将 _content 渲染为 HTML 并更新 text_view。"""
        self.text_view.setHtml(md_to_html(self._content))

    def _adjust_height(self) -> None:
        """根据渲染后的文档高度动态调整 text_view 高度。"""
        w = min(
            self.text_view.width() or (self._max_width - 28),
            self._max_width - 28,
        )
        self.text_view.document().setTextWidth(w)
        h = int(self.text_view.document().size().height()) + 24
        self.text_view.setFixedHeight(max(36, h))

    # ══════════════════════════════════════════════════════════════════════════
    # 流式追加（外部调用）
    # ══════════════════════════════════════════════════════════════════════════

    def append_chunk(self, chunk: str) -> None:
        """
        追加一个流式 token，同时驱动 TTS（如已启用）。
        必须在主线程调用。
        """
        self._content += chunk
        self._render()
        cursor = self.text_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.text_view.setTextCursor(cursor)
        self._adjust_height()

        if self._tts_capable and self._tts_started:
            self._feed_tts_token(chunk)

    def set_content(self, text: str) -> None:
        """非流式场景：一次性设置完整内容。"""
        self._content = text
        self._render()
        self._adjust_height()

    # ══════════════════════════════════════════════════════════════════════════
    # TTS 公共接口
    # ══════════════════════════════════════════════════════════════════════════

    def start_tts(self) -> None:
        """
        启动 TTS 线程。
        幂等：重复调用无副作用。
        环境变量 DASHSCOPE_API_KEY 缺失时静默跳过。
        """
        if not self._tts_capable or self._tts_started:
            return

        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            return  # 无 key，静默跳过

        # 延迟导入，避免在未安装语音依赖的环境中启动失败
        try:
            from service.voice_sdk.audio.player import StreamingAudioPlayer
            from service.voice_sdk.tts.pipeline import stream_interview_tts_from_tokens
        except ImportError:
            return

        self._tts_queue  = queue.Queue()
        self._tts_player = StreamingAudioPlayer()
        self._tts_last_token     = ""
        self._tts_sentence_cache = []
        self._tts_last_sentence  = ""

        def _token_iter():
            assert self._tts_queue is not None
            while True:
                token = self._tts_queue.get()
                if token is None:
                    break
                yield token

        def _runner():
            try:
                stream_interview_tts_from_tokens(
                    token_stream=_token_iter(),
                    on_audio_chunk=self._on_tts_audio_chunk,
                    api_key=api_key,
                    model=self._tts_model,
                    voice=self._tts_voice,
                    sentence_punctuations=frozenset(
                        {".", "。", "!", "！", "?", "？", ";", "；", ":", "：", "\n"}
                    ),
                    ordered_output=False,
                    max_workers=1,
                    max_buffer_length=64,
                )
            except Exception as exc:
                print(f"[ChatBubble TTS] error: {exc}")
            finally:
                if self._tts_player is not None:
                    self._tts_player.close()

        self._tts_thread = threading.Thread(target=_runner, daemon=True)
        self._tts_thread.start()
        self._tts_started = True

    def stop_tts(self, force: bool = False) -> None:
        """
        停止 TTS。

        force=False（默认）：发送结束哨兵，等待当前句子播完（最多 8 s）。
        force=True         ：立即终止，可能截断最后一句。
        """
        if not self._tts_started:
            return

        if self._tts_queue is not None:
            try:
                self._tts_queue.put(None)
            except Exception:
                pass

        if self._tts_thread and self._tts_thread.is_alive():
            timeout = 2.0 if force else 8.0
            self._tts_thread.join(timeout=timeout)
            if self._tts_thread.is_alive() and not force:
                # 仍在合成中，非强制模式不关闭播放器，避免截断
                return

        if self._tts_player is not None:
            try:
                self._tts_player.close()
                self._tts_player.join(timeout=2.0)
            except Exception:
                pass

        self._reset_tts_state()

    def _reset_tts_state(self) -> None:
        self._tts_queue   = None
        self._tts_thread  = None
        self._tts_player  = None
        self._tts_started = False
        self._tts_last_token     = ""
        self._tts_sentence_cache = []
        self._tts_last_sentence  = ""

    # ══════════════════════════════════════════════════════════════════════════
    # TTS 内部实现
    # ══════════════════════════════════════════════════════════════════════════

    def _feed_tts_token(self, token: str) -> None:
        """向 TTS 队列投递 token（含去重逻辑）。"""
        if self._tts_queue is None:
            return

        token_text = str(token or "").strip("\r")
        if not token_text:
            return

        # 去重 1：连续重复 token
        if token_text == self._tts_last_token:
            return

        # 去重 2：完整句子在短窗口内重复
        stripped = token_text.strip()
        if stripped and stripped[-1] in {"。", "！", "？", ".", "!", "?", "\n"} and len(stripped) > 3:
            if stripped in self._tts_sentence_cache:
                self._tts_last_token = token_text
                return
            self._tts_sentence_cache.append(stripped)
            if len(self._tts_sentence_cache) > 12:
                self._tts_sentence_cache.pop(0)

        self._tts_last_token = token_text
        self._tts_queue.put(token_text)

    def _on_tts_audio_chunk(self, audio_chunk: bytes, sentence: str) -> None:
        """TTS 回调：将音频块交给播放器（从 TTS 线程调用）。"""
        if not audio_chunk:
            return
        if sentence != self._tts_last_sentence:
            print(f"[ChatBubble TTS] playing: {sentence}")
            self._tts_last_sentence = sentence
        if self._tts_player is not None:
            self._tts_player.submit(audio_chunk)