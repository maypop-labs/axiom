#!/usr/bin/env python3
"""
AXIOM Project - Graph Accessor Module

Wraps AxiomGraphDatabase for use by the MCP server. Holds its own
AxiomDatabase reference for cross-database lookups (resolving chunk_id
to source filename / citation when formatting evidence rows).

The GraphAccessor returns plain dicts (not sqlite3.Row) so MCP tool
results serialize cleanly.
"""

import json
import logging
import sqlite3
import sys
from pathlib import Path

# Make lib/ importable
SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_LIB = SCRIPT_DIR.parent / "Python" / "lib"
sys.path.insert(0, str(PYTHON_LIB))

from axiom_db import AxiomDatabase
from axiom_graph_db import AxiomGraphDatabase
from species import derive_species, SPECIES_RELEVANT_TYPES

logger = logging.getLogger(__name__)


# Permitted values for the V13 grounding_type column on edge_evidence
# and node_observations. The DB layer is permissive (no CHECK constraint);
# this set is the authoritative enum at the MCP layer.
PERMITTED_GROUNDING_TYPES = frozenset({
    "corpus_primary",
    "corpus_inline_cited",
    "lexicon",
    "common_knowledge",
    "background_weak",
})

# Permitted values for the V18 assertion_status column on edge_evidence
# and node_observations. Orthogonal to grounding_type: grounding_type
# records where a row came from, assertion_status records which way it
# cuts. 'refuting' means the source tested the claim and found it absent,
# which is a positive finding about a negative result, not weak grounding.
PERMITTED_ASSERTION_STATUS = frozenset({
    "asserting",
    "refuting",
})


