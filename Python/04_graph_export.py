#!/usr/bin/env python3
"""
AXIOM Project - Graph Export

Exports the mechanistic knowledge graph from axiom_graph.db into formats
suitable for visualization and sharing. Joins evidence records against
the AXIOM corpus database (axiom.db) so each exported edge carries a
self-contained APA citation; chunk text is intentionally not embedded
(just citation + DOI/PubMed pointer).

Formats:
    cytoscape-js  cytoscape.js JSON for in-browser visualization. Full
                  fidelity: conditions and evidence stay as nested arrays.
    graphml       GraphML XML for desktop import (Cytoscape, Gephi,
                  yEd) or NetworkX. Conditions and evidence are flattened.
    tsv           A nodes.tsv / edges.tsv pair for spreadsheets.
    all           Emit all three into the same output directory.

Usage:
    # In-browser visualization, with companion viewer.html:
    python 04_graph_export.py --format cytoscape-js --output-dir export/

    # Desktop GraphML for Cytoscape Desktop:
    python 04_graph_export.py --format graphml --output graph.graphml

    # Spreadsheet-friendly tables:
    python 04_graph_export.py --format tsv --output-dir export-tsv/

    # Everything at once:
    python 04_graph_export.py --format all --output-dir export/

    # Filtered: only well-supported edges from 2020 or later:
    python 04_graph_export.py --format cytoscape-js \\
        --output-dir export/ --min-coverage 2 --year-min 2020

Filters:
    --node-type TYPE        keep nodes of this type (repeatable)
    --edge-type TYPE        keep edges of this type (repeatable)
    --min-coverage N        keep edges with coverage >= N
    --year-min YEAR         keep edges with at least one evidence record
                            from this year or later
    --year-max YEAR         keep edges with at least one evidence record
                            from this year or earlier

Privacy:
    The conversation_question and conversation_date stamps that the MCP
    server records on every evidence row and observation row are
    SCRUBBED from the export by default. Pass
    --include-conversation-metadata to retain them (e.g. for internal
    audit dumps that won't be published).

License / redaction:
    DrugBank-derived content (the `drugbank` / `drugbank_target`
    cross-reference keys, LEXICON DrugBank citations on evidence and
    observation rows, and any notes/observation text carrying the
    curator-applied `[Source: DrugBank]` suffix, a DB##### accession, a
    BE####### target id, or a `[drugbank ...]` provenance prefix) is left
    in place by default. Pass --redact-drugbank to strip
    it from the export for license-compliant public artifacts; the
    curated graph DB is never modified. A drugbank_redaction_report.txt
    is written alongside the output listing every redacted field (with
    its original text) plus any field that merely mentions DrugBank
    without an identifier, so a manual pass can reinstate DrugBank-free
    curation where wanted.
"""

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# Make lib/ importable
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from axiom_db import AxiomDatabase
from axiom_graph_db import AxiomGraphDatabase
from species import derive_species

GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
CDN_CYTOSCAPE_VERSION = "3.30.0"
TEMPLATE_DIR = SCRIPT_DIR / "templates"
VIEWER_TEMPLATE = TEMPLATE_DIR / "viewer.html"

REDACTION_MARKER = "[redacted: DrugBank]"
# The literal suffix curators append to any field whose text reproduces
# DrugBank-restricted content (per SOP_paper_extraction "DrugBank attribution
# suffix"). This is the primary redaction trigger; the accession-id /
# `[drugbank ...]` pattern below is the backstop for untagged rows.
DRUGBANK_ATTRIBUTION_SUFFIX = "[Source: DrugBank]"
# A free-text field carries DrugBank content if it has a DrugBank accession
# (DB + 5 digits), a DrugBank target id (BE + 7 digits), or a LEXICON
# `[drugbank ...]` provenance prefix. Plain mentions ("not in DrugBank",
# "undrugged") carry no identifier and are not auto-redacted.
_DRUGBANK_CONTENT_RE = re.compile(r"\[drugbank\b|\bDB\d{5}\b|\bBE\d{7}\b", re.IGNORECASE)
_DRUGBANK_MENTION_RE = re.compile(r"drugbank", re.IGNORECASE)


