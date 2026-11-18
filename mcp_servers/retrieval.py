"""Hybrid retrieval pipeline: BM25 ∥ dense → RRF → rerank (design §1.2, §1.4).

Local PoC backends (design §1.3):
- sparse: rank-bm25 BM25Okapi (real algorithm, k1/b per request)
- dense: mock hashed TF-IDF vectors (EMBED_BACKEND=mock)
- rerank: lexical-overlap sigmoid (RERANK_BACKEND=mock)
Results from mock backends are tagged `indicative` in any KPI report.
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass
from typing import Literal

import numpy as np
from rank_bm25 import BM25Okapi

from contracts.tools import HybridRetrieveIn, HybridRetrieveOut, ScoredChunk
from mcp_servers.vault import Chunk, Vault

TOKEN = re.compile(r"[a-z0-9]+")
DIM = 512


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def _hash_vec(tokens: list[str], idf: dict[int, float] | None = None) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float64)
    for tok in tokens:
        vec[zlib.crc32(tok.encode()) % DIM] += 1.0
    if idf is not None:
        for i in np.nonzero(vec)[0]:
            vec[i] *= idf.get(int(i), 1.0)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


@dataclass
class Index:
    vault: Vault
    chunk_tokens: list[list[str]]
    dense: np.ndarray  # (n_chunks, DIM), L2-normalised
    idf: dict[int, float]
    version: str

    @classmethod
    def build(cls, vault: Vault) -> Index:
        chunk_tokens = [tokenize(c.text) for c in vault.chunks]
        n = len(chunk_tokens)
        df: dict[int, int] = {}
        for toks in chunk_tokens:
            for i in {zlib.crc32(t.encode()) % DIM for t in toks}:
                df[i] = df.get(i, 0) + 1
        idf = {i: float(np.log((1 + n) / (1 + d)) + 1.0) for i, d in df.items()}
        dense = np.stack([_hash_vec(t, idf) for t in chunk_tokens]) if n else np.zeros((0, DIM))
        return cls(vault, chunk_tokens, dense, idf, vault.version)

    @property
    def bm25_docs(self) -> int:
        return len(self.chunk_tokens)


def embed(index: Index, text: str) -> list[float]:
    """Mock dense embedding of a query (EMBED_BACKEND=mock); used by the semantic cache tier."""
    return [round(float(x), 6) for x in _hash_vec(tokenize(text), index.idf)]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + float(np.exp(-x)))


def hybrid_retrieve(index: Index, req: HybridRetrieveIn) -> HybridRetrieveOut:
    stage: dict[Literal["bm25", "dense", "rrf", "rerank"], float] = {}
    q_tokens = tokenize(req.query)
    n = index.bm25_docs
    if n == 0 or not q_tokens:
        return HybridRetrieveOut(
            chunks=[],
            abstain=True,
            top_score=0.0,
            stage_ms={"bm25": 0.0, "dense": 0.0, "rrf": 0.0, "rerank": 0.0},
        )

    t0 = time.perf_counter()
    bm25 = BM25Okapi(index.chunk_tokens, k1=req.k1, b=req.b)
    bm_scores = bm25.get_scores(q_tokens)
    sparse_order = [int(i) for i in np.argsort(-bm_scores)[: req.top_k_sparse] if bm_scores[i] > 0]
    stage["bm25"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    q_vec = _hash_vec(q_tokens, index.idf)
    cos = index.dense @ q_vec
    dense_order = [int(i) for i in np.argsort(-cos)[: req.top_k_dense] if cos[i] > 0]
    stage["dense"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    rrf: dict[int, float] = {}
    for ranked in (sparse_order, dense_order):
        for rank, idx in enumerate(ranked, start=1):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (req.rrf_k + rank)
    fused = sorted(rrf, key=lambda i: -rrf[i])[: req.pool]
    stage["rrf"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    q_set = set(q_tokens)
    scored: list[tuple[float, int]] = []
    for idx in fused:
        overlap = len(q_set & set(index.chunk_tokens[idx])) / len(q_set)
        scored.append((_sigmoid(8.0 * (overlap - 0.5)), idx))
    scored.sort(key=lambda s: -s[0])
    stage["rerank"] = (time.perf_counter() - t0) * 1000

    rrf_rank = {idx: r for r, idx in enumerate(fused, start=1)}
    top_score = scored[0][0] if scored else 0.0
    kept: list[ScoredChunk] = []
    for ce, idx in scored[: req.rerank_k]:
        if ce <= req.threshold:
            continue
        chunk: Chunk = index.vault.chunks[idx]
        kept.append(
            ScoredChunk(
                chunk_id=chunk.chunk_id,
                chunk_hash=chunk.chunk_hash,
                note_id=chunk.note_id,
                text=chunk.text,
                bm25=round(float(bm_scores[idx]), 6),
                cosine=round(float(cos[idx]), 6),
                rrf_rank=rrf_rank[idx],
                ce_score=round(ce, 6),
            )
        )
    return HybridRetrieveOut(
        chunks=kept,
        abstain=top_score <= req.threshold,
        top_score=round(top_score, 6),
        stage_ms={k: round(v, 4) for k, v in stage.items()},
    )
