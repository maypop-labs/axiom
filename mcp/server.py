#!/usr/bin/env python3
"""
AXIOM Project - MCP Server

Exposes the AXIOM corpus as MCP tools over stdio. Designed for use with
Claude Desktop or any MCP-compatible client.

Tools:
    semantic_search    embedding-based retrieval (cosine similarity)
    keyword_search     word-bounded GLOB matching across chunks
    hybrid_search      reciprocal rank fusion of the above
    get_source         source metadata by id or filename
    get_source_chunks  all chunks for a source, in order
    get_chunk          a single chunk by id with source metadata
    find_entity        PubTator NER lookup (genes, diseases, chemicals, etc.)

    graph_get_node               one node by id or (name, type), with
                                 aliases, cross_references, and observations
    graph_find_nodes             nodes matching a name/alias substring,
                                 with observation_count per match
    graph_find_nodes_batch       batch version of graph_find_nodes
    graph_find_nodes_by_species  gene/protein nodes by derived species
                                 (pass 'unknown' to audit unclassified)
    graph_get_edges              edges incident to a node
    graph_get_edge               full edge details (nodes, conditions, evidence)
    graph_neighbors              one-hop neighbors of a node
    graph_get_observations       all corpus-grounded observations for a node
    graph_add_node               create a node (optional aliases, cross_references)
    graph_add_alias              add an alternate name to a node
    graph_add_edge               create an edge with conditions and evidence
    graph_add_evidence           add an evidence record to an existing edge
    graph_add_condition          add a precondition to an existing edge
    graph_add_observation        add a corpus-grounded observation to a node
    graph_update_observation     in-place edit of observation text or notes
    graph_update_node            in-place edit of canonical_name, node_type, or notes
    graph_update_edge            in-place edit of subject, object, edge_type, or notes
    graph_set_cross_references   set or replace cross_references on a node
    graph_apply_proposal         apply a multi-section proposal in one
                                 transaction (bulk creates, updates,
                                 rewrites, cross-refs)
    graph_delete_node            delete a node (cascades)
    graph_delete_edge            delete an edge (cascades)
    graph_delete_evidence        delete one evidence row from an edge
    graph_delete_condition       delete one condition row from an edge
    graph_delete_observation     delete one observation row from a node
    graph_delete_alias           delete one alias row from a node
    graph_stats                  graph counts and histograms

Startup costs (one-time):
    - bge-base-en-v1.5 model load: ~2.5s warm, ~16s first time (440MB DL)
    - Embedding cache load: ~1-2s for ~80k chunks (~239MB resident)

Per-query costs:
    - semantic_search: ~30-50ms (query embed + matmul + content fetch)
    - keyword_search: ~50-200ms (depends on candidate count)
    - hybrid_search: roughly the sum of the above
"""

import logging
import sys
import threading
from pathlib import Path

# Make lib/ importable
SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_LIB = SCRIPT_DIR.parent / "Python" / "lib"
sys.path.insert(0, str(PYTHON_LIB))

from mcp.server.fastmcp import FastMCP

from axiom_db import AxiomDatabase
from retrieval import AxiomRetriever
from graph import GraphAccessor


# Logging to stderr; stdout is reserved for the MCP protocol over stdio.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("axiom_mcp")


# -----------------------------------------------------------------------------
# Initialization (runs at import time)
# -----------------------------------------------------------------------------

logger.info("Starting AXIOM MCP server")
retriever = AxiomRetriever()
# Cheap path only: open and migrate the corpus DB so the MCP transport and
# tools/list come up immediately. The bge model and the ~239MB embedding cache
# are deferred to the first corpus search via _ensure_retriever_loaded() below,
# so a cold start no longer blocks tool registration.
retriever.db.initialize()
db = retriever.db
stats = db.get_stats()
logger.info(
    "DB ready: %d sources, %d chunks, %d pubtator entities "
    "(embedding model deferred to first corpus search)",
    stats["sources"], stats["chunks"], stats["pubtator_entities"],
)

graph = GraphAccessor()
graph.initialize()
graph_stats = graph.get_stats()
logger.info(
    "Graph: %d nodes, %d edges, %d conditions, %d evidence, %d observations",
    graph_stats["nodes"], graph_stats["edges"],
    graph_stats["conditions"], graph_stats["evidence"],
    graph_stats["observations"],
)

# Lazy, thread-safe load of the embedding model + cache. Called at the top of
# the three corpus-search tools. retriever.load() is itself idempotent; the
# module-level flag and lock keep a concurrent first-call from double-loading.
_retriever_loaded = False
_retriever_load_lock = threading.Lock()


def _ensure_retriever_loaded():
    """Load the embedding model and cache on first corpus search."""
    global _retriever_loaded
    if _retriever_loaded:
        return
    with _retriever_load_lock:
        if not _retriever_loaded:
            retriever.load()
            _retriever_loaded = True


mcp = FastMCP("axiom")


# -----------------------------------------------------------------------------
# Search tools
# -----------------------------------------------------------------------------

@mcp.tool()
def semantic_search(
    query: str,
    k: int = 10,
    source_type: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    exclude_references: bool = True,
) -> list[dict]:
    """
    Search the AXIOM corpus by semantic similarity.

    Embeds the query with bge-base-en-v1.5 and returns the chunks with the
    highest cosine similarity. Best for conceptual or thematic queries
    where the corpus's vocabulary may differ from the user's (pathways,
    mechanisms, biological processes). For exact-symbol queries (specific
    gene names, technical terms, identifiers) prefer keyword_search.

    Args:
        query: The natural-language search query.
        k: Number of results to return. Default 10.
        source_type: Restrict to one of 'journal_article', 'book',
            'preprint', 'other'. None = all types.
        year_min: Restrict to sources published in or after this year.
        year_max: Restrict to sources published in or before this year.
        exclude_references: Drop chunks from reference/bibliography
            sections. Default True; set False to include them.

    Returns:
        List of dicts with: chunk_id, source_id, filename, title, year,
        section, token_count, rank, score (cosine similarity, 0-1),
        content (chunk text), citation (APA-formatted).
    """
    _ensure_retriever_loaded()
    return retriever.semantic_search(
        query, k=k, source_type=source_type,
        year_min=year_min, year_max=year_max,
        exclude_references=exclude_references,
    )


