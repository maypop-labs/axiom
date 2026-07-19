#!/usr/bin/env python3
"""
AXIOM Project - Graph Database Module

Handles SQLite database operations for the curated mechanistic knowledge
graph that grows from corpus-grounded answers.

Tables:
    nodes              - one row per biological entity (gene, protein,
                         miRNA, PTM-state, complex, process, phenotype,
                         compartment, condition, small_molecule, other).
                         Carries a JSON cross_references field for
                         canonical IDs from public databases (LEXICON).
    node_aliases       - alternate names attached to nodes (per-node
                         unique; same alias string may legitimately
                         point to multiple nodes)
    node_observations  - per-node corpus-derived findings. One row per
                         supporting chunk. Append-by-default with
                         in-place edit for rewrites.
    edges              - directed, typed relations between nodes;
                         multigraph by edge_type (one edge per
                         (subject, object, type))
    edge_conditions    - preconditions that scope when an edge holds
    edge_evidence      - per-observation provenance records; coverage
                         of an edge is COUNT(*) of its evidence rows

Usage:
    from axiom_graph_db import AxiomGraphDatabase

    db = AxiomGraphDatabase()
    db.initialize()

    yap_id = db.add_node("YAP", "protein")
    db.add_alias(yap_id, "YAP1")
    tead_id = db.add_node("TEAD", "protein")
    edge_id = db.add_edge(yap_id, tead_id, "binds")
    db.add_condition(edge_id, "compartment", "nucleus")
    db.add_evidence(
        edge_id,
        source_filename="(2022) Sladitschek-Martens - YAP TAZ.md",
        chunk_id=12345,
        method="ChIP-seq",
        cell_system="primary fibroblasts",
        conversation_question="How do YAP and TEAD interact?",
    )
"""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
DATABASE_PATH = SCRIPT_DIR / "data" / "axiom_graph.db"

# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------

