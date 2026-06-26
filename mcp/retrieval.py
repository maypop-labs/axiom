#!/usr/bin/env python3
"""
AXIOM Project - Retrieval Module

Embedding cache and search logic for the AXIOM MCP server.

Loads at startup:
    1. The bge-base-en-v1.5 embedding model (~440MB, GPU if available)
    2. All chunk embeddings from SQLite into a numpy matrix (~239MB)
    3. Aligned per-chunk metadata for filtering and result formatting

Provides three search paths:
    semantic_search   cosine similarity in embedding space
    keyword_search    word-bounded GLOB matching (sqlite-side)
    hybrid_search     reciprocal rank fusion of the above

All three accept the same filter set (source_type, year_min, year_max,
exclude_references) and return result dicts with chunk content plus
source metadata for direct citation.
"""

import logging
import re
import sys
import time
from pathlib import Path

import numpy as np

try:
    import torch
except ImportError:
    torch = None

from sentence_transformers import SentenceTransformer

# Make lib/ importable
SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_LIB = SCRIPT_DIR.parent / "Python" / "lib"
sys.path.insert(0, str(PYTHON_LIB))

from axiom_db import AxiomDatabase


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

EMBEDDING_MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBEDDING_DIM = 768

# Reciprocal Rank Fusion smoothing constant. 60 is the de facto default
# from the original RRF paper and is insensitive to exact value.
RRF_K = 60

