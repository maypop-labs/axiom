"""
list_unprocessed_papers.py

Emit a markdown checklist of corpus papers that are fully indexed (present in
axiom.db.sources, chunked) but have never contributed to the curated graph,
i.e. no row in axiom_graph.db.edge_evidence or node_observations references
their filename.

A paper with no graph contribution is NOT provably unread. Under the
"considered but skipped" discipline a paper may have been read and
deliberately yielded nothing. There is no per-source reviewed marker in
either schema, so this report cannot distinguish never-read from
read-and-skipped. That caveat is written into the output header.

Both databases are opened read-only. The graph-vs-corpus set difference is
done in Python (the two DBs live in separate files; no ATTACH or cross-DB
join is needed).

Usage:
    python list_unprocessed_papers.py
    python list_unprocessed_papers.py --output E:/bin/axiom/Python/export_public/unprocessed_papers.md
    python list_unprocessed_papers.py --sort title
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "lib" / "data"

DEFAULT_CORPUS_DB = DATA_DIR / "axiom.db"
DEFAULT_GRAPH_DB = DATA_DIR / "axiom_graph.db"
DEFAULT_OUTPUT = SCRIPT_DIR / "export_public" / "unprocessed_papers.md"


def connect_readonly(path):
    """Open a SQLite file read-only so the report never locks or mutates it."""
    path = Path(path)
    if not path.exists():
        sys.exit(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def graph_filenames(graph_conn):
    """Set of source_filenames referenced by any edge evidence or observation."""
    rows = graph_conn.execute(
        """
        SELECT source_filename FROM edge_evidence
        WHERE source_filename IS NOT NULL
        UNION
        SELECT source_filename FROM node_observations
        WHERE source_filename IS NOT NULL
        """
    ).fetchall()
    return {r["source_filename"] for r in rows}


def corpus_sources(corpus_conn):
    """All indexed sources, lightest column set needed for the checklist."""
    return corpus_conn.execute(
        """
        SELECT id, filename, title, year, journal, source_type
        FROM sources
        """
    ).fetchall()


def sort_key(row, mode):
    if mode == "title":
        return ((row["title"] or row["filename"] or "").lower(),)
    # default: year ascending (NULL years last), then title
    year = row["year"]
    return (year is None, year if year is not None else 0,
            (row["title"] or row["filename"] or "").lower())


def render_markdown(backlog, totals, sort_mode):
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("# Unprocessed papers checklist")
    lines.append("")
    lines.append(f"Generated: {generated}")
    lines.append(f"Corpus sources indexed: {totals['corpus_total']}")
    lines.append(f"Already in graph: {totals['in_graph']}")
    lines.append(f"Awaiting graph entry: {totals['awaiting']}")
    lines.append("")
    lines.append("> Awaiting means no edge evidence or node observation "
                 "references this paper. It cannot distinguish a paper that "
                 "was never read from one that was read and deliberately "
                 "skipped, because no per-source reviewed marker exists in "
                 "either schema.")
    lines.append("")

    ordered = sorted(backlog, key=lambda r: sort_key(r, sort_mode))

    if sort_mode == "title":
        for row in ordered:
            lines.append(checklist_line(row))
    else:
        current_year = object()  # sentinel so the first row always opens a heading
        for row in ordered:
            year = row["year"]
            if year != current_year:
                current_year = year
                heading = str(year) if year is not None else "Unknown year"
                lines.append("")
                lines.append(f"## {heading}")
                lines.append("")
            lines.append(checklist_line(row))

    lines.append("")
    return "\n".join(lines)


def checklist_line(row):
    title = row["title"] or row["filename"] or "(untitled)"
    title = title.strip()
    return f"- [ ] {title} (id {row['id']})"


def main():
    parser = argparse.ArgumentParser(
        description="Markdown checklist of indexed papers not yet in the graph."
    )
    parser.add_argument("--corpus-db", default=str(DEFAULT_CORPUS_DB),
                        help="path to axiom.db (corpus index)")
    parser.add_argument("--graph-db", default=str(DEFAULT_GRAPH_DB),
                        help="path to axiom_graph.db (curated graph)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="markdown file to write")
    parser.add_argument("--sort", choices=["year", "title"], default="year",
                        help="grouping/order of the checklist")
    args = parser.parse_args()

    corpus_conn = connect_readonly(args.corpus_db)
    graph_conn = connect_readonly(args.graph_db)

    try:
        sources = corpus_sources(corpus_conn)
        in_graph_fns = graph_filenames(graph_conn)
    finally:
        corpus_conn.close()
        graph_conn.close()

    corpus_fns = {row["filename"] for row in sources}
    backlog = [row for row in sources if row["filename"] not in in_graph_fns]

    totals = {
        "corpus_total": len(sources),
        "in_graph": len(sources) - len(backlog),
        "awaiting": len(backlog),
    }

    # Diagnostic only: graph references whose filename is absent from the corpus
    # index. Non-empty means the two stores have drifted and the join is leaking.
    orphans = sorted(in_graph_fns - corpus_fns)

    markdown = render_markdown(backlog, totals, args.sort)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    print(f"corpus sources indexed : {totals['corpus_total']}")
    print(f"already in graph       : {totals['in_graph']}")
    print(f"awaiting graph entry   : {totals['awaiting']}")
    print(f"checklist written to   : {output_path.resolve()}")
    if orphans:
        print(f"\nwarning: {len(orphans)} graph filename(s) not found in the "
              f"corpus index (possible drift):")
        for fn in orphans:
            print(f"  {fn}")


if __name__ == "__main__":
    main()