# -----------------------------------------------------------------------------
# DrugBank redaction (license-compliant public export)
# -----------------------------------------------------------------------------

def _is_drugbank_lexicon(row):
    """True if an enriched evidence/observation row is grounded in a DrugBank
    LEXICON lookup (so its citation/provenance carry a DrugBank identifier)."""
    if (row.get("grounding_type") or "") != "lexicon":
        return False
    extra = row.get("provenance_extra") or {}
    source = (extra.get("lexicon_source") or "").lower()
    identifier = extra.get("lexicon_identifier") or ""
    return "drugbank" in source or bool(re.match(r"^(DB\d{5}|BE\d{7})$", identifier))


def _scrub_lexicon_row(row):
    """Blank the DrugBank citation and drop DrugBank identifiers from the
    provenance of a lexicon-grounded evidence/observation row, in place."""
    row["citation"] = REDACTION_MARKER
    extra = row.get("provenance_extra")
    if isinstance(extra, dict):
        for k in ("lexicon_source", "lexicon_identifier"):
            extra.pop(k, None)


def _has_drugbank_content(text):
    if not text:
        return False
    if DRUGBANK_ATTRIBUTION_SUFFIX in text:
        return True
    return bool(_DRUGBANK_CONTENT_RE.search(text))


def redact_drugbank(nodes, edges):
    """Strip DrugBank-derived content from the in-memory graph before export.

    Two surfaces:
      Surface A (deterministic): the `drugbank` / `drugbank_target` keys in
      cross_references, and the DrugBank citation + provenance on any
      LEXICON-grounded evidence/observation row.
      Surface B (free text): node/edge `notes` and observation text that carry
      a DrugBank accession (DB#####), a DrugBank target id (BE#######), or a
      `[drugbank ...]` LEXICON prefix are replaced wholesale with a marker,
      because DrugBank prose is interleaved with curated analysis and has no
      reliable in-text boundary.

    The curated graph DB is never touched; only these in-memory copies are.
    Returns a report dict with `redacted` (fields stripped, original text kept)
    and `review` (fields that mention DrugBank but carry no identifier, kept
    as-is) so a manual pass can reinstate DrugBank-free curation if wanted.
    """
    report = {"redacted": [], "review": []}

    def handle_text(kind, ident, name, field, text):
        if _has_drugbank_content(text):
            report["redacted"].append({
                "kind": kind, "id": ident, "name": name,
                "field": field, "original": text,
            })
            return REDACTION_MARKER
        if text and _DRUGBANK_MENTION_RE.search(text):
            report["review"].append({
                "kind": kind, "id": ident, "name": name,
                "field": field, "original": text,
            })
        return text

    for n in nodes:
        cr = n.get("cross_references")
        if isinstance(cr, dict):
            for k in list(cr.keys()):
                if k.lower().startswith("drugbank"):
                    cr.pop(k, None)
        aliases = n.get("aliases")
        if isinstance(aliases, list):
            kept_aliases = []
            for alias in aliases:
                if isinstance(alias, str) and re.match(r"^(DB\d{5}|BE\d{7})$", alias):
                    report["redacted"].append({
                        "kind": "node_alias", "id": n["id"],
                        "name": n["canonical_name"], "field": "alias",
                        "original": alias,
                    })
                else:
                    kept_aliases.append(alias)
            n["aliases"] = kept_aliases
        n["notes"] = handle_text(
            "node", n["id"], n["canonical_name"], "notes", n.get("notes")
        )
        for obs in n.get("observations", []):
            if _is_drugbank_lexicon(obs):
                _scrub_lexicon_row(obs)
            obs["observation"] = handle_text(
                "node_observation", n["id"], n["canonical_name"],
                f"observation:{obs.get('id', '')}", obs.get("observation"),
            )
            obs["notes"] = handle_text(
                "node_observation", n["id"], n["canonical_name"],
                f"observation_notes:{obs.get('id', '')}", obs.get("notes"),
            )

    for e in edges:
        e["notes"] = handle_text(
            "edge", e["id"], e["edge_type"], "notes", e.get("notes")
        )
        for ev in e.get("evidence", []):
            if _is_drugbank_lexicon(ev):
                _scrub_lexicon_row(ev)
            ev["notes"] = handle_text(
                "edge_evidence", e["id"], e["edge_type"],
                f"evidence_notes:{ev.get('id', '')}", ev.get("notes"),
            )

    return report


