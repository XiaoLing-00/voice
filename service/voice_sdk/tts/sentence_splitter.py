"""将 LLM token 流聚合为完整句子。

与 TTS 后端完全无关，可单独测试。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

DEFAULT_SENTENCE_PUNCTUATIONS: frozenset[str] = frozenset({
    ".", "。", "!", "！", "?", "？",
    ",", "，", ";", "；", ":", "：", "\n",
})


def iter_sentences_from_token_stream(
    token_stream: Iterable[str],
    sentence_punctuations: set[str] | frozenset[str] = DEFAULT_SENTENCE_PUNCTUATIONS,
    flush_tail: bool = True,
    max_buffer_length: int | None = None,
) -> Iterator[str]:
    """将 token 流聚合为完整句子。

    Args:
        token_stream:         LLM 按 token 产出的文本流。
        sentence_punctuations: 触发分句的标点集合。
        flush_tail:           流结束且缓冲区非空时，是否输出最后残余文本。
        max_buffer_length:    无标点时缓冲区最大长度，超过即强制切句。

    Yields:
        按标点切分后的句子（保留结尾标点）。
    """
    if not sentence_punctuations:
        raise ValueError("sentence_punctuations 不能为空")
    if max_buffer_length is not None and max_buffer_length <= 0:
        raise ValueError("max_buffer_length 必须大于 0")

    punct_re = re.compile("[" + re.escape("".join(sentence_punctuations)) + "]")
    buffer   = ""
    seen:    set[str] = set()

    def _emit(candidate: str) -> Iterator[str]:
        normalized = candidate.strip()
        if len(normalized) < 3 or normalized in seen:
            return
        seen.add(normalized)
        yield normalized

    for token in token_stream:
        if not token:
            continue
        buffer += str(token)

        if max_buffer_length and len(buffer) >= max_buffer_length:
            sentence = buffer[:max_buffer_length].strip()
            buffer   = buffer[max_buffer_length:]
            if sentence:
                yield from _emit(sentence)

        while True:
            m = punct_re.search(buffer)
            if m is None:
                break
            sentence = buffer[: m.end()].strip()
            buffer   = buffer[m.end():]
            if sentence:
                yield from _emit(sentence)

    if flush_tail:
        tail = buffer.strip()
        if tail:
            yield from _emit(tail)