@mcp.tool()
def keyword_search(
    query: str,
    k: int = 10,
    source_type: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    exclude_references: bool = True,
) -> list[dict]:
    """
    Search the AXIOM corpus by word-bounded keyword match.

    Splits the query on whitespace and finds chunks containing every term
    as a whole word (AND semantics; case-sensitive). Results are scored
    by total occurrence count across all terms. Best for exact-symbol
    queries (gene symbols, drug names, technical terms) where conceptual
    similarity is not what you want.

    Examples:
        keyword_search("TEAD1")             chunks mentioning TEAD1
        keyword_search("YAP cGAS")          chunks mentioning both YAP and cGAS
        keyword_search("rapamycin mTOR")    chunks mentioning both terms

    Args:
        query: One or more search terms. Multiple terms are joined by AND.
        k: Number of results to return. Default 10.
        source_type, year_min, year_max, exclude_references: as for
            semantic_search.

    Returns:
        Same shape as semantic_search results. The score field is total
        whole-word occurrence count across all query terms.
    """
    _ensure_retriever_loaded()
    return retriever.keyword_search(
        query, k=k, source_type=source_type,
        year_min=year_min, year_max=year_max,
        exclude_references=exclude_references,
    )


@mcp.tool()
def hybrid_search(
    query: str,
    k: int = 10,
    source_type: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    exclude_references: bool = True,
) -> list[dict]:
    """
    Search the AXIOM corpus by hybrid retrieval (semantic + keyword).

    Runs semantic_search and keyword_search in parallel and fuses the
    rankings via Reciprocal Rank Fusion. The default search choice when
    you don't have a strong reason to prefer one mode. Useful when the
    query mixes conceptual content with specific symbols (e.g.,
    'YAP/TAZ regulation by actin dynamics').

    Args:
        query: The search query.
        k: Number of results to return. Default 10.
        source_type, year_min, year_max, exclude_references: as for
            semantic_search.

    Returns:
        Same shape as semantic_search results. The score field is the
        Reciprocal Rank Fusion score (higher is better; not directly
        comparable to cosine similarity).
    """
    _ensure_retriever_loaded()
    return retriever.hybrid_search(
        query, k=k, source_type=source_type,
        year_min=year_min, year_max=year_max,
        exclude_references=exclude_references,
    )


# -----------------------------------------------------------------------------
# Source-level access
# -----------------------------------------------------------------------------

@mcp.tool()
def get_source(
    source_id: int | None = None,
    filename: str | None = None,
) -> dict | None:
    """
    Get full metadata for a source. Provide exactly one of source_id or filename.

    Args:
        source_id: Numeric source ID.
        filename: Markdown filename (e.g., '(2022) Hallmarks of aging.md').

    Returns:
        Dict with: id, filename, source_type, title, authors, journal,
        year, volume, issue, pages, doi, pmid, abstract, citation_apa,
        citation_mla, markdown_path, metadata_source. None if not found.
    """
    if source_id is not None:
        row = db.get_reference(source_id)
    elif filename:
        row = db.get_reference_by_filename(filename)
    else:
        return None
    return dict(row) if row else None


@mcp.tool()
def get_source_chunks(
    source_id: int,
    sections: list[str] | None = None,
    include_content: bool = True,
) -> list[dict]:
    """
    Get chunks for a source, ordered by chunk_index.

    Useful for reading a paper end-to-end after surfacing it via search.
    Returns chunk content but not embeddings.

    On large papers (e.g. Nature-format with a long Methods section) the
    full chunk list can be sizeable. Two parameters keep the response
    bounded:

    - include_content=False returns a cheap section map: every chunk's
      id, index, section, and token_count, with the heavy content text
      omitted. Call this first to see the section layout, then pull the
      substantive sections.
    - sections=[...] restricts the result to chunks whose section matches
      at least one of the given fragments (case-insensitive substring
      match). E.g. sections=["Discussion", "Spermine"] returns the
      discussion plus any spermine-titled section.

    The two compose: get_source_chunks(id, include_content=False) to map
    the paper, then get_source_chunks(id, sections=[...]) to read the
    chosen sections.

    Args:
        source_id: Numeric source ID.
        sections: Optional list of section-name fragments. None = all
            sections. Matching is case-insensitive substring.
        include_content: When False, omit the content text from each
            returned chunk (cheap section map). Default True.

    Returns:
        List of dicts with: chunk_id, chunk_index, section, token_count,
        and (when include_content is True) content.
    """
    chunks = db.get_chunks_for_source(
        source_id, sections=sections, include_content=include_content,
    )
    result = []
    for c in chunks:
        row = {
            "chunk_id": c["id"],
            "chunk_index": c["chunk_index"],
            "section": c["section"],
            "token_count": c["token_count"],
        }
        if include_content:
            row["content"] = c["content"]
        result.append(row)
    return result


@mcp.tool()
def get_chunk(chunk_id: int) -> dict | None:
    """
    Get a single chunk by id, with source metadata for citation.

    Args:
        chunk_id: Numeric chunk ID.

    Returns:
        Dict with: chunk_id, source_id, chunk_index, filename, title,
        year, section, token_count, content, citation. None if not found.
    """
    chunk = db.get_chunk(chunk_id)
    if chunk is None:
        return None
    source = db.get_reference(chunk["source_id"])
    return {
        "chunk_id": chunk["id"],
        "source_id": chunk["source_id"],
        "chunk_index": chunk["chunk_index"],
        "filename": source["filename"] if source else None,
        "title": source["title"] if source else None,
        "year": source["year"] if source else None,
        "section": chunk["section"],
        "token_count": chunk["token_count"],
        "content": chunk["content"],
        "citation": source["citation_apa"] if source else None,
    }


# -----------------------------------------------------------------------------
# Entity lookup
# -----------------------------------------------------------------------------

