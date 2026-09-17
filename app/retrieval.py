"""
Hybrid retrieval over the indexed policy chunks.

Pipeline (Section 4.2):
  1. Dense semantic search  -- ChromaDB + sentence-transformers embeddings
  2. Sparse lexical search  -- BM25 (rank_bm25)
  3. Fusion                 -- Reciprocal Rank Fusion (RRF)
  4. Reranking              -- cross-encoder reranker on the fused top-N

Run `python -m app.ingestion` first to produce data/policy_chunks.json,
then call `build_indexes()` once at startup to build the Chroma collection
and BM25 index from those chunks.
"""

import json
from pathlib import Path
from typing import List

from rank_bm25 import BM25Okapi
import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import CrossEncoder

from app.models import PolicyChunk, RetrievedEvidence

CHUNKS_PATH = Path("data/policy_chunks.json")
CHROMA_DIR = "data/chroma_db"
COLLECTION_NAME = "policy_chunks"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"          # small, free, local
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # free, local
                                                # swap for BAAI/bge-reranker-base if you have GPU headroom

TOP_K_DENSE = 15
TOP_K_SPARSE = 15
TOP_K_FUSED = 15
TOP_K_FINAL = 6  # after reranking, what we hand to the agents


class HybridRetriever:
    def __init__(self):
        self.chunks: List[PolicyChunk] = []
        self._bm25 = None
        self._tokenized_corpus = None
        self._chroma_client = None
        self._collection = None
        self._reranker = None

    # ---- Index build ----------------------------------------------------

    def load_chunks(self) -> None:
        raw = json.loads(CHUNKS_PATH.read_text())
        self.chunks = [PolicyChunk(**c) for c in raw]
        if not self.chunks:
            raise RuntimeError(
                f"No chunks found in {CHUNKS_PATH}. Run "
                "`python -m app.ingestion <policy.pdf>` first."
            )

    def build_bm25(self) -> None:
        self._tokenized_corpus = [c.text.lower().split() for c in self.chunks]
        self._bm25 = BM25Okapi(self._tokenized_corpus)

    def build_chroma(self) -> None:
        self._chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
        embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        # Recreate collection each time we rebuild the index, so stale
        # chunks never linger.
        try:
            self._chroma_client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self._collection = self._chroma_client.create_collection(
            COLLECTION_NAME, embedding_function=embed_fn
        )
        self._collection.add(
            ids=[c.chunk_id for c in self.chunks],
            documents=[c.text for c in self.chunks],
            metadatas=[
                {"page": c.page, "section": c.section, "subsection": c.subsection or ""}
                for c in self.chunks
            ],
        )

    def build_all(self) -> None:
        self.load_chunks()
        self.build_bm25()
        self.build_chroma()

    def _load_reranker(self):
        if self._reranker is None:
            self._reranker = CrossEncoder(RERANKER_MODEL)
        return self._reranker

    # ---- Query ------------------------------------------------------------

    def _dense_search(self, query: str, k: int = TOP_K_DENSE):
        result = self._collection.query(query_texts=[query], n_results=k)
        chunk_ids = result["ids"][0]
        distances = result["distances"][0]  # smaller = more similar (cosine distance)
        return list(zip(chunk_ids, distances))

    def _sparse_search(self, query: str, k: int = TOP_K_SPARSE):
        scores = self._bm25.get_scores(query.lower().split())
        ranked = sorted(
            zip([c.chunk_id for c in self.chunks], scores),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:k]

    @staticmethod
    def _reciprocal_rank_fusion(dense_ranked, sparse_ranked, k: int = 60):
        """RRF: score(d) = sum over rankers of 1 / (k + rank_in_that_ranker)."""
        scores = {}
        for rank, (chunk_id, _) in enumerate(dense_ranked):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
        for rank, (chunk_id, _) in enumerate(sparse_ranked):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def retrieve(self, query: str, top_k: int = TOP_K_FINAL) -> List[RetrievedEvidence]:
        chunk_by_id = {c.chunk_id: c for c in self.chunks}

        dense_ranked = self._dense_search(query)
        sparse_ranked = self._sparse_search(query)
        dense_scores = dict(dense_ranked)
        sparse_scores = dict(sparse_ranked)

        fused = self._reciprocal_rank_fusion(dense_ranked, sparse_ranked)[:TOP_K_FUSED]

        # Rerank the fused candidates with a cross-encoder.
        reranker = self._load_reranker()
        pairs = [(query, chunk_by_id[cid].text) for cid, _ in fused]
        rerank_scores = reranker.predict(pairs) if pairs else []

        results = []
        for (chunk_id, fused_score), rerank_score in zip(fused, rerank_scores):
            chunk = chunk_by_id[chunk_id]
            results.append(
                RetrievedEvidence(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    page=chunk.page,
                    section=chunk.section,
                    subsection=chunk.subsection,
                    dense_score=dense_scores.get(chunk_id),
                    sparse_score=sparse_scores.get(chunk_id),
                    fused_score=fused_score,
                    rerank_score=float(rerank_score),
                )
            )

        results.sort(key=lambda r: r.rerank_score, reverse=True)
        return results[:top_k]


# Module-level singleton so FastAPI/Streamlit don't rebuild indexes per request.
_retriever_instance: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
        _retriever_instance.build_all()
    return _retriever_instance


if __name__ == "__main__":
    # Quick sanity check -- run after app/ingestion.py has produced
    # data/policy_chunks.json.
    retriever = get_retriever()
    test_queries = [
        "does a 30 day waiting period apply to a new claim",
        "pre-existing disease waiting period 48 months",
        "definition of hospital minimum criteria",
        "cosmetic surgery exclusion",
        "domiciliary hospitalization sub-limit",
    ]
    for q in test_queries:
        print(f"\nQuery: {q!r}")
        for r in retriever.retrieve(q, top_k=3):
            print(f"  [{r.chunk_id}] p.{r.page} {r.section}/{r.subsection or ''} "
                  f"rerank={r.rerank_score:.3f}  {r.text[:80]!r}")