class GraphAccessor:
    """
    MCP-facing wrapper over AxiomGraphDatabase + cross-DB lookups
    against the AXIOM corpus database.
    """

    def __init__(self, graph_db_path=None, corpus_db_path=None):
        self.graph_db = AxiomGraphDatabase(db_path=graph_db_path)
        self.corpus_db = AxiomDatabase(db_path=corpus_db_path)

    def initialize(self):
        """Initialize both databases. Idempotent."""
        self.graph_db.initialize()
        self.corpus_db.initialize()
        return self

    # -------------------------------------------------------------------------
    # Internal formatting helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _deserialize_json_field(raw):
        """Deserialize a TEXT JSON value into a dict (or None on empty/invalid)."""
        if raw is None or raw == "":
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _validate_and_normalize_grounding(fields):
        """
        Validate grounding_type and source-type-specific provenance.

        Folds flat provenance kwargs (upstream_reference, lexicon_source,
        lexicon_identifier, retrieval_date, justification,
        weakest_grounding) into fields["provenance_extra"] (a dict)
        before validation, so callers can pass them either as a
        pre-built dict or as flat kwargs. Flat values overwrite dict
        values on conflict (rare; user error).

        Mutates `fields` in place. Removes the folded flat keys so they
        do not leak through to the SQL INSERT.

        Raises ValueError on:
        - Unknown grounding_type
        - Missing chunk_id when required (corpus_primary, corpus_inline_cited)
        - Forbidden chunk_id (non-corpus types must not carry one)
        - Missing required keys per grounding_type
        """
        grounding_type = fields.get("grounding_type") or "corpus_primary"
        if grounding_type not in PERMITTED_GROUNDING_TYPES:
            raise ValueError(
                f"Invalid grounding_type {grounding_type!r}. Permitted: "
                f"{sorted(PERMITTED_GROUNDING_TYPES)}"
            )
        fields["grounding_type"] = grounding_type

        # V18 assertion polarity. Validated ahead of the pre-serialized
        # provenance_extra early-return below, so a refuting row cannot
        # slip through unchecked by passing provenance_extra as a string.
        assertion_status = fields.get("assertion_status") or "asserting"
        if assertion_status not in PERMITTED_ASSERTION_STATUS:
            raise ValueError(
                f"Invalid assertion_status {assertion_status!r}. Permitted: "
                f"{sorted(PERMITTED_ASSERTION_STATUS)}"
            )
        fields["assertion_status"] = assertion_status
        if assertion_status == "refuting":
            raw_extra = fields.get("provenance_extra")
            if isinstance(raw_extra, str):
                try:
                    probe = json.loads(raw_extra)
                except (json.JSONDecodeError, TypeError):
                    probe = {}
            elif isinstance(raw_extra, dict):
                probe = raw_extra
            else:
                probe = {}
            if not isinstance(probe, dict):
                probe = {}
            if not (fields.get("method")
                    or fields.get("justification")
                    or probe.get("justification")):
                raise ValueError(
                    "assertion_status='refuting' requires either `method` "
                    "(the test or assay that returned null) or "
                    "`justification`. A refutation without the test that "
                    "produced it is not interpretable by a later reader, "
                    "and cannot be distinguished from 'never tested'."
                )

        # Pre-serialized JSON strings bypass dict-level validation.
        extra = fields.get("provenance_extra")
        if isinstance(extra, str):
            return fields

        # Start from existing dict (if any), then fold in flat kwargs.
        extra = dict(extra) if isinstance(extra, dict) else {}
        flat_keys = (
            "upstream_reference",
            "lexicon_source",
            "lexicon_identifier",
            "retrieval_date",
            "justification",
            "weakest_grounding",
        )
        for key in flat_keys:
            if key in fields:
                value = fields.pop(key)
                if value is not None:
                    extra[key] = value

        chunk_id = fields.get("chunk_id")

        if grounding_type in ("corpus_primary", "corpus_inline_cited"):
            if chunk_id is None:
                raise ValueError(
                    f"grounding_type={grounding_type!r} requires chunk_id"
                )
            if grounding_type == "corpus_inline_cited" and not extra.get("upstream_reference"):
                raise ValueError(
                    "grounding_type='corpus_inline_cited' requires "
                    "upstream_reference (the inline citation as it "
                    "appears in the chunk)"
                )
        else:
            # lexicon, common_knowledge, background_weak
            if chunk_id is not None:
                raise ValueError(
                    f"grounding_type={grounding_type!r} must not carry chunk_id; "
                    f"non-corpus grounding cannot reference a corpus chunk"
                )
            if grounding_type == "lexicon":
                missing = [
                    k for k in ("lexicon_source", "lexicon_identifier", "retrieval_date")
                    if not extra.get(k)
                ]
                if missing:
                    raise ValueError(
                        f"grounding_type='lexicon' requires: {missing}"
                    )
            elif grounding_type in ("common_knowledge", "background_weak"):
                if not extra.get("justification"):
                    raise ValueError(
                        f"grounding_type={grounding_type!r} requires justification"
                    )
                if grounding_type == "background_weak":
                    extra.setdefault("weakest_grounding", True)

        fields["provenance_extra"] = extra if extra else None
        return fields

    def _format_citation(self, d):
        """
        Build the `citation` field on an evidence/observation row dict
        based on its grounding_type and (deserialized) provenance_extra.
        Mutates the dict in place.

        For corpus_primary / corpus_inline_cited: look up the source via
        chunk_id (preferred) or source_filename (fallback) in the AXIOM
        corpus DB. When the source resolves, set citation to the APA
        string and populate title and year. corpus_inline_cited appends
        the upstream_reference. When neither chunk_id nor source_filename
        resolves, citation/title/year are left unset (existing behavior).

        For lexicon: build a citation string like
        '<source> <identifier> (retrieved <date>)' from provenance_extra.

        For common_knowledge / background_weak: build a citation string
        from the justification field. background_weak is prefixed with
        [weakest grounding].

        Title and year are only populated for corpus types; for
        non-corpus types they are intentionally not set.
        """
        grounding_type = d.get("grounding_type") or "corpus_primary"
        extra = d.get("provenance_extra") or {}

        if grounding_type in ("corpus_primary", "corpus_inline_cited"):
            source = None
            if d.get("chunk_id"):
                chunk = self.corpus_db.get_chunk(d["chunk_id"])
                if chunk is not None:
                    source = self.corpus_db.get_reference(chunk["source_id"])
            if source is None and d.get("source_filename"):
                source = self.corpus_db.get_reference_by_filename(
                    d["source_filename"]
                )
            if source is not None:
                d["title"] = source["title"]
                d["year"] = source["year"]
                base = source["citation_apa"]
                if grounding_type == "corpus_inline_cited":
                    upstream = extra.get("upstream_reference")
                    d["citation"] = f"{base}; cites: {upstream}" if upstream else base
                else:
                    d["citation"] = base

        elif grounding_type == "lexicon":
            source_name = extra.get("lexicon_source") or "LEXICON"
            identifier = extra.get("lexicon_identifier")
            retrieval_date = extra.get("retrieval_date")
            citation = source_name
            if identifier:
                citation = f"{citation} {identifier}"
            if retrieval_date:
                citation = f"{citation} (retrieved {retrieval_date})"
            d["citation"] = citation

        elif grounding_type == "common_knowledge":
            justification = (extra.get("justification") or "").strip()
            if justification:
                d["citation"] = (
                    f"Claude background (common knowledge): {justification}"
                )
            else:
                d["citation"] = "Claude background (common knowledge)"

        elif grounding_type == "background_weak":
            justification = (extra.get("justification") or "").strip()
            if justification:
                d["citation"] = (
                    f"[weakest grounding] Claude background: {justification}"
                )
            else:
                d["citation"] = "[weakest grounding] Claude background"

        return d

    def _node_dict(self, node_row, include_aliases=True,
                   include_observations=False):
        """
        Serialize a node row to a plain dict.

        Always includes observation_count. cross_references is
        deserialized from JSON. Aliases are included by default. Full
        observations (with cross-DB citation enrichment) are included
        when include_observations is True.
        """
        if node_row is None:
            return None
        d = dict(node_row)
        if "cross_references" in d:
            d["cross_references"] = self._deserialize_json_field(
                d["cross_references"]
            )
        d["species"] = derive_species(
            d.get("node_type"), d.get("cross_references")
        )
        if include_aliases:
            aliases = self.graph_db.get_aliases(node_row["id"])
            d["aliases"] = [a["alias"] for a in aliases]
        d["observation_count"] = self.graph_db.count_observations(
            node_id=node_row["id"]
        )
        if include_observations:
            obs_rows = self.graph_db.get_observations_for_node(node_row["id"])
            d["observations"] = [self._observation_dict(o) for o in obs_rows]
        return d

    def _observation_dict(self, obs_row):
        """Observation with citation formatted per grounding_type."""
        d = dict(obs_row)
        if "provenance_extra" in d:
            d["provenance_extra"] = self._deserialize_json_field(
                d["provenance_extra"]
            )
        self._format_citation(d)
        return d

    def _edge_dict_brief(self, edge_row):
        """Edge with subject/object names and coverage, but no full evidence."""
        d = dict(edge_row)
        subj = self.graph_db.get_node(d["subject_id"])
        obj = self.graph_db.get_node(d["object_id"])
        d["subject_name"] = subj["canonical_name"] if subj else None
        d["subject_type"] = subj["node_type"] if subj else None
        d["object_name"] = obj["canonical_name"] if obj else None
        d["object_type"] = obj["node_type"] if obj else None
        d["coverage"] = self.graph_db.count_evidence(edge_id=d["id"])
        return d

    def _evidence_dict(self, evidence_row):
        """Evidence with citation formatted per grounding_type."""
        d = dict(evidence_row)
        if "provenance_extra" in d:
            d["provenance_extra"] = self._deserialize_json_field(
                d["provenance_extra"]
            )
        self._format_citation(d)
        return d

    # -------------------------------------------------------------------------
    # Read paths
    # -------------------------------------------------------------------------

    def get_node(self, node_id=None, name=None, node_type=None):
        if node_id is not None:
            row = self.graph_db.get_node(node_id)
        elif name is not None and node_type is not None:
            row = self.graph_db.get_node_by_name_and_type(name, node_type)
        else:
            return None
        return self._node_dict(row, include_observations=True)

    def find_nodes(self, query, node_type=None, k=20):
        rows = self.graph_db.find_nodes_by_name(query, node_type=node_type)
        return [self._node_dict(r) for r in rows[:k]]

    def find_nodes_by_species(self, species, node_type=None, k=200):
        """List gene/protein nodes whose derived species matches.

        Species is derived (not stored), the same way node reads and the
        graph export derive it. Pass species='unknown' to audit nodes that
        will export without a species classification. node_type restricts
        to 'gene' or 'protein' when given.

        Classifies directly from each node row (no per-node alias or
        observation queries) and returns slim dicts: id, canonical_name,
        node_type, cross_references, species.
        """
        out = []
        for r in self.graph_db.get_all_nodes(node_type=node_type):
            cross_refs = self._deserialize_json_field(r["cross_references"])
            sp = derive_species(r["node_type"], cross_refs)
            if sp == species:
                out.append({
                    "id": r["id"],
                    "canonical_name": r["canonical_name"],
                    "node_type": r["node_type"],
                    "cross_references": cross_refs,
                    "species": sp,
                })
                if len(out) >= k:
                    break
        return out

    def find_nodes_batch(self, queries, node_type=None, k=20):
        """
        Run multiple find_nodes queries in one call.

        Per-query semantics are identical to find_nodes (case-insensitive
        substring match against canonical_name and aliases). The shared
        node_type and k apply to every query.

        Args:
            queries: List of search strings. Empty list returns [].
            node_type: Optional filter applied to every query.
            k: Max results per query.

        Returns:
            List of {'query': str, 'matches': list[node_dict]} in input
            order. Duplicate queries get duplicate entries (each carries
            the same matches).
        """
        return [
            {
                "query": q,
                "matches": self.find_nodes(q, node_type=node_type, k=k),
            }
            for q in queries
        ]

    def get_edges_for_node(self, node_id, direction="both", edge_type=None):
        rows = self.graph_db.get_edges_for_node(
            node_id, direction=direction, edge_type=edge_type,
        )
        return [self._edge_dict_brief(r) for r in rows]

    def get_edge_full(self, edge_id):
        edge = self.graph_db.get_edge(edge_id)
        if edge is None:
            return None
        d = self._edge_dict_brief(edge)

        conditions = self.graph_db.get_conditions(edge_id)
        d["conditions"] = [
            {
                "id": c["id"],
                "condition_type": c["condition_type"],
                "condition_value": c["condition_value"],
            }
            for c in conditions
        ]

        evidence = self.graph_db.get_evidence_for_edge(edge_id)
        d["evidence"] = [self._evidence_dict(e) for e in evidence]
        d["coverage"] = len(d["evidence"])
        return d

    def get_neighbors(self, node_id, direction="both", edge_type=None):
        rows = self.graph_db.get_neighbors(
            node_id, direction=direction, edge_type=edge_type,
        )
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Write paths (with friendly errors and cross-DB validation)
    # -------------------------------------------------------------------------

    def add_node(self, canonical_name, node_type, notes=None, aliases=None,
                 cross_references=None, commit=True):
        try:
            node_id = self.graph_db.add_node(
                canonical_name, node_type, notes=notes,
                cross_references=cross_references, commit=commit,
            )
        except sqlite3.IntegrityError:
            existing = self.graph_db.get_node_by_name_and_type(
                canonical_name, node_type,
            )
            if existing is not None:
                raise ValueError(
                    f"Node already exists: '{canonical_name}' ({node_type}) "
                    f"with id {existing['id']}. Use graph_add_alias to add an "
                    f"alternate name, or use the existing id directly."
                )
            raise

        if aliases:
            for alias in aliases:
                try:
                    self.graph_db.add_alias(node_id, alias, commit=commit)
                except sqlite3.IntegrityError:
                    # Same alias already attached to this node; ignore.
                    pass
        return node_id

    def set_cross_references(self, node_id, cross_references, commit=True):
        """
        Set or replace cross_references on an existing node. Validates
        that the node exists. cross_references may be a dict or a
        JSON-serialized string; None clears the field.
        """
        if self.graph_db.get_node(node_id) is None:
            raise ValueError(f"Node {node_id} not found")
        return self.graph_db.set_cross_references(
            node_id, cross_references, commit=commit,
        )

    def update_node(self, node_id, canonical_name=None, node_type=None,
                    notes=None, commit=True):
        """
        Update mutable fields on an existing node.

        Mutable fields are canonical_name, node_type, and notes. Pass
        None to leave a field unchanged. cross_references has its own
        path (set_cross_references); aliases have add_alias /
        delete_alias. Bumps updated_at.

        Raises ValueError if the node does not exist or if the proposed
        (canonical_name, node_type) collides with another existing node.
        """
        current = self.graph_db.get_node(node_id)
        if current is None:
            raise ValueError(f"Node {node_id} not found")

        updates = {}
        if canonical_name is not None:
            updates["canonical_name"] = canonical_name
        if node_type is not None:
            updates["node_type"] = node_type
        if notes is not None:
            updates["notes"] = notes
        if not updates:
            return node_id

        try:
            return self.graph_db.update_node(node_id, commit=commit, **updates)
        except sqlite3.IntegrityError:
            new_name = updates.get(
                "canonical_name", current["canonical_name"]
            )
            new_type = updates.get("node_type", current["node_type"])
            existing = self.graph_db.get_node_by_name_and_type(
                new_name, new_type,
            )
            if existing is not None and existing["id"] != node_id:
                raise ValueError(
                    f"Cannot update: another node ({existing['id']}) "
                    f"already exists with canonical_name='{new_name}' "
                    f"and node_type='{new_type}'."
                )
            raise

    def add_alias(self, node_id, alias, notes=None, commit=True):
        if self.graph_db.get_node(node_id) is None:
            raise ValueError(f"Node {node_id} not found")
        try:
            return self.graph_db.add_alias(
                node_id, alias, notes=notes, commit=commit,
            )
        except sqlite3.IntegrityError:
            raise ValueError(
                f"Alias '{alias}' already attached to node {node_id}"
            )

    def add_edge(self, subject_id, object_id, edge_type,
                 conditions=None, evidence=None, notes=None, commit=True):
        if self.graph_db.get_node(subject_id) is None:
            raise ValueError(f"Subject node {subject_id} not found")
        if self.graph_db.get_node(object_id) is None:
            raise ValueError(f"Object node {object_id} not found")

        try:
            edge_id = self.graph_db.add_edge(
                subject_id, object_id, edge_type, notes=notes, commit=commit,
            )
        except sqlite3.IntegrityError:
            existing = self.graph_db.get_edge_by_triple(
                subject_id, object_id, edge_type,
            )
            if existing is not None:
                raise ValueError(
                    f"Edge already exists: ({subject_id}) --{edge_type}--> "
                    f"({object_id}) with id {existing['id']}. Use "
                    f"graph_add_evidence to add a new observation, or "
                    f"graph_add_condition to add a precondition."
                )
            raise

        # Evidence is written BEFORE conditions (V18, reversed from the
        # original order) so that a condition in the same call can scope
        # itself to one of these evidence rows via `evidence_index`.
        evidence_ids = []
        if evidence:
            for e in evidence:
                evidence_ids.append(
                    self.add_evidence(edge_id, commit=commit, **e)
                )

        if conditions:
            for c in conditions:
                try:
                    ctype = (c["condition_type"] if "condition_type" in c
                             else c["type"])
                    cvalue = (c["condition_value"] if "condition_value" in c
                              else c["value"])
                except KeyError as e:
                    raise ValueError(
                        f"Condition dict missing required key: {e}. "
                        f"Expected 'condition_type'/'condition_value' or 'type'/'value'."
                    )
                evidence_index = c.get("evidence_index")
                scoped_evidence_id = None
                if evidence_index is not None:
                    if not isinstance(evidence_index, int):
                        raise ValueError(
                            "Condition 'evidence_index' must be an int "
                            "position into this edge's `evidence` list."
                        )
                    if not 0 <= evidence_index < len(evidence_ids):
                        raise ValueError(
                            f"Condition 'evidence_index' {evidence_index} is "
                            f"out of range; this edge was given "
                            f"{len(evidence_ids)} evidence row(s)."
                        )
                    scoped_evidence_id = evidence_ids[evidence_index]
                self.graph_db.add_condition(
                    edge_id, ctype, cvalue,
                    evidence_id=scoped_evidence_id,
                    commit=commit,
                )

        return edge_id

    def add_evidence(self, edge_id, commit=True, **fields):
        """
        Add an evidence record.

        Validates grounding_type (defaulting to 'corpus_primary') and
        the source-type-specific provenance via
        _validate_and_normalize_grounding. For corpus-grounded types,
        also validates chunk_id against the corpus DB and auto-fills
        source_filename from the chunk's parent source when not
        explicitly supplied. Non-corpus types (lexicon, common_knowledge,
        background_weak) must not carry a chunk_id.
        """
        if self.graph_db.get_edge(edge_id) is None:
            raise ValueError(f"Edge {edge_id} not found")

        self._validate_and_normalize_grounding(fields)

        chunk_id = fields.get("chunk_id")
        if chunk_id is not None:
            chunk = self.corpus_db.get_chunk(chunk_id)
            if chunk is None:
                raise ValueError(
                    f"chunk_id {chunk_id} not found in the AXIOM corpus database"
                )
            if not fields.get("source_filename"):
                source = self.corpus_db.get_reference(chunk["source_id"])
                if source is not None:
                    fields["source_filename"] = source["filename"]

        return self.graph_db.add_evidence(edge_id, commit=commit, **fields)

    def add_condition(self, edge_id, condition_type, condition_value,
                      evidence_id=None, commit=True):
        """
        Add a precondition to an edge.

        evidence_id=None (default) scopes the condition to the whole edge.
        Passing an evidence_id scopes it to that single evidence row, which
        is how one edge carries two rows differing only in the test applied.
        The evidence row must belong to this edge; a mismatch is an error
        rather than a silent reparent.
        """
        if self.graph_db.get_edge(edge_id) is None:
            raise ValueError(f"Edge {edge_id} not found")
        if evidence_id is not None:
            owners = {
                row["id"] for row in self.graph_db.get_evidence_for_edge(edge_id)
            }
            if evidence_id not in owners:
                raise ValueError(
                    f"Evidence {evidence_id} does not belong to edge "
                    f"{edge_id}; a condition can only scope an evidence row "
                    f"on its own edge."
                )
        return self.graph_db.add_condition(
            edge_id, condition_type, condition_value,
            evidence_id=evidence_id, commit=commit,
        )

    def update_edge(self, edge_id, subject_id=None, object_id=None,
                    edge_type=None, notes=None, commit=True):
        """
        Update mutable fields on an existing edge.

        Mutable fields are subject_id, object_id, edge_type, and notes.
        Pass None to leave a field unchanged. Conditions and evidence
        keep their own add/delete paths. Bumps updated_at.

        Use case: correcting an edge whose subject was misattributed
        (e.g., a marker cited as the driver). Existing evidence rows
        stay attached to the same edge id, so the audit trail and
        coverage are preserved across the rewrite.

        Raises ValueError if the edge does not exist; if a referenced
        new subject or object node does not exist; or if the proposed
        (subject_id, object_id, edge_type) collides with another
        existing edge.
        """
        current = self.graph_db.get_edge(edge_id)
        if current is None:
            raise ValueError(f"Edge {edge_id} not found")
        if subject_id is not None and self.graph_db.get_node(subject_id) is None:
            raise ValueError(f"Subject node {subject_id} not found")
        if object_id is not None and self.graph_db.get_node(object_id) is None:
            raise ValueError(f"Object node {object_id} not found")

        updates = {}
        if subject_id is not None:
            updates["subject_id"] = subject_id
        if object_id is not None:
            updates["object_id"] = object_id
        if edge_type is not None:
            updates["edge_type"] = edge_type
        if notes is not None:
            updates["notes"] = notes
        if not updates:
            return edge_id

        try:
            return self.graph_db.update_edge(edge_id, commit=commit, **updates)
        except sqlite3.IntegrityError:
            new_subject = updates.get("subject_id", current["subject_id"])
            new_object = updates.get("object_id", current["object_id"])
            new_type = updates.get("edge_type", current["edge_type"])
            existing = self.graph_db.get_edge_by_triple(
                new_subject, new_object, new_type,
            )
            if existing is not None and existing["id"] != edge_id:
                raise ValueError(
                    f"Cannot update: another edge ({existing['id']}) "
                    f"already exists with ({new_subject}) "
                    f"--{new_type}--> ({new_object}). Add evidence to "
                    f"that edge instead."
                )
            raise

    # -------------------------------------------------------------------------
    # Observation paths (with friendly errors and cross-DB validation)
    # -------------------------------------------------------------------------

    def get_observations(self, node_id):
        """
        Return all observations for a node, with corpus citation
        enrichment looked up from axiom.db.
        """
        if self.graph_db.get_node(node_id) is None:
            raise ValueError(f"Node {node_id} not found")
        rows = self.graph_db.get_observations_for_node(node_id)
        return [self._observation_dict(r) for r in rows]

    def add_observation(self, node_id, observation, commit=True, **fields):
        """
        Add an observation to a node.

        Validates grounding_type (defaulting to 'corpus_primary') and
        the source-type-specific provenance via
        _validate_and_normalize_grounding. For corpus-grounded types,
        also validates chunk_id against the corpus DB and auto-fills
        source_filename from the chunk's parent source when not
        explicitly supplied. Non-corpus types (lexicon, common_knowledge,
        background_weak) must not carry a chunk_id.
        """
        if self.graph_db.get_node(node_id) is None:
            raise ValueError(f"Node {node_id} not found")

        self._validate_and_normalize_grounding(fields)

        chunk_id = fields.get("chunk_id")
        if chunk_id is not None:
            chunk = self.corpus_db.get_chunk(chunk_id)
            if chunk is None:
                raise ValueError(
                    f"chunk_id {chunk_id} not found in the AXIOM corpus database"
                )
            if not fields.get("source_filename"):
                source = self.corpus_db.get_reference(chunk["source_id"])
                if source is not None:
                    fields["source_filename"] = source["filename"]

        return self.graph_db.add_observation(
            node_id, observation, commit=commit, **fields,
        )

    def update_observation(self, observation_id, observation=None, notes=None,
                           commit=True):
        """
        In-place edit of observation text and/or notes. Provenance
        fields (chunk_id, source_*, conversation_*) are immutable; if a
        different chunk supports a different claim, add a new observation.
        """
        if self.graph_db.get_observation(observation_id) is None:
            raise ValueError(f"Observation {observation_id} not found")
        return self.graph_db.update_observation(
            observation_id, observation=observation, notes=notes,
            commit=commit,
        )

    # -------------------------------------------------------------------------
    # Bulk proposal (single transaction)
    # -------------------------------------------------------------------------

    # Order in which rollback delete calls are issued to minimize cascade
    # noise. Lower numbers go first.
    _ROLLBACK_TOOL_ORDER = {
        "graph_delete_observation": 1,
        "graph_delete_alias": 2,
        "graph_delete_evidence": 3,
        "graph_delete_condition": 4,
        "graph_delete_edge": 5,
        "graph_delete_node": 6,
    }

    def apply_proposal(self, proposal):
        """
        Apply a multi-section graph proposal in a single SQLite transaction.

        Proposal sections (all optional):
            new_nodes, new_edges, new_observations,
            alias_additions, evidence_additions, condition_additions,
            observation_rewrites, cross_reference_updates,
            node_updates, edge_updates.

        Node references in new_edges and new_observations may be:
            int                              -> existing node id
            {"id": int}                       -> same, dict form
            {"name": str, "node_type": str}   -> in-payload new_nodes first,
                                                 then DB lookup by
                                                 (canonical_name, node_type)

        Idempotency: a new_node whose (canonical_name, node_type) already
        exists in the DB is matched (existing id reused, aliases merged
        additively, cross_references merged with new keys added and existing
        keys preserved). A new_edge whose (subject, object, edge_type) already
        exists is matched (conditions and evidence from the proposal appended).
        The 'result' field in items distinguishes 'created' from 'matched'.

        Two phases. Validation runs first against the entire payload (grounding
        rules, ID existence, name+type resolution, chunk_id resolution against
        axiom.db). On any error, returns {"status": "rejected", "phase":
        "validation", "errors": [...]} with no writes. On success, the write
        phase runs every change in a single transaction with commit=False
        per call and a single commit at the end. Any exception during writes
        triggers rollback and returns {"status": "rejected", "phase": "write",
        "error": ...}.

        On success, returns:
            status                              "committed"
            stats                               per-section counts
            items                               per-section IDs with
                                                'created' vs 'matched'
            rollback_additions                  delete-tool recipe to undo
                                                the additions (sorted in the
                                                order that minimizes cascade
                                                noise)
            previous_values_for_in_place_edits  pre-edit values captured for
                                                rewrites, cross_reference,
                                                node, and edge updates (the
                                                user can manually restore
                                                via the existing update tools)
            graph_stats                         post-commit graph_stats()
        """
        if not isinstance(proposal, dict):
            return {
                "status": "rejected",
                "phase": "validation",
                "errors": [{
                    "section": "(root)", "index": 0,
                    "error": "proposal must be a dict",
                }],
            }

        validated, errors = self._validate_proposal(proposal)
        if errors:
            return {
                "status": "rejected",
                "phase": "validation",
                "errors": errors,
            }

        try:
            report = self._apply_validated_proposal(validated)
        except Exception as exc:
            try:
                self.graph_db.connection.rollback()
            except Exception:
                pass
            return {
                "status": "rejected",
                "phase": "write",
                "error": str(exc),
            }

        try:
            self.graph_db.connection.commit()
        except Exception as exc:
            try:
                self.graph_db.connection.rollback()
            except Exception:
                pass
            return {
                "status": "rejected",
                "phase": "commit",
                "error": str(exc),
            }

        report["rollback_additions"].sort(
            key=lambda r: self._ROLLBACK_TOOL_ORDER.get(r["tool"], 99)
        )
        report["status"] = "committed"
        report["graph_stats"] = self.get_stats()
        return report

    def _resolve_node_ref(self, ref, node_index):
        """
        Resolve a node reference. Returns (resolved_dict, error_message_or_None).

        resolved_dict has keys: id (may be None for pending_new entries
        during validation; populated by the apply phase), source
        ('existing', 'matched', 'pending_new'), key ((name, type) tuple or
        None for direct-id refs).
        """
        if isinstance(ref, int):
            if self.graph_db.get_node(ref) is None:
                return None, f"node id {ref} not found"
            return {"id": ref, "source": "existing", "key": None}, None
        if isinstance(ref, dict):
            if "id" in ref:
                rid = ref["id"]
                if not isinstance(rid, int):
                    return None, "'id' must be int"
                if self.graph_db.get_node(rid) is None:
                    return None, f"node id {rid} not found"
                return {"id": rid, "source": "existing", "key": None}, None
            if "name" in ref and "node_type" in ref:
                key = (ref["name"], ref["node_type"])
                if key in node_index:
                    entry = node_index[key]
                    return (
                        {"id": entry["id"], "source": entry["source"],
                         "key": key},
                        None,
                    )
                existing = self.graph_db.get_node_by_name_and_type(
                    ref["name"], ref["node_type"],
                )
                if existing is not None:
                    node_index[key] = {"id": existing["id"],
                                       "source": "matched"}
                    return (
                        {"id": existing["id"], "source": "matched",
                         "key": key},
                        None,
                    )
                return None, (
                    f"no node with canonical_name={ref['name']!r} and "
                    f"node_type={ref['node_type']!r} (not in payload "
                    f"new_nodes, not in DB)"
                )
            return None, (
                "dict ref must have either 'id' or both 'name' and 'node_type'"
            )
        return None, (
            "must be int (node id) or dict with 'id' or 'name'+'node_type'"
        )

    def _validate_grounding_fields(self, fields):
        """
        Validate a single evidence/observation field dict's grounding rules
        and chunk_id resolution without mutating the caller's dict.
        Returns an error message string or None.
        """
        if not isinstance(fields, dict):
            return "must be a dict"
        check = dict(fields)
        try:
            self._validate_and_normalize_grounding(check)
        except ValueError as exc:
            return str(exc)
        chunk_id = check.get("chunk_id")
        if chunk_id is not None:
            if self.corpus_db.get_chunk(chunk_id) is None:
                return (
                    f"chunk_id {chunk_id} not found in the AXIOM corpus database"
                )
        return None

    def _validate_proposal(self, proposal):
        """
        Validate the entire proposal without writing. Returns (validated,
        errors). errors is a list of {"section", "index", "error"} dicts;
        empty list means success. validated is the input restructured with
        node refs pre-resolved and pre-edit values captured.
        """
        errors = []
        # (canonical_name, node_type) -> {"id": int_or_None, "source": str}
        node_index = {}

        known_sections = {
            "new_nodes", "new_edges", "new_observations",
            "alias_additions", "evidence_additions", "condition_additions",
            "observation_rewrites", "cross_reference_updates",
            "node_updates", "edge_updates",
        }
        for k in proposal.keys():
            if k not in known_sections:
                errors.append({"section": "(root)", "index": 0,
                               "error": f"unknown top-level key {k!r}"})

        # 1. new_nodes
        new_nodes_v = []
        for i, spec in enumerate(proposal.get("new_nodes") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "new_nodes", "index": i,
                               "error": "must be a dict"})
                continue
            name = spec.get("canonical_name")
            ntype = spec.get("node_type")
            if not name or not isinstance(name, str):
                errors.append({"section": "new_nodes", "index": i,
                               "error": "canonical_name required "
                                        "(non-empty string)"})
                continue
            if not ntype or not isinstance(ntype, str):
                errors.append({"section": "new_nodes", "index": i,
                               "error": "node_type required "
                                        "(non-empty string)"})
                continue
            key = (name, ntype)
            if key in node_index:
                errors.append({"section": "new_nodes", "index": i,
                               "error": (
                                   f"duplicate (canonical_name, node_type) "
                                   f"within payload: ({name!r}, {ntype!r})"
                               )})
                continue
            aliases = spec.get("aliases")
            if aliases is None:
                aliases = []
            elif not isinstance(aliases, list):
                errors.append({"section": "new_nodes", "index": i,
                               "error": "aliases must be a list"})
                continue
            cross_refs = spec.get("cross_references")
            if cross_refs is not None and not isinstance(cross_refs, dict):
                errors.append({"section": "new_nodes", "index": i,
                               "error": "cross_references must be a dict "
                                        "or None"})
                continue
            existing = self.graph_db.get_node_by_name_and_type(name, ntype)
            if existing is not None:
                node_index[key] = {"id": existing["id"], "source": "matched"}
            else:
                node_index[key] = {"id": None, "source": "pending_new"}
            new_nodes_v.append({
                "input_index": i,
                "canonical_name": name,
                "node_type": ntype,
                "notes": spec.get("notes"),
                "aliases": aliases,
                "cross_references": cross_refs,
            })

        # 2. new_edges
        new_edges_v = []
        for i, spec in enumerate(proposal.get("new_edges") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "new_edges", "index": i,
                               "error": "must be a dict"})
                continue
            edge_type = spec.get("edge_type")
            if not edge_type or not isinstance(edge_type, str):
                errors.append({"section": "new_edges", "index": i,
                               "error": "edge_type required "
                                        "(non-empty string)"})
                continue
            subj_r, err = self._resolve_node_ref(
                spec.get("subject"), node_index,
            )
            if err:
                errors.append({"section": "new_edges", "index": i,
                               "error": f"subject: {err}"})
                continue
            obj_r, err = self._resolve_node_ref(
                spec.get("object"), node_index,
            )
            if err:
                errors.append({"section": "new_edges", "index": i,
                               "error": f"object: {err}"})
                continue
            conds = spec.get("conditions") or []
            if not isinstance(conds, list):
                errors.append({"section": "new_edges", "index": i,
                               "error": "conditions must be a list"})
                continue
            cond_ok = True
            for j, c in enumerate(conds):
                if not isinstance(c, dict):
                    errors.append({"section": "new_edges", "index": i,
                                   "error": f"conditions[{j}] must be a dict"})
                    cond_ok = False
                    continue
                ct = c.get("condition_type", c.get("type"))
                cv = c.get("condition_value", c.get("value"))
                if not ct or not cv:
                    errors.append({"section": "new_edges", "index": i,
                                   "error": (
                                       f"conditions[{j}] requires "
                                       f"condition_type and condition_value"
                                   )})
                    cond_ok = False
            if not cond_ok:
                continue
            ev_list = spec.get("evidence") or []
            if not isinstance(ev_list, list):
                errors.append({"section": "new_edges", "index": i,
                               "error": "evidence must be a list"})
                continue
            ev_ok = True
            for j, ev in enumerate(ev_list):
                err = self._validate_grounding_fields(ev)
                if err:
                    errors.append({"section": "new_edges", "index": i,
                                   "error": f"evidence[{j}]: {err}"})
                    ev_ok = False
            if not ev_ok:
                continue
            # `evidence_index` on an inline condition is a position into this
            # edge's own `evidence` list. Validate it here so a bad index is
            # reported alongside every other error, rather than raising in
            # the write phase and rejecting the whole batch.
            idx_ok = True
            for j, c in enumerate(conds):
                evidence_index = c.get("evidence_index")
                if evidence_index is None:
                    continue
                if isinstance(evidence_index, bool) or not isinstance(
                        evidence_index, int):
                    errors.append({"section": "new_edges", "index": i,
                                   "error": (
                                       f"conditions[{j}] 'evidence_index' "
                                       f"must be an int position into this "
                                       f"edge's evidence list"
                                   )})
                    idx_ok = False
                    continue
                if not 0 <= evidence_index < len(ev_list):
                    errors.append({"section": "new_edges", "index": i,
                                   "error": (
                                       f"conditions[{j}] 'evidence_index' "
                                       f"{evidence_index} is out of range; "
                                       f"this edge was given {len(ev_list)} "
                                       f"evidence row(s)"
                                   )})
                    idx_ok = False
            if not idx_ok:
                continue
            new_edges_v.append({
                "input_index": i,
                "subject_resolved": subj_r,
                "object_resolved": obj_r,
                "edge_type": edge_type,
                "conditions": conds,
                "evidence": ev_list,
                "notes": spec.get("notes"),
            })

        # 3. new_observations
        new_obs_v = []
        for i, spec in enumerate(proposal.get("new_observations") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "new_observations", "index": i,
                               "error": "must be a dict"})
                continue
            obs_text = spec.get("observation")
            if not obs_text or not isinstance(obs_text, str):
                errors.append({"section": "new_observations", "index": i,
                               "error": "observation required "
                                        "(non-empty string)"})
                continue
            node_r, err = self._resolve_node_ref(
                spec.get("node"), node_index,
            )
            if err:
                errors.append({"section": "new_observations", "index": i,
                               "error": f"node: {err}"})
                continue
            obs_fields = {k: v for k, v in spec.items()
                          if k not in ("node", "observation")}
            err = self._validate_grounding_fields(obs_fields)
            if err:
                errors.append({"section": "new_observations", "index": i,
                               "error": err})
                continue
            new_obs_v.append({
                "input_index": i,
                "node_resolved": node_r,
                "observation": obs_text,
                "fields": obs_fields,
            })

        # 4. alias_additions
        alias_v = []
        for i, spec in enumerate(proposal.get("alias_additions") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "alias_additions", "index": i,
                               "error": "must be a dict"})
                continue
            node_id = spec.get("node_id")
            alias = spec.get("alias")
            if not isinstance(node_id, int):
                errors.append({"section": "alias_additions", "index": i,
                               "error": "node_id required (int)"})
                continue
            if not alias or not isinstance(alias, str):
                errors.append({"section": "alias_additions", "index": i,
                               "error": "alias required (non-empty string)"})
                continue
            if self.graph_db.get_node(node_id) is None:
                errors.append({"section": "alias_additions", "index": i,
                               "error": f"node {node_id} not found"})
                continue
            alias_v.append({
                "input_index": i,
                "node_id": node_id,
                "alias": alias,
                "notes": spec.get("notes"),
            })

        # 5. evidence_additions
        evidence_v = []
        for i, spec in enumerate(proposal.get("evidence_additions") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "evidence_additions", "index": i,
                               "error": "must be a dict"})
                continue
            edge_id = spec.get("edge_id")
            if not isinstance(edge_id, int):
                errors.append({"section": "evidence_additions", "index": i,
                               "error": "edge_id required (int)"})
                continue
            if self.graph_db.get_edge(edge_id) is None:
                errors.append({"section": "evidence_additions", "index": i,
                               "error": f"edge {edge_id} not found"})
                continue
            fields = {k: v for k, v in spec.items() if k != "edge_id"}
            err = self._validate_grounding_fields(fields)
            if err:
                errors.append({"section": "evidence_additions", "index": i,
                               "error": err})
                continue
            evidence_v.append({
                "input_index": i,
                "edge_id": edge_id,
                "fields": fields,
            })

        # 6. condition_additions
        condition_v = []
        for i, spec in enumerate(proposal.get("condition_additions") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "condition_additions", "index": i,
                               "error": "must be a dict"})
                continue
            edge_id = spec.get("edge_id")
            ct = spec.get("condition_type")
            cv = spec.get("condition_value")
            if not isinstance(edge_id, int):
                errors.append({"section": "condition_additions", "index": i,
                               "error": "edge_id required (int)"})
                continue
            if not ct or not cv:
                errors.append({"section": "condition_additions", "index": i,
                               "error": "condition_type and condition_value "
                                        "required"})
                continue
            if self.graph_db.get_edge(edge_id) is None:
                errors.append({"section": "condition_additions", "index": i,
                               "error": f"edge {edge_id} not found"})
                continue

            # V18 optional evidence scoping. Either an existing evidence_id,
            # or a forward reference into this same payload's
            # evidence_additions (whose row ids do not exist yet at
            # authoring time). Both are validated against the same edge.
            ev_id = spec.get("evidence_id")
            ev_ref = spec.get("evidence_addition_index")
            if ev_id is not None and ev_ref is not None:
                errors.append({"section": "condition_additions", "index": i,
                               "error": "pass at most one of 'evidence_id' "
                                        "and 'evidence_addition_index'"})
                continue
            if ev_id is not None:
                if not isinstance(ev_id, int):
                    errors.append({"section": "condition_additions",
                                   "index": i,
                                   "error": "evidence_id must be an int"})
                    continue
                owners = {
                    r["id"]
                    for r in self.graph_db.get_evidence_for_edge(edge_id)
                }
                if ev_id not in owners:
                    errors.append({
                        "section": "condition_additions", "index": i,
                        "error": f"evidence {ev_id} does not belong to edge "
                                 f"{edge_id}",
                    })
                    continue
            if ev_ref is not None:
                if not isinstance(ev_ref, int):
                    errors.append({
                        "section": "condition_additions", "index": i,
                        "error": "evidence_addition_index must be an int",
                    })
                    continue
                target = next(
                    (v for v in evidence_v if v["input_index"] == ev_ref),
                    None,
                )
                if target is None:
                    errors.append({
                        "section": "condition_additions", "index": i,
                        "error": f"evidence_addition_index {ev_ref} does not "
                                 f"match any validated evidence_additions "
                                 f"entry",
                    })
                    continue
                if target["edge_id"] != edge_id:
                    errors.append({
                        "section": "condition_additions", "index": i,
                        "error": f"evidence_addition_index {ev_ref} targets "
                                 f"edge {target['edge_id']}, not edge "
                                 f"{edge_id}",
                    })
                    continue
            condition_v.append({
                "input_index": i,
                "edge_id": edge_id,
                "condition_type": ct,
                "condition_value": cv,
                "evidence_id": ev_id,
                "evidence_addition_index": ev_ref,
            })

        # 7. observation_rewrites
        rewrite_v = []
        for i, spec in enumerate(proposal.get("observation_rewrites") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "observation_rewrites", "index": i,
                               "error": "must be a dict"})
                continue
            obs_id = spec.get("observation_id")
            if not isinstance(obs_id, int):
                errors.append({"section": "observation_rewrites", "index": i,
                               "error": "observation_id required (int)"})
                continue
            current = self.graph_db.get_observation(obs_id)
            if current is None:
                errors.append({"section": "observation_rewrites", "index": i,
                               "error": f"observation {obs_id} not found"})
                continue
            if "observation" not in spec and "notes" not in spec:
                errors.append({"section": "observation_rewrites", "index": i,
                               "error": "must provide observation and/or notes"})
                continue
            rewrite_v.append({
                "input_index": i,
                "observation_id": obs_id,
                "observation": spec.get("observation"),
                "notes": spec.get("notes"),
                "previous_observation": current["observation"],
                "previous_notes": current["notes"],
            })

        # 8. cross_reference_updates
        xref_v = []
        for i, spec in enumerate(
            proposal.get("cross_reference_updates") or []
        ):
            if not isinstance(spec, dict):
                errors.append({"section": "cross_reference_updates",
                               "index": i, "error": "must be a dict"})
                continue
            node_id = spec.get("node_id")
            if not isinstance(node_id, int):
                errors.append({"section": "cross_reference_updates",
                               "index": i,
                               "error": "node_id required (int)"})
                continue
            current = self.graph_db.get_node(node_id)
            if current is None:
                errors.append({"section": "cross_reference_updates",
                               "index": i,
                               "error": f"node {node_id} not found"})
                continue
            cross_refs = spec.get("cross_references")
            if cross_refs is not None and not isinstance(cross_refs, dict):
                errors.append({"section": "cross_reference_updates",
                               "index": i,
                               "error": "cross_references must be a dict "
                                        "or None"})
                continue
            xref_v.append({
                "input_index": i,
                "node_id": node_id,
                "cross_references": cross_refs,
                "previous_cross_references": self._deserialize_json_field(
                    current["cross_references"]
                ),
            })

        # 9. node_updates
        node_upd_v = []
        for i, spec in enumerate(proposal.get("node_updates") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "node_updates", "index": i,
                               "error": "must be a dict"})
                continue
            node_id = spec.get("node_id")
            if not isinstance(node_id, int):
                errors.append({"section": "node_updates", "index": i,
                               "error": "node_id required (int)"})
                continue
            current = self.graph_db.get_node(node_id)
            if current is None:
                errors.append({"section": "node_updates", "index": i,
                               "error": f"node {node_id} not found"})
                continue
            has_change = any(
                spec.get(k) is not None
                for k in ("canonical_name", "node_type", "notes")
            )
            if not has_change:
                errors.append({"section": "node_updates", "index": i,
                               "error": "must provide at least one of "
                                        "canonical_name, node_type, notes"})
                continue
            node_upd_v.append({
                "input_index": i,
                "node_id": node_id,
                "canonical_name": spec.get("canonical_name"),
                "node_type": spec.get("node_type"),
                "notes": spec.get("notes"),
                "previous_canonical_name": current["canonical_name"],
                "previous_node_type": current["node_type"],
                "previous_notes": current["notes"],
            })

        # 10. edge_updates
        edge_upd_v = []
        for i, spec in enumerate(proposal.get("edge_updates") or []):
            if not isinstance(spec, dict):
                errors.append({"section": "edge_updates", "index": i,
                               "error": "must be a dict"})
                continue
            edge_id = spec.get("edge_id")
            if not isinstance(edge_id, int):
                errors.append({"section": "edge_updates", "index": i,
                               "error": "edge_id required (int)"})
                continue
            current = self.graph_db.get_edge(edge_id)
            if current is None:
                errors.append({"section": "edge_updates", "index": i,
                               "error": f"edge {edge_id} not found"})
                continue
            has_change = any(
                spec.get(k) is not None
                for k in ("subject_id", "object_id", "edge_type", "notes")
            )
            if not has_change:
                errors.append({"section": "edge_updates", "index": i,
                               "error": "must provide at least one of "
                                        "subject_id, object_id, edge_type, "
                                        "notes"})
                continue
            edge_upd_v.append({
                "input_index": i,
                "edge_id": edge_id,
                "subject_id": spec.get("subject_id"),
                "object_id": spec.get("object_id"),
                "edge_type": spec.get("edge_type"),
                "notes": spec.get("notes"),
                "previous_subject_id": current["subject_id"],
                "previous_object_id": current["object_id"],
                "previous_edge_type": current["edge_type"],
                "previous_notes": current["notes"],
            })

        validated = {
            "new_nodes": new_nodes_v,
            "new_edges": new_edges_v,
            "new_observations": new_obs_v,
            "alias_additions": alias_v,
            "evidence_additions": evidence_v,
            "condition_additions": condition_v,
            "observation_rewrites": rewrite_v,
            "cross_reference_updates": xref_v,
            "node_updates": node_upd_v,
            "edge_updates": edge_upd_v,
            "_node_index": node_index,
        }
        return validated, errors

    def _apply_validated_proposal(self, validated):
        """
        Apply a validated proposal. Caller manages the transaction. Each
        graph write call uses commit=False so the entire batch is atomic.
        """
        report = {
            "stats": {
                "nodes_created": 0,
                "nodes_matched": 0,
                "aliases_added": 0,
                "edges_created": 0,
                "edges_matched": 0,
                "evidence_added": 0,
                "conditions_added": 0,
                "observations_added": 0,
                "observations_rewritten": 0,
                "cross_references_updated": 0,
                "nodes_updated": 0,
                "edges_updated": 0,
            },
            "items": {
                "new_nodes": [],
                "new_edges": [],
                "new_observations": [],
                "alias_additions": [],
                "evidence_additions": [],
                "condition_additions": [],
                "observation_rewrites": [],
                "cross_reference_updates": [],
                "node_updates": [],
                "edge_updates": [],
            },
            "rollback_additions": [],
            "previous_values_for_in_place_edits": [],
        }
        node_index = validated["_node_index"]
        # input_index of an evidence_additions entry -> the row id it created.
        # Lets a condition_addition in the same payload scope itself to an
        # evidence row that did not exist when the payload was written.
        evidence_ids_by_input_index = {}

        # 1. new_nodes
        for vnode in validated["new_nodes"]:
            key = (vnode["canonical_name"], vnode["node_type"])
            entry = node_index[key]
            if entry["source"] == "matched":
                node_id = entry["id"]
                added_aliases = []
                for alias in vnode["aliases"]:
                    try:
                        self.graph_db.add_alias(
                            node_id, alias, commit=False,
                        )
                        added_aliases.append(alias)
                        report["stats"]["aliases_added"] += 1
                    except sqlite3.IntegrityError:
                        # Already attached; skip silently.
                        pass
                xrefs_added_keys = []
                if vnode["cross_references"]:
                    current = self.graph_db.get_node(node_id)
                    existing_refs = self._deserialize_json_field(
                        current["cross_references"]
                    ) or {}
                    merged = dict(existing_refs)
                    for k, v in vnode["cross_references"].items():
                        if k not in merged:
                            merged[k] = v
                            xrefs_added_keys.append(k)
                    if xrefs_added_keys:
                        self.graph_db.set_cross_references(
                            node_id, merged, commit=False,
                        )
                        report["stats"]["cross_references_updated"] += 1
                report["stats"]["nodes_matched"] += 1
                report["items"]["new_nodes"].append({
                    "input_index": vnode["input_index"],
                    "id": node_id,
                    "result": "matched",
                    "canonical_name": vnode["canonical_name"],
                    "node_type": vnode["node_type"],
                    "aliases_added_to_existing": added_aliases,
                    "cross_reference_keys_added": xrefs_added_keys,
                })
            else:
                # Create new.
                node_id = self.add_node(
                    vnode["canonical_name"], vnode["node_type"],
                    notes=vnode["notes"], aliases=vnode["aliases"],
                    cross_references=vnode["cross_references"],
                    commit=False,
                )
                report["stats"]["nodes_created"] += 1
                report["stats"]["aliases_added"] += len(vnode["aliases"])
                report["items"]["new_nodes"].append({
                    "input_index": vnode["input_index"],
                    "id": node_id,
                    "result": "created",
                    "canonical_name": vnode["canonical_name"],
                    "node_type": vnode["node_type"],
                })
                report["rollback_additions"].append({
                    "tool": "graph_delete_node",
                    "args": {"node_id": node_id},
                })
                node_index[key]["id"] = node_id
                node_index[key]["source"] = "matched"

        def _final_id(resolved):
            """Look up the post-creation id for a resolved ref."""
            if resolved["key"] is not None:
                return node_index[resolved["key"]]["id"]
            return resolved["id"]

        # 2. new_edges
        for vedge in validated["new_edges"]:
            subj_id = _final_id(vedge["subject_resolved"])
            obj_id = _final_id(vedge["object_resolved"])
            existing = self.graph_db.get_edge_by_triple(
                subj_id, obj_id, vedge["edge_type"],
            )
            if existing is not None:
                edge_id = existing["id"]
                # Evidence is written BEFORE conditions (V18), matching the
                # order in add_edge, so that a condition in this same payload
                # can scope itself to one of these evidence rows via
                # `evidence_index`. Without this, an inline condition on a
                # new_edge that happened to match an existing edge would
                # silently land edge-scoped.
                ev_added_ids = []
                for ev in vedge["evidence"]:
                    new_ev_id = self.add_evidence(
                        edge_id, commit=False, **ev,
                    )
                    ev_added_ids.append(new_ev_id)
                    report["stats"]["evidence_added"] += 1
                    report["rollback_additions"].append({
                        "tool": "graph_delete_evidence",
                        "args": {"evidence_id": new_ev_id},
                    })
                cond_added = 0
                for c in vedge["conditions"]:
                    ct = c.get("condition_type", c.get("type"))
                    cv = c.get("condition_value", c.get("value"))
                    evidence_index = c.get("evidence_index")
                    scoped_evidence_id = None
                    if evidence_index is not None:
                        if not isinstance(evidence_index, int):
                            raise ValueError(
                                "Condition 'evidence_index' must be an int "
                                "position into this edge's `evidence` list."
                            )
                        if not 0 <= evidence_index < len(ev_added_ids):
                            raise ValueError(
                                f"Condition 'evidence_index' {evidence_index} "
                                f"is out of range; this edge was given "
                                f"{len(ev_added_ids)} evidence row(s)."
                            )
                        scoped_evidence_id = ev_added_ids[evidence_index]
                    new_cid = self.graph_db.add_condition(
                        edge_id, ct, cv,
                        evidence_id=scoped_evidence_id,
                        commit=False,
                    )
                    if new_cid:
                        cond_added += 1
                        report["stats"]["conditions_added"] += 1
                        report["rollback_additions"].append({
                            "tool": "graph_delete_condition",
                            "args": {"condition_id": new_cid},
                        })
                report["stats"]["edges_matched"] += 1
                report["items"]["new_edges"].append({
                    "input_index": vedge["input_index"],
                    "id": edge_id,
                    "result": "matched",
                    "subject_id": subj_id,
                    "object_id": obj_id,
                    "edge_type": vedge["edge_type"],
                    "conditions_added_to_existing": cond_added,
                    "evidence_added_to_existing": len(ev_added_ids),
                })
            else:
                edge_id = self.add_edge(
                    subj_id, obj_id, vedge["edge_type"],
                    conditions=vedge["conditions"],
                    evidence=vedge["evidence"],
                    notes=vedge["notes"],
                    commit=False,
                )
                report["stats"]["edges_created"] += 1
                report["stats"]["conditions_added"] += len(
                    vedge["conditions"]
                )
                report["stats"]["evidence_added"] += len(vedge["evidence"])
                report["items"]["new_edges"].append({
                    "input_index": vedge["input_index"],
                    "id": edge_id,
                    "result": "created",
                    "subject_id": subj_id,
                    "object_id": obj_id,
                    "edge_type": vedge["edge_type"],
                })
                report["rollback_additions"].append({
                    "tool": "graph_delete_edge",
                    "args": {"edge_id": edge_id},
                })

        # 3. new_observations
        for vobs in validated["new_observations"]:
            node_id = _final_id(vobs["node_resolved"])
            obs_id = self.add_observation(
                node_id, vobs["observation"],
                commit=False, **vobs["fields"],
            )
            report["stats"]["observations_added"] += 1
            report["items"]["new_observations"].append({
                "input_index": vobs["input_index"],
                "id": obs_id,
                "node_id": node_id,
            })
            report["rollback_additions"].append({
                "tool": "graph_delete_observation",
                "args": {"observation_id": obs_id},
            })

        # 4. alias_additions
        for va in validated["alias_additions"]:
            try:
                aid = self.graph_db.add_alias(
                    va["node_id"], va["alias"],
                    notes=va["notes"], commit=False,
                )
                report["stats"]["aliases_added"] += 1
                report["items"]["alias_additions"].append({
                    "input_index": va["input_index"],
                    "id": aid,
                    "node_id": va["node_id"],
                    "alias": va["alias"],
                    "result": "added",
                })
                report["rollback_additions"].append({
                    "tool": "graph_delete_alias",
                    "args": {"alias_id": aid},
                })
            except sqlite3.IntegrityError:
                report["items"]["alias_additions"].append({
                    "input_index": va["input_index"],
                    "id": None,
                    "node_id": va["node_id"],
                    "alias": va["alias"],
                    "result": "already_present",
                })

        # 5. evidence_additions
        for ve in validated["evidence_additions"]:
            ev_id = self.add_evidence(
                ve["edge_id"], commit=False, **ve["fields"],
            )
            report["stats"]["evidence_added"] += 1
            report["items"]["evidence_additions"].append({
                "input_index": ve["input_index"],
                "id": ev_id,
                "edge_id": ve["edge_id"],
            })
            report["rollback_additions"].append({
                "tool": "graph_delete_evidence",
                "args": {"evidence_id": ev_id},
            })
            evidence_ids_by_input_index[ve["input_index"]] = ev_id

        # 6. condition_additions
        for vc in validated["condition_additions"]:
            scoped_evidence_id = vc.get("evidence_id")
            if scoped_evidence_id is None:
                ref = vc.get("evidence_addition_index")
                if ref is not None:
                    scoped_evidence_id = evidence_ids_by_input_index.get(ref)
            cid = self.graph_db.add_condition(
                vc["edge_id"], vc["condition_type"], vc["condition_value"],
                evidence_id=scoped_evidence_id,
                commit=False,
            )
            if cid:
                report["stats"]["conditions_added"] += 1
                report["items"]["condition_additions"].append({
                    "input_index": vc["input_index"],
                    "id": cid,
                    "edge_id": vc["edge_id"],
                    "result": "added",
                })
                report["rollback_additions"].append({
                    "tool": "graph_delete_condition",
                    "args": {"condition_id": cid},
                })
            else:
                report["items"]["condition_additions"].append({
                    "input_index": vc["input_index"],
                    "id": None,
                    "edge_id": vc["edge_id"],
                    "result": "already_present",
                })

        # 7. observation_rewrites
        for vr in validated["observation_rewrites"]:
            self.graph_db.update_observation(
                vr["observation_id"],
                observation=vr["observation"],
                notes=vr["notes"],
                commit=False,
            )
            report["stats"]["observations_rewritten"] += 1
            report["items"]["observation_rewrites"].append({
                "input_index": vr["input_index"],
                "observation_id": vr["observation_id"],
            })
            report["previous_values_for_in_place_edits"].append({
                "type": "observation_rewrite",
                "observation_id": vr["observation_id"],
                "previous_observation": vr["previous_observation"],
                "previous_notes": vr["previous_notes"],
            })

        # 8. cross_reference_updates
        for vx in validated["cross_reference_updates"]:
            self.graph_db.set_cross_references(
                vx["node_id"], vx["cross_references"], commit=False,
            )
            report["stats"]["cross_references_updated"] += 1
            report["items"]["cross_reference_updates"].append({
                "input_index": vx["input_index"],
                "node_id": vx["node_id"],
            })
            report["previous_values_for_in_place_edits"].append({
                "type": "cross_reference_update",
                "node_id": vx["node_id"],
                "previous_cross_references": vx["previous_cross_references"],
            })

        # 9. node_updates
        for vn in validated["node_updates"]:
            updates = {}
            if vn["canonical_name"] is not None:
                updates["canonical_name"] = vn["canonical_name"]
            if vn["node_type"] is not None:
                updates["node_type"] = vn["node_type"]
            if vn["notes"] is not None:
                updates["notes"] = vn["notes"]
            self.graph_db.update_node(
                vn["node_id"], commit=False, **updates,
            )
            report["stats"]["nodes_updated"] += 1
            report["items"]["node_updates"].append({
                "input_index": vn["input_index"],
                "node_id": vn["node_id"],
            })
            report["previous_values_for_in_place_edits"].append({
                "type": "node_update",
                "node_id": vn["node_id"],
                "previous_canonical_name": vn["previous_canonical_name"],
                "previous_node_type": vn["previous_node_type"],
                "previous_notes": vn["previous_notes"],
            })

        # 10. edge_updates
        for ve in validated["edge_updates"]:
            updates = {}
            if ve["subject_id"] is not None:
                updates["subject_id"] = ve["subject_id"]
            if ve["object_id"] is not None:
                updates["object_id"] = ve["object_id"]
            if ve["edge_type"] is not None:
                updates["edge_type"] = ve["edge_type"]
            if ve["notes"] is not None:
                updates["notes"] = ve["notes"]
            self.graph_db.update_edge(
                ve["edge_id"], commit=False, **updates,
            )
            report["stats"]["edges_updated"] += 1
            report["items"]["edge_updates"].append({
                "input_index": ve["input_index"],
                "edge_id": ve["edge_id"],
            })
            report["previous_values_for_in_place_edits"].append({
                "type": "edge_update",
                "edge_id": ve["edge_id"],
                "previous_subject_id": ve["previous_subject_id"],
                "previous_object_id": ve["previous_object_id"],
                "previous_edge_type": ve["previous_edge_type"],
                "previous_notes": ve["previous_notes"],
            })

        return report

    def delete_node(self, node_id):
        return self.graph_db.delete_node(node_id)

    def delete_edge(self, edge_id):
        return self.graph_db.delete_edge(edge_id)

    def delete_evidence(self, evidence_id):
        return self.graph_db.delete_evidence(evidence_id)

    def delete_condition(self, condition_id):
        return self.graph_db.delete_condition(condition_id)

    def delete_observation(self, observation_id):
        return self.graph_db.delete_observation(observation_id)

    def delete_alias(self, alias_id):
        return self.graph_db.delete_alias(alias_id)

    # -------------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------------

    def get_stats(self):
        stats = self.graph_db.get_stats()
        # Species histogram over gene/protein nodes. Derived from
        # cross_references at read time (not stored), so it reflects the
        # same classifier the graph export and node reads use. Surfaces
        # the 'unknown' count as a tracked metric.
        species_hist = {}
        for row in self.graph_db.get_all_nodes():
            node_type = row["node_type"]
            if node_type not in SPECIES_RELEVANT_TYPES:
                continue
            cross_refs = self._deserialize_json_field(row["cross_references"])
            sp = derive_species(node_type, cross_refs)
            species_hist[sp] = species_hist.get(sp, 0) + 1
        stats["species_histogram"] = species_hist
        return stats
