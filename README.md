# AXIOM

A curated biomedical literature corpus and a curated mechanistic knowledge graph, built for AI-assisted aging research. AXIOM pairs a hand-selected corpus with a hand-reviewed graph of mechanistic relationships and exposes both to a frontier language model (Claude) through local Model Context Protocol (MCP) servers. The model retrieves and reasons over the corpus; every proposed addition to the graph passes through explicit human review before it is committed.

The organizing standard is engineering-actionability toward human lifespan extension. Content earns a place in the graph only when it carries mechanistic, intervention-relevant signal that plain search cannot surface on its own.

## What this repository is, and is not

This repository holds the ingestion pipeline, the AXIOM MCP server, helper utilities, launchers, and the standard operating procedures that govern curation. It does not hold the corpus or the curated databases.

Not included, by design: the source PDFs and converted markdown (third-party copyrighted material that lives outside the repository), the corpus index database, the curated graph database, the Python virtual environment, the graph visualizer, and all generated exports. See `.gitignore` for the full list. The corpus index is rebuildable from the markdown corpus; the curated graph is not, and is backed up and published through a separate, redacted channel.

The practical consequence: cloning this repository gives you the engine and the editorial method, not the curated knowledge itself.

## Architecture

AXIOM is two stacks behind one model.

The AXIOM stack owns ingestion (Stages 01 to 04) and two SQLite databases: a corpus index (`axiom.db`) and the curated graph (`axiom_graph.db`). The AXIOM MCP server exposes corpus retrieval and graph read and write tools.

The LEXICON stack is a standalone, project-agnostic MCP server that enriches candidate graph entities against public reference databases: MyGene.info, HGNC, UniProt, PubChem, QuickGO, and a locally indexed copy of DrugBank under an academic license. LEXICON lives in a sibling MCP collection and is not part of this repository.

A third standalone server (NCBI E-utilities) supports citation verification and corpus-candidate discovery. It is not in the retrieval path.

## Pipeline

Four independent, idempotent stages. Each numbered script can be re-run safely, and each has a convenience launcher at the repository root.

1. `01_pdf_to_markdown.py` converts source PDFs to markdown via Marker (vision OCR through Surya), writing atomically.
2. `02_PMID_lookup.py` resolves bibliographic metadata from PubMed, classifies books by page count, and runs PubTator3 named-entity recognition for papers.
3. `03_chunk_and_embed.py` splits markdown into paragraph-level chunks and embeds them with bge-base-en-v1.5, stored as float32 blobs.
4. `04_graph_export.py` exports the curated graph to cytoscape-js, GraphML, or TSV, with a redaction mode for public, DrugBank-clean snapshots.

## The knowledge graph

The graph is a directed multigraph keyed by edge type. Nodes are biological entities (genes, proteins, processes, small molecules, phenotypes, and similar); edges are mechanistic relationships; per-node corpus-derived findings are stored as node observations. Every edge and observation traces to a citable source: a corpus chunk, a reference-database return, or labeled background knowledge. Coverage and observation counts are derived from the underlying evidence rows; there is no confidence column.

Additions follow a propose-and-review protocol. The model drafts a structured proposal, the curator reviews it freeform, and an approved proposal commits in a single atomic transaction.

## Design principles

Curation is the asset. The hand-selected corpus and the hand-approved graph are the point; the tooling is replaceable.

Markdown is the source of truth. The corpus index is rebuildable; the graph is not.

No local generation. Embeddings and PDF layout are deterministic; all generative reasoning goes through Claude.

Citations are first-class. Every retrieval, evidence row, observation, and enrichment return carries provenance.

The model can be swapped. The architecture does not couple to a single vendor.

## Repository layout

```
Python/        Ingestion pipeline (Stages 01-04), graph export, lib, utilities
mcp/           AXIOM MCP server (corpus retrieval and graph tools)
claude/        Project overview, custom instructions, and curation SOPs
run_*.bat      Convenience launchers for the pipeline stages and exports
```

## Getting started

The pipeline and MCP server run on Windows against a project-local Python virtual environment, created by `Python/setup.bat`. The pipeline stages run through the `run_*.bat` launchers at the repository root, and `mcp/README.md` covers the MCP server and its Claude Desktop wiring.

A working deployment also requires the corpus and databases, which are not distributed here. Without them the code is readable and the method is reproducible, but the retrieval and graph tools have nothing to serve.

## Status

AXIOM is in active single-user development. As of mid-2026 the corpus holds on the order of 870 hand-selected sources, and the curated graph holds several hundred nodes and edges grown through conversation-time review. The predecessor project, MINT, attempted batch triple extraction with small local models; quality was insufficient, so AXIOM kept the corpus and moved extraction to frontier-model proposal-and-review.

## Attribution and licensing

AXIOM is developed by Maypop Labs.

The corpus consists of third-party copyrighted publications and is not redistributed. DrugBank-derived content is used under an academic license; public graph exports redact DrugBank narrative and relational content. A license for the code in this repository is still to be determined.
