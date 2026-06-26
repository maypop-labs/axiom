#!/usr/bin/env python3
"""
AXIOM Project - PubMed Lookup and Enrichment

For each markdown file in the corpus that is not yet in the sources table:
  1. Parse filename to extract title and year
  2. Read the page count of the corresponding PDF. If pages >= 100,
     treat the source as a book: skip PubMed and PubTator entirely
     and insert the row with source_type='book' and
     metadata_source='filename'.
  3. Otherwise (paper path):
     a. Search PubMed and fetch full bibliographic metadata
     b. Insert a row into sources (with PubMed metadata, or filename
        fallback)
     c. If a PMID was found, fetch PubTator3 NER annotations and
        insert into pubtator_entities

Idempotent: re-running only processes filenames not already in sources.

Usage:
    python 02_PMID_lookup.py
"""

import sys
import traceback
from pathlib import Path

# Make lib/ importable when this script is run directly
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from axiom_db import AxiomDatabase, parse_filename
from pubmed import lookup_paper
from pubtator import fetch_pubtator_annotations

try:
    from pypdf import PdfReader
except ImportError:
    print("ERROR: pypdf is not installed.")
    print("Please run: pip install pypdf")
    sys.exit(1)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

MARKDOWN_DIR = Path("E:/data/literature/markdown")
PDF_DIR = Path("E:/data/literature/pdf")

# Page-count threshold for the book / paper classifier. PDFs at or above
# this length are treated as books (PubMed and PubTator are skipped).
BOOK_PAGE_THRESHOLD = 100

