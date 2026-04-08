"""流式音频播放器。

StreamingAudioPlayer 只负责：
- 接收音频 bytes chunk（PCM 或 WAV）并顺序播放
- 管理自己的 PyAudio 实例和后台播放线程
"""

from __future__ import annotations

import io
import queue
import threading
import wave

import pyaudio


class StreamingAudioPlayer:
    """将流式音频 chunk 放入后台线程顺序播放。

    统一以 24kHz / 单声道 / 16-bit PCM 输出，避免频繁重建设备流。
    """

    def __init__(
        self,
        default_sample_rate: int = 24000,
        default_channels: int    = 1,
    ) -> None:
        self.default_sample_rate = default_sample_rate or 24000
        self.default_channels    = default_channels    or 1

        bytes_per_sample         = 2   # paInt16
        self._bytes_per_second   = self.default_sample_rate * self.default_channels * bytes_per_sample

        # 启播前预缓冲：降低首句和句间抖动
        self._prebuffer_bytes    = max(int(self._bytes_per_second * 0.2), 2048)
        # 固定写入块：避免频繁写入极小 chunk 导致音频下溢卡顿
        self._write_block_bytes  = max(int(self._bytes_per_second * 0.06), 2048)

        self._ingress_queue: queue.Queue[bytes | None] = queue.Queue()
        self._closed = threading.Event()

        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.default_channels,
            rate=self.default_sample_rate,
            output=True,
        )

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ── 公开接口 ────────────────────────────────────────────────────────────

    def submit(self, audio_chunk: bytes) -> None:
        """提交一个音频 chunk，异步排队播放。"""
        if self._closed.is_set() or not audio_chunk:
            return
        self._ingress_queue.put(audio_chunk)

    def close(self) -> None:
        """通知播放器：所有 chunk 已提交，可以在播完后关闭。"""
        if self._closed.is_set():
            return
        self._closed.set()
        self._ingress_queue.put(None)   # sentinel

    def join(self, timeout: float | None = None) -> None:
        """等待后台线程结束。"""
        self._thread.join(timeout)

    # ── 内部实现 ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        pcm_buffer = bytearray()
        started    = False

        try:
            while True:
                item = self._ingress_queue.get()
                if item is None:
                    break   # sentinel → 冲刷尾部并退出

                try:
                    pcm_bytes = self._decode_chunk(item)
                    if not pcm_bytes:
                        continue
                    pcm_buffer.extend(pcm_bytes)

                    # 达到预缓冲量后才开始播放，降低抖动
                    if not started and len(pcm_buffer) < self._prebuffer_bytes:
                        continue
                    started = True

                    while len(pcm_buffer) >= self._write_block_bytes:
                        block = bytes(pcm_buffer[: self._write_block_bytes])
                        del pcm_buffer[: self._write_block_bytes]
                        self._stream.write(block)

                except Exception as exc:
                    print(f"[TTS Player] chunk play failed: {exc}")

            # 冲刷尾部残余
            if pcm_buffer:
                try:
                    self._stream.write(bytes(pcm_buffer))
                except Exception as exc:
                    print(f"[TTS Player] final flush failed: {exc}")
        finally:
            for method in (self._stream.stop_stream, self._stream.close, self._pa.terminate):
                try:
                    method()
                except Exception:
                    pass

    def _decode_chunk(self, audio_chunk: bytes) -> bytes:
        """优先原生 PCM 播放；若遇到 WAV 数据则提取 PCM 帧后再播放。"""
        if not audio_chunk:
            return b""

        if not audio_chunk.startswith(b"RIFF"):
            return audio_chunk  # 原生 PCM，直接返回

        # 解析 WAV —— 严格要求与当前流参数一致
        try:
            with wave.open(io.BytesIO(audio_chunk), "rb") as wf:
                if wf.getcomptype()   != "NONE":                     return b""
                if wf.getnchannels()  != self.default_channels:      return b""
                if wf.getsampwidth()  != 2:                          return b""
                if wf.getframerate()  != self.default_sample_rate:   return b""
                return wf.readframes(wf.getnframes())
        except Exception:
            return b""
