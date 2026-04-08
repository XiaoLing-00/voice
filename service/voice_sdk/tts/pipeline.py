"""流式 TTS 编排管线。

将 LLM token 流 → 句子 → 并发 TTS → 按序（或乱序）回调音频 chunk。

pipeline 只依赖 TTSClient 抽象，不感知任何具体 TTS 后端细节。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from service.voice_sdk.tts.client import TTSClient, DashScopeTTSClient
from service.voice_sdk.tts.sentence_splitter import (
    DEFAULT_SENTENCE_PUNCTUATIONS,
    iter_sentences_from_token_stream,
)

AudioChunkCallback = Callable[[bytes, str], None]


def stream_interview_tts_from_tokens(
    token_stream: Iterable[str],
    on_audio_chunk: AudioChunkCallback,
    *,
    tts_client: TTSClient | None = None,
    # 向后兼容：当 tts_client=None 时仍接受 DashScope 参数
    api_key: str = "",
    model: str   = "",
    voice: str   = "",
    api_base_url: str = "",
    # 编排参数
    sentence_punctuations: set[str] | frozenset[str] = DEFAULT_SENTENCE_PUNCTUATIONS,
    max_workers: int = 4,
    ordered_output: bool = False,
    max_buffer_length: int | None = 120,
    allow_retry_on_failed: bool = True,
    max_failed_retries: int = 1,
    start_playback_after_sentences: int = 2,
) -> None:
    """将 LLM token 流转换为流式语音并回调音频 chunk。

    流程：
    1. 按标点将 token 流聚合为句子。
    2. 对每个句子调用 tts_client.stream_chunks()。
    3. 每产出一个音频 chunk，调用 on_audio_chunk(chunk, sentence)。

    Args:
        token_stream:           上游文本 token 可迭代对象。
        on_audio_chunk:         chunk 回调，签名为 (audio_chunk: bytes, source_sentence: str)。
        tts_client:             TTSClient 实例；None 时自动构建 DashScopeTTSClient。
        api_key / model / voice / api_base_url:
                                tts_client=None 时透传给 DashScopeTTSClient（向后兼容）。
        sentence_punctuations:  分句标点集合。
        max_workers:            并发合成的最大线程数。
        ordered_output:         True = 按句子原始顺序输出；False = 谁先好谁先播。
        max_buffer_length:      无标点时的强制切句字符数。
        allow_retry_on_failed:  失败句是否允许重试合成。
        max_failed_retries:     同一句允许重试的最大次数。
        start_playback_after_sentences:
                                顺序模式下，至少缓存几句后再开始播放（降低首句抖动）。
    """
    if on_audio_chunk is None:
        raise ValueError("on_audio_chunk 不能为空")
    if max_workers <= 0:
        raise ValueError("max_workers 必须大于 0")
    if max_failed_retries < 0:
        raise ValueError("max_failed_retries 不能小于 0")
    if start_playback_after_sentences <= 0:
        raise ValueError("start_playback_after_sentences 必须大于 0")

    # 构建默认客户端（向后兼容）
    if tts_client is None:
        from ..config import (
            ALIYUN_API_KEY,
            DEFAULT_TTS_API_BASE_URL,
            DEFAULT_TTS_MODEL,
            DEFAULT_TTS_VOICE,
        )
        tts_client = DashScopeTTSClient(
            api_key     = api_key      or ALIYUN_API_KEY,
            model       = model        or DEFAULT_TTS_MODEL,
            voice       = voice        or DEFAULT_TTS_VOICE,
            api_base_url= api_base_url or DEFAULT_TTS_API_BASE_URL,
        )

    errors: list[Exception] = []

    # ── 句子级状态机（防重复合成）──────────────────────────────────────────
    sentence_states:     dict[str, str] = {}
    failed_retry_counts: dict[str, int] = {}
    state_lock = threading.Lock()

    def _try_claim(sentence: str) -> bool:
        key = sentence.strip()
        if not key:
            return False
        with state_lock:
            state = sentence_states.get(key)
            if state in {"pending", "synthesizing", "done"}:
                return False
            if state == "failed":
                if not allow_retry_on_failed:
                    return False
                retried = failed_retry_counts.get(key, 0)
                if retried >= max_failed_retries:
                    return False
                failed_retry_counts[key] = retried + 1
            sentence_states[key] = "synthesizing"
            return True

    def _mark(sentence: str, state: str) -> None:
        key = sentence.strip()
        if key:
            with state_lock:
                sentence_states[key] = state

    # ── 两种模式的 worker ──────────────────────────────────────────────────

    def _collect(sentence: str) -> tuple[str, list[bytes]]:
        """顺序模式：收集全部 chunk 后再返回。"""
        if not _try_claim(sentence):
            return sentence, []
        chunks: list[bytes] = []
        try:
            for chunk in tts_client.stream_chunks(sentence):
                chunks.append(chunk)
            _mark(sentence, "done")
        except Exception:
            _mark(sentence, "failed")
            raise
        return sentence, chunks

    def _stream(sentence: str) -> None:
        """乱序模式：边产出边回调。"""
        if not _try_claim(sentence):
            return
        try:
            for chunk in tts_client.stream_chunks(sentence):
                on_audio_chunk(chunk, sentence)
            _mark(sentence, "done")
        except Exception as exc:
            print(f"[TTS pipeline] error: {exc}")
            _mark(sentence, "failed")
            errors.append(exc)

    sentence_iter = iter_sentences_from_token_stream(
        token_stream          = token_stream,
        sentence_punctuations = sentence_punctuations,
        flush_tail            = True,
        max_buffer_length     = max_buffer_length,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if not ordered_output:
            # ── 乱序模式 ────────────────────────────────────────────────
            futures = [executor.submit(_stream, s) for s in sentence_iter]
            for f in futures:
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        else:
            # ── 顺序模式 ────────────────────────────────────────────────
            failed_sentinel = object()
            completed:       dict[int, Any]  = {}
            submitted_count  = 0
            next_emit_index  = 0
            producer_done    = threading.Event()
            submission_errors: list[Exception] = []
            started_playback = False
            sync_cond        = threading.Condition()

            def _on_future_done(index: int, future: Future) -> None:
                try:
                    result = future.result()
                except Exception as exc:
                    with sync_cond:
                        completed[index] = failed_sentinel
                        errors.append(exc)
                        sync_cond.notify_all()
                    return
                with sync_cond:
                    completed[index] = result
                    sync_cond.notify_all()

            def _producer() -> None:
                nonlocal submitted_count
                try:
                    for sentence in sentence_iter:
                        with sync_cond:
                            idx = submitted_count
                            submitted_count += 1
                        f = executor.submit(_collect, sentence)
                        f.add_done_callback(
                            lambda future, i=idx: _on_future_done(i, future)
                        )
                except Exception as exc:
                    submission_errors.append(exc)
                finally:
                    producer_done.set()
                    with sync_cond:
                        sync_cond.notify_all()

            def _can_start() -> bool:
                nonlocal started_playback
                if started_playback:
                    return True
                with sync_cond:
                    n = submitted_count
                if n == 0:
                    return False
                warmup = min(start_playback_after_sentences, n)
                if producer_done.is_set() and n < start_playback_after_sentences:
                    if all(i in completed for i in range(n)):
                        started_playback = True
                        return True
                    return False
                if all(i in completed for i in range(warmup)):
                    started_playback = True
                    return True
                return False

            def _flush() -> None:
                nonlocal next_emit_index
                while True:
                    with sync_cond:
                        if next_emit_index not in completed:
                            return
                        result = completed.pop(next_emit_index)
                    next_emit_index += 1
                    if result is failed_sentinel:
                        continue
                    sentence, chunks = result
                    for chunk in chunks:
                        on_audio_chunk(chunk, sentence)

            producer_thread = threading.Thread(target=_producer, daemon=True)
            producer_thread.start()

            try:
                while True:
                    if _can_start():
                        _flush()
                    with sync_cond:
                        done = (
                            producer_done.is_set()
                            and next_emit_index >= submitted_count
                            and not completed
                        )
                        if done:
                            break
                        sync_cond.wait(timeout=0.05)
            finally:
                producer_thread.join()

            errors.extend(submission_errors)

    if errors:
        raise RuntimeError(f"流式 TTS 执行失败：{errors[0]}")