SCHEMA_SQL = """
-- Nodes: one row per biological entity. Open-ended node_type taxonomy.
-- cross_references is a JSON-serialized dict of canonical identifiers from
-- authoritative databases (NCBI gene id, Ensembl, UniProt, HGNC, OMIM,
-- PubChem CID, GO id, InChIKey, etc.), populated from LEXICON lookups.
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    node_type TEXT NOT NULL,
    notes TEXT,
    cross_references TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_name, node_type)
);

CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(canonical_name);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);

-- Aliases: alternate names. Per-node uniqueness only; the same alias
-- string may legitimately point to multiple nodes (e.g., the gene "AKT"
-- vs the protein "AKT"). Lookups by alias return all matches.
CREATE TABLE IF NOT EXISTS node_aliases (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    UNIQUE(node_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_aliases_alias ON node_aliases(alias);

-- Observations: per-node corpus-derived findings. One row per chunk
-- citation. Like edge_evidence in shape, but anchored to a node and
-- carrying the observation text directly (since the claim is the
-- observation itself, not the edge between two endpoints).
--
-- updated_at exists because observations support in-place edits when
-- a rewrite proposal supersedes an older entry with greater precision.
-- The chunk_id, source_filename, and conversation_* fields are treated
-- as immutable after creation; only `observation` and `notes` may be
-- edited via update_observation.
CREATE TABLE IF NOT EXISTS node_observations (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL,
    observation TEXT NOT NULL,

    -- Grounding type (V13). One of: corpus_primary, corpus_inline_cited,
    -- lexicon, common_knowledge, background_weak. Open-ended at the SQL
    -- layer (no CHECK constraint); validated at write time by
    -- GraphAccessor in the MCP layer.
    grounding_type TEXT NOT NULL DEFAULT 'corpus_primary',

    -- Assertion polarity (V18). One of: 'asserting' (this source supports
    -- the observation) or 'refuting' (this source tested the claim and
    -- found it absent). Orthogonal to grounding_type: grounding_type says
    -- where the row came from, assertion_status says which way it cuts.
    -- A 'refuting' row must carry `method` or a justification naming the
    -- test that returned null; validated by GraphAccessor, not by SQLite.
    assertion_status TEXT NOT NULL DEFAULT 'asserting',

    -- Source-type-specific provenance, stored as JSON. Schema by
    -- grounding_type:
    --   corpus_primary       : NULL
    --   corpus_inline_cited  : {"upstream_reference": "<as-cited-in-chunk>"}
    --   lexicon              : {"lexicon_source": "DrugBank",
    --                           "lexicon_identifier": "DB00001",
    --                           "retrieval_date": "YYYY-MM-DD"}
    --   common_knowledge     : {"justification": "<brief>"}
    --   background_weak      : {"justification": "<brief>",
    --                           "weakest_grounding": true}
    provenance_extra TEXT,

    -- Corpus layer (cross-DB; validated at write time, not by SQLite).
    -- Required when grounding_type is corpus_primary or corpus_inline_cited;
    -- NULL for lexicon, common_knowledge, background_weak.
    source_filename TEXT,
    source_doi TEXT,
    source_pmid TEXT,
    chunk_id INTEGER,
    method TEXT,
    cell_system TEXT,

    -- Conversation layer
    conversation_date TEXT NOT NULL,
    conversation_question TEXT,

    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_observations_node ON node_observations(node_id);
CREATE INDEX IF NOT EXISTS idx_observations_filename ON node_observations(source_filename);
CREATE INDEX IF NOT EXISTS idx_observations_pmid ON node_observations(source_pmid);
CREATE INDEX IF NOT EXISTS idx_observations_chunk ON node_observations(chunk_id);

-- Edges: directed, typed. UNIQUE(subject, object, edge_type) means one
-- edge per directed-typed-relation; different edge types between the
-- same node pair are separate edges (multigraph by edge_type).
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL,
    object_id INTEGER NOT NULL,
    edge_type TEXT NOT NULL,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subject_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (object_id) REFERENCES nodes(id) ON DELETE CASCADE,
    UNIQUE(subject_id, object_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_subject ON edges(subject_id);
CREATE INDEX IF NOT EXISTS idx_edges_object ON edges(object_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(edge_type);

-- Conditions: preconditions that scope when the edge holds. Free-form
-- (type, value) pairs: cell_type=HEK293, compartment=nucleus,
-- ptm_state=S473-phosphorylated, cofactor=ATP, etc.
--
-- evidence_id (V18) controls the scope of the condition:
--   NULL     -> the condition scopes the whole edge (the original and
--               still the default behaviour).
--   not NULL -> the condition scopes exactly one evidence row. This is
--               what lets one edge carry two evidence rows that differ
--               only in the test applied (the ITP log-rank versus Gehan
--               case), which edge-level conditions alone cannot express.
--
-- Uniqueness is enforced by two partial indexes rather than a table-level
-- UNIQUE, because SQLite treats NULLs as distinct in a UNIQUE constraint:
-- a plain UNIQUE(edge_id, evidence_id, condition_type, condition_value)
-- would silently stop de-duplicating edge-scoped conditions.
--
-- The evidence_id index and the two partial unique indexes are NOT created
-- here. They live in _apply_migrations, which runs after this script and is
-- therefore the only place where the column is guaranteed to exist. Creating
-- them here would abort executescript on any pre-V18 database, because
-- CREATE TABLE IF NOT EXISTS is a no-op on the old table and the very next
-- index statement would reference a column that the migration has not added
-- yet. Do not move them back.
CREATE TABLE IF NOT EXISTS edge_conditions (
    id INTEGER PRIMARY KEY,
    edge_id INTEGER NOT NULL,
    evidence_id INTEGER DEFAULT NULL,
    condition_type TEXT NOT NULL,
    condition_value TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (edge_id) REFERENCES edges(id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id) REFERENCES edge_evidence(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conditions_edge ON edge_conditions(edge_id);
CREATE INDEX IF NOT EXISTS idx_conditions_type ON edge_conditions(condition_type);

-- Evidence: per-observation provenance. Coverage is derived as
-- COUNT(*) of evidence rows for an edge, optionally grouped by
-- cell_system, method, or condition. Cross-DB references to axiom.db
-- are stored as plain strings/ints; SQLite cannot enforce them.
CREATE TABLE IF NOT EXISTS edge_evidence (
    id INTEGER PRIMARY KEY,
    edge_id INTEGER NOT NULL,

    -- Grounding type (V13). One of: corpus_primary, corpus_inline_cited,
    -- lexicon, common_knowledge, background_weak. Open-ended at the SQL
    -- layer (no CHECK constraint); validated at write time by
    -- GraphAccessor in the MCP layer.
    grounding_type TEXT NOT NULL DEFAULT 'corpus_primary',

    -- Assertion polarity (V18). One of: 'asserting' or 'refuting'. See
    -- node_observations.assertion_status. The edge-level rollup used by
    -- the analysis layer is derived from these rows, never stored:
    -- refuted  = at least one evidence row, all of them refuting
    -- contested = both asserting and refuting rows present
    -- asserted = everything else, including zero-evidence edges
    assertion_status TEXT NOT NULL DEFAULT 'asserting',

    -- Source-type-specific provenance, stored as JSON. Schema by
    -- grounding_type matches node_observations.provenance_extra.
    provenance_extra TEXT,

    -- Corpus layer (cross-DB; validated at write time, not by SQLite).
    -- Required when grounding_type is corpus_primary or corpus_inline_cited;
    -- NULL for lexicon, common_knowledge, background_weak.
    source_filename TEXT,
    source_doi TEXT,
    source_pmid TEXT,
    chunk_id INTEGER,
    method TEXT,
    cell_system TEXT,

    -- Conversation layer
    conversation_date TEXT NOT NULL,
    conversation_question TEXT,

    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (edge_id) REFERENCES edges(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_evidence_edge ON edge_evidence(edge_id);
CREATE INDEX IF NOT EXISTS idx_evidence_filename ON edge_evidence(source_filename);
CREATE INDEX IF NOT EXISTS idx_evidence_pmid ON edge_evidence(source_pmid);
CREATE INDEX IF NOT EXISTS idx_evidence_chunk ON edge_evidence(chunk_id);
"""

