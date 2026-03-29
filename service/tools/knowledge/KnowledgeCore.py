# service/tools/knowledge/KnowledgeCore.py
"""
KnowledgeCore — 阿里云百炼 RAG SDK 封装

设计原则：
  - 认证信息（AK/SK、API Key、workspace）统一从环境变量读取
  - knowledge_base_id 通过构造函数注入，支持多实例对应多知识库
  - 内部自动探测可用模式：official_sdk > http_api
  - 对外只暴露两个方法：retrieve() 和 retrieve_as_context()

多知识库使用示例：
    tech_kb   = KnowledgeCore(knowledge_base_id="xxx_tech",   label="技术知识库")
    course_kb = KnowledgeCore(knowledge_base_id="xxx_course", label="课程知识库")
"""
from __future__ import annotations

import os
from typing import List, Optional

import requests

try:
    from alibabacloud_bailian20231229 import models as bailian_models
    from alibabacloud_bailian20231229.client import Client as BailianClient
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models
    _HAS_OFFICIAL_SDK = True
except ImportError:
    _HAS_OFFICIAL_SDK = False


class KnowledgeCore:
    """
    单知识库 RAG 检索客户端。

    构造参数：
        knowledge_base_id — 必填，百炼控制台的 Index ID（每个知识库不同）
        label             — 可读标签，用于日志区分，默认截取 id 末 8 位

    认证信息全部从环境变量读取（无需在构造时传入）：
        DASHSCOPE_API_KEY              — HTTP 模式必填
        BAILOU_WORKSPACE_ID            — SDK 模式必填
        ALIBABA_CLOUD_ACCESS_KEY_ID    — SDK 模式必填
        ALIBABA_CLOUD_ACCESS_KEY_SECRET— SDK 模式必填

    优先级：官方 SDK 模式 > HTTP API 模式
    """

    def __init__(
        self,
        knowledge_base_id: str,
        label: str = "",
    ):
        if not knowledge_base_id:
            raise ValueError(
                "[KnowledgeCore] knowledge_base_id 不能为空，"
                "请在调用处传入对应知识库的 Index ID"
            )

        self.knowledge_base_id = knowledge_base_id
        self.label = label or f"kb-{knowledge_base_id[-8:]}"

        # ── 从环境变量读取认证信息 ────────────────────────────────────────
        self._api_key            = os.getenv("DASHSCOPE_API_KEY", "")
        self._workspace_id       = os.getenv("BAILOU_WORKSPACE_ID", "")
        self._access_key_id      = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        self._access_key_secret  = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")

        # ── 选择运行模式 ──────────────────────────────────────────────────
        if (
            _HAS_OFFICIAL_SDK
            and self._access_key_id
            and self._access_key_secret
            and self._workspace_id
        ):
            self._mode = "official_sdk"
            config = open_api_models.Config(
                access_key_id=self._access_key_id,
                access_key_secret=self._access_key_secret,
                endpoint="bailian.cn-beijing.aliyuncs.com",
            )
            self._sdk_client = BailianClient(config)
            print(f"[KnowledgeCore:{self.label}] ✅ 官方 SDK 模式，index_id={self.knowledge_base_id}")
        elif self._api_key:
            self._mode = "http_api"
            self._sdk_client = None
            print(f"[KnowledgeCore:{self.label}] ✅ HTTP API 模式，index_id={self.knowledge_base_id}")
        else:
            raise ValueError(
                f"[KnowledgeCore:{self.label}] 认证信息缺失：\n"
                "  SDK 模式需要：BAILOU_WORKSPACE_ID + ALIBABA_CLOUD_ACCESS_KEY_ID + ALIBABA_CLOUD_ACCESS_KEY_SECRET\n"
                "  HTTP 模式需要：DASHSCOPE_API_KEY"
            )

    # ═══════════════════════════════════════════════════════════════
    # 对外接口
    # ═══════════════════════════════════════════════════════════════

    def retrieve(self, query: str, top_k: int = 3) -> List[str]:
        """
        检索知识库，返回文本列表。
        每条结果格式：【文件名】文本内容（相关度: x.xx）
        出错时返回包含错误描述的单元素列表，不抛异常。
        """
        try:
            raw_nodes = (
                self._retrieve_sdk(query, top_k)
                if self._mode == "official_sdk"
                else self._retrieve_http(query, top_k)
            )
        except Exception as e:
            import traceback
            print(f"[KnowledgeCore:{self.label}] ❌ 检索异常:\n{traceback.format_exc()}")
            return [f"⚠️ 知识库「{self.label}」检索异常：{type(e).__name__}: {e}"]

        if not raw_nodes:
            return [f"📭 知识库「{self.label}」中未找到与「{query}」相关的内容。"]

        results = []
        for node in raw_nodes:
            text  = node.get("text", "").strip()
            score = node.get("score", 0.0)
            title = node.get("title", "")
            if not text:
                continue
            parts = []
            if title:
                parts.append(f"【{title}】")
            parts.append(text)
            if score:
                parts.append(f"(相关度: {score:.2f})")
            results.append(" ".join(parts))

        return results or [f"📭 知识库「{self.label}」中未找到相关内容。"]

    def retrieve_as_context(self, query: str, top_k: int = 3) -> str:
        """
        检索并拼接为可直接嵌入 prompt 的 context 字符串。
        结果为空或出错时返回空字符串，调用方可安全 `if context:` 判断。
        """
        results = self.retrieve(query, top_k=top_k)
        if not results or results[0].startswith(("📭", "⚠️")):
            return ""
        lines = [f"【参考知识库：{self.label}】"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    # 内部：官方 SDK 检索
    # ═══════════════════════════════════════════════════════════════

    def _retrieve_sdk(self, query: str, top_k: int) -> list[dict]:
        """
        使用 alibabacloud-bailian20231229 SDK 检索。
        参数说明（经实测确认）：
          index_id              — 知识库 Index ID
          rerank_top_n          — 最终返回条数
          dense_similarity_top_k— 向量召回候选数，建议为 rerank_top_n 的 4 倍
        """
        request = bailian_models.RetrieveRequest(
            index_id=self.knowledge_base_id,
            query=query,
            rerank_top_n=top_k,
            dense_similarity_top_k=top_k * 4,
            enable_reranking=True,
        )
        runtime  = util_models.RuntimeOptions()
        response = self._sdk_client.retrieve_with_options(
            self._workspace_id, request, {}, runtime
        )

        body  = getattr(response, "body", None)
        data  = getattr(body,  "data", None)
        nodes = getattr(data,  "nodes", None) or []

        result = []
        for node in nodes:
            text     = getattr(node, "text", "") or ""
            score    = getattr(node, "score", 0) or 0
            metadata = getattr(node, "metadata", {}) or {}
            if isinstance(metadata, dict):
                title = metadata.get("file_name") or metadata.get("title") or ""
            else:
                title = getattr(metadata, "file_name", "") or getattr(metadata, "title", "") or ""
            result.append({
                "text":  str(text).strip(),
                "score": float(score),
                "title": str(title),
            })
        return result

    # ═══════════════════════════════════════════════════════════════
    # 内部：HTTP API 检索
    # ═══════════════════════════════════════════════════════════════

    def _retrieve_http(self, query: str, top_k: int) -> list[dict]:
        """
        使用 DashScope HTTP API 检索。
        """
        payload = {
            "index_id": self.knowledge_base_id,   # 新版字段名
            "query":    query,
            "top_k":    top_k,
        }
        resp = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/indices/query",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=15,
        )

        # ── 调试：打印原始响应，帮助排查字段名/格式问题 ──────────────────
        print(f"[KnowledgeCore:{self.label}] HTTP {resp.status_code} | body={resp.text[:300]!r}")

        if resp.status_code != 200:
            raise RuntimeError(
                f"HTTP {resp.status_code}：{resp.text[:300]}\n"
                f"请求 payload：{payload}"
            )

        raw_text = resp.text.strip()
        if not raw_text:
            raise RuntimeError(
                "API 返回了空响应体（HTTP 200），"
                f"请确认 knowledge_base_id={self.knowledge_base_id!r} 是否正确"
            )

        try:
            raw = resp.json()
        except Exception as parse_err:
            raise RuntimeError(
                f"响应体无法解析为 JSON：{parse_err}\n"
                f"原始内容：{raw_text[:300]}"
            )

        # ── 兼容两种响应结构 ──────────────────────────────────────────────
        # 结构 A（旧）: {"output": {"nodes": [{"node": {...}, "score": 0.9}]}}
        # 结构 B（新）: {"output": {"records": [{"text": "...", "score": 0.9}]}}
        output = raw.get("output", {})
        nodes  = []

        if isinstance(output, dict):
            nodes = output.get("nodes", []) or output.get("records", []) or []
        elif isinstance(output, list):
            nodes = output

        result = []
        for item in nodes:
            # 结构 A：item = {"node": {"text": ...}, "score": ...}
            node  = item.get("node", item)
            score = item.get("score", node.get("score", 0))
            text  = node.get("text", "") or node.get("content", "")
            meta  = node.get("metadata", {})
            title = ""
            if isinstance(meta, dict):
                title = meta.get("file_name") or meta.get("title") or ""
            result.append({
                "text":  text.strip(),
                "score": float(score),
                "title": title,
            })

        return result

    # ═══════════════════════════════════════════════════════════════
    # 元信息
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "label":             self.label,
            "knowledge_base_id": self.knowledge_base_id,
            "mode":              self._mode,
        }

    def __repr__(self) -> str:
        return (
            f"KnowledgeCore(label={self.label!r}, "
            f"id={self.knowledge_base_id!r}, mode={self._mode})"
        )