#!/usr/bin/env python3
"""
AXIOM
Stage 01: PDF to Markdown Conversion

Converts PDF files in the corpus PDF directory to markdown using Marker,
writing them to the corpus markdown directory with the same base filename
and a ".md" extension.

Behavior:
    - Reads PDFs from CORPUS_PDF_DIR.
    - Writes markdown to CORPUS_MARKDOWN_DIR.
    - Skips any PDF whose corresponding .md file already exists.
    - Writes atomically via a .md.tmp file that is renamed on success,
      so an interrupted run never leaves a partial .md in the corpus.
    - Loads Marker models once and reuses them for the whole batch.
    - Handles Ctrl+C gracefully: finishes the current file, then exits.
      A second Ctrl+C forces an immediate exit.

Usage:
    python 01_pdf_to_markdown.py
"""

import os
import signal
import sys
import time
from pathlib import Path

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

CORPUS_PDF_DIR = Path("E:/data/literature/pdf")
CORPUS_MARKDOWN_DIR = Path("E:/data/literature/markdown")

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
    print("\n\nShutdown requested. Will exit after the current file completes...")
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


# -----------------------------------------------------------------------------
# Marker Import
# -----------------------------------------------------------------------------

try:
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered
except ImportError:
    print("ERROR: Marker is not installed.")
    print("Please run: pip install marker-pdf")
    sys.exit(1)


# -----------------------------------------------------------------------------
# PDF Discovery
# -----------------------------------------------------------------------------

def discover_pdfs():
    """
    Walk CORPUS_PDF_DIR once and partition the PDFs into those that still
    need conversion and those whose markdown already exists.

    Returns:
        tuple: (pending: list[Path], skipped: int)
    """
    if not CORPUS_PDF_DIR.exists():
        print(f"ERROR: PDF directory does not exist: {CORPUS_PDF_DIR}")
        return [], 0

    CORPUS_MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)

    pending = []
    skipped = 0

    for pdf_path in CORPUS_PDF_DIR.glob("*.pdf"):
        md_path = CORPUS_MARKDOWN_DIR / (pdf_path.stem + ".md")
        if md_path.exists():
            skipped += 1
        else:
            pending.append(pdf_path)

    return sorted(pending), skipped


# -----------------------------------------------------------------------------
# Conversion
# -----------------------------------------------------------------------------

def convert_pdf_to_markdown(pdf_path, converter):
    """
    Convert a single PDF to markdown via Marker.

    Writes the markdown to <stem>.md.tmp first and renames it to <stem>.md
    on success. If the conversion raises, any partial .md.tmp is removed
    and no .md is created.

    Args:
        pdf_path: Path to the source PDF.
        converter: An initialized marker PdfConverter instance.

    Returns:
        tuple: (success: bool, markdown_path: Path or None, stats: dict)
    """
    stats = {
        "conversion_time": 0.0,
        "characters": 0,
        "error": None,
    }

    final_path = CORPUS_MARKDOWN_DIR / (pdf_path.stem + ".md")
    tmp_path = CORPUS_MARKDOWN_DIR / (pdf_path.stem + ".md.tmp")

    start = time.time()
    try:
        rendered = converter(str(pdf_path))
        text, _, _images = text_from_rendered(rendered)

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(text)

        os.replace(tmp_path, final_path)

        stats["conversion_time"] = time.time() - start
        stats["characters"] = len(text)
        return True, final_path, stats

    except Exception as e:
        stats["conversion_time"] = time.time() - start
        stats["error"] = f"{type(e).__name__}: {e}"

        # Clean up the partial tmp file if it exists
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

        return False, None, stats


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    """Main entry point."""
    pipeline_start = time.time()

    print("=" * 70)
    print("AXIOM - Stage 01: PDF to Markdown")
    print("=" * 70)
    print(f"PDF source:        {CORPUS_PDF_DIR}")
    print(f"Markdown output:   {CORPUS_MARKDOWN_DIR}")
    print()

    signal.signal(signal.SIGINT, request_shutdown)

    pending, skipped = discover_pdfs()
    total = len(pending) + skipped

    print(f"PDFs found:        {total}")
    print(f"Already converted: {skipped}")
    print(f"To convert:        {len(pending)}")
    print()

    if not pending:
        print("Nothing to do. All PDFs already have corresponding markdown files.")
        return 0

    print("Loading Marker models (first run may download ~1GB)...")
    model_load_start = time.time()
    converter = PdfConverter(
        artifact_dict=create_model_dict(),
    )
    print(f"Models loaded in {format_elapsed_time(time.time() - model_load_start)}.")
    print()

    converted = 0
    failed = 0
    failed_files = []

    for i, pdf_path in enumerate(pending, 1):
        if shutdown_requested():
            print("Shutdown requested. Stopping before the next file.")
            break

        print(f"[{i}/{len(pending)}] {pdf_path.name}")
        success, _md_path, stats = convert_pdf_to_markdown(pdf_path, converter)

        if success:
            converted += 1
            print(
                f"    OK: {format_elapsed_time(stats['conversion_time'])}, "
                f"{stats['characters']:,} characters"
            )
        else:
            failed += 1
            failed_files.append(pdf_path.name)
            print(f"    FAILED: {stats['error']}")

    elapsed = time.time() - pipeline_start

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total elapsed time: {format_elapsed_time(elapsed)}")
    print(f"Converted:          {converted}")
    print(f"Failed:             {failed}")
    print(f"Skipped (existing): {skipped}")

    if failed_files:
        print()
        print("Failed files:")
        for name in failed_files:
            print(f"  - {name}")

    if shutdown_requested():
        print()
        print("Note: Run was stopped early by user request.")
        print("Re-run the script to continue converting remaining files.")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
