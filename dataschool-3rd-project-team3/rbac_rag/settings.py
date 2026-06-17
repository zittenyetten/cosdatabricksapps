import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RagSettings:
    llm_model: str = "databricks-qwen3-next-80b-a3b-instruct"
    embedding_model: str = "databricks-qwen3-embedding-0-6b"
    vs_endpoint_name: str = "cos-rag-endpoint"
    vs_index_name: str = "cos_adb.search.metadata_chunks_index"
    vs_source_table: str = "cos_adb.search.metadata_chunks"
    catalog: str = "cos_adb"
    log_table: str = "cos_adb.governance.rag_sql_query_logs"
    top_k_default: int = 5

    @classmethod
    def from_env(cls) -> "RagSettings":
        return cls(
            llm_model=os.getenv("RBAC_RAG_LLM_MODEL", cls.llm_model),
            embedding_model=os.getenv("RBAC_RAG_EMBEDDING_MODEL", cls.embedding_model),
            vs_endpoint_name=os.getenv("RBAC_RAG_VS_ENDPOINT_NAME", cls.vs_endpoint_name),
            vs_index_name=os.getenv("RBAC_RAG_VS_INDEX_NAME", cls.vs_index_name),
            vs_source_table=os.getenv("RBAC_RAG_VS_SOURCE_TABLE", cls.vs_source_table),
            catalog=os.getenv("RBAC_RAG_CATALOG", cls.catalog),
            log_table=os.getenv("RBAC_RAG_LOG_TABLE", cls.log_table),
            top_k_default=int(os.getenv("RBAC_RAG_TOP_K_DEFAULT", str(cls.top_k_default))),
        )


@dataclass
class RuntimeFlags:
    selected_role: str = "GENERAL_EMPLOYEE"
    rbac_enabled: bool = True
    post_check_enabled: bool = True
    question: str = ""
    allowed_domains: list[str] = field(default_factory=list)