@mcp.tool()
def find_entity(
    mention: str,
    entity_type: str | None = None,
    k: int = 20,
) -> list[dict]:
    """
    Look up sources where PubTator tagged a given entity.

    PubTator's NER coverage is precision-oriented: it tags entities that
    are central to a paper's findings, not every mention. Useful for
    finding papers ABOUT a gene/disease/chemical. Less useful as a recall
    tool (use keyword_search for that).

    Args:
        mention: The entity text (e.g., 'YAP', 'p53', 'rapamycin').
            Match is case-insensitive against the surface form PubTator
            captured.
        entity_type: Optional filter on entity type. One of: 'Gene',
            'Disease', 'Chemical', 'Species', 'Mutation', 'CellLine',
            'Variant'. None = all types.
        k: Maximum sources to return, ranked by mention count. Default 20.

    Returns:
        List of dicts with: source_id, filename, title, year, mention_count,
        normalized_id (the canonical identifier PubTator assigned, e.g.,
        an NCBI Gene ID).
    """
    sql = (
        "SELECT s.id AS source_id, s.filename, s.title, s.year, "
        "COUNT(*) AS mention_count, "
        "MIN(pe.normalized_id) AS normalized_id "
        "FROM pubtator_entities pe "
        "JOIN sources s ON s.id = pe.source_id "
        "WHERE LOWER(pe.mention) = LOWER(?)"
    )
    params = [mention]
    if entity_type:
        sql += " AND pe.entity_type = ?"
        params.append(entity_type)
    sql += " GROUP BY s.id ORDER BY mention_count DESC, s.year DESC LIMIT ?"
    params.append(k)

    cursor = db.connection.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


# -----------------------------------------------------------------------------
# Graph: read tools
# -----------------------------------------------------------------------------

@mcp.tool()
def graph_get_node(
    node_id: int | None = None,
    name: str | None = None,
    node_type: str | None = None,
) -> dict | None:
    """
    Get one node from the mechanistic knowledge graph.

    Provide either node_id, or both name and node_type. The returned
    dict carries the node's full per-node content: aliases,
    cross_references (canonical IDs from public databases, deserialized
    from JSON), observation_count, and the full observations list with
    cross-DB citation enrichment.

    Args:
        node_id: Numeric node id.
        name: Canonical name (must be paired with node_type).
        node_type: Suggested values are 'gene', 'protein', 'miRNA',
            'oligonucleotide', 'PTM_state', 'complex', 'process',
            'phenotype', 'compartment', 'condition', 'small_molecule',
            'other'.

    Returns:
        Dict with id, canonical_name, node_type, notes, cross_references
        (dict or None), aliases (list of strings), observation_count,
        observations (list of observation dicts; each carries
        grounding_type, provenance_extra, and citation per V13's
        grounding policy, plus title and year for corpus-resolvable
        rows), created_at, updated_at. None if not found.
    """
    return graph.get_node(node_id=node_id, name=name, node_type=node_type)


@mcp.tool()
def graph_find_nodes(
    query: str,
    node_type: str | None = None,
    k: int = 20,
) -> list[dict]:
    """
    Find nodes whose canonical_name or any alias matches the query.

    Substring match, case-insensitive. Used during graph proposals to
    surface existing nodes that might be the same biological entity as
    a candidate addition. Each match carries observation_count so callers
    can see at a glance which matches have accumulated content.

    Args:
        query: Search string.
        node_type: Optional filter on node_type.
        k: Maximum results. Default 20.

    Returns:
        List of node dicts (id, canonical_name, node_type, notes,
        cross_references, aliases, observation_count). Full observations
        are not included; call graph_get_observations or graph_get_node
        on a specific match to retrieve them.
    """
    return graph.find_nodes(query, node_type=node_type, k=k)


@mcp.tool()
def graph_find_nodes_by_species(
    species: str,
    node_type: str | None = None,
    k: int = 200,
) -> list[dict]:
    """
    List gene/protein nodes whose derived species matches `species`.

    Species is derived from each node's cross_references at read time
    (the same classifier the graph export uses): a per-organism
    nomenclature key (hgnc/mgi/rgd/wormbase) wins, else a recognized
    NCBI taxid under 'taxid'. Values: 'human', 'mouse', 'rat', 'worm',
    'unknown'. Pass species='unknown' to audit gene/protein nodes that
    will export without a species classification (the set to backfill
    via LEXICON re-enrichment). `node_type` restricts to 'gene' or
    'protein'.

    Returns:
        List of node dicts (same shape as graph_find_nodes), each
        including the derived `species` field.
    """
    return graph.find_nodes_by_species(species, node_type=node_type, k=k)


@mcp.tool()
def graph_find_nodes_batch(
    queries: list[str],
    node_type: str | None = None,
    k: int = 20,
) -> list[dict]:
    """
    Batch version of graph_find_nodes.

    Runs multiple name/alias queries against the graph in one MCP round
    trip. Per-query semantics are identical to graph_find_nodes
    (case-insensitive substring match against canonical_name and
    aliases, with observation_count on every match). Intended for
    proposal pre-flight when several candidate node names need to be
    checked at once.

    Args:
        queries: List of search strings, one per candidate. An empty
            list returns []. Duplicate queries are allowed and produce
            duplicate entries in the output (each carrying the same
            matches).
        node_type: Optional filter applied to every query. None = no
            filter.
        k: Maximum results per query. Default 20.

    Returns:
        List of dicts in input order, one per query, each with:
            query: the original query string.
            matches: list of node dicts (id, canonical_name, node_type,
                notes, cross_references, aliases, observation_count).
                As with graph_find_nodes, full observations are not
                included; call graph_get_observations or graph_get_node
                on a specific match to retrieve them.

    Any DB error fails the whole call (consistent with the per-call
    tool's all-or-nothing semantics).
    """
    return graph.find_nodes_batch(queries, node_type=node_type, k=k)


@mcp.tool()
def graph_get_edges(
    node_id: int,
    direction: str = "both",
    edge_type: str | None = None,
) -> list[dict]:
    """
    Get all edges incident to a node, with subject/object names and coverage.

    Args:
        node_id: Numeric node id.
        direction: 'in' (node is object), 'out' (node is subject), or
            'both' (default).
        edge_type: Optional filter on edge_type.

    Returns:
        List of dicts with id, subject_id, subject_name, subject_type,
        object_id, object_name, object_type, edge_type, coverage,
        notes, timestamps.
    """
    return graph.get_edges_for_node(
        node_id, direction=direction, edge_type=edge_type,
    )


