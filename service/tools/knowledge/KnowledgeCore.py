# service/tools/knowledge/core.py
"""
KnowledgeCore — 阿里云百炼 RAG 检索能力封装

每个 KnowledgeCore 实例对应一个独立的知识库（knowledge_base_id）。
多知识库场景下，分别实例化即可：

    tech_kb      = KnowledgeCore(knowledge_base_id="xxx_tech")
    interview_kb = KnowledgeCore(knowledge_base_id="xxx_interview")

内部支持两种模式（自动探测）：
  official_sdk — alibabacloud-bailian20231229，需要 AK/SK + workspace_id
  http_api     — 仅需 DASHSCOPE_API_KEY，通用 fallback

对外核心接口：
  retrieve(query, top_k)           -> List[str]   原始文本列表
  retrieve_as_context(query, top_k)-> str          拼好的 prompt context 字符串
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

    参数优先级：构造参数 > 环境变量。
    这样不同知识库实例可以各自指定 knowledge_base_id，共用同一套认证信息。
    """

    def __init__(
        self,
        knowledge_base_id: Optional[str] = None,
        api_key: Optional[str] = None,
        workspace_id: Optional[str] = None,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        label: str = "",                        # 可读标签，用于日志区分多知识库
    ):
        self.knowledge_base_id = (
            knowledge_base_id or os.getenv("BAILOU_KNOWLEDGE_BASE_ID", "")
        )
        self.api_key           = api_key           or os.getenv("DASHSCOPE_API_KEY", "")
        self.workspace_id      = workspace_id      or os.getenv("BAILOU_WORKSPACE_ID", "")
        self.access_key_id     = access_key_id     or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        self.access_key_secret = access_key_secret or os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        self.label             = label or self.knowledge_base_id

        if not self.knowledge_base_id:
            raise ValueError(
                f"[KnowledgeCore:{self.label}] knowledge_base_id 未设置，"
                "请传入参数或在 .env 中配置 BAILOU_KNOWLEDGE_BASE_ID"
            )

        # 选择模式
        if (
            _HAS_OFFICIAL_SDK
            and self.access_key_id
            and self.access_key_secret
            and self.workspace_id
        ):
            self._mode = "official_sdk"
            config = open_api_models.Config(
                access_key_id=self.access_key_id,
                access_key_secret=self.access_key_secret,
                endpoint="bailian.cn-beijing.aliyuncs.com",
            )
            self._sdk_client = BailianClient(config)
            print(f"[KnowledgeCore:{self.label}] ✅ 官方 SDK 模式")
        elif self.api_key:
            self._mode = "http_api"
            print(f"[KnowledgeCore:{self.label}] ✅ HTTP API 模式")
        else:
            raise ValueError(
                f"[KnowledgeCore:{self.label}] 请配置 DASHSCOPE_API_KEY 或 "
                "ALIBABA_CLOUD 密钥三件套（AK/SK + workspace_id）"
            )

    # ── 核心检索 ──────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 3) -> List[str]:
        """
        检索并返回文本列表。
        每条结果格式：【文件名】文本内容（相关度: x.xx）
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
        结果为空或出错时返回空字符串，调用方可安全 if context: 判断。
        """
        results = self.retrieve(query, top_k=top_k)
        if not results or results[0].startswith(("📭", "⚠️")):
            return ""
        lines = [f"【参考知识库：{self.label}】"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r}")
        return "\n".join(lines)

    # ── 内部：官方 SDK ────────────────────────────────────────────────────────

    def _retrieve_sdk(self, query: str, top_k: int) -> list[dict]:
        request = bailian_models.RetrieveRequest(
            index_id=self.knowledge_base_id,
            query=query,
            rerank_top_n=top_k,
            dense_similarity_top_k=top_k * 4,
            enable_reranking=True,
        )
        runtime  = util_models.RuntimeOptions()
        response = self._sdk_client.retrieve_with_options(
            self.workspace_id, request, {}, runtime
        )
        body  = getattr(response, "body", None)
        data  = getattr(body, "data", None)
        nodes = getattr(data, "nodes", None) or []

        result = []
        for node in nodes:
            text     = getattr(node, "text", "") or ""
            score    = getattr(node, "score", 0) or 0
            metadata = getattr(node, "metadata", {}) or {}
            if isinstance(metadata, dict):
                title = metadata.get("file_name") or metadata.get("title") or ""
            else:
                title = getattr(metadata, "file_name", "") or getattr(metadata, "title", "") or ""
            result.append({"text": str(text).strip(), "score": float(score), "title": str(title)})
        return result

    # ── 内部：HTTP API ────────────────────────────────────────────────────────

    def _retrieve_http(self, query: str, top_k: int) -> list[dict]:
        resp = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/indices/query",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type":  "application/json",
            },
            json={"pipeline_id": self.knowledge_base_id, "query": query, "top_k": top_k},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        raw   = resp.json()
        nodes = raw.get("output", {}).get("nodes", [])
        result = []
        for item in nodes:
            node  = item.get("node", item)
            score = item.get("score", 0)
            text  = node.get("text", "") or node.get("content", "")
            meta  = node.get("metadata", {})
            title = (meta.get("file_name") or meta.get("title") or "") if isinstance(meta, dict) else ""
            result.append({"text": text.strip(), "score": float(score), "title": title})
        return result

    # ── 元信息 ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "label":             self.label,
            "knowledge_base_id": self.knowledge_base_id,
            "mode":              self._mode,
        }

    def __repr__(self) -> str:
        return f"KnowledgeCore(label={self.label!r}, id={self.knowledge_base_id!r}, mode={self._mode})"