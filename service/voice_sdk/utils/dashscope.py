"""DashScope SDK 返回值的通用解析工具。

与具体业务（STT / TTS）无关，可在多处复用。
"""

from __future__ import annotations

from typing import Any


def normalize_payload(payload: Any) -> Any:
    """将 DashScope SDK 返回的对象递归转换为基础 Python 类型。"""
    if payload is None or isinstance(payload, (str, int, float, bool)):
        return payload
    if isinstance(payload, dict):
        return {k: normalize_payload(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [normalize_payload(item) for item in payload]
    if hasattr(payload, "__dict__"):
        return {
            k: normalize_payload(v)
            for k, v in vars(payload).items()
            if not k.startswith("_")
        }
    return payload


def extract_audio_base64(payload: Any) -> str | None:
    """从 DashScope 流式事件里提取音频 base64 字段。"""
    if isinstance(payload, dict):
        # 直接层
        if isinstance(payload.get("audio"), dict):
            data = payload["audio"].get("data")
            if isinstance(data, str) and data:
                return data

        # output 层
        output = payload.get("output")
        if isinstance(output, dict):
            if isinstance(output.get("audio"), dict):
                data = output["audio"].get("data")
                if isinstance(data, str) and data:
                    return data

            choices = output.get("choices")
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    message = choice.get("message")
                    if not isinstance(message, dict):
                        continue
                    content_list = message.get("content")
                    if not isinstance(content_list, list):
                        continue
                    for content in content_list:
                        if not isinstance(content, dict):
                            continue
                        if isinstance(content.get("audio"), dict):
                            data = content["audio"].get("data")
                            if isinstance(data, str) and data:
                                return data

        # 递归兜底
        for value in payload.values():
            nested = extract_audio_base64(value)
            if nested:
                return nested

    if isinstance(payload, list):
        for item in payload:
            nested = extract_audio_base64(item)
            if nested:
                return nested

    return None


def extract_audio_url(payload: Any) -> str | None:
    """从 DashScope 返回里提取完整音频 URL（非流式回退用）。"""
    if isinstance(payload, dict):
        if isinstance(payload.get("audio"), dict):
            url = payload["audio"].get("url")
            if isinstance(url, str) and url:
                return url

        output = payload.get("output")
        if isinstance(output, dict) and isinstance(output.get("audio"), dict):
            url = output["audio"].get("url")
            if isinstance(url, str) and url:
                return url

        for value in payload.values():
            nested = extract_audio_url(value)
            if nested:
                return nested

    if isinstance(payload, list):
        for item in payload:
            nested = extract_audio_url(item)
            if nested:
                return nested

    return None
