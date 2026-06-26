#!/usr/bin/env python3
"""
AXIOM Project - Database Module

Handles SQLite database operations for the bibliographic corpus.

Tables:
    sources             - one row per source document (paper or book)
    pubtator_entities   - PubTator3 NER annotations keyed to sources by PMID
    chunks              - paragraph-level retrievable text spans with embeddings

Usage:
    from axiom_db import AxiomDatabase

    db = AxiomDatabase()
    db.initialize()

    source_id = db.add_reference(
        filename="(2013) The Hallmarks of Aging.md",
        source_type="journal_article",
        title="The Hallmarks of Aging",
        year=2013,
        authors="Lopez-Otin, C; Blasco, MA; ...",
        doi="10.1016/j.cell.2013.05.039",
    )
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
DATABASE_PATH = SCRIPT_DIR / "data" / "axiom.db"

# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------

SCHEMA_SQL = """
-- Sources table: bibliographic information for each source document
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT 'journal_article',
    title TEXT,
    authors TEXT,
    journal TEXT,
    year INTEGER,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    doi TEXT,
    pmid TEXT,
    abstract TEXT,
    citation_apa TEXT,
    citation_mla TEXT,
    markdown_path TEXT,
    metadata_source TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sources_year ON sources(year);
CREATE INDEX IF NOT EXISTS idx_sources_doi ON sources(doi);
CREATE INDEX IF NOT EXISTS idx_sources_pmid ON sources(pmid);
CREATE INDEX IF NOT EXISTS idx_sources_type ON sources(source_type);

-- PubTator entities: pre-annotated NER from PubTator3 API
CREATE TABLE IF NOT EXISTS pubtator_entities (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    mention TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    normalized_id TEXT NOT NULL,
    normalized_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    UNIQUE(source_id, mention, entity_type, normalized_id)
);

CREATE INDEX IF NOT EXISTS idx_pubtator_source ON pubtator_entities(source_id);
CREATE INDEX IF NOT EXISTS idx_pubtator_type ON pubtator_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_pubtator_mention ON pubtator_entities(mention);