# Section names that look like reference lists. Match is performed after
# stripping leading punctuation, lowercasing, and collapsing whitespace.
REFERENCE_SECTION_RE = re.compile(
    r"^\s*[\"']?\s*("
    r"references?|bibliography|literature\s+cited|works\s+cited|reference\s+list"
    r")\s*[.:]?\s*$",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _is_reference_section(section):
    """Return True if a section name looks like a reference/bibliography list."""
    if not section:
        return False
    return bool(REFERENCE_SECTION_RE.match(section))


def _word_bounded_glob(term):
    """Build a SQLite GLOB pattern that matches `term` as a whole word."""
    return f"*[^a-zA-Z0-9_]{term}[^a-zA-Z0-9_]*"


def _word_count_regex(term):
    """Build a Python regex that counts whole-word occurrences of `term`."""
    return re.compile(
        r"(?:^|[^a-zA-Z0-9_])" + re.escape(term) + r"(?:[^a-zA-Z0-9_]|$)"
    )


# -----------------------------------------------------------------------------
# Retriever
# -----------------------------------------------------------------------------

class AxiomRetriever:
    """
    Loads embeddings into memory once at startup and runs retrieval against them.

    The retriever holds:
        embeddings:   (N, EMBEDDING_DIM) float32 numpy array, L2-normalized
        metadata:     list of dicts, length N, parallel to `embeddings`
        chunk_id_to_idx: {chunk_id: index_into_embeddings}

    All search methods return list[dict]; see _format_results for fields.
    """

    def __init__(self, db_path=None):
        self.db = AxiomDatabase(db_path=db_path) if db_path else AxiomDatabase()
        self.model = None
        self.embeddings = np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        self.metadata = []
        self.chunk_id_to_idx = {}
        # Precomputed filter arrays (set in _load_embedding_cache)
        self._is_reference = np.empty(0, dtype=bool)
        self._source_types = []
        self._years = np.empty(0, dtype=np.int32)
        self._loaded = False

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def load(self):
        """Initialize DB, load the embedding model and the embedding cache."""
        if self._loaded:
            return
        self.db.initialize()
        self._load_model()
        self._load_embedding_cache()
        self._loaded = True

    def _load_model(self):
        device = "cuda" if (torch is not None and torch.cuda.is_available()) else "cpu"
        t0 = time.time()
        logger.info("Loading embedding model %s on %s", EMBEDDING_MODEL_NAME, device)
        self.model = SentenceTransformer(EMBEDDING_MODEL_NAME, device=device)
        logger.info("Model loaded in %.1fs", time.time() - t0)

    def _load_embedding_cache(self):
        t0 = time.time()
        rows = self.db.get_all_chunks_with_embeddings()

        embedding_list = []
        metadata = []
        skipped = 0

        for row in rows:
            if row["embedding"] is None or row["embedding_dim"] != EMBEDDING_DIM:
                skipped += 1
                continue
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if vec.shape[0] != EMBEDDING_DIM:
                skipped += 1
                continue
            embedding_list.append(vec)
            metadata.append({
                "chunk_id": row["id"],
                "source_id": row["source_id"],
                "chunk_index": row["chunk_index"],
                "section": row["section"],
                "token_count": row["token_count"],
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "filename": row["filename"],
                "title": row["title"],
                "source_type": row["source_type"],
                "year": row["year"],
                "authors": row["authors"],
                "citation_apa": row["citation_apa"],
                "doi": row["doi"],
                "pmid": row["pmid"],
                "is_reference": _is_reference_section(row["section"]),
            })

        if embedding_list:
            self.embeddings = np.stack(embedding_list).astype(np.float32, copy=False)
        self.metadata = metadata
        self.chunk_id_to_idx = {m["chunk_id"]: i for i, m in enumerate(metadata)}
        self._is_reference = np.array(
            [m["is_reference"] for m in metadata], dtype=bool
        )
        self._source_types = [m["source_type"] for m in metadata]
        self._years = np.array(
            [m["year"] if m["year"] is not None else -1 for m in metadata],
            dtype=np.int32,
        )

        elapsed = time.time() - t0
        n_ref = int(self._is_reference.sum())
        mb = self.embeddings.nbytes / 1024 / 1024
        logger.info(
            "Loaded %d chunk embeddings (%.1f MB) in %.1fs; %d marked as references; %d skipped",
            len(metadata), mb, elapsed, n_ref, skipped,
        )

    # -------------------------------------------------------------------------
    # Filtering
    # -------------------------------------------------------------------------

    def _build_filter_mask(self, source_type, year_min, year_max, exclude_references):
        """Return a boolean mask of length N, or None if no filters are active."""
        if (
            source_type is None
            and year_min is None
            and year_max is None
            and not exclude_references
        ):
            return None

        n = len(self.metadata)
        mask = np.ones(n, dtype=bool)
        if exclude_references:
            mask &= ~self._is_reference
        if source_type is not None:
            mask &= np.array(
                [st == source_type for st in self._source_types], dtype=bool
            )
        if year_min is not None:
            mask &= (self._years >= year_min) & (self._years != -1)
        if year_max is not None:
            mask &= (self._years <= year_max) & (self._years != -1)
        return mask

    # -------------------------------------------------------------------------
    # Result formatting
    # -------------------------------------------------------------------------

    def _format_results(self, indices, scores=None, score_map=None):
        """Turn a list of metadata indices into result dicts with chunk content."""
        results = []
        for rank, idx in enumerate(indices, start=1):
            m = self.metadata[idx]
            chunk = self.db.get_chunk(m["chunk_id"])
            if chunk is None:
                continue
            if score_map is not None:
                score = float(score_map.get(idx, 0.0))
            elif scores is not None:
                score = float(scores[idx])
            else:
                score = 0.0
            results.append({
                "chunk_id": m["chunk_id"],
                "source_id": m["source_id"],
                "filename": m["filename"],
                "title": m["title"],
                "year": m["year"],
                "section": m["section"],
                "token_count": m["token_count"],
                "rank": rank,
                "score": round(score, 4),
                "content": chunk["content"],
                "citation": m["citation_apa"],
            })
        return results

    # -------------------------------------------------------------------------
    # Semantic search
    # -------------------------------------------------------------------------

    def semantic_search(
        self, query, k=10, source_type=None, year_min=None, year_max=None,
        exclude_references=True,
    ):
        """Cosine similarity over the embedding cache. See module docstring."""
        if not self._loaded:
            raise RuntimeError("Retriever not loaded; call load() first.")
        if not query or not query.strip():
            return []
        if len(self.metadata) == 0:
            return []

        t0 = time.time()
        q_vec = self.model.encode(
            query.strip(),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype(np.float32, copy=False)

        scores = self.embeddings @ q_vec  # (N,)

        mask = self._build_filter_mask(source_type, year_min, year_max, exclude_references)
        if mask is not None:
            valid_idx = np.where(mask)[0]
            if len(valid_idx) == 0:
                return []
            valid_scores = scores[valid_idx]
            kk = min(k, len(valid_idx))
            partial = np.argpartition(-valid_scores, kk - 1)[:kk] if kk < len(valid_idx) else np.arange(len(valid_idx))
            order = partial[np.argsort(-valid_scores[partial])]
            top_idx = valid_idx[order]
        else:
            kk = min(k, len(scores))
            partial = np.argpartition(-scores, kk - 1)[:kk] if kk < len(scores) else np.arange(len(scores))
            top_idx = partial[np.argsort(-scores[partial])]

        results = self._format_results(list(top_idx), scores=scores)
        logger.info(
            "semantic_search('%s', k=%d) -> %d results in %.3fs",
            (query[:60] + "...") if len(query) > 60 else query,
            k, len(results), time.time() - t0,
        )
        return results

    # -------------------------------------------------------------------------
    # Keyword search
    # -------------------------------------------------------------------------

    def keyword_search(
        self, query, k=10, source_type=None, year_min=None, year_max=None,
        exclude_references=True,
    ):
        """Word-bounded GLOB matching across chunks. Multiple terms are AND."""
        if not self._loaded:
            raise RuntimeError("Retriever not loaded; call load() first.")
        if not query or not query.strip():
            return []

        terms = [t for t in query.split() if t]
        if not terms:
            return []

        t0 = time.time()

        where = []
        params = []
        for term in terms:
            where.append("c.content GLOB ?")
            params.append(_word_bounded_glob(term))

        sql = (
            "SELECT c.id, c.content "
            "FROM chunks c JOIN sources s ON s.id = c.source_id "
            "WHERE " + " AND ".join(where)
        )
        if source_type is not None:
            sql += " AND s.source_type = ?"
            params.append(source_type)
        if year_min is not None:
            sql += " AND s.year >= ?"
            params.append(year_min)
        if year_max is not None:
            sql += " AND s.year <= ?"
            params.append(year_max)

        cursor = self.db.connection.execute(sql, params)
        rows = cursor.fetchall()

        # Score by total whole-word occurrences across all terms (cheap
        # proxy for relevance; will be replaced with FTS5/BM25 later).
        regexes = [_word_count_regex(t) for t in terms]
        scored = []
        for row in rows:
            chunk_id = row["id"]
            idx = self.chunk_id_to_idx.get(chunk_id)
            if idx is None:
                continue
            if exclude_references and self.metadata[idx]["is_reference"]:
                continue
            content = row["content"]
            score = sum(len(rx.findall(content)) for rx in regexes)
            scored.append((idx, score))

        scored.sort(key=lambda x: -x[1])
        top = scored[:k]
        indices = [idx for idx, _ in top]
        score_map = {idx: float(s) for idx, s in top}

        results = self._format_results(indices, score_map=score_map)
        logger.info(
            "keyword_search('%s', k=%d) -> %d results (%d candidates) in %.3fs",
            (query[:60] + "...") if len(query) > 60 else query,
            k, len(results), len(rows), time.time() - t0,
        )
        return results

    # -------------------------------------------------------------------------
    # Hybrid search (Reciprocal Rank Fusion)
    # -------------------------------------------------------------------------

    def hybrid_search(
        self, query, k=10, source_type=None, year_min=None, year_max=None,
        exclude_references=True,
    ):
        """
        Run semantic_search and keyword_search, fuse with Reciprocal Rank
        Fusion. Each input list is oversampled to 3*k so the fusion has
        room to choose.
        """
        if not self._loaded:
            raise RuntimeError("Retriever not loaded; call load() first.")
        if not query or not query.strip():
            return []

        t0 = time.time()
        oversample = max(k * 3, 30)

        sem = self.semantic_search(
            query, k=oversample, source_type=source_type,
            year_min=year_min, year_max=year_max,
            exclude_references=exclude_references,
        )
        kw = self.keyword_search(
            query, k=oversample, source_type=source_type,
            year_min=year_min, year_max=year_max,
            exclude_references=exclude_references,
        )

        rrf = {}
        for r in sem:
            cid = r["chunk_id"]
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (RRF_K + r["rank"])
        for r in kw:
            cid = r["chunk_id"]
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (RRF_K + r["rank"])

        by_id = {}
        for r in sem + kw:
            by_id.setdefault(r["chunk_id"], r)

        ranked_ids = sorted(rrf.keys(), key=lambda c: -rrf[c])[:k]

        results = []
        for rank, cid in enumerate(ranked_ids, start=1):
            r = dict(by_id[cid])
            r["rank"] = rank
            r["score"] = round(rrf[cid], 4)
            results.append(r)

        logger.info(
            "hybrid_search('%s', k=%d) -> %d results (sem=%d, kw=%d) in %.3fs",
            (query[:60] + "...") if len(query) > 60 else query,
            k, len(results), len(sem), len(kw), time.time() - t0,
        )
        return results