@mcp.tool()
def graph_get_edge(edge_id: int) -> dict | None:
    """
    Get full edge details: nodes, type, conditions, evidence with citations.

    Coverage equals len(evidence). Each evidence record includes the
    source citation (looked up from the AXIOM corpus database) when the
    chunk_id or source_filename is resolvable.

    Args:
        edge_id: Numeric edge id.

    Returns:
        Dict with id, subject_id, subject_name, subject_type, object_id,
        object_name, object_type, edge_type, notes, conditions (list of
        {condition_type, condition_value}), evidence (list of records;
        each carries grounding_type, provenance_extra, and citation per
        V13's grounding policy, plus title and year for corpus-resolvable
        rows), coverage. None if not found.
    """
    return graph.get_edge_full(edge_id)


@mcp.tool()
def graph_neighbors(
    node_id: int,
    direction: str = "both",
    edge_type: str | None = None,
) -> list[dict]:
    """
    Get one-hop neighbors of a node, joined with the connecting edge.

    Args:
        node_id: Numeric node id.
        direction: 'in', 'out', or 'both' (default).
        edge_type: Optional filter on edge_type.

    Returns:
        List of dicts with neighbor_id, neighbor_canonical_name,
        neighbor_node_type, edge_id, edge_type, edge_direction
        ('in' or 'out').
    """
    return graph.get_neighbors(
        node_id, direction=direction, edge_type=edge_type,
    )


@mcp.tool()
def graph_get_observations(node_id: int) -> list[dict]:
    """
    Get all corpus-grounded observations attached to a node.

    Each observation is a finding extracted from a single corpus chunk
    during a prior conversation, in Claude's or the user's own words
    (not a quote). One row per supporting chunk; the same chunk_id can
    legitimately back multiple distinct observations, and the same
    observation can have separate rows from different chunks.

    Use this proactively whenever a node is matched during a graph
    pre-flight, so accumulated content can inform the response before
    it is composed.

    Args:
        node_id: Numeric node id.

    Returns:
        List of observation dicts, ordered by conversation_date and id.
        Each dict carries id, node_id, observation, grounding_type,
        provenance_extra, source_filename, source_doi, source_pmid,
        chunk_id, method, cell_system, conversation_date,
        conversation_question, notes, and citation. The citation
        format depends on grounding_type: corpus types yield an APA
        string (with title and year alongside) when the source resolves
        in axiom.db; lexicon yields '<source> <identifier> (retrieved
        <date>)'; common_knowledge and background_weak yield a labeled
        justification string.

    Raises:
        ValueError if the node does not exist.
    """
    return graph.get_observations(node_id)


# -----------------------------------------------------------------------------
# Graph: write tools
# -----------------------------------------------------------------------------

@mcp.tool()
def graph_add_node(
    canonical_name: str,
    node_type: str,
    notes: str | None = None,
    aliases: list[str] | None = None,
    cross_references: dict | None = None,
) -> dict:
    """
    Create a new node.

    Suggested node_type values: 'gene', 'protein', 'miRNA',
    'oligonucleotide', 'PTM_state', 'complex', 'process', 'phenotype',
    'compartment', 'condition', 'small_molecule', 'other'. Not enforced;
    you can introduce a new type if needed.

    'oligonucleotide' is a therapeutic nucleic-acid agent acting by
    sequence-complementary target engagement (siRNA, antisense
    oligonucleotide, aptamer, mRNA payload, guide RNA). Use 'miRNA' only
    for an endogenous regulatory microRNA, never for an administered
    agent; that is the boundary these two types exist to separate.

    Args:
        canonical_name: Primary name for the entity.
        node_type: Type of biological entity.
        notes: Optional free-form notes.
        aliases: Optional list of alternate names attached in the same call.
        cross_references: Optional dict of canonical IDs from public
            databases. Typical keys (depending on node_type and source):
            'ncbi_gene_id', 'ensembl_gene', 'uniprot', 'hgnc', 'omim',
            'pubchem_cid', 'inchi_key', 'go'. Stored as a JSON-serialized
            string in the database; returned deserialized.

    Returns:
        Dict with id (the new node's id) and message.

    Raises:
        ValueError if a node with the same (canonical_name, node_type)
        already exists; the error message includes the existing id.
    """
    node_id = graph.add_node(
        canonical_name, node_type, notes=notes, aliases=aliases,
        cross_references=cross_references,
    )
    return {
        "id": node_id,
        "message": f"Created node {node_id}: '{canonical_name}' ({node_type})",
    }


@mcp.tool()
def graph_add_alias(
    node_id: int,
    alias: str,
    notes: str | None = None,
) -> dict:
    """
    Add an alternate name to an existing node.

    Args:
        node_id: Target node.
        alias: Alternate name string.
        notes: Optional notes (e.g., 'former gene symbol', 'UniProt ID').

    Returns:
        Dict with id (alias row id) and message.
    """
    alias_id = graph.add_alias(node_id, alias, notes=notes)
    return {
        "id": alias_id,
        "message": f"Added alias '{alias}' to node {node_id}",
    }


@mcp.tool()
def graph_add_edge(
    subject_id: int,
    object_id: int,
    edge_type: str,
    conditions: list[dict] | None = None,
    evidence: list[dict] | None = None,
    notes: str | None = None,
) -> dict:
    """
    Create a new edge with optional conditions and evidence.

    Each edge has a primary type (e.g., 'activates', 'inhibits', 'binds',
    'phosphorylates', 'transcribes', 'translocates', 'sequesters',
    'requires', 'part_of') plus an array of conditions that scope when
    it holds. Evidence records carry the corpus and conversation
    provenance for each observation.

    Args:
        subject_id: Node id of the subject (actor).
        object_id: Node id of the object (target).
        edge_type: Relation type.
        conditions: Optional list of dicts. Each dict must include
            'condition_type' and 'condition_value' (or shorthand 'type'
            and 'value').
        evidence: Optional list of evidence dicts. At least one is strongly
            recommended. Each dict may include grounding_type (default
            'corpus_primary'), source_filename, source_doi, source_pmid,
            chunk_id, method, cell_system, upstream_reference,
            lexicon_source, lexicon_identifier, retrieval_date,
            justification, conversation_question, notes. See
            graph_add_evidence for per-grounding_type field requirements
            (e.g., a LEXICON evidence dict requires lexicon_source,
            lexicon_identifier, retrieval_date and must not carry a
            chunk_id).
        notes: Optional notes on the edge itself.

    Returns:
        Dict with id (new edge id) and message.

    Raises:
        ValueError if an edge with the same (subject, object, edge_type)
        already exists, or if subject_id or object_id is unknown.
    """
    edge_id = graph.add_edge(
        subject_id, object_id, edge_type,
        conditions=conditions, evidence=evidence, notes=notes,
    )
    return {
        "id": edge_id,
        "message": (
            f"Created edge {edge_id}: ({subject_id}) "
            f"--{edge_type}--> ({object_id})"
        ),
    }