def write_redaction_report(report, path):
    """Write a human-readable DrugBank redaction report next to the export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "AXIOM DrugBank redaction report",
        f"generated: {datetime.now().isoformat()}",
        "",
        f"{len(report['redacted'])} field(s) redacted: DrugBank content removed "
        "from the export. Originals remain in axiom_graph.db.",
        f"{len(report['review'])} field(s) kept: mention DrugBank but carry no "
        "identifier or provenance prefix (likely absence/negative findings). "
        "Review if you want them gone too.",
    ]

    def _block(title, items):
        out = ["", "=" * 78, title, "=" * 78]
        if not items:
            out.append("")
            out.append("(none)")
        for item in items:
            out.append("")
            out.append(
                f"[{item['kind']}] id={item['id']} "
                f"name={item['name']} field={item['field']}"
            )
            out.append("-" * 78)
            out.append(item["original"] or "")
        return out

    lines += _block("REDACTED (replaced with marker in the export)", report["redacted"])
    lines += _block(
        "REVIEW (kept as-is; mentions DrugBank without an identifier)",
        report["review"],
    )
    path.write_text("\n".join(lines), encoding="utf-8")


# -----------------------------------------------------------------------------
# Data fetch
# -----------------------------------------------------------------------------



def _format_citation(d, corpus_db):
    """
    Build the `citation` field on an evidence/observation row dict
    based on its grounding_type and (deserialized) provenance_extra.
    Mutates the dict in place. Mirrors the V13 logic in mcp/graph.py's
    GraphAccessor._format_citation, kept duplicated so this script
    depends only on lib/ and not on mcp/.

    For corpus_primary / corpus_inline_cited: look up the source via
    chunk_id (preferred) or source_filename (fallback). Set citation,
    title, year, doi, pmid when the source resolves. corpus_inline_cited
    appends the upstream_reference. When neither chunk_id nor
    source_filename resolves, those fields are left unset.

    For lexicon: build '<source> <identifier> (retrieved <date>)' from
    provenance_extra.

    For common_knowledge / background_weak: build a citation from the
    justification field. background_weak prefixes [weakest grounding].

    Title, year, doi, pmid are only populated for corpus types.
    """
    grounding_type = d.get("grounding_type") or "corpus_primary"
    extra = d.get("provenance_extra") or {}

    if grounding_type in ("corpus_primary", "corpus_inline_cited"):
        source = None
        if d.get("chunk_id"):
            chunk = corpus_db.get_chunk(d["chunk_id"])
            if chunk is not None:
                source = corpus_db.get_reference(chunk["source_id"])
        if source is None and d.get("source_filename"):
            source = corpus_db.get_reference_by_filename(d["source_filename"])
        if source is not None:
            d["title"] = source["title"]
            d["year"] = source["year"]
            d["doi"] = d.get("doi") or source["doi"]
            d["pmid"] = d.get("pmid") or source["pmid"]
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
        d["citation"] = (
            f"Claude background (common knowledge): {justification}"
            if justification else "Claude background (common knowledge)"
        )

    elif grounding_type == "background_weak":
        justification = (extra.get("justification") or "").strip()
        d["citation"] = (
            f"[weakest grounding] Claude background: {justification}"
            if justification else "[weakest grounding] Claude background"
        )

    return d


def _enrich_evidence(ev_row, corpus_db):
    """Enrich an evidence row with V13-aware citation and per-type fields."""
    ev = dict(ev_row)
    if "provenance_extra" in ev:
        ev["provenance_extra"] = _deserialize_json_field(ev["provenance_extra"])
    return _format_citation(ev, corpus_db)


def _enrich_observation(obs_row, corpus_db):
    """Enrich an observation row with V13-aware citation and per-type fields."""
    obs = dict(obs_row)
    if "provenance_extra" in obs:
        obs["provenance_extra"] = _deserialize_json_field(obs["provenance_extra"])
    return _format_citation(obs, corpus_db)


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


def _evidence_year(ev):
    """Return the year of an enriched evidence dict, or None."""
    y = ev.get("year")
    if y is None:
        return None
    try:
        return int(y)
    except (ValueError, TypeError):
        return None


def fetch_graph(graph_db, corpus_db, filters):
    """Read all graph data, joining citations and applying filters."""
    node_type_filter = filters.get("node_types")
    edge_type_filter = filters.get("edge_types")
    min_coverage = filters.get("min_coverage")
    year_min = filters.get("year_min")
    year_max = filters.get("year_max")
    include_conv = filters.get("include_conversation_metadata", False)

    # --- Nodes ---
    raw_nodes = graph_db.get_all_nodes()
    nodes = []
    for n in raw_nodes:
        if node_type_filter and n["node_type"] not in node_type_filter:
            continue
        aliases = [a["alias"] for a in graph_db.get_aliases(n["id"])]
        incident_edges = graph_db.get_edges_for_node(n["id"])
        total_evidence = sum(
            graph_db.count_evidence(edge_id=e["id"]) for e in incident_edges
        )
        cross_refs = _deserialize_json_field(
            n["cross_references"] if "cross_references" in n.keys() else None
        )
        observation_rows = graph_db.get_observations_for_node(n["id"])
        observations = [_enrich_observation(o, corpus_db) for o in observation_rows]
        if not include_conv:
            for obs in observations:
                obs.pop("conversation_question", None)
                obs.pop("conversation_date", None)
        nodes.append({
            "id": n["id"],
            "canonical_name": n["canonical_name"],
            "node_type": n["node_type"],
            "aliases": aliases,
            "notes": n["notes"],
            "cross_references": cross_refs,
            "species": derive_species(n["node_type"], cross_refs),
            "total_evidence": total_evidence,
            "observation_count": len(observations),
            "observations": observations,
        })

    node_id_set = {n["id"] for n in nodes}

    # --- Edges ---
    cursor = graph_db.connection.execute("SELECT * FROM edges ORDER BY id")
    raw_edges = cursor.fetchall()
    edges = []
    for e in raw_edges:
        if edge_type_filter and e["edge_type"] not in edge_type_filter:
            continue
        # Both endpoints must survive node filtering, otherwise the edge dangles.
        if e["subject_id"] not in node_id_set or e["object_id"] not in node_id_set:
            continue

        evidence_rows = graph_db.get_evidence_for_edge(e["id"])
        evidence = [_enrich_evidence(ev, corpus_db) for ev in evidence_rows]

        if min_coverage is not None and len(evidence) < min_coverage:
            continue

        years = [y for y in (_evidence_year(ev) for ev in evidence) if y is not None]
        if year_min is not None and (not years or max(years) < year_min):
            continue
        if year_max is not None and (not years or min(years) > year_max):
            continue

        if not include_conv:
            for ev in evidence:
                ev.pop("conversation_question", None)
                ev.pop("conversation_date", None)

        conditions = [
            {
                "condition_type": c["condition_type"],
                "condition_value": c["condition_value"],
            }
            for c in graph_db.get_conditions(e["id"])
        ]

        edges.append({
            "id": e["id"],
            "source": e["subject_id"],
            "target": e["object_id"],
            "edge_type": e["edge_type"],
            "notes": e["notes"],
            "coverage": len(evidence),
            "conditions": conditions,
            "evidence": evidence,
        })

    return nodes, edges


# -----------------------------------------------------------------------------
# Cytoscape.js JSON
# -----------------------------------------------------------------------------

def export_cytoscape_js(nodes, edges, output_path, filters_summary):
    """Emit a single JSON file consumable by cytoscape.js."""
    payload = {
        "metadata": {
            "exported_at": datetime.now().isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "filters": filters_summary,
        },
        "elements": {
            "nodes": [
                {"data": {**n, "id": f"n{n['id']}"}}
                for n in nodes
            ],
            "edges": [
                {
                    "data": {
                        **e,
                        "id": f"e{e['id']}",
                        "source": f"n{e['source']}",
                        "target": f"n{e['target']}",
                    }
                }
                for e in edges
            ],
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


# -----------------------------------------------------------------------------
# GraphML
# -----------------------------------------------------------------------------

def _add_data(parent, key, text):
    d = ET.SubElement(parent, f"{{{GRAPHML_NS}}}data")
    d.set("key", key)
    d.text = "" if text is None else str(text)


def export_graphml(nodes, edges, output_path):
    """Emit a GraphML file. Conditions and evidence are flattened."""
    ET.register_namespace("", GRAPHML_NS)
    root = ET.Element(f"{{{GRAPHML_NS}}}graphml")

    keys = [
        ("node", "canonical_name", "string"),
        ("node", "node_type", "string"),
        ("node", "aliases", "string"),
        ("node", "notes", "string"),
        ("node", "cross_references", "string"),
        ("node", "total_evidence", "int"),
        ("node", "observation_count", "int"),
        ("node", "observation_excerpt", "string"),
        ("edge", "edge_type", "string"),
        ("edge", "coverage", "int"),
        ("edge", "conditions", "string"),
        ("edge", "top_citation", "string"),
        ("edge", "top_year", "int"),
        ("edge", "top_grounding_type", "string"),
        ("edge", "evidence_count", "int"),
        ("edge", "notes", "string"),
    ]
    for target, name, attr_type in keys:
        k = ET.SubElement(root, f"{{{GRAPHML_NS}}}key")
        k.set("id", f"{target}_{name}")
        k.set("for", target)
        k.set("attr.name", name)
        k.set("attr.type", attr_type)

    graph = ET.SubElement(root, f"{{{GRAPHML_NS}}}graph")
    graph.set("edgedefault", "directed")

    for n in nodes:
        node = ET.SubElement(graph, f"{{{GRAPHML_NS}}}node")
        node.set("id", f"n{n['id']}")
        _add_data(node, "node_canonical_name", n["canonical_name"])
        _add_data(node, "node_node_type", n["node_type"])
        _add_data(node, "node_aliases", "|".join(n["aliases"]) if n["aliases"] else "")
        _add_data(node, "node_notes", n["notes"])
        _add_data(
            node,
            "node_cross_references",
            json.dumps(n["cross_references"]) if n.get("cross_references") else "",
        )
        _add_data(node, "node_total_evidence", n["total_evidence"])
        _add_data(node, "node_observation_count", n.get("observation_count", 0))
        # Top observation excerpts (first 3, pipe-joined). The full text
        # is preserved in the cytoscape-js JSON export.
        excerpt_obs = (n.get("observations") or [])[:3]
        excerpts = [
            (o.get("observation") or "").replace("|", "/").replace("\n", " ").strip()
            for o in excerpt_obs
        ]
        _add_data(node, "node_observation_excerpt", " | ".join(excerpts))

    for e in edges:
        edge = ET.SubElement(graph, f"{{{GRAPHML_NS}}}edge")
        edge.set("id", f"e{e['id']}")
        edge.set("source", f"n{e['source']}")
        edge.set("target", f"n{e['target']}")
        _add_data(edge, "edge_edge_type", e["edge_type"])
        _add_data(edge, "edge_coverage", e["coverage"])
        _add_data(
            edge,
            "edge_conditions",
            " | ".join(
                f"{c['condition_type']}={c['condition_value']}"
                for c in e["conditions"]
            ),
        )
        top = e["evidence"][0] if e["evidence"] else {}
        _add_data(edge, "edge_top_citation", top.get("citation"))
        _add_data(edge, "edge_top_year", top.get("year"))
        _add_data(edge, "edge_top_grounding_type", top.get("grounding_type"))
        _add_data(edge, "edge_evidence_count", len(e["evidence"]))
        _add_data(edge, "edge_notes", e["notes"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, xml_declaration=True, encoding="utf-8")


# -----------------------------------------------------------------------------
# TSV
# -----------------------------------------------------------------------------

def export_tsv(nodes, edges, output_dir):
    """Emit nodes.tsv + edges.tsv + observations.tsv into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = output_dir / "nodes.tsv"
    with open(nodes_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(
            ["id", "canonical_name", "node_type", "aliases",
             "cross_references", "total_evidence", "observation_count",
             "notes"]
        )
        for n in nodes:
            writer.writerow([
                n["id"],
                n["canonical_name"],
                n["node_type"],
                "|".join(n["aliases"]) if n["aliases"] else "",
                json.dumps(n["cross_references"]) if n.get("cross_references") else "",
                n["total_evidence"],
                n.get("observation_count", 0),
                n["notes"] or "",
            ])

    edges_path = output_dir / "edges.tsv"
    with open(edges_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow([
            "id", "source_id", "target_id", "edge_type", "coverage",
            "conditions", "evidence_count", "top_citation", "top_year",
            "notes", "top_grounding_type",
        ])
        for e in edges:
            top = e["evidence"][0] if e["evidence"] else {}
            writer.writerow([
                e["id"],
                e["source"],
                e["target"],
                e["edge_type"],
                e["coverage"],
                " | ".join(
                    f"{c['condition_type']}={c['condition_value']}"
                    for c in e["conditions"]
                ),
                len(e["evidence"]),
                top.get("citation", ""),
                top.get("year", ""),
                e["notes"] or "",
                top.get("grounding_type", "") or "",
            ])

    observations_path = output_dir / "observations.tsv"
    with open(observations_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow([
            "id", "node_id", "node_canonical_name", "node_type",
            "observation", "chunk_id", "source_filename",
            "citation", "year", "doi", "pmid",
            "method", "cell_system", "notes",
            "grounding_type", "provenance_extra",
        ])
        for n in nodes:
            for obs in n.get("observations", []):
                provenance = obs.get("provenance_extra")
                provenance_text = (
                    json.dumps(provenance) if provenance else ""
                )
                writer.writerow([
                    obs.get("id", ""),
                    n["id"],
                    n["canonical_name"],
                    n["node_type"],
                    (obs.get("observation") or "").replace("\n", " ").strip(),
                    obs.get("chunk_id", "") or "",
                    obs.get("source_filename", "") or "",
                    obs.get("citation", "") or "",
                    obs.get("year", "") or "",
                    obs.get("doi", "") or "",
                    obs.get("pmid", "") or "",
                    obs.get("method", "") or "",
                    obs.get("cell_system", "") or "",
                    obs.get("notes", "") or "",
                    obs.get("grounding_type", "") or "",
                    provenance_text,
                ])

    return nodes_path, edges_path, observations_path


# -----------------------------------------------------------------------------
# Viewer
# -----------------------------------------------------------------------------

def copy_viewer(output_dir):
    """Copy the companion viewer.html into output_dir, next to graph.json."""
    if not VIEWER_TEMPLATE.exists():
        print(
            f"warning: viewer template not found at {VIEWER_TEMPLATE}; skipping",
            file=sys.stderr,
        )
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "viewer.html"
    shutil.copy(VIEWER_TEMPLATE, target)
    return target


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Export the AXIOM mechanistic knowledge graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format",
        choices=["cytoscape-js", "graphml", "tsv", "all"],
        required=True,
        help="Output format.",
    )
    parser.add_argument(
        "--output", type=Path,
        help="Output file path (for cytoscape-js or graphml without --output-dir).",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="Output directory (required for tsv and all; preferred for "
             "cytoscape-js when including the viewer).",
    )
    parser.add_argument(
        "--no-viewer", action="store_true",
        help="Suppress the companion viewer.html (default: included with "
             "cytoscape-js and all).",
    )
    parser.add_argument(
        "--include-conversation-metadata", action="store_true",
        help="Retain conversation_question and conversation_date on evidence "
             "rows. Default: scrubbed from the export.",
    )
    parser.add_argument(
        "--redact-drugbank", action="store_true",
        help="Strip DrugBank-derived content (cross-reference keys, LEXICON "
             "DrugBank citations, and notes/observation text carrying a "
             "DB##### / BE####### id or a [drugbank ...] prefix) from the "
             "export, and write drugbank_redaction_report.txt alongside it. "
             "The graph DB is not modified.",
    )
    parser.add_argument(
        "--node-type", action="append", default=None,
        help="Restrict to one or more node types. Repeatable.",
    )
    parser.add_argument(
        "--edge-type", action="append", default=None,
        help="Restrict to one or more edge types. Repeatable.",
    )
    parser.add_argument("--min-coverage", type=int, default=None)
    parser.add_argument("--year-min", type=int, default=None)
    parser.add_argument("--year-max", type=int, default=None)
    return parser.parse_args()


def main():
    args = parse_args()

    filters = {
        "node_types": set(args.node_type) if args.node_type else None,
        "edge_types": set(args.edge_type) if args.edge_type else None,
        "min_coverage": args.min_coverage,
        "year_min": args.year_min,
        "year_max": args.year_max,
        "include_conversation_metadata": args.include_conversation_metadata,
        "redact_drugbank": args.redact_drugbank,
    }
    filters_summary = {k: (sorted(v) if isinstance(v, set) else v)
                       for k, v in filters.items() if v not in (None, False)}

    graph_db = AxiomGraphDatabase().initialize()
    corpus_db = AxiomDatabase().initialize()

    nodes, edges = fetch_graph(graph_db, corpus_db, filters)
    print(
        f"Fetched {len(nodes)} nodes and {len(edges)} edges from the graph.",
        file=sys.stderr,
    )
    if filters_summary:
        print(f"Filters applied: {filters_summary}", file=sys.stderr)

    if args.redact_drugbank:
        report = redact_drugbank(nodes, edges)
        report_dir = args.output_dir or (
            args.output.parent if args.output else Path(".")
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "drugbank_redaction_report.txt"
        write_redaction_report(report, report_path)
        print(
            f"  DrugBank redaction: {len(report['redacted'])} field(s) "
            f"redacted, {len(report['review'])} flagged for review; "
            f"report at {report_path}",
            file=sys.stderr,
        )

    want_viewer = (
        args.format in ("cytoscape-js", "all") and not args.no_viewer
    )

    fmt = args.format

    if fmt == "cytoscape-js":
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            cyjs_path = args.output_dir / "graph.json"
        elif args.output:
            cyjs_path = args.output
        else:
            print("error: --output or --output-dir required", file=sys.stderr)
            sys.exit(2)
        export_cytoscape_js(nodes, edges, cyjs_path, filters_summary)
        print(f"  wrote {cyjs_path}", file=sys.stderr)
        if want_viewer:
            target_dir = args.output_dir or args.output.parent
            v = copy_viewer(target_dir)
            if v:
                print(f"  wrote {v}", file=sys.stderr)

    elif fmt == "graphml":
        if not args.output:
            print("error: --output required for graphml format", file=sys.stderr)
            sys.exit(2)
        export_graphml(nodes, edges, args.output)
        print(f"  wrote {args.output}", file=sys.stderr)

    elif fmt == "tsv":
        if not args.output_dir:
            print("error: --output-dir required for tsv format", file=sys.stderr)
            sys.exit(2)
        n_path, e_path, o_path = export_tsv(nodes, edges, args.output_dir)
        print(f"  wrote {n_path}", file=sys.stderr)
        print(f"  wrote {e_path}", file=sys.stderr)
        print(f"  wrote {o_path}", file=sys.stderr)

    elif fmt == "all":
        if not args.output_dir:
            print("error: --output-dir required for 'all' format", file=sys.stderr)
            sys.exit(2)
        args.output_dir.mkdir(parents=True, exist_ok=True)

        cyjs_path = args.output_dir / "graph.json"
        export_cytoscape_js(nodes, edges, cyjs_path, filters_summary)
        print(f"  wrote {cyjs_path}", file=sys.stderr)

        graphml_path = args.output_dir / "graph.graphml"
        export_graphml(nodes, edges, graphml_path)
        print(f"  wrote {graphml_path}", file=sys.stderr)

        tsv_dir = args.output_dir / "tsv"
        n_path, e_path, o_path = export_tsv(nodes, edges, tsv_dir)
        print(f"  wrote {n_path}", file=sys.stderr)
        print(f"  wrote {e_path}", file=sys.stderr)
        print(f"  wrote {o_path}", file=sys.stderr)

        if want_viewer:
            v = copy_viewer(args.output_dir)
            if v:
                print(f"  wrote {v}", file=sys.stderr)

    print("done.", file=sys.stderr)


if __name__ == "__main__":
    main()