# Columns from the PubMed metadata dict that map directly to sources columns
PUBMED_FIELDS = (
    "pmid",
    "title",
    "authors",
    "journal",
    "year",
    "volume",
    "issue",
    "pages",
    "doi",
    "abstract",
    "citation_apa",
    "citation_mla",
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def derive_pdf_path(md_path):
    """Map a markdown file path to its corresponding PDF path."""
    return PDF_DIR / (md_path.stem + ".pdf")


def count_pdf_pages(pdf_path):
    """
    Return the number of pages in a PDF.

    Returns None if the PDF doesn't exist or cannot be read; the caller
    should treat that as "unable to classify" and fall through to the
    paper path.
    """
    if not pdf_path.exists():
        return None
    try:
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception as e:
        print(f"    pypdf error reading {pdf_path.name}: {e}")
        return None


# -----------------------------------------------------------------------------
# Per-source enrichment
# -----------------------------------------------------------------------------

def enrich_paper(db, md_path):
    """
    Process a single markdown file end-to-end.

    Reads the page count of the corresponding PDF and branches:
      - pages >= BOOK_PAGE_THRESHOLD: book path. Inserts a sources row
        with source_type='book' and metadata_source='filename'. Skips
        PubMed and PubTator.
      - otherwise: paper path. Looks up PubMed metadata, inserts the
        sources row, and (if a PMID was found) fetches and inserts
        PubTator entities.

    A missing or unreadable PDF falls through to the paper path so we
    do not lose existing behavior on a markdown file whose PDF has been
    moved or removed.

    Returns:
        dict: {filename, source_type, page_count, pmid, entities}
    """
    filename = md_path.name
    summary = {
        "filename": filename,
        "source_type": "journal_article",
        "page_count": None,
        "pmid": None,
        "entities": 0,
    }

    # --- Parse filename for title and year ---
    parsed = parse_filename(filename)
    title = parsed.get("title")
    year = parsed.get("year")

    # --- Page-count classifier ---
    pdf_path = derive_pdf_path(md_path)
    page_count = count_pdf_pages(pdf_path)
    summary["page_count"] = page_count
    is_book = page_count is not None and page_count >= BOOK_PAGE_THRESHOLD

    # --- Book path: skip PubMed and PubTator entirely ---
    if is_book:
        summary["source_type"] = "book"
        source_fields = {
            "source_type": "book",
            "markdown_path": str(md_path),
            "metadata_source": "filename",
        }
        if title:
            source_fields["title"] = title
        if year:
            source_fields["year"] = year
        db.add_reference(filename=filename, **source_fields)
        return summary

    # --- Paper path: PubMed lookup (best effort) ---
    metadata = None
    if title:
        try:
            metadata = lookup_paper(title, year)
        except Exception as e:
            print(f"    PubMed lookup error: {e}")
            metadata = None

    # --- Build sources row ---
    source_fields = {
        "source_type": "journal_article",
        "markdown_path": str(md_path),
    }

    if metadata:
        for col in PUBMED_FIELDS:
            value = metadata.get(col)
            if value is not None:
                source_fields[col] = value
        source_fields["metadata_source"] = "pubmed"
        summary["pmid"] = metadata.get("pmid")
    else:
        if title:
            source_fields["title"] = title
        if year:
            source_fields["year"] = year
        source_fields["metadata_source"] = "filename"

    # --- Insert sources row ---
    source_id = db.add_reference(filename=filename, **source_fields)

    # --- Fetch and insert PubTator entities (only if we have a PMID) ---
    if summary["pmid"]:
        try:
            raw_annotations = fetch_pubtator_annotations([summary["pmid"]])
            annotations = [
                {
                    "mention": a["mention"],
                    "entity_type": a["entity_type"],
                    "normalized_id": a["normalized_id"],
                    "normalized_name": a.get("normalized_name", ""),
                }
                for a in raw_annotations
                if a.get("pmid") == summary["pmid"]
            ]
            if annotations:
                db.add_pubtator_entities_batch(source_id, annotations)
                summary["entities"] = len(annotations)
        except Exception as e:
            print(f"    PubTator fetch error: {e}")

    return summary


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("AXIOM - PubMed Lookup and Enrichment")
    print("=" * 70)
    print()

    # Validate input directory
    if not MARKDOWN_DIR.exists():
        print(f"ERROR: Markdown directory does not exist: {MARKDOWN_DIR}")
        sys.exit(1)

    # Initialize database (creates axiom.db and tables if absent)
    db = AxiomDatabase()
    db.initialize()
    print(f"Database: {db.db_path}")
    print(f"Markdown: {MARKDOWN_DIR}")
    print()

    # Discover work
    all_md_files = sorted(MARKDOWN_DIR.glob("*.md"))
    existing_filenames = set(db.get_all_filenames())
    pending = [p for p in all_md_files if p.name not in existing_filenames]

    print(f"Markdown files in corpus: {len(all_md_files)}")
    print(f"Already in database:      {len(existing_filenames)}")
    print(f"To be processed:          {len(pending)}")
    print()

    if not pending:
        print("Nothing to do.")
        db.close()
        return

    # Counters
    total = len(pending)
    pubmed_hits = 0
    pubmed_misses = 0
    books = 0
    total_entities = 0
    errors = 0

    for i, md_path in enumerate(pending, start=1):
        print(f"[{i}/{total}] {md_path.name}")
        try:
            summary = enrich_paper(db, md_path)

            if summary["source_type"] == "book":
                books += 1
                print(
                    f"    -> type=book | pages={summary['page_count']} "
                    f"(PubMed/PubTator skipped)"
                )
            else:
                if summary["pmid"]:
                    pubmed_hits += 1
                else:
                    pubmed_misses += 1
                total_entities += summary["entities"]

                pmid_str = summary["pmid"] or "none"
                pages_str = (
                    f" | pages={summary['page_count']}"
                    if summary["page_count"] is not None
                    else ""
                )
                print(
                    f"    -> pmid={pmid_str} | "
                    f"entities={summary['entities']}{pages_str}"
                )
        except Exception as e:
            errors += 1
            print(f"    ERROR: {e}")
            traceback.print_exc()

        print()

    # Final summary
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Sources processed:    {total}")
    print(f"Books:                {books}")
    print(f"Papers (PubMed hit):  {pubmed_hits}")
    print(f"Papers (PubMed miss): {pubmed_misses}")
    print(f"Entities inserted:    {total_entities}")
    print(f"Errors:               {errors}")
    print()
    print("Database state:")
    for k, v in db.get_stats().items():
        print(f"  {k}: {v}")

    db.close()


if __name__ == "__main__":
    main()
