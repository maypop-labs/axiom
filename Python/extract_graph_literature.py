#!/usr/bin/env python3
"""
Extract a reference list of all corpus literature that has grounded
content in the AXIOM graph.

A corpus document counts as contributing if at least one edge_evidence
or node_observation row with a corpus grounding type (corpus_primary or
corpus_inline_cited) resolves to it, either through its chunk_id
(canonical, via chunks.source_id) or, as a fallback, through its
denormalized source_filename.

Non-corpus grounding (lexicon, common_knowledge, background_weak) is not
literature and is excluded from the reference list; its counts are
reported separately. Corpus-typed rows that resolve to no source are
reported as unattributable.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_GRAPH_DB = Path("E:/bin/axiom/Python/lib/data/axiom_graph.db")
DEFAULT_CORPUS_DB = Path("E:/bin/axiom/Python/lib/data/axiom.db")

CORPUS_GROUNDING_TYPES = ("corpus_primary", "corpus_inline_cited")


def connect(graph_db, corpus_db):
    conn = sqlite3.connect(str(graph_db))
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS corpus", (str(corpus_db),))
    return conn


def fetch_contributing_sources(conn):
    placeholders = ",".join("?" for _ in CORPUS_GROUNDING_TYPES)
    sql = f"""
    WITH grounding AS (
        SELECT 'edge' AS kind, chunk_id, source_filename, grounding_type
        FROM edge_evidence
        UNION ALL
        SELECT 'obs' AS kind, chunk_id, source_filename, grounding_type
        FROM node_observations
    ),
    resolved AS (
        SELECT g.kind,
               COALESCE(c.source_id, sf.id) AS source_id
        FROM grounding g
        LEFT JOIN corpus.chunks c ON c.id = g.chunk_id
        LEFT JOIN corpus.sources sf ON sf.filename = g.source_filename
        WHERE g.grounding_type IN ({placeholders})
    )
    SELECT s.id            AS source_id,
           s.filename      AS filename,
           s.citation_apa  AS citation_apa,
           s.authors       AS authors,
           s.year          AS year,
           s.title         AS title,
           s.doi           AS doi,
           s.pmid          AS pmid,
           SUM(CASE WHEN r.kind = 'edge' THEN 1 ELSE 0 END) AS n_edge_evidence,
           SUM(CASE WHEN r.kind = 'obs'  THEN 1 ELSE 0 END) AS n_observations
    FROM resolved r
    JOIN corpus.sources s ON s.id = r.source_id
    GROUP BY s.id
    ORDER BY COALESCE(s.citation_apa, s.authors, s.title, s.filename) COLLATE NOCASE
    """
    return conn.execute(sql, CORPUS_GROUNDING_TYPES).fetchall()


def fetch_grounding_breakdown(conn):
    sql = """
    WITH grounding AS (
        SELECT chunk_id, source_filename, grounding_type FROM edge_evidence
        UNION ALL
        SELECT chunk_id, source_filename, grounding_type FROM node_observations
    )
    SELECT g.grounding_type AS grounding_type,
           COUNT(*) AS n,
           SUM(CASE WHEN COALESCE(c.source_id, sf.id) IS NULL THEN 1 ELSE 0 END)
               AS unresolved
    FROM grounding g
    LEFT JOIN corpus.chunks c ON c.id = g.chunk_id
    LEFT JOIN corpus.sources sf ON sf.filename = g.source_filename
    GROUP BY g.grounding_type
    ORDER BY g.grounding_type
    """
    return conn.execute(sql).fetchall()


def format_citation(row):
    apa = row["citation_apa"]
    if apa and apa.strip():
        return apa.strip()
    authors = (row["authors"] or "").strip()
    year = row["year"]
    title = (row["title"] or "").strip()
    parts = []
    if authors:
        parts.append(authors)
    if year:
        parts.append(f"({year})")
    if title:
        parts.append(title if title.endswith(".") else title + ".")
    return " ".join(parts) if parts else row["filename"]


def compute_summary(sources, breakdown):
    corpus_types = set(CORPUS_GROUNDING_TYPES)
    excluded = [
        (r["grounding_type"], r["n"])
        for r in breakdown
        if r["grounding_type"] not in corpus_types
    ]
    unattributable = sum(
        r["unresolved"] for r in breakdown if r["grounding_type"] in corpus_types
    )
    return {
        "n_sources": len(sources),
        "total_edges": sum(r["n_edge_evidence"] for r in sources),
        "total_obs": sum(r["n_observations"] for r in sources),
        "excluded": excluded,
        "unattributable": unattributable,
    }


def render_txt(sources, summary, show_counts):
    lines = []
    lines.append("AXIOM graph reference list")
    lines.append("=" * 60)
    lines.append(f"Distinct contributing corpus documents: {summary['n_sources']}")
    lines.append(
        f"Grounded rows attributed: {summary['total_edges']} edge-evidence, "
        f"{summary['total_obs']} observations"
    )
    if summary["excluded"]:
        excl = ", ".join(f"{t} {n}" for t, n in summary["excluded"])
        lines.append(f"Excluded non-corpus grounding: {excl}")
    if summary["unattributable"]:
        lines.append(
            "Unattributable corpus-typed rows "
            f"(no chunk_id and no matching filename): {summary['unattributable']}"
        )
    lines.append("")

    for i, row in enumerate(sources, start=1):
        citation = format_citation(row)
        tag = (
            f"  [{row['n_edge_evidence']} edges, {row['n_observations']} obs]"
            if show_counts
            else ""
        )
        lines.append(f"{i}. {citation}{tag}")

    return "\n".join(lines)


def render_md(sources, summary, show_counts):
    lines = []
    lines.append("# AXIOM graph reference list")
    lines.append("")
    lines.append(
        f"- Distinct contributing corpus documents: {summary['n_sources']}"
    )
    lines.append(
        f"- Grounded rows attributed: {summary['total_edges']} edge-evidence, "
        f"{summary['total_obs']} observations"
    )
    if summary["excluded"]:
        excl = ", ".join(f"{t} {n}" for t, n in summary["excluded"])
        lines.append(f"- Excluded non-corpus grounding: {excl}")
    if summary["unattributable"]:
        lines.append(
            f"- Unattributable corpus-typed rows "
            f"(no chunk_id and no matching filename): {summary['unattributable']}"
        )
    lines.append("")
    lines.append("## References")
    lines.append("")

    for i, row in enumerate(sources, start=1):
        citation = format_citation(row)
        tag = (
            f" *({row['n_edge_evidence']} edges, {row['n_observations']} obs)*"
            if show_counts
            else ""
        )
        lines.append(f"{i}. {citation}{tag}")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-db", type=Path, default=DEFAULT_GRAPH_DB)
    parser.add_argument("--corpus-db", type=Path, default=DEFAULT_CORPUS_DB)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write the list to this file. Defaults to stdout.",
    )
    parser.add_argument(
        "--plain", action="store_true",
        help="Suppress the per-source edge/observation contribution counts.",
    )
    parser.add_argument(
        "--format", choices=["txt", "md"], default="txt",
        help="Output format: 'txt' (default plain text) or 'md' (markdown).",
    )
    args = parser.parse_args()

    for db in (args.graph_db, args.corpus_db):
        if not db.exists():
            print(f"Database not found: {db}", file=sys.stderr)
            sys.exit(1)

    conn = connect(args.graph_db, args.corpus_db)
    try:
        sources = fetch_contributing_sources(conn)
        breakdown = fetch_grounding_breakdown(conn)
    finally:
        conn.close()

    summary = compute_summary(sources, breakdown)
    renderer = render_md if args.format == "md" else render_txt
    output = renderer(sources, summary, show_counts=not args.plain)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {len(sources)} references to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
