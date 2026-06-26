#!/usr/bin/env python3
"""
AXIOM
Stage 03: Chunk and Embed

For each source in the database:
  1. Read the markdown file
  2. Split it into paragraph-level chunks (with the most-recent heading
     text recorded in the chunks.section column)
  3. Embed each chunk with BAAI/bge-base-en-v1.5
  4. Insert the chunks (with embeddings) into the chunks table

Idempotent by default: skips any source that already has chunks. Use
--force to wipe and rebuild a source's chunks.

Heading handling for journal_article sources:
  - The first heading in the file is treated as the document title and
    skipped; it is not recorded as a section.
  - Subsequent headings (at any level) are recorded into the section
    column on every following chunk until the next heading is seen.
  - chunks.chapter and chunks.subsection are left NULL for papers; they
    will be populated once the book pipeline lands.

Chunk size:
  - Paragraphs under MIN_CHUNK_CHARS (200) are dropped.
  - Paragraphs over MAX_CHUNK_CHARS (1500) are split at sentence
    boundaries with a one-sentence overlap between consecutive
    sub-chunks.
  - The current section heading is prepended to chunk content (e.g.
    "## Methods\n\n<paragraph>") before embedding so the model sees
    topical context. char_start and char_end refer to the raw paragraph
    text in the source markdown, not the prepended content.

Usage:
    python 03_chunk_and_embed.py
    python 03_chunk_and_embed.py --force
    python 03_chunk_and_embed.py --source-id 5 10 --force
    python 03_chunk_and_embed.py --source-type journal_article --limit 10
    python 03_chunk_and_embed.py --dry-run
"""

import argparse
import re
import signal
import sys
import time
import traceback
from pathlib import Path

# Make lib/ importable when this script is run directly
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from axiom_db import AxiomDatabase

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy is not installed.")
    print("Run: pip install numpy")
    sys.exit(1)

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("ERROR: sentence-transformers is not installed.")
    print("Run: pip install sentence-transformers")
    sys.exit(1)

try:
    import torch
except ImportError:
    print("ERROR: torch is not installed.")
    print("See requirements.txt for the CUDA install command.")
    sys.exit(1)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

EMBEDDING_MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBEDDING_DIM = 768
MAX_TOKENS = 512                # bge-base context cap
MIN_CHUNK_CHARS = 200           # paragraphs below this are dropped
MAX_CHUNK_CHARS = 1500          # paragraphs above this are sentence-split
DEFAULT_BATCH_SIZE = 32
TITLE_MATCH_THRESHOLD = 0.8     # Jaccard similarity for title-vs-heading matching

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# Sentence boundary: punctuation followed by whitespace and a capital letter
# or opening bracket. Approximate but good enough for biomedical prose.
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
# Strip emphasis markers from heading text
HEADING_FORMATTING_RE = re.compile(r"[*_`]")

VALID_SOURCE_TYPES = ("journal_article", "book", "preprint", "other")


# -----------------------------------------------------------------------------
# Graceful Shutdown
# -----------------------------------------------------------------------------

_shutdown_requested = False


def request_shutdown(signum, frame):
    """Handle Ctrl+C by requesting graceful shutdown."""
    global _shutdown_requested
    if _shutdown_requested:
        print("\n\nForce quit requested. Exiting immediately.")
        sys.exit(1)
    print("\n\nShutdown requested. Will exit after the current source completes...")
    print("(Press Ctrl+C again to force quit.)\n")
    _shutdown_requested = True


