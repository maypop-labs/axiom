# AXIOM MCP Server

The MCP (Model Context Protocol) server that exposes the AXIOM corpus to
Claude. Runs locally over stdio. Loads the bge-base-en-v1.5 embedding
model and the full chunk embedding cache once at startup; subsequent
queries are served in tens to a few hundred milliseconds.

## Files

| File             | Purpose                                                                         |
|------------------|---------------------------------------------------------------------------------|
| `server.py`      | MCP entry point. Defines the seven tools and starts the FastMCP stdio loop.     |
| `retrieval.py`   | `AxiomRetriever` class. Embedding cache, semantic/keyword/hybrid search.        |
| `requirements.txt` | Python dependencies (mostly shared with `Python/requirements.txt`).            |
| `run_server.bat` | Launcher: activates the shared venv and runs `server.py`.                       |

## Tools exposed

- `semantic_search(query, k, filters)` - cosine similarity over the embedding
  cache. Best for conceptual queries.
- `keyword_search(query, k, filters)` - word-bounded GLOB matching across
  chunks, AND semantics across multiple terms. Best for exact-symbol queries.
- `hybrid_search(query, k, filters)` - reciprocal rank fusion of the above.
  Sensible default when in doubt.
- `get_source(source_id_or_filename)` - full source metadata.
- `get_source_chunks(source_id)` - all chunks for a source, in order.
- `get_chunk(chunk_id)` - a single chunk with source metadata for citation.
- `find_entity(mention, entity_type, k)` - PubTator NER lookup.

All search tools accept the same filter set: `source_type`, `year_min`,
`year_max`, `exclude_references` (default `True`).

## Setup

The MCP server uses the same Python virtual environment as the Stage 03
chunk-and-embed pipeline.

```cmd
cd E:\bin\axiom\Python
call venv\Scripts\activate.bat
pip install -r E:\bin\axiom\mcp\requirements.txt
```

Most of the requirements (sentence-transformers, numpy, torch) are
already installed; this should only add the `mcp` package itself.

## Configuring Claude Desktop

Edit `claude_desktop_config.json` (the file lives at
`%APPDATA%\Claude\claude_desktop_config.json` on Windows) to add an
entry under `mcpServers`. Two equivalent options:

**Option A (recommended): direct python.exe call**

```json
{
  "mcpServers": {
    "axiom": {
      "command": "E:\\bin\\axiom\\Python\\venv\\Scripts\\python.exe",
      "args": ["E:\\bin\\axiom\\mcp\\server.py"]
    }
  }
}
```

**Option B: via the launcher batch file**

```json
{
  "mcpServers": {
    "axiom": {
      "command": "E:\\bin\\axiom\\mcp\\run_server.bat"
    }
  }
}
```

Restart Claude Desktop after editing. On first connect, AXIOM will
appear in the available tools list. The server takes 5-20 seconds to
become responsive on first launch (model load, embedding cache load);
subsequent launches are faster once the model is on disk.

## Smoke testing from the command line

You can launch the server manually to verify it starts cleanly:

```cmd
E:\bin\axiom\mcp\run_server.bat
```

Status messages appear on stderr. The server then waits silently for
MCP protocol messages on stdin. Press Ctrl+C to exit. If you see logged
sources, chunks, and entity counts that match your database state,
startup is healthy.

## Performance notes

- First-launch download of the bge model is ~440 MB to the HuggingFace
  cache. Subsequent launches reuse the cached weights.
- Resident memory after startup is roughly the embedding matrix
  (~239 MB for ~80k chunks at 768 dims float32) plus the model
  (~440 MB on CPU, similar on GPU but in VRAM).
- Per-query latency on an RTX 2080 Ti: semantic search ~30-50 ms,
  keyword search ~50-200 ms depending on candidate count, hybrid the
  sum of those.

## Schema dependency

This server reads from `E:\bin\axiom\Python\lib\data\axiom.db` via the
`AxiomDatabase` class in `Python\lib\axiom_db.py`. It expects:

- A populated `sources` table.
- A populated `chunks` table with embeddings (`embedding` BLOB column,
  `embedding_model` and `embedding_dim` set). Chunks without embeddings
  are skipped silently at load time.
- The `pubtator_entities` table for the `find_entity` tool. If empty,
  `find_entity` returns an empty list rather than failing.