@mcp.tool()
def graph_add_evidence(
    edge_id: int,
    grounding_type: str = "corpus_primary",
    assertion_status: str = "asserting",
    source_filename: str | None = None,
    source_doi: str | None = None,
    source_pmid: str | None = None,
    chunk_id: int | None = None,
    method: str | None = None,
    cell_system: str | None = None,
    upstream_reference: str | None = None,
    lexicon_source: str | None = None,
    lexicon_identifier: str | None = None,
    retrieval_date: str | None = None,
    justification: str | None = None,
    conversation_question: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Add a new evidence record to an existing edge. Increments coverage by 1.

    Per V13's broadened grounding policy, `grounding_type` selects the
    provenance shape:

    'corpus_primary' (default): chunk_id required and validated against
        the AXIOM corpus DB; source_filename auto-filled from the chunk's
        parent source when not provided. The classic case.

    'corpus_inline_cited': chunk_id required (the corpus chunk that
        contains the inline citation). upstream_reference is required
        and captures how the citation appears in the chunk (e.g.,
        'Pluquet et al. 2015', '[15]', a PMID).

    'lexicon': chunk_id must NOT be provided. lexicon_source (e.g.,
        'DrugBank', 'UniProt'), lexicon_identifier (e.g., 'DB00001',
        'P04637'), and retrieval_date (YYYY-MM-DD) are required.

    'common_knowledge': chunk_id must NOT be provided. justification is
        required (brief reasoning for why this is textbook-grade,
        multi-source-confirmed biomedical fact).

    'background_weak': chunk_id must NOT be provided. justification is
        required. The row is tagged with a weakest_grounding flag in
        provenance_extra so future readers can weight it appropriately.
        Use sparingly when no stronger source is available.

    Args:
        edge_id: Target edge.
        grounding_type: One of 'corpus_primary', 'corpus_inline_cited',
            'lexicon', 'common_knowledge', 'background_weak'.
        assertion_status: 'asserting' (default) or 'refuting' (V18).
            'refuting' records that this source tested the edge's claim
            and did not find it, which is how a negative result enters
            the graph. Either method or justification must be supplied
            with a refuting row, so the reader can see what was tested.
            The edge-level rollup is derived, never stored: an edge is
            refuted when it has evidence and all of it refutes,
            contested when both kinds are present, and asserted
            otherwise. Refuted edges are excluded from the analysis
            passes by default.
        source_filename: Markdown filename in the AXIOM corpus.
            Auto-filled from chunk_id when not provided.
        source_doi: DOI of the source paper.
        source_pmid: PubMed ID.
        chunk_id: Specific chunk that supports the claim. Required for
            corpus_primary and corpus_inline_cited; forbidden otherwise.
        method: Experimental method as stated in the source (e.g.,
            'ChIP-seq', 'CRISPR KO', 'in vitro kinase assay').
        cell_system: Cell line or biological system (e.g., 'HEK293',
            'mouse liver', 'primary T cells').
        upstream_reference: For corpus_inline_cited, the inline citation
            as it appears in the chunk.
        lexicon_source: For lexicon, the source database name (e.g.,
            'DrugBank', 'UniProt', 'PubChem', 'MyGene', 'HGNC',
            'QuickGO').
        lexicon_identifier: For lexicon, the source-specific identifier
            (e.g., 'DB00001' for DrugBank, 'P04637' for UniProt).
        retrieval_date: For lexicon, the date the LEXICON return was
            retrieved (YYYY-MM-DD).
        justification: For common_knowledge or background_weak, brief
            reasoning for the claim.
        conversation_question: The user question that prompted this extraction.
        notes: Optional free-form notes.

    Returns:
        Dict with id (evidence id), edge_id, new_coverage, grounding_type,
        assertion_status, and message.
    """
    evidence_id = graph.add_evidence(
        edge_id,
        grounding_type=grounding_type,
        source_filename=source_filename,
        source_doi=source_doi,
        source_pmid=source_pmid,
        chunk_id=chunk_id,
        method=method,
        cell_system=cell_system,
        upstream_reference=upstream_reference,
        lexicon_source=lexicon_source,
        lexicon_identifier=lexicon_identifier,
        retrieval_date=retrieval_date,
        justification=justification,
        conversation_question=conversation_question,
        notes=notes,
        assertion_status=assertion_status,
    )
    coverage = graph.graph_db.count_evidence(edge_id=edge_id)
    return {
        "id": evidence_id,
        "edge_id": edge_id,
        "new_coverage": coverage,
        "grounding_type": grounding_type,
        "assertion_status": assertion_status,
        "message": (
            f"Added evidence {evidence_id} to edge {edge_id} "
            f"({grounding_type}, {assertion_status}); "
            f"coverage now {coverage}"
        ),
    }


@mcp.tool()
def graph_add_condition(
    edge_id: int,
    condition_type: str,
    condition_value: str,
    evidence_id: int | None = None,
) -> dict:
    """
    Add a precondition to an existing edge.

    Conditions describe when the edge holds (cell type, compartment,
    PTM state, cofactor, etc.). They scope the claim, separate from
    where it was observed (which lives on evidence records).

    Args:
        edge_id: Target edge.
        condition_type: Free-form (e.g., 'cell_type', 'compartment',
            'ptm_state', 'cofactor').
        condition_value: Value (e.g., 'HEK293', 'nucleus',
            'S473-phosphorylated').
        evidence_id: Optional (V18). When omitted, the condition scopes
            the whole edge, which is the usual case. When supplied, it
            scopes only that one evidence row, which is how a single
            edge carries two rows that differ solely in the test used
            (for example a log-rank null alongside a Gehan positive).
            The evidence row must belong to this edge.

    Returns:
        Dict with id, scope, and message. Duplicate condition tuples are
        silently ignored (id will be 0). Edge-scoped and evidence-scoped
        duplicates are tracked independently, so the same (type, value)
        may legitimately appear once per evidence row.
    """
    cond_id = graph.add_condition(
        edge_id, condition_type, condition_value, evidence_id=evidence_id,
    )
    scope = "edge" if evidence_id is None else f"evidence {evidence_id}"
    return {
        "id": cond_id,
        "edge_id": edge_id,
        "evidence_id": evidence_id,
        "scope": scope,
        "message": (
            f"Added condition {condition_type}={condition_value} "
            f"to edge {edge_id} (scope: {scope})"
        ),
    }


@mcp.tool()
def graph_add_observation(
    node_id: int,
    observation: str,
    grounding_type: str = "corpus_primary",
    assertion_status: str = "asserting",
    chunk_id: int | None = None,
    source_filename: str | None = None,
    source_doi: str | None = None,
    source_pmid: str | None = None,
    method: str | None = None,
    cell_system: str | None = None,
    upstream_reference: str | None = None,
    lexicon_source: str | None = None,
    lexicon_identifier: str | None = None,
    retrieval_date: str | None = None,
    justification: str | None = None,
    conversation_question: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Attach an observation to a node.

    An observation is a per-node finding that does not reduce to a
    (subject, edge_type, object) triple but is real, citation-worthy,
    and worth carrying into future conversations. Examples: 'senescent
    cells accumulate giant mitochondria and lipofuscin at an accelerated
    rate'; 'BACH2 has no approved or investigational drugs targeting it
    in DrugBank as of 2026-05-09 (null lexicon_find_drugs_by_target)'.

    The observation text must be in the user's or Claude's own words
    (not a verbatim quote from the source). One row per supporting
    source; if a different source supports a different finding, add a
    new observation rather than rewriting an existing one.

    Per V13's broadened grounding policy, `grounding_type` selects the
    provenance shape. See graph_add_evidence for the per-type field
    requirements; the same rules apply here:

    - corpus_primary (default): chunk_id required.
    - corpus_inline_cited: chunk_id + upstream_reference required.
    - lexicon: chunk_id forbidden; lexicon_source, lexicon_identifier,
      retrieval_date required.
    - common_knowledge: chunk_id forbidden; justification required.
    - background_weak: chunk_id forbidden; justification required;
      auto-tagged with weakest_grounding=True in provenance_extra.

    Args:
        node_id: Target node.
        observation: The finding text (in your own words).
        grounding_type: One of 'corpus_primary', 'corpus_inline_cited',
            'lexicon', 'common_knowledge', 'background_weak'.
        assertion_status: 'asserting' (default) or 'refuting' (V18).
            Use 'refuting' for a finding that tested a claim and did not
            support it. See graph_add_evidence for the full semantics.
        chunk_id: Specific chunk that supports the observation. Required
            for corpus_primary and corpus_inline_cited; forbidden otherwise.
        source_filename: Markdown filename in the AXIOM corpus.
            Auto-filled from chunk_id when not provided.
        source_doi: DOI of the source paper.
        source_pmid: PubMed ID.
        method: Experimental method as stated in the source.
        cell_system: Cell line or biological system.
        upstream_reference: For corpus_inline_cited, the inline citation
            as it appears in the chunk.
        lexicon_source: For lexicon, the source database name.
        lexicon_identifier: For lexicon, the source-specific identifier.
        retrieval_date: For lexicon, the date the return was retrieved
            (YYYY-MM-DD).
        justification: For common_knowledge or background_weak, brief
            reasoning for the claim.
        conversation_question: The user question that prompted the extraction.
        notes: Free-form notes (e.g., 'evidence is indirect',
            'supersedes obs #14 with greater precision').

    Returns:
        Dict with id (observation row id), node_id,
        new_observation_count, grounding_type, assertion_status, and
        message.

    Raises:
        ValueError if the node does not exist, if chunk_id is provided
        but does not resolve, or if the grounding_type's required
        fields are missing.
    """
    obs_id = graph.add_observation(
        node_id,
        observation,
        grounding_type=grounding_type,
        chunk_id=chunk_id,
        source_filename=source_filename,
        source_doi=source_doi,
        source_pmid=source_pmid,
        method=method,
        cell_system=cell_system,
        upstream_reference=upstream_reference,
        lexicon_source=lexicon_source,
        lexicon_identifier=lexicon_identifier,
        retrieval_date=retrieval_date,
        justification=justification,
        conversation_question=conversation_question,
        notes=notes,
        assertion_status=assertion_status,
    )
    new_count = graph.graph_db.count_observations(node_id=node_id)
    return {
        "id": obs_id,
        "node_id": node_id,
        "new_observation_count": new_count,
        "grounding_type": grounding_type,
        "assertion_status": assertion_status,
        "message": (
            f"Added observation {obs_id} to node {node_id} "
            f"({grounding_type}, {assertion_status}); "
            f"observation count now {new_count}"
        ),
    }


@mcp.tool()
def graph_update_node(
    node_id: int,
    canonical_name: str | None = None,
    node_type: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Update mutable fields on an existing node.

    Mutable fields are canonical_name, node_type, and notes. Pass None
    to leave a field unchanged. cross_references has its own path
    (graph_set_cross_references); aliases have graph_add_alias and
    graph_delete_alias. Bumps updated_at.

    Use sparingly. Renaming or recategorizing a node propagates to
    every edge and observation that references it; consider whether
    a delete-and-recreate makes the audit trail cleaner.

    Args:
        node_id: Target node id.
        canonical_name: New canonical name. None = leave unchanged.
        node_type: New node_type. None = leave unchanged.
        notes: New notes text. None = leave unchanged.

    Returns:
        Dict with id and message.

    Raises:
        ValueError if the node does not exist, or if the proposed
        (canonical_name, node_type) collides with another existing node.
    """
    graph.update_node(
        node_id,
        canonical_name=canonical_name,
        node_type=node_type,
        notes=notes,
    )
    return {
        "id": node_id,
        "message": f"Updated node {node_id}",
    }


@mcp.tool()
def graph_update_edge(
    edge_id: int,
    subject_id: int | None = None,
    object_id: int | None = None,
    edge_type: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    Update mutable fields on an existing edge.

    Mutable fields are subject_id, object_id, edge_type, and notes.
    Pass None to leave a field unchanged. Conditions and evidence keep
    their own add/delete paths. Bumps updated_at.

    Use case: correcting an edge whose subject was misattributed (e.g.,
    a marker cited as the driver). The existing evidence rows stay
    attached to the same edge id, so the audit trail and coverage are
    preserved across the rewrite.

    Args:
        edge_id: Target edge id.
        subject_id: New subject node id. None = leave unchanged.
        object_id: New object node id. None = leave unchanged.
        edge_type: New edge_type. None = leave unchanged.
        notes: New notes text. None = leave unchanged.

    Returns:
        Dict with id and message.

    Raises:
        ValueError if the edge does not exist; if a referenced new
        subject or object node does not exist; or if the proposed
        (subject_id, object_id, edge_type) collides with another
        existing edge.
    """
    graph.update_edge(
        edge_id,
        subject_id=subject_id,
        object_id=object_id,
        edge_type=edge_type,
        notes=notes,
    )
    return {
        "id": edge_id,
        "message": f"Updated edge {edge_id}",
    }


@mcp.tool()
def graph_update_observation(
    observation_id: int,
    observation: str | None = None,
    notes: str | None = None,
) -> dict:
    """
    In-place edit of an existing observation's text and/or notes.

    Use this when proposing a rewrite that supersedes an older entry
    with greater precision (the new text refines what the same chunk
    actually supports), or when adding a meta-remark in the notes
    field. Bumps updated_at.

    Provenance fields (chunk_id, source_*, conversation_*) are
    immutable; if a different chunk supports a different claim, use
    graph_add_observation instead.

    Args:
        observation_id: Target observation row id.
        observation: New observation text. Pass None to leave unchanged.
        notes: New notes text. Pass None to leave unchanged.

    Returns:
        Dict with id and message.

    Raises:
        ValueError if the observation does not exist.
    """
    graph.update_observation(
        observation_id, observation=observation, notes=notes,
    )
    return {
        "id": observation_id,
        "message": f"Updated observation {observation_id}",
    }


@mcp.tool()
def graph_set_cross_references(
    node_id: int,
    cross_references: dict | None,
) -> dict:
    """
    Set or replace the cross_references field on an existing node.

    Used when LEXICON enrichment surfaces canonical IDs for a node that
    was originally created without them, or when a previous set of IDs
    needs to be updated. Pass cross_references=None to clear the field.

    Args:
        node_id: Target node.
        cross_references: Dict of canonical IDs (e.g.
            {"ncbi_gene_id": "6662", "uniprot": "P48436",
             "hgnc": "HGNC:11204"}), or None to clear.

    Returns:
        Dict with id and message.

    Raises:
        ValueError if the node does not exist.
    """
    graph.set_cross_references(node_id, cross_references)
    if cross_references is None:
        msg = f"Cleared cross_references on node {node_id}"
    else:
        keys = ", ".join(sorted(cross_references.keys()))
        msg = f"Set cross_references on node {node_id} ({keys})"
    return {"id": node_id, "message": msg}


# -----------------------------------------------------------------------------
# Graph: bulk proposal
# -----------------------------------------------------------------------------

@mcp.tool()
def graph_apply_proposal(proposal: dict) -> dict:
    """
    Apply a multi-section graph proposal in a single SQLite transaction.

    Use this to commit many graph edits in one round trip instead of one
    tool call per node/edge/observation. The companion of the existing
    fine-grained graph_add_* / graph_update_* tools: same validation, same
    friendly errors, but atomic over an arbitrary batch.

    SECTIONS (all optional):

        new_nodes               creates nodes (aliases + cross_references inline)
        new_edges               creates edges (conditions + evidence inline)
        new_observations        attaches observations to nodes
        alias_additions         adds aliases to existing nodes
        evidence_additions      adds evidence to existing edges
        condition_additions     adds conditions to existing edges
        observation_rewrites    in-place edits of observation text/notes
        cross_reference_updates set/replace cross_references on nodes
        node_updates            update canonical_name/node_type/notes
        edge_updates            update subject/object/edge_type/notes

    NODE REFERENCES (in new_edges and new_observations):

        int                              -> existing node id
        {"id": int}                       -> same, dict form
        {"name": str, "node_type": str}   -> resolves in-payload new_nodes
                                             first, then by (canonical_name,
                                             node_type) in the DB. This is
                                             how forward refs to nodes being
                                             created in the same payload work.

    IDEMPOTENCY:

        A new_node whose (canonical_name, node_type) already exists in the DB
        is matched, not duplicated. Existing aliases are preserved; new
        aliases from the payload are appended. cross_references are merged:
        new keys added, existing keys kept. Observations and edges anchored
        to the matched node still apply.

        A new_edge whose (subject, object, edge_type) already exists is
        matched. Conditions from the payload are appended (duplicates
        silently ignored). Evidence rows are always appended (the normal
        way to grow coverage on an existing edge).

        Items in the report carry result='created' or result='matched'.

    PER-SECTION SCHEMAS:

        new_nodes[i] = {
            "canonical_name": str (required),
            "node_type": str (required),
            "notes": str | None,
            "aliases": [str, ...] | None,
            "cross_references": {key: value, ...} | None,
        }

        new_edges[i] = {
            "subject": <node ref>,
            "object": <node ref>,
            "edge_type": str (required),
            "conditions": [{"condition_type", "condition_value",
                            "evidence_index": int | None}, ...] | None,
            "evidence": [{<grounding fields>}, ...] | None,
            "notes": str | None,
        }

        new_observations[i] = {
            "node": <node ref>,
            "observation": str (required),
            <grounding fields per graph_add_observation>,
        }

        alias_additions[i] = {
            "node_id": int, "alias": str, "notes": str | None,
        }

        evidence_additions[i] = {
            "edge_id": int,
            <grounding fields per graph_add_evidence>,
        }

        condition_additions[i] = {
            "edge_id": int,
            "condition_type": str, "condition_value": str,
            "evidence_id": int | None,
            "evidence_addition_index": int | None,
        }

        observation_rewrites[i] = {
            "observation_id": int,
            "observation": str | None, "notes": str | None,
        }

        cross_reference_updates[i] = {
            "node_id": int,
            "cross_references": {key: value, ...} | None,
        }

        node_updates[i] = {
            "node_id": int,
            "canonical_name": str | None,
            "node_type": str | None,
            "notes": str | None,
        }

        edge_updates[i] = {
            "edge_id": int,
            "subject_id": int | None,
            "object_id": int | None,
            "edge_type": str | None,
            "notes": str | None,
        }

    GROUNDING FIELDS (for evidence and observations) follow the same rules
    as graph_add_evidence and graph_add_observation. grounding_type selects
    the shape:
        corpus_primary      -> chunk_id required
        corpus_inline_cited -> chunk_id + upstream_reference required
        lexicon             -> lexicon_source, lexicon_identifier,
                               retrieval_date required; chunk_id forbidden
        common_knowledge    -> justification required; chunk_id forbidden
        background_weak     -> justification required; chunk_id forbidden;
                               auto-tagged weakest_grounding=True

    Every evidence and observation dict also accepts assertion_status,
    'asserting' (default) or 'refuting' (V18). A refuting row requires
    method or justification. See graph_add_evidence.

    CONDITION SCOPING (V18). A condition scopes the whole edge when no
    evidence is named, which is the usual case. To scope one evidence
    row instead:
        in new_edges, set "evidence_index" on the inline condition to a
            position in that same edge's "evidence" list;
        in condition_additions, set "evidence_id" to an existing row on
            that edge, or "evidence_addition_index" to a position in
            this payload's evidence_additions. Pass at most one.
    Either way the referenced evidence must belong to the same edge.

    RETURNS:

    On successful validation and commit:
        {
            "status": "committed",
            "stats": {<per-section counts>},
            "items": {<per-section detail with ids and created/matched>},
            "rollback_additions": [<delete-tool calls to undo additions,
                                    sorted to minimize cascade noise>],
            "previous_values_for_in_place_edits": [<pre-edit values for
                rewrites and cross_reference/node/edge updates; the user
                can manually restore via the existing update tools>],
            "graph_stats": {<post-commit stats>},
        }

    On validation failure (all errors reported, no writes):
        {
            "status": "rejected",
            "phase": "validation",
            "errors": [{"section": str, "index": int, "error": str}, ...],
        }

    On write or commit failure (atomic rollback, no partial state):
        {
            "status": "rejected",
            "phase": "write" | "commit",
            "error": str,
        }

    Args:
        proposal: The payload dict. See section schemas above.

    Returns:
        The status report dict.
    """
    return graph.apply_proposal(proposal)


# -----------------------------------------------------------------------------
# Graph: maintenance and stats
# -----------------------------------------------------------------------------

@mcp.tool()
def graph_delete_node(node_id: int) -> dict:
    """
    Delete a node. Cascades to all edges incident to it (and their
    conditions and evidence).

    Args:
        node_id: Target node.

    Returns:
        Dict with rows_deleted count and a message.
    """
    n = graph.delete_node(node_id)
    return {
        "rows_deleted": n,
        "message": f"Deleted node {node_id}" if n else f"Node {node_id} not found",
    }


@mcp.tool()
def graph_delete_edge(edge_id: int) -> dict:
    """
    Delete an edge. Cascades to its conditions and evidence.

    Args:
        edge_id: Target edge.

    Returns:
        Dict with rows_deleted count and a message.
    """
    n = graph.delete_edge(edge_id)
    return {
        "rows_deleted": n,
        "message": f"Deleted edge {edge_id}" if n else f"Edge {edge_id} not found",
    }


@mcp.tool()
def graph_delete_evidence(evidence_id: int) -> dict:
    """
    Delete a single evidence row from an edge.

    Decrements the edge's coverage by 1. The edge itself is preserved.
    Use to prune low-value or duplicate evidence; for full edge removal
    use graph_delete_edge.

    Args:
        evidence_id: Target evidence row id.

    Returns:
        Dict with rows_deleted count and a message.
    """
    n = graph.delete_evidence(evidence_id)
    return {
        "rows_deleted": n,
        "message": (
            f"Deleted evidence {evidence_id}"
            if n
            else f"Evidence {evidence_id} not found"
        ),
    }


@mcp.tool()
def graph_delete_condition(condition_id: int) -> dict:
    """
    Delete a single condition row from an edge.

    The edge itself is preserved. Use to prune over-narrow or incorrect
    preconditions; for full edge removal use graph_delete_edge.

    Args:
        condition_id: Target condition row id.

    Returns:
        Dict with rows_deleted count and a message.
    """
    n = graph.delete_condition(condition_id)
    return {
        "rows_deleted": n,
        "message": (
            f"Deleted condition {condition_id}"
            if n
            else f"Condition {condition_id} not found"
        ),
    }


@mcp.tool()
def graph_delete_observation(observation_id: int) -> dict:
    """
    Delete a single observation row from a node.

    The node itself and its other observations are preserved. Use to
    prune descriptive, methodological, or otherwise low-value
    observations; for full node removal use graph_delete_node.

    Args:
        observation_id: Target observation row id.

    Returns:
        Dict with rows_deleted count and a message.
    """
    n = graph.delete_observation(observation_id)
    return {
        "rows_deleted": n,
        "message": (
            f"Deleted observation {observation_id}"
            if n
            else f"Observation {observation_id} not found"
        ),
    }


@mcp.tool()
def graph_delete_alias(alias_id: int) -> dict:
    """
    Delete a single alias row from a node.

    The node itself and its other aliases are preserved. Use to prune
    misleading or out-of-date alternate names; for full node removal
    use graph_delete_node.

    Args:
        alias_id: Target alias row id.

    Returns:
        Dict with rows_deleted count and a message.
    """
    n = graph.delete_alias(alias_id)
    return {
        "rows_deleted": n,
        "message": (
            f"Deleted alias {alias_id}"
            if n
            else f"Alias {alias_id} not found"
        ),
    }


@mcp.tool()
def graph_stats() -> dict:
    """
    Return counts and histograms for the mechanistic knowledge graph.

    Returns:
        Dict with: nodes (count), edges, conditions, evidence,
        observations (counts), node_types (histogram by node_type),
        edge_types (histogram by edge_type), top_sources (most-cited
        corpus sources by edge_evidence count, up to 10),
        top_observed_nodes (nodes with the most attached observations,
        up to 10), species_histogram (gene/protein nodes by derived
        species: human/mouse/rat/worm/unknown).
    """
    return graph.get_stats()


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