def shutdown_requested():
    """Check if a shutdown has been requested."""
    return _shutdown_requested


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def format_elapsed_time(seconds):
    """Format elapsed seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.0f}s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}h {minutes}m {secs:.0f}s"


def strip_heading_formatting(text):
    """Remove markdown emphasis markers from heading text."""
    return HEADING_FORMATTING_RE.sub("", text).strip()


def normalize_for_title_match(s):
    """
    Normalize a heading or title for fuzzy comparison.

    Lowercases, replaces non-alphanumeric characters with spaces, and
    collapses whitespace. Returns "" for None or empty input.
    """
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def heading_matches_title(heading, title_tokens):
    """
    Return True if the heading should be skipped as the paper title.

    Uses Jaccard similarity (|A and B| / |A or B|) between the heading's
    normalized token set and the supplied title token set, compared to
    TITLE_MATCH_THRESHOLD. Handles common variations between PubMed
    titles and the headings Marker emits: missing or extra stopwords
    ("the", "of"), bracketed translation markers ("[Title]."), and
    trailing punctuation.
    """
    if not title_tokens:
        return False
    h_tokens = set(normalize_for_title_match(heading).split())
    if not h_tokens:
        return False
    inter = len(h_tokens & title_tokens)
    union = len(h_tokens | title_tokens)
    return (inter / union) >= TITLE_MATCH_THRESHOLD


# -----------------------------------------------------------------------------
# Markdown Chunker
# -----------------------------------------------------------------------------

def split_paragraph_into_subchunks(paragraph_text, paragraph_start_offset):
    """
    Split a paragraph longer than MAX_CHUNK_CHARS at sentence boundaries.

    Yields (sub_chunk_text, char_start_in_source) tuples.

    Uses a one-sentence overlap between consecutive sub-chunks. If a single
    sentence exceeds MAX_CHUNK_CHARS, it is emitted as its own sub-chunk
    (over the cap); the bge tokenizer will truncate it to MAX_TOKENS at
    embed time.
    """
    # Build a list of (sentence_text, start_in_paragraph, end_in_paragraph)
    sentences = []
    cursor = 0
    for m in SENTENCE_RE.finditer(paragraph_text):
        raw = paragraph_text[cursor:m.start()]
        leading = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if text:
            start = cursor + leading
            sentences.append((text, start, start + len(text)))
        cursor = m.end()
    # Final sentence after the last boundary
    raw = paragraph_text[cursor:]
    leading = len(raw) - len(raw.lstrip())
    text = raw.strip()
    if text:
        start = cursor + leading
        sentences.append((text, start, start + len(text)))

    if not sentences:
        return

    # Greedy pack with one-sentence overlap on overflow
    buffer = []          # indices into sentences
    buffer_chars = 0     # running char count for the buffer

    for i in range(len(sentences)):
        s_text = sentences[i][0]
        sep_len = 1 if buffer else 0  # space between sentences
        added = sep_len + len(s_text)

        if buffer and buffer_chars + added > MAX_CHUNK_CHARS:
            # Flush current buffer
            first = sentences[buffer[0]]
            last = sentences[buffer[-1]]
            sub_text = paragraph_text[first[1]:last[2]]
            yield sub_text, paragraph_start_offset + first[1]

            # One-sentence overlap: retain the last sentence in the new
            # buffer if it leaves room for the current sentence. If the
            # overlap sentence or the current sentence is itself over
            # MAX_CHUNK_CHARS, drop the overlap so the loop can advance;
            # the over-cap sub-chunk will be truncated at embed time.
            last_idx = buffer[-1]
            overlap_len = len(sentences[last_idx][0])
            if overlap_len + 1 + len(s_text) <= MAX_CHUNK_CHARS:
                buffer = [last_idx, i]
                buffer_chars = overlap_len + 1 + len(s_text)
            else:
                buffer = [i]
                buffer_chars = len(s_text)
        else:
            buffer.append(i)
            buffer_chars += added

    # Trailing flush
    if buffer:
        first = sentences[buffer[0]]
        last = sentences[buffer[-1]]
        sub_text = paragraph_text[first[1]:last[2]]
        yield sub_text, paragraph_start_offset + first[1]


def build_chunk(source_text, char_start, section, tokenizer):
    """
    Build a chunk dict from a paragraph (or sub-chunk) of source text.

    The current section heading is prepended to content for embedding
    context. char_start and char_end describe the raw source_text slice
    in the markdown file, not the prepended content.
    """
    if section:
        content = f"## {section}\n\n{source_text}"
    else:
        content = source_text

    token_count = len(tokenizer.encode(content, add_special_tokens=False))

    return {
        "content": content,
        "char_start": char_start,
        "char_end": char_start + len(source_text),
        "token_count": token_count,
        "chapter": None,
        "section": section,
        "subsection": None,
    }


def emit_chunks_from_paragraph(paragraph_text, paragraph_start, section, tokenizer):
    """
    Yield chunk dicts for a single paragraph.

    Drops paragraphs under MIN_CHUNK_CHARS. Splits paragraphs over
    MAX_CHUNK_CHARS at sentence boundaries.
    """
    if len(paragraph_text) < MIN_CHUNK_CHARS:
        return

    if len(paragraph_text) <= MAX_CHUNK_CHARS:
        yield build_chunk(paragraph_text, paragraph_start, section, tokenizer)
        return

    for sub_text, sub_start in split_paragraph_into_subchunks(
        paragraph_text, paragraph_start
    ):
        if len(sub_text) >= MIN_CHUNK_CHARS:
            yield build_chunk(sub_text, sub_start, section, tokenizer)


def chunk_markdown(text, source_type, tokenizer, source_title=None):
    """
    Split markdown into paragraph-level chunks.

    Walks the file line-by-line. The first heading is treated as the
    document title and skipped. Additionally, any subsequent heading
    whose normalized text matches source_title is also skipped; this
    handles papers where Marker emits a journal banner or article-type
    heading before the actual paper title. Other headings update the
    "current section" used to label following chunks.

    Returns a list of chunk dicts with chunk_index assigned.
    """
    chunks = []
    current_section = None
    title_seen = False
    title_tokens = set(normalize_for_title_match(source_title).split()) if source_title else set()
    paragraph_lines = []
    paragraph_start = None
    offset = 0

    for line in text.splitlines(keepends=True):
        stripped_for_match = line.rstrip("\r\n").strip()
        heading_match = HEADING_RE.match(stripped_for_match)

        if heading_match:
            # Flush any open paragraph before processing the heading
            if paragraph_lines:
                joined = "".join(paragraph_lines).rstrip()
                for c in emit_chunks_from_paragraph(
                    joined, paragraph_start, current_section, tokenizer
                ):
                    chunks.append(c)
                paragraph_lines = []
                paragraph_start = None

            heading_text = strip_heading_formatting(heading_match.group(2))

            # Determine whether to skip this heading as the paper title.
            # Always skip the first heading (typical case). Also skip any
            # subsequent heading whose token set fuzzy-matches the DB
            # title (handles cases where Marker emits a journal banner
            # or article-type heading before the actual title, and where
            # PubMed and Marker spell the title with minor variations).
            is_title = False
            if not title_seen:
                is_title = True
                title_seen = True
            elif heading_matches_title(heading_text, title_tokens):
                is_title = True

            if not is_title and heading_text:
                current_section = heading_text

        elif stripped_for_match == "":
            # Blank line - flush any open paragraph
            if paragraph_lines:
                joined = "".join(paragraph_lines).rstrip()
                for c in emit_chunks_from_paragraph(
                    joined, paragraph_start, current_section, tokenizer
                ):
                    chunks.append(c)
                paragraph_lines = []
                paragraph_start = None

        else:
            # Regular content line
            if not paragraph_lines:
                paragraph_start = offset
            paragraph_lines.append(line)

        offset += len(line)

    # Trailing flush
    if paragraph_lines:
        joined = "".join(paragraph_lines).rstrip()
        for c in emit_chunks_from_paragraph(
            joined, paragraph_start, current_section, tokenizer
        ):
            chunks.append(c)

    # Assign chunk_index in order
    for i, c in enumerate(chunks):
        c["chunk_index"] = i

    return chunks


# -----------------------------------------------------------------------------
# Embedding
# -----------------------------------------------------------------------------

def embed_chunks(model, chunks, batch_size):
    """
    Compute embeddings for a list of chunk dicts and mutate them in place.

    Stores L2-normalized float32 vectors as raw bytes in the embedding
    field, along with embedding_model and embedding_dim.
    """
    if not chunks:
        return chunks

    texts = [c["content"] for c in chunks]
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    for c, vec in zip(chunks, embeddings):
        vec_f32 = np.asarray(vec, dtype=np.float32)
        c["embedding"] = vec_f32.tobytes()
        c["embedding_model"] = EMBEDDING_MODEL_NAME
        c["embedding_dim"] = int(vec_f32.shape[0])

    return chunks


# -----------------------------------------------------------------------------
# Per-source pipeline
# -----------------------------------------------------------------------------

def process_source(db, source_row, model, tokenizer, batch_size, force, dry_run):
    """
    Process a single source: chunk, embed, and insert.

    Returns:
        dict with keys:
            chunks  (int): number of chunks created
            tokens  (int): total tokens across all chunks
            skipped (bool): True if existing chunks were left in place
            elapsed (float): seconds spent
    """
    source_id = source_row["id"]
    markdown_path_str = source_row["markdown_path"]
    source_type = source_row["source_type"]
    source_title = source_row["title"]

    result = {"chunks": 0, "tokens": 0, "skipped": False, "elapsed": 0.0}
    start = time.time()

    # Idempotency check
    has_existing = db.chunks_exist_for_source(source_id)
    if has_existing and not force:
        result["skipped"] = True
        result["elapsed"] = time.time() - start
        return result

    # Read markdown
    if not markdown_path_str:
        raise ValueError(f"source {source_id} has no markdown_path")
    md_path = Path(markdown_path_str)
    if not md_path.exists():
        raise FileNotFoundError(f"markdown not found: {md_path}")
    text = md_path.read_text(encoding="utf-8")

    # Chunk
    chunks = chunk_markdown(text, source_type, tokenizer, source_title=source_title)

    if not chunks:
        result["elapsed"] = time.time() - start
        return result

    if dry_run:
        result["chunks"] = len(chunks)
        result["tokens"] = sum(c["token_count"] for c in chunks)
        result["elapsed"] = time.time() - start
        return result

    # Embed
    embed_chunks(model, chunks, batch_size)

    # Wipe prior chunks if forcing a rebuild
    if has_existing and force:
        db.delete_chunks_for_source(source_id)

    # Insert
    db.add_chunks_batch(source_id, chunks)

    result["chunks"] = len(chunks)
    result["tokens"] = sum(c["token_count"] for c in chunks)
    result["elapsed"] = time.time() - start
    return result


# -----------------------------------------------------------------------------
# Source selection
# -----------------------------------------------------------------------------

def select_sources(db, args):
    """Determine which sources to process based on CLI args."""
    if args.source_id:
        rows = []
        for sid in args.source_id:
            row = db.get_reference(sid)
            if row is None:
                print(f"WARNING: --source-id {sid} not found, skipping")
                continue
            rows.append(row)
        return rows

    rows = db.get_all_references(order_by="id ASC")

    if args.source_type:
        rows = [r for r in rows if r["source_type"] == args.source_type]

    if args.limit is not None:
        rows = rows[: args.limit]

    return rows


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="AXIOM Stage 03: chunk markdown sources and embed the chunks.",
    )
    parser.add_argument(
        "--source-id",
        type=int,
        nargs="+",
        help="Process only these source IDs (space-separated).",
    )
    parser.add_argument(
        "--source-type",
        choices=VALID_SOURCE_TYPES,
        help="Process only sources of this type.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most N sources.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Embedding batch size (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild chunks for sources that already have them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chunk but do not embed or write to the database.",
    )
    return parser.parse_args()


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    args = parse_args()
    pipeline_start = time.time()

    print("=" * 70)
    print("AXIOM - Stage 03: Chunk and Embed")
    print("=" * 70)

    signal.signal(signal.SIGINT, request_shutdown)

    # Initialize database (creates the chunks table on first run if absent)
    db = AxiomDatabase()
    db.initialize()
    print(f"Database:           {db.db_path}")
    print(f"Embedding model:    {EMBEDDING_MODEL_NAME}")
    print(f"Batch size:         {args.batch_size}")
    print(f"Force rebuild:      {args.force}")
    print(f"Dry run:            {args.dry_run}")
    print()

    # Select sources to process
    sources = select_sources(db, args)
    if not sources:
        print("No sources match the given selection. Nothing to do.")
        db.close()
        return 0

    print(f"Sources selected:   {len(sources)}")
    print()

    # Load embedding model (skip in dry-run to keep iteration fast)
    model = None
    tokenizer = None
    if args.dry_run:
        # We still need a tokenizer to compute token_count, even in dry-run
        print("Loading tokenizer (dry run, embedding model skipped)...")
        from transformers import AutoTokenizer
        tok_load_start = time.time()
        tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
        print(
            f"Tokenizer loaded in "
            f"{format_elapsed_time(time.time() - tok_load_start)}."
        )
    else:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading embedding model on {device} (first run downloads ~440MB)...")
        model_load_start = time.time()
        model = SentenceTransformer(EMBEDDING_MODEL_NAME, device=device)
        tokenizer = model.tokenizer
        print(
            f"Model loaded in "
            f"{format_elapsed_time(time.time() - model_load_start)}."
        )
    print()

    # Counters
    total = len(sources)
    processed = 0
    skipped = 0
    errors = 0
    total_chunks = 0
    total_tokens = 0
    error_files = []

    for i, source_row in enumerate(sources, start=1):
        if shutdown_requested():
            print("Shutdown requested. Stopping before the next source.")
            break

        filename = source_row["filename"]
        print(f"[{i}/{total}] {filename}")

        try:
            stats = process_source(
                db,
                source_row,
                model,
                tokenizer,
                args.batch_size,
                args.force,
                args.dry_run,
            )

            if stats["skipped"]:
                skipped += 1
                print("    -> skipped (chunks exist; pass --force to rebuild)")
            else:
                processed += 1
                total_chunks += stats["chunks"]
                total_tokens += stats["tokens"]
                print(
                    f"    -> chunks={stats['chunks']} | "
                    f"tokens={stats['tokens']} | "
                    f"time={format_elapsed_time(stats['elapsed'])}"
                )

        except Exception as e:
            errors += 1
            error_files.append(filename)
            print(f"    ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()

        print()

    # Summary
    elapsed = time.time() - pipeline_start
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total elapsed time: {format_elapsed_time(elapsed)}")
    print(f"Sources processed:  {processed}")
    print(f"Sources skipped:    {skipped}")
    print(f"Errors:             {errors}")
    print(f"Chunks created:     {total_chunks}")
    print(f"Tokens (sum):       {total_tokens}")

    if error_files:
        print()
        print("Errored files:")
        for name in error_files:
            print(f"  - {name}")

    if shutdown_requested():
        print()
        print("Note: run was stopped early by user request.")
        print("Re-run the script to continue with remaining sources.")

    print()
    print("Database state:")
    for k, v in db.get_stats().items():
        print(f"  {k}: {v}")

    db.close()
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