-- Chunks: paragraph-level retrievable text spans with embeddings
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,

    -- Heading hierarchy (NULL where not applicable)
    chapter TEXT,
    section TEXT,
    subsection TEXT,

    -- Content
    content TEXT NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    token_count INTEGER NOT NULL,

    -- Embedding
    embedding BLOB,
    embedding_model TEXT,
    embedding_dim INTEGER,

    -- Timestamps
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE,
    UNIQUE(source_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
"""

# -----------------------------------------------------------------------------
# Database Class
# -----------------------------------------------------------------------------

class AxiomDatabase:
    """SQLite database interface for AXIOM project."""

    def __init__(self, db_path=None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to data/axiom.db.
        """
        self.db_path = Path(db_path) if db_path else DATABASE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = None

    @property
    def connection(self):
        """Lazy database connection with row factory."""
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

    def close(self):
        """Close database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def initialize(self):
        """Create database schema if not exists."""
        self.connection.executescript(SCHEMA_SQL)
        self.connection.commit()
        return self

    # -------------------------------------------------------------------------
    # Source Operations
    # -------------------------------------------------------------------------

    def add_reference(self, filename, **kwargs):
        """
        Add a new source record.

        Args:
            filename: Markdown filename (required, used as unique key).
            **kwargs: Any column values (source_type, title, authors, year,
                doi, pmid, citation_apa, markdown_path, metadata_source, etc.)

        Returns:
            int: The source ID.
        """
        kwargs["filename"] = filename
        kwargs["created_at"] = datetime.now().isoformat()
        kwargs["updated_at"] = kwargs["created_at"]

        columns = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?" for _ in kwargs])

        cursor = self.connection.execute(
            f"INSERT INTO sources ({columns}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        self.connection.commit()
        return cursor.lastrowid

    def update_reference(self, ref_id, **kwargs):
        """Update an existing source record."""
        if not kwargs:
            return ref_id

        kwargs["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])

        self.connection.execute(
            f"UPDATE sources SET {set_clause} WHERE id = ?",
            list(kwargs.values()) + [ref_id],
        )
        self.connection.commit()
        return ref_id

    def get_reference(self, ref_id):
        cursor = self.connection.execute(
            "SELECT * FROM sources WHERE id = ?", (ref_id,)
        )
        return cursor.fetchone()

    def get_reference_by_filename(self, filename):
        cursor = self.connection.execute(
            "SELECT * FROM sources WHERE filename = ?", (filename,)
        )
        return cursor.fetchone()

    def get_reference_by_doi(self, doi):
        cursor = self.connection.execute(
            "SELECT * FROM sources WHERE doi = ?", (doi,)
        )
        return cursor.fetchone()

    def get_reference_by_pmid(self, pmid):
        cursor = self.connection.execute(
            "SELECT * FROM sources WHERE pmid = ?", (pmid,)
        )
        return cursor.fetchone()

    def get_all_references(self, order_by="year DESC"):
        cursor = self.connection.execute(
            f"SELECT * FROM sources ORDER BY {order_by}"
        )
        return cursor.fetchall()

    def get_all_filenames(self):
        """Get all filenames currently in the sources table."""
        cursor = self.connection.execute("SELECT filename FROM sources")
        return [row[0] for row in cursor.fetchall()]

    def count_references(self):
        cursor = self.connection.execute("SELECT COUNT(*) FROM sources")
        return cursor.fetchone()[0]

    def reference_exists(self, filename):
        cursor = self.connection.execute(
            "SELECT 1 FROM sources WHERE filename = ?", (filename,)
        )
        return cursor.fetchone() is not None

    # -------------------------------------------------------------------------
    # PubTator Entity Operations
    # -------------------------------------------------------------------------

    def add_pubtator_entities_batch(self, source_id, annotations):
        """
        Add PubTator entity annotations for a source in a batch.

        Args:
            source_id: FK to sources.id
            annotations: List of dicts with keys:
                mention, entity_type, normalized_id, normalized_name

        Returns:
            int: Number of annotations submitted (duplicates silently ignored).
        """
        if not annotations:
            return 0

        now = datetime.now().isoformat()
        rows = [
            (
                source_id,
                a["mention"],
                a["entity_type"],
                a["normalized_id"],
                a.get("normalized_name", ""),
                now,
            )
            for a in annotations
        ]

        self.connection.executemany(
            """INSERT OR IGNORE INTO pubtator_entities
               (source_id, mention, entity_type, normalized_id, normalized_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.connection.commit()
        return len(rows)

    def get_pubtator_entities(self, source_id):
        cursor = self.connection.execute(
            "SELECT * FROM pubtator_entities WHERE source_id = ? ORDER BY entity_type, mention",
            (source_id,),
        )
        return cursor.fetchall()

    def pubtator_entities_exist(self, source_id):
        cursor = self.connection.execute(
            "SELECT 1 FROM pubtator_entities WHERE source_id = ? LIMIT 1",
            (source_id,),
        )
        return cursor.fetchone() is not None

    def count_pubtator_entities(self, source_id=None):
        if source_id:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM pubtator_entities WHERE source_id = ?",
                (source_id,),
            )
        else:
            cursor = self.connection.execute("SELECT COUNT(*) FROM pubtator_entities")
        return cursor.fetchone()[0]

    def get_pubtator_mentions_for_source(self, source_id):
        """Get unique PubTator mention texts for a source (lowercased)."""
        cursor = self.connection.execute(
            "SELECT DISTINCT lower(mention) as mention FROM pubtator_entities WHERE source_id = ?",
            (source_id,),
        )
        return [row["mention"] for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Chunk Operations
    # -------------------------------------------------------------------------

    def add_chunks_batch(self, source_id, chunks):
        """
        Add multiple chunks for a source in a single batch.

        Args:
            source_id: FK to sources.id
            chunks: List of dicts with required keys:
                      chunk_index, content, char_start, char_end, token_count
                    Optional keys:
                      chapter, section, subsection,
                      embedding (bytes), embedding_model, embedding_dim

        Returns:
            int: Number of chunks inserted.
        """
        if not chunks:
            return 0

        now = datetime.now().isoformat()
        rows = [
            (
                source_id,
                c["chunk_index"],
                c.get("chapter"),
                c.get("section"),
                c.get("subsection"),
                c["content"],
                c["char_start"],
                c["char_end"],
                c["token_count"],
                c.get("embedding"),
                c.get("embedding_model"),
                c.get("embedding_dim"),
                now,
                now,
            )
            for c in chunks
        ]

        self.connection.executemany(
            """INSERT INTO chunks
               (source_id, chunk_index, chapter, section, subsection,
                content, char_start, char_end, token_count,
                embedding, embedding_model, embedding_dim,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.connection.commit()
        return len(rows)

    def update_chunk_embedding(self, chunk_id, embedding, model, dim):
        """
        Set or update the embedding for a chunk.

        Args:
            chunk_id: Chunk ID
            embedding: Raw bytes (numpy float32 array converted via tobytes())
            model: Embedding model identifier (e.g., 'BAAI/bge-base-en-v1.5')
            dim: Embedding dimension (e.g., 768)
        """
        self.connection.execute(
            """UPDATE chunks
               SET embedding = ?, embedding_model = ?, embedding_dim = ?, updated_at = ?
               WHERE id = ?""",
            (embedding, model, dim, datetime.now().isoformat(), chunk_id),
        )
        self.connection.commit()
        return chunk_id

    def get_chunk(self, chunk_id):
        """Get a single chunk by ID."""
        cursor = self.connection.execute(
            "SELECT * FROM chunks WHERE id = ?", (chunk_id,)
        )
        return cursor.fetchone()

    def get_chunks_for_source(self, source_id, sections=None, include_content=True):
        """
        Get chunks for a source, ordered by chunk_index.

        Args:
            source_id: FK to sources.id.
            sections: Optional list of section-name fragments. When provided,
                only chunks whose section matches at least one fragment are
                returned (case-insensitive substring match, OR-combined).
                None or empty = all sections.
            include_content: When False, the heavy content (and embedding)
                columns are omitted from the SELECT so the caller can pull a
                cheap section map without paying the body-text cost. The
                returned rows then carry no 'content' key. Default True.

        Returns:
            List of sqlite3.Row ordered by chunk_index.
        """
        if include_content:
            columns = "*"
        else:
            columns = (
                "id, source_id, chunk_index, chapter, section, subsection, "
                "char_start, char_end, token_count, embedding_model, "
                "embedding_dim, created_at, updated_at"
            )
        query = f"SELECT {columns} FROM chunks WHERE source_id = ?"
        params = [source_id]
        if sections:
            clauses = " OR ".join(
                "section LIKE ? COLLATE NOCASE" for _ in sections
            )
            query += f" AND ({clauses})"
            params.extend(f"%{s}%" for s in sections)
        query += " ORDER BY chunk_index"
        cursor = self.connection.execute(query, params)
        return cursor.fetchall()

    def get_chunks_without_embeddings(self, source_id=None):
        """Get chunks that don't have embeddings yet."""
        if source_id is not None:
            cursor = self.connection.execute(
                "SELECT * FROM chunks WHERE source_id = ? AND embedding IS NULL ORDER BY id",
                (source_id,),
            )
        else:
            cursor = self.connection.execute(
                "SELECT * FROM chunks WHERE embedding IS NULL ORDER BY id"
            )
        return cursor.fetchall()

    def get_all_chunks_with_embeddings(self, source_type=None, year_min=None, year_max=None):
        """
        Get all chunks that have embeddings, joined with source metadata.

        Used by the MCP server for semantic search. Returns chunk fields
        plus filename, title, source_type, year, authors, citation_apa,
        doi, and pmid from the source.

        Args:
            source_type: Optional filter on source.source_type
            year_min: Optional minimum year (inclusive)
            year_max: Optional maximum year (inclusive)
        """
        query = """
            SELECT c.*, s.filename, s.title, s.source_type, s.year,
                   s.authors, s.citation_apa, s.doi, s.pmid
            FROM chunks c
            JOIN sources s ON c.source_id = s.id
            WHERE c.embedding IS NOT NULL
        """
        params = []
        if source_type is not None:
            query += " AND s.source_type = ?"
            params.append(source_type)
        if year_min is not None:
            query += " AND s.year >= ?"
            params.append(year_min)
        if year_max is not None:
            query += " AND s.year <= ?"
            params.append(year_max)
        query += " ORDER BY c.id"

        cursor = self.connection.execute(query, params)
        return cursor.fetchall()

    def chunks_exist_for_source(self, source_id):
        """Check whether any chunks exist for a source (for idempotency)."""
        cursor = self.connection.execute(
            "SELECT 1 FROM chunks WHERE source_id = ? LIMIT 1", (source_id,)
        )
        return cursor.fetchone() is not None

    def count_chunks(self, source_id=None):
        """Count chunks for a source, or all chunks if source_id is None."""
        if source_id is not None:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_id = ?", (source_id,)
            )
        else:
            cursor = self.connection.execute("SELECT COUNT(*) FROM chunks")
        return cursor.fetchone()[0]

    def delete_chunks_for_source(self, source_id):
        """Delete all chunks for a source. Returns number of rows deleted."""
        cursor = self.connection.execute(
            "DELETE FROM chunks WHERE source_id = ?", (source_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_stats(self):
        """Get database statistics."""
        return {
            "sources": self.count_references(),
            "pubtator_entities": self.count_pubtator_entities(),
            "chunks": self.count_chunks(),
        }


# -----------------------------------------------------------------------------
# Filename Parser
# -----------------------------------------------------------------------------

def parse_filename(filename):
    """
    Extract year and title from markdown filename.

    Expected format: "(YYYY) Title text here.md"

    Args:
        filename: Markdown filename

    Returns:
        dict: {"year": int or None, "title": str or None}
    """
    result = {"year": None, "title": None}

    match = re.match(r"^\((\d{4})\)\s+(.+)\.md$", filename)
    if match:
        result["year"] = int(match.group(1))
        title = match.group(2).strip()
        # Convention: " - " in filenames replaces ":" in original titles
        title = re.sub(r"\s+-\s+", ": ", title)
        result["title"] = title

    return result
