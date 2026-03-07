# service/knowledge_store.py
"""
RAG 知识库
使用 ChromaDB 做本地向量存储 + DashScope text-embedding-v2 生成向量。
数据持久化在 ./chroma_db/，打包时带上该目录即可离线运行。
"""
import os
from datetime import datetime
from typing import List, Optional
from pathlib import Path


from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

try:
    # 推荐：langchain-dashscope 官方包（pip install langchain-dashscope）
    from langchain_dashscope import DashScopeEmbeddings
except ImportError:
    # 兜底：直接用 dashscope SDK 手写一个兼容类
    import dashscope
    from langchain_core.embeddings import Embeddings

    class DashScopeEmbeddings(Embeddings):
        def __init__(self, model: str = "text-embedding-v2", dashscope_api_key: str = ""):
            self.model = model
            dashscope.api_key = dashscope_api_key or os.getenv("DASHSCOPE_API_KEY", "")

        def embed_documents(self, texts: List[str]) -> List[List[float]]:
            from dashscope import TextEmbedding
            result = []
            # DashScope 单次最多 25 条，分批处理
            batch = 25
            for i in range(0, len(texts), batch):
                resp = TextEmbedding.call(
                    model=self.model,
                    input=texts[i:i + batch],
                )
                if resp.status_code == 200:
                    result.extend([item["embedding"] for item in resp.output["embeddings"]])
                else:
                    raise RuntimeError(f"DashScope embedding 失败: {resp.message}")
            return result

        def embed_query(self, text: str) -> List[float]:
            return self.embed_documents([text])[0]


_PERSIST_DIR = str(Path(__file__).parent.parent / "chroma_db")


def _get_embeddings() -> DashScopeEmbeddings:
    return DashScopeEmbeddings(
        model="text-embedding-v2",
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", ""),
    )


def _collection_name(job_position_id: int) -> str:
    """每个岗位独立一个 Chroma collection，方便按岗位过滤"""
    return f"job_{job_position_id}" if job_position_id > 0 else "job_common"


class KnowledgeStore:
    """
    知识库管理器

    用法：
        ks = KnowledgeStore(db)
        ks.add_text("Spring Boot 是什么...", job_position_id=1, source="spring文档")
        results = ks.retrieve("Spring Boot 自动配置原理", job_position_id=1)
    """

    def __init__(self, db, persist_dir: str = _PERSIST_DIR):
        self.db = db
        self.persist_dir = persist_dir
        self.embeddings = _get_embeddings()
        Path(persist_dir).mkdir(parents=True, exist_ok=True)

    # ── 写入 ──────────────────────────────────────────────────────────────────

    def add_text(
        self,
        text: str,
        job_position_id: int = 0,
        source: str = "manual",
        chunk_size: int = 400,
        chunk_overlap: int = 50,
    ) -> int:
        """分块、向量化并存入 ChromaDB + SQLite 索引，返回写入分块数"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )
        chunks = splitter.split_text(text)
        if not chunks:
            return 0

        now = datetime.now().isoformat()
        docs = [
            Document(
                page_content=c,
                metadata={"job_position_id": job_position_id, "source": source, "chunk_index": i},
            )
            for i, c in enumerate(chunks)
        ]

        Chroma.from_documents(
            docs,
            self.embeddings,
            persist_directory=self.persist_dir,
            collection_name=_collection_name(job_position_id),
        )

        # 同步写入 SQLite（方便查看/管理，不参与检索）
        self.db.executemany(
            "INSERT INTO knowledge_chunk (job_position_id, source, chunk_text, chunk_index, created_at) "
            "VALUES (?,?,?,?,?)",
            [(job_position_id, source, c, i, now) for i, c in enumerate(chunks)],
        )
        return len(chunks)

    def add_file(self, file_path: str, job_position_id: int = 0) -> int:
        """从本地文本文件读取并导入知识库"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.add_text(text, job_position_id=job_position_id, source=path.name)

    def add_qa_pairs(self, qa_list: List[dict], job_position_id: int = 0) -> int:
        """
        批量导入问答对（题库答案直接入库）
        qa_list: [{"question": "...", "answer": "..."}, ...]
        """
        total = 0
        for qa in qa_list:
            text = f"问题：{qa['question']}\n答案：{qa['answer']}"
            total += self.add_text(text, job_position_id=job_position_id, source="题库答案")
        return total

    # ── 检索 ──────────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        job_position_id: int = 0,
        top_k: int = 3,
    ) -> List[str]:
        """返回与 query 最相关的 top_k 个文本片段"""
        try:
            store = Chroma(
                persist_directory=self.persist_dir,
                embedding_function=self.embeddings,
                collection_name=_collection_name(job_position_id),
            )
            docs = store.similarity_search(query, k=top_k)
            return [d.page_content for d in docs]
        except Exception:
            # collection 可能尚未创建（知识库为空）
            return []

    def retrieve_as_context(
        self,
        query: str,
        job_position_id: int = 0,
        top_k: int = 3,
    ) -> str:
        """检索并拼接成可直接插入 prompt 的上下文字符串"""
        chunks = self.retrieve(query, job_position_id, top_k)
        if not chunks:
            return ""
        lines = ["【参考知识库】"]
        for i, c in enumerate(chunks, 1):
            lines.append(f"{i}. {c}")
        return "\n".join(lines)

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def count(self, job_position_id: Optional[int] = None) -> int:
        if job_position_id is not None:
            return self.db.fetchone(
                "SELECT COUNT(*) FROM knowledge_chunk WHERE job_position_id=?",
                (job_position_id,),
            )[0]
        return self.db.fetchone("SELECT COUNT(*) FROM knowledge_chunk")[0]