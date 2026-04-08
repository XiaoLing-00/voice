"""TTS 客户端抽象层 + DashScope 实现。

设计原则：
- TTSClient 是换模型时唯一需要替换的边界。
- 上层 pipeline 只依赖 TTSClient，不感知 DashScope 细节。
- DashScopeTTSClient 封装所有 DashScope SDK 调用、重试、回退逻辑。
"""

from __future__ import annotations

import base64
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator

import requests

from ..config import (
    ALIYUN_API_KEY,
    DEFAULT_TTS_API_BASE_URL,
    DEFAULT_TTS_MODEL,
    DEFAULT_TTS_VOICE,
)
from ..utils.dashscope import extract_audio_base64, extract_audio_url, normalize_payload


# ── 抽象接口 ──────────────────────────────────────────────────────────────────

class TTSClient(ABC):
    """TTS 后端抽象接口。换模型时实现此类即可，pipeline 无需改动。"""

    @abstractmethod
    def stream_chunks(self, sentence: str) -> Iterator[bytes]:
        """对单句文本进行 TTS，逐块产出 PCM / WAV 二进制数据。

        Args:
            sentence: 需要合成的完整句子（已 strip）。

        Yields:
            bytes: 每个音频 chunk 的二进制数据。

        Raises:
            RuntimeError: 合成失败且无可用音频时抛出。
        """
        ...


# ── DashScope 实现 ────────────────────────────────────────────────────────────

_TRANSIENT_ERROR_MARKERS = (
    "ssl", "ssleoferror", "connection reset", "connection aborted",
    "eof occurred in violation of protocol",
    "temporarily unavailable", "timed out", "timeout",
)


def _is_transient(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_ERROR_MARKERS)


class DashScopeTTSClient(TTSClient):
    """基于阿里云 DashScope MultiModalConversation 的流式 TTS 客户端。

    Args:
        api_key:      DashScope API Key，默认读取 config.ALIYUN_API_KEY。
        model:        TTS 模型名称。
        voice:        朗读音色；qwen3-tts 系列不支持 Cherry，自动回退 Elias。
        api_base_url: DashScope 接口地址，可切换地域。
        max_retries:  瞬态网络错误的最大重试次数（含首次尝试）。
    """

    def __init__(
        self,
        api_key: str       = "",
        model: str         = DEFAULT_TTS_MODEL,
        voice: str         = DEFAULT_TTS_VOICE,
        api_base_url: str  = DEFAULT_TTS_API_BASE_URL,
        max_retries: int   = 4,
    ) -> None:
        self.api_key     = api_key or ALIYUN_API_KEY
        self.model       = model
        self.voice       = "Elias" if voice == "Cherry" else voice
        self.api_base_url= api_base_url or DEFAULT_TTS_API_BASE_URL
        self.max_retries = max_retries

        if not self.api_key:
            raise ValueError("DashScopeTTSClient 需要 api_key 或环境变量 DASHSCOPE_API_KEY")

    def stream_chunks(self, sentence: str) -> Iterator[bytes]:
        text = sentence.strip()
        if not text:
            return

        try:
            import dashscope
            from dashscope import MultiModalConversation
        except ImportError as exc:
            raise ImportError(
                "缺少 dashscope 依赖，请先安装/升级: pip install -U dashscope"
            ) from exc

        dashscope.api_key           = self.api_key
        dashscope.base_http_api_url = self.api_base_url

        print(f"[TTS] sentence={text!r}  model={self.model}  voice={self.voice}")

        backoff       = [0.5, 1.0, 2.0]
        last_exc: Exception | None = None
        emitted       = False

        for attempt in range(1, self.max_retries + 1):
            attempt_chunks:    list[bytes] = []
            has_stream_chunk   = False
            fallback_url: str | None = None

            try:
                response_stream = MultiModalConversation.call(
                    model  = self.model,
                    api_key= self.api_key,
                    text   = text,
                    voice  = self.voice,
                    stream = True,
                )

                for event in response_stream:
                    normalized = normalize_payload(event)

                    audio_b64 = extract_audio_base64(normalized)
                    if audio_b64:
                        try:
                            chunk = base64.b64decode(audio_b64.strip())
                        except Exception:
                            chunk = b""
                        if chunk:
                            has_stream_chunk = True
                            attempt_chunks.append(chunk)
                            continue

                    if not has_stream_chunk:
                        url = extract_audio_url(normalized)
                        if url:
                            fallback_url = url

                # 只有完全没有流式 chunk 时才回退下载，避免重播
                if not has_stream_chunk and fallback_url:
                    try:
                        resp = requests.get(fallback_url, timeout=30)
                        resp.raise_for_status()
                        attempt_chunks.append(resp.content)
                    except Exception as exc:
                        print(f"[TTS] fallback url download failed: {exc}")

                if attempt_chunks:
                    emitted = True
                    yield from attempt_chunks
                    return

            except Exception as exc:
                last_exc = exc
                if emitted:
                    return  # 已部分产出，不重试以免重播

                if attempt < self.max_retries and _is_transient(exc):
                    print(f"[TTS] transient error, retry {attempt}/{self.max_retries}: {exc}")
                    time.sleep(backoff[attempt - 1])
                    continue

                print(f"[TTS] non-retryable error on attempt {attempt}: {exc}")
                raise RuntimeError("TTS 未返回可用音频 chunk") from exc

        if not emitted:
            raise RuntimeError("TTS 未返回可用音频 chunk") from last_exc