# -----------------------------------------------------------------------------
# Database Class
# -----------------------------------------------------------------------------

class AxiomGraphDatabase:
    """SQLite database interface for the AXIOM mechanistic knowledge graph."""

    def __init__(self, db_path=None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to data/axiom_graph.db.
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
        """Create database schema if not exists, then apply migrations."""
        self.connection.executescript(SCHEMA_SQL)
        self._apply_migrations()
        self.connection.commit()
        return self

    def _apply_migrations(self):
        """
        Apply schema migrations for databases created before later columns
        existed. Idempotent and cheap; safe to run on every initialize().

        Migration log:
            - V8 -> V9: add `cross_references TEXT` column to `nodes`.
              The CREATE TABLE IF NOT EXISTS path adds this for fresh
              databases; this branch covers existing databases.
            - V12 -> V13: add `grounding_type` and `provenance_extra`
              columns to `edge_evidence` and `node_observations`, plus
              an index on `grounding_type` for both tables. Existing
              rows backfill to grounding_type = 'corpus_primary' with
              provenance_extra = NULL. Semantic validation of which
              fields belong with which grounding_type lives in
              GraphAccessor at the MCP layer.
            - V17 -> V18: add `assertion_status` to `edge_evidence` and
              `node_observations`, backfilling every existing row to
              'asserting' (correct: nothing recorded before V18 was a
              refutation). Rebuild `edge_conditions` to add a nullable
              `evidence_id` and to replace the table-level UNIQUE with
              two partial unique indexes. The rebuild is required rather
              than a plain ADD COLUMN because SQLite cannot alter a
              constraint in place, and because UNIQUE treats NULLs as
              distinct, which would have silently disabled edge-scoped
              condition de-duplication.
        """
        cursor = self.connection.execute("PRAGMA table_info(nodes)")
        node_columns = {row[1] for row in cursor.fetchall()}
        if "cross_references" not in node_columns:
            self.connection.execute(
                "ALTER TABLE nodes ADD COLUMN cross_references TEXT"
            )

        for table in ("edge_evidence", "node_observations"):
            cursor = self.connection.execute(f"PRAGMA table_info({table})")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "grounding_type" not in existing_cols:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN grounding_type TEXT "
                    f"NOT NULL DEFAULT 'corpus_primary'"
                )
            if "provenance_extra" not in existing_cols:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN provenance_extra TEXT"
                )

        # Indices on the new column. Idempotent; safe to re-run.
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_grounding_type "
            "ON edge_evidence(grounding_type)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_observations_grounding_type "
            "ON node_observations(grounding_type)"
        )

        # V17 -> V18: assertion polarity on evidence and observations.
        for table in ("edge_evidence", "node_observations"):
            cursor = self.connection.execute(f"PRAGMA table_info({table})")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "assertion_status" not in existing_cols:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN assertion_status TEXT "
                    f"NOT NULL DEFAULT 'asserting'"
                )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_assertion_status "
            "ON edge_evidence(assertion_status)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_observations_assertion_status "
            "ON node_observations(assertion_status)"
        )

        # V17 -> V18: per-evidence condition scoping. Needs a table rebuild.
        cursor = self.connection.execute("PRAGMA table_info(edge_conditions)")
        condition_cols = {row[1] for row in cursor.fetchall()}
        if "evidence_id" not in condition_cols:
            self._rebuild_edge_conditions_v18()

        # Partial unique indexes replacing the old table-level UNIQUE, plus
        # the plain index on evidence_id. This is the ONLY place all three
        # are created, for both fresh and migrated databases. SCHEMA_SQL
        # deliberately omits them, because it runs before this method and
        # would reference evidence_id on a pre-V18 table. Idempotent.
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conditions_evidence "
            "ON edge_conditions(evidence_id)"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_conditions_unique_edge_scoped "
            "ON edge_conditions(edge_id, condition_type, condition_value) "
            "WHERE evidence_id IS NULL"
        )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_conditions_unique_evidence_scoped "
            "ON edge_conditions(edge_id, evidence_id, condition_type, "
            "condition_value) WHERE evidence_id IS NOT NULL"
        )

    def _rebuild_edge_conditions_v18(self):
        """
        Rebuild `edge_conditions` to add the nullable `evidence_id` column
        and drop the table-level UNIQUE constraint.

        SQLite cannot drop or alter a constraint in place, so the standard
        create-copy-drop-rename dance is the only route. Every existing row
        copies across with evidence_id = NULL, which preserves the original
        edge-scoped semantics exactly.

        Foreign keys are disabled for the swap. `edge_conditions` is a child
        table on both of its foreign keys, so nothing cascades into it from
        the drop, but the pragma keeps the rename from tripping SQLite's
        reference rewriting. The pragma is a no-op inside a transaction, so
        the pending work is committed first.
        """
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.executescript(
                """
                CREATE TABLE edge_conditions_v18 (
                    id INTEGER PRIMARY KEY,
                    edge_id INTEGER NOT NULL,
                    evidence_id INTEGER DEFAULT NULL,
                    condition_type TEXT NOT NULL,
                    condition_value TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (edge_id) REFERENCES edges(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (evidence_id) REFERENCES edge_evidence(id)
                        ON DELETE CASCADE
                );

                INSERT INTO edge_conditions_v18
                    (id, edge_id, evidence_id, condition_type,
                     condition_value, created_at)
                SELECT id, edge_id, NULL, condition_type,
                       condition_value, created_at
                FROM edge_conditions;

                DROP TABLE edge_conditions;
                ALTER TABLE edge_conditions_v18 RENAME TO edge_conditions;

                CREATE INDEX IF NOT EXISTS idx_conditions_edge
                    ON edge_conditions(edge_id);
                CREATE INDEX IF NOT EXISTS idx_conditions_type
                    ON edge_conditions(condition_type);
                """
            )
        finally:
            self.connection.commit()
            self.connection.execute("PRAGMA foreign_keys = ON")

    # -------------------------------------------------------------------------
    # Node Operations
    # -------------------------------------------------------------------------

    def add_node(self, canonical_name, node_type, notes=None,
                 cross_references=None, commit=True):
        """
        Create a new node.

        Args:
            canonical_name: Primary name for the entity.
            node_type: 'gene', 'protein', 'miRNA', 'PTM_state', 'complex',
                'process', 'phenotype', 'compartment', 'condition',
                'small_molecule', 'other'. Not enforced; suggested.
            notes: Optional free-form notes.
            cross_references: Optional dict of canonical IDs (e.g. from
                LEXICON). Stored as JSON-serialized TEXT. May also be
                passed as a pre-serialized JSON string.

        Returns:
            int: The node ID.

        Raises:
            sqlite3.IntegrityError: if (canonical_name, node_type) already exists.
        """
        if cross_references is not None and not isinstance(cross_references, str):
            cross_references = json.dumps(cross_references)
        now = datetime.now().isoformat()
        cursor = self.connection.execute(
            """INSERT INTO nodes
               (canonical_name, node_type, notes, cross_references,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (canonical_name, node_type, notes, cross_references, now, now),
        )
        if commit:
            self.connection.commit()
        return cursor.lastrowid

    def set_cross_references(self, node_id, cross_references, commit=True):
        """
        Set or replace the cross_references JSON on an existing node.

        Args:
            node_id: Target node.
            cross_references: dict to JSON-serialize, or a pre-serialized
                JSON string, or None to clear.

        Returns:
            int: The node id.
        """
        if cross_references is not None and not isinstance(cross_references, str):
            cross_references = json.dumps(cross_references)
        self.connection.execute(
            "UPDATE nodes SET cross_references = ?, updated_at = ? WHERE id = ?",
            (cross_references, datetime.now().isoformat(), node_id),
        )
        if commit:
            self.connection.commit()
        return node_id

    def update_node(self, node_id, commit=True, **kwargs):
        """Update one or more columns on a node."""
        if not kwargs:
            return node_id
        kwargs["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        self.connection.execute(
            f"UPDATE nodes SET {set_clause} WHERE id = ?",
            list(kwargs.values()) + [node_id],
        )
        if commit:
            self.connection.commit()
        return node_id

    def get_node(self, node_id):
        cursor = self.connection.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        )
        return cursor.fetchone()

    def get_node_by_name_and_type(self, canonical_name, node_type):
        cursor = self.connection.execute(
            "SELECT * FROM nodes WHERE canonical_name = ? AND node_type = ?",
            (canonical_name, node_type),
        )
        return cursor.fetchone()

    def find_nodes_by_name(self, query, node_type=None, exact=False):
        """
        Find nodes whose canonical_name or any alias matches the query.

        Args:
            query: Search string (case-insensitive).
            node_type: Optional filter on node_type.
            exact: If True, exact match. If False (default), substring match.

        Returns:
            List of node Rows. Each node appears at most once even if it
            matches on both canonical_name and an alias.
        """
        if exact:
            sql = (
                "SELECT DISTINCT n.* FROM nodes n "
                "LEFT JOIN node_aliases a ON a.node_id = n.id "
                "WHERE (LOWER(n.canonical_name) = LOWER(?) "
                "       OR LOWER(a.alias) = LOWER(?))"
            )
            params = [query, query]
        else:
            like = f"%{query.lower()}%"
            sql = (
                "SELECT DISTINCT n.* FROM nodes n "
                "LEFT JOIN node_aliases a ON a.node_id = n.id "
                "WHERE (LOWER(n.canonical_name) LIKE ? "
                "       OR LOWER(a.alias) LIKE ?)"
            )
            params = [like, like]

        if node_type is not None:
            sql += " AND n.node_type = ?"
            params.append(node_type)

        sql += " ORDER BY n.canonical_name"

        cursor = self.connection.execute(sql, params)
        return cursor.fetchall()

    def get_all_nodes(self, node_type=None):
        if node_type is not None:
            cursor = self.connection.execute(
                "SELECT * FROM nodes WHERE node_type = ? ORDER BY canonical_name",
                (node_type,),
            )
        else:
            cursor = self.connection.execute(
                "SELECT * FROM nodes ORDER BY canonical_name"
            )
        return cursor.fetchall()

    def count_nodes(self, node_type=None):
        if node_type is not None:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_type = ?", (node_type,)
            )
        else:
            cursor = self.connection.execute("SELECT COUNT(*) FROM nodes")
        return cursor.fetchone()[0]

    def delete_node(self, node_id):
        """Delete a node. Cascades to incident edges (and their conditions/evidence)."""
        cursor = self.connection.execute(
            "DELETE FROM nodes WHERE id = ?", (node_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Alias Operations
    # -------------------------------------------------------------------------

    def add_alias(self, node_id, alias, notes=None, commit=True):
        """Add an alias to a node. Raises IntegrityError if (node_id, alias) exists."""
        now = datetime.now().isoformat()
        cursor = self.connection.execute(
            """INSERT INTO node_aliases (node_id, alias, notes, created_at)
               VALUES (?, ?, ?, ?)""",
            (node_id, alias, notes, now),
        )
        if commit:
            self.connection.commit()
        return cursor.lastrowid

    def get_aliases(self, node_id):
        cursor = self.connection.execute(
            "SELECT * FROM node_aliases WHERE node_id = ? ORDER BY alias",
            (node_id,),
        )
        return cursor.fetchall()

    def delete_alias(self, alias_id):
        cursor = self.connection.execute(
            "DELETE FROM node_aliases WHERE id = ?", (alias_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Edge Operations
    # -------------------------------------------------------------------------

    def add_edge(self, subject_id, object_id, edge_type, notes=None, commit=True):
        """
        Create a new edge.

        Args:
            subject_id: Node id of the subject (actor).
            object_id: Node id of the object (target).
            edge_type: Relation type (activates, inhibits, binds, phosphorylates,
                transcribes, translocates, sequesters, requires, part_of, etc.).
            notes: Optional notes on the edge itself.

        Returns:
            int: The edge ID.

        Raises:
            sqlite3.IntegrityError: if (subject_id, object_id, edge_type)
                already exists. Use get_edge_by_triple to find the existing
                edge and add evidence to it instead.
        """
        now = datetime.now().isoformat()
        cursor = self.connection.execute(
            """INSERT INTO edges
               (subject_id, object_id, edge_type, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (subject_id, object_id, edge_type, notes, now, now),
        )
        if commit:
            self.connection.commit()
        return cursor.lastrowid

    def update_edge(self, edge_id, commit=True, **kwargs):
        """Update one or more columns on an edge."""
        if not kwargs:
            return edge_id
        kwargs["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        self.connection.execute(
            f"UPDATE edges SET {set_clause} WHERE id = ?",
            list(kwargs.values()) + [edge_id],
        )
        if commit:
            self.connection.commit()
        return edge_id

    def get_edge(self, edge_id):
        cursor = self.connection.execute(
            "SELECT * FROM edges WHERE id = ?", (edge_id,)
        )
        return cursor.fetchone()

    def get_edge_by_triple(self, subject_id, object_id, edge_type):
        cursor = self.connection.execute(
            "SELECT * FROM edges WHERE subject_id = ? AND object_id = ? AND edge_type = ?",
            (subject_id, object_id, edge_type),
        )
        return cursor.fetchone()

    def get_edges_for_node(self, node_id, direction="both", edge_type=None):
        """
        Get edges incident to a node.

        Args:
            node_id: Target node.
            direction: 'in' (node is object), 'out' (node is subject),
                or 'both' (default).
            edge_type: Optional filter on edge_type.
        """
        if direction == "in":
            where = "object_id = ?"
            params = [node_id]
        elif direction == "out":
            where = "subject_id = ?"
            params = [node_id]
        elif direction == "both":
            where = "(subject_id = ? OR object_id = ?)"
            params = [node_id, node_id]
        else:
            raise ValueError(f"Invalid direction: {direction!r}; expected 'in', 'out', or 'both'")

        sql = f"SELECT * FROM edges WHERE {where}"
        if edge_type is not None:
            sql += " AND edge_type = ?"
            params.append(edge_type)
        sql += " ORDER BY id"

        cursor = self.connection.execute(sql, params)
        return cursor.fetchall()

    def get_neighbors(self, node_id, direction="both", edge_type=None):
        """
        Get one-hop neighbors of a node, joined with the connecting edge.

        Returns rows with: neighbor_id, neighbor_canonical_name,
        neighbor_node_type, edge_id, edge_type, edge_direction ('in' or 'out').
        """
        out_sql = (
            "SELECT n.id AS neighbor_id, "
            "n.canonical_name AS neighbor_canonical_name, "
            "n.node_type AS neighbor_node_type, "
            "e.id AS edge_id, e.edge_type, 'out' AS edge_direction "
            "FROM edges e JOIN nodes n ON n.id = e.object_id "
            "WHERE e.subject_id = ?"
        )
        in_sql = (
            "SELECT n.id AS neighbor_id, "
            "n.canonical_name AS neighbor_canonical_name, "
            "n.node_type AS neighbor_node_type, "
            "e.id AS edge_id, e.edge_type, 'in' AS edge_direction "
            "FROM edges e JOIN nodes n ON n.id = e.subject_id "
            "WHERE e.object_id = ?"
        )

        type_clause = " AND e.edge_type = ?" if edge_type is not None else ""

        results = []
        if direction in ("out", "both"):
            params = [node_id] + ([edge_type] if edge_type is not None else [])
            cursor = self.connection.execute(out_sql + type_clause, params)
            results.extend(cursor.fetchall())
        if direction in ("in", "both"):
            params = [node_id] + ([edge_type] if edge_type is not None else [])
            cursor = self.connection.execute(in_sql + type_clause, params)
            results.extend(cursor.fetchall())
        if direction not in ("in", "out", "both"):
            raise ValueError(f"Invalid direction: {direction!r}; expected 'in', 'out', or 'both'")
        return results

    def count_edges(self, edge_type=None):
        if edge_type is not None:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM edges WHERE edge_type = ?", (edge_type,)
            )
        else:
            cursor = self.connection.execute("SELECT COUNT(*) FROM edges")
        return cursor.fetchone()[0]

    def delete_edge(self, edge_id):
        """Delete an edge. Cascades to its conditions and evidence."""
        cursor = self.connection.execute(
            "DELETE FROM edges WHERE id = ?", (edge_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Condition Operations
    # -------------------------------------------------------------------------

    def add_condition(self, edge_id, condition_type, condition_value,
                      evidence_id=None, commit=True):
        """
        Add a precondition to an edge. Silently ignores duplicates.

        Args:
            edge_id: Target edge.
            condition_type: Free-form scope dimension.
            condition_value: Scope value.
            evidence_id: When None (default), the condition scopes the whole
                edge, which is the original behaviour. When set, it scopes
                exactly that one evidence row, so two evidence rows on the
                same edge can carry the same (type, value) pair without
                colliding. Caller is responsible for checking that the
                evidence row belongs to this edge; GraphAccessor does that.

        Duplicate suppression is enforced by two partial unique indexes
        rather than one table-level UNIQUE, so edge-scoped and
        evidence-scoped conditions de-duplicate independently.

        Returns:
            int: The condition row id, or 0 if it was a duplicate.
        """
        now = datetime.now().isoformat()
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO edge_conditions
               (edge_id, evidence_id, condition_type, condition_value,
                created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (edge_id, evidence_id, condition_type, condition_value, now),
        )
        if commit:
            self.connection.commit()
        # On a suppressed INSERT OR IGNORE, sqlite3 leaves lastrowid at the
        # connection's previous value, so it would report a real but
        # unrelated condition id. rowcount is the reliable discriminator.
        if cursor.rowcount == 0:
            return 0
        return cursor.lastrowid

    def get_conditions(self, edge_id):
        cursor = self.connection.execute(
            """SELECT * FROM edge_conditions WHERE edge_id = ?
               ORDER BY condition_type, condition_value""",
            (edge_id,),
        )
        return cursor.fetchall()

    def delete_condition(self, condition_id):
        cursor = self.connection.execute(
            "DELETE FROM edge_conditions WHERE id = ?", (condition_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Evidence Operations
    # -------------------------------------------------------------------------

    def add_evidence(self, edge_id, conversation_date=None, commit=True, **fields):
        """
        Add an evidence record to an edge. Increments coverage by 1.

        Args:
            edge_id: Target edge.
            conversation_date: ISO date string. Defaults to today.
            **fields: Any of grounding_type, provenance_extra,
                source_filename, source_doi, source_pmid, chunk_id,
                method, cell_system, conversation_question, notes.

                grounding_type defaults to 'corpus_primary' at the column
                level when omitted. Permitted values per V13:
                'corpus_primary', 'corpus_inline_cited', 'lexicon',
                'common_knowledge', 'background_weak'. Semantic
                validation of which fields are required per type lives
                in GraphAccessor at the MCP layer; this method stores
                what it is given.

                provenance_extra may be a dict (auto-serialized to JSON)
                or a pre-serialized JSON string.

        Returns:
            int: The evidence row id.
        """
        if conversation_date is None:
            conversation_date = date.today().isoformat()

        # Auto-serialize provenance_extra if a dict was passed
        if isinstance(fields.get("provenance_extra"), dict):
            fields["provenance_extra"] = json.dumps(fields["provenance_extra"])

        # Strip None values so they don't override DB defaults if any
        fields = {k: v for k, v in fields.items() if v is not None}
        fields["edge_id"] = edge_id
        fields["conversation_date"] = conversation_date
        fields["created_at"] = datetime.now().isoformat()

        columns = ", ".join(fields.keys())
        placeholders = ", ".join(["?" for _ in fields])

        cursor = self.connection.execute(
            f"INSERT INTO edge_evidence ({columns}) VALUES ({placeholders})",
            list(fields.values()),
        )
        if commit:
            self.connection.commit()
        return cursor.lastrowid

    def get_evidence_for_edge(self, edge_id):
        cursor = self.connection.execute(
            """SELECT * FROM edge_evidence WHERE edge_id = ?
               ORDER BY conversation_date, id""",
            (edge_id,),
        )
        return cursor.fetchall()

    def count_evidence(self, edge_id=None):
        if edge_id is not None:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM edge_evidence WHERE edge_id = ?", (edge_id,)
            )
        else:
            cursor = self.connection.execute("SELECT COUNT(*) FROM edge_evidence")
        return cursor.fetchone()[0]

    def count_conditions(self, edge_id=None):
        if edge_id is not None:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM edge_conditions WHERE edge_id = ?", (edge_id,)
            )
        else:
            cursor = self.connection.execute("SELECT COUNT(*) FROM edge_conditions")
        return cursor.fetchone()[0]

    def delete_evidence(self, evidence_id):
        cursor = self.connection.execute(
            "DELETE FROM edge_evidence WHERE id = ?", (evidence_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Observation Operations
    # -------------------------------------------------------------------------

    def add_observation(self, node_id, observation, conversation_date=None,
                        commit=True, **fields):
        """
        Add a per-node observation. One row per supporting source.

        Args:
            node_id: Target node.
            observation: The observation text (in the user's or Claude's
                own words; not a quote from the source).
            conversation_date: ISO date string. Defaults to today.
            **fields: Any of grounding_type, provenance_extra,
                source_filename, source_doi, source_pmid, chunk_id,
                method, cell_system, conversation_question, notes.

                grounding_type defaults to 'corpus_primary' at the column
                level when omitted. Permitted values per V13:
                'corpus_primary', 'corpus_inline_cited', 'lexicon',
                'common_knowledge', 'background_weak'. Semantic
                validation of which fields are required per type lives
                in GraphAccessor at the MCP layer; this method stores
                what it is given.

                provenance_extra may be a dict (auto-serialized to JSON)
                or a pre-serialized JSON string.

        Returns:
            int: The observation row id.
        """
        if conversation_date is None:
            conversation_date = date.today().isoformat()

        # Auto-serialize provenance_extra if a dict was passed
        if isinstance(fields.get("provenance_extra"), dict):
            fields["provenance_extra"] = json.dumps(fields["provenance_extra"])

        # Strip None values so they don't override DB defaults
        fields = {k: v for k, v in fields.items() if v is not None}
        fields["node_id"] = node_id
        fields["observation"] = observation
        fields["conversation_date"] = conversation_date
        now = datetime.now().isoformat()
        fields["created_at"] = now
        fields["updated_at"] = now

        columns = ", ".join(fields.keys())
        placeholders = ", ".join(["?" for _ in fields])

        cursor = self.connection.execute(
            f"INSERT INTO node_observations ({columns}) VALUES ({placeholders})",
            list(fields.values()),
        )
        if commit:
            self.connection.commit()
        return cursor.lastrowid

    def get_observations_for_node(self, node_id):
        """
        Return all observations attached to a node, ordered by date and id.
        """
        cursor = self.connection.execute(
            """SELECT * FROM node_observations WHERE node_id = ?
               ORDER BY conversation_date, id""",
            (node_id,),
        )
        return cursor.fetchall()

    def get_observation(self, observation_id):
        cursor = self.connection.execute(
            "SELECT * FROM node_observations WHERE id = ?", (observation_id,)
        )
        return cursor.fetchone()

    def update_observation(self, observation_id, observation=None, notes=None,
                           commit=True):
        """
        In-place edit of the observation text and/or notes.

        Bumps updated_at. The chunk_id and other provenance fields are
        immutable; if a different chunk truly supports a different claim,
        add a new observation rather than rewriting an existing one.
        """
        updates = {}
        if observation is not None:
            updates["observation"] = observation
        if notes is not None:
            updates["notes"] = notes
        if not updates:
            return observation_id
        updates["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        self.connection.execute(
            f"UPDATE node_observations SET {set_clause} WHERE id = ?",
            list(updates.values()) + [observation_id],
        )
        if commit:
            self.connection.commit()
        return observation_id

    def count_observations(self, node_id=None):
        if node_id is not None:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM node_observations WHERE node_id = ?",
                (node_id,),
            )
        else:
            cursor = self.connection.execute(
                "SELECT COUNT(*) FROM node_observations"
            )
        return cursor.fetchone()[0]

    def delete_observation(self, observation_id):
        cursor = self.connection.execute(
            "DELETE FROM node_observations WHERE id = ?", (observation_id,)
        )
        self.connection.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_stats(self):
        """
        Return summary statistics.

        Includes per-type histograms for nodes and edges, the top 10
        most-cited corpus sources by edge_evidence count, and the top 10
        nodes ranked by observation count.
        """
        stats = {
            "nodes": self.count_nodes(),
            "edges": self.count_edges(),
            "conditions": self.count_conditions(),
            "evidence": self.count_evidence(),
            "observations": self.count_observations(),
            "node_types": {},
            "edge_types": {},
            "top_sources": [],
            "top_observed_nodes": [],
        }

        cursor = self.connection.execute(
            "SELECT node_type, COUNT(*) FROM nodes GROUP BY node_type ORDER BY 2 DESC"
        )
        stats["node_types"] = {row[0]: row[1] for row in cursor.fetchall()}

        cursor = self.connection.execute(
            "SELECT edge_type, COUNT(*) FROM edges GROUP BY edge_type ORDER BY 2 DESC"
        )
        stats["edge_types"] = {row[0]: row[1] for row in cursor.fetchall()}

        cursor = self.connection.execute(
            """SELECT source_filename, COUNT(*) AS cite_count
               FROM edge_evidence
               WHERE source_filename IS NOT NULL
               GROUP BY source_filename
               ORDER BY cite_count DESC
               LIMIT 10"""
        )
        stats["top_sources"] = [
            {"filename": row[0], "evidence_count": row[1]}
            for row in cursor.fetchall()
        ]

        cursor = self.connection.execute(
            """SELECT n.id, n.canonical_name, n.node_type, COUNT(o.id) AS obs_count
               FROM nodes n
               JOIN node_observations o ON o.node_id = n.id
               GROUP BY n.id
               ORDER BY obs_count DESC, n.canonical_name
               LIMIT 10"""
        )
        stats["top_observed_nodes"] = [
            {
                "node_id": row[0],
                "canonical_name": row[1],
                "node_type": row[2],
                "observation_count": row[3],
            }
            for row in cursor.fetchall()
        ]

        return stats
