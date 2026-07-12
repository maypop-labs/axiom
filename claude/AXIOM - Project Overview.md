# Project AXIOM (V17)

A curated biomedical literature corpus plus a curated mechanistic knowledge graph, accessed by Claude through local MCP servers. Single user, single machine, single corpus, single graph. The user (Neil) hand-selects sources; Claude retrieves and reasons over them; proposed graph additions go through explicit user review before commit. The asset is the curation, not the tooling.

A predecessor project, MINT, attempted to extract triples in batch using local 7-8B models. Quality was insufficient. AXIOM keeps the corpus and pivots extraction to conversation-time, frontier-model-driven proposal-and-review.

## Information model

Four sources Claude reads from. One storage location Claude writes to.

Sources:
1. The corpus (markdown documents, retrieved via the AXIOM MCP).
2. Claude's background knowledge (always labeled when used as such).
3. The Graph (read via AXIOM MCP graph tools).
4. LEXICON (read via the standalone LEXICON MCP).

Storage: the Graph (`axiom_graph.db`).

Per-node corpus-derived findings ("node observations") live as a per-node attribute table inside the graph database, structurally parallel to `node_aliases` and `edge_evidence`. There is no separate dossier component. LEXICON's caches are regenerable from upstream and are never treated as authoritative storage.

## Architecture

```
+-----------------------------+        +-----------------------------+
|        AXIOM stack          |        |       LEXICON stack         |
|                             |        |                             |
| Stages 01-04                |        | Public DB lookups + caches  |
|        |                    |        |        |                    |
|        v                    |        |        v                    |
| axiom.db    axiom_graph.db  |        | cache.db (HTTP, TTL=7d)     |
|        \      /             |        | drugbank.db (local index)   |
|         v    v              |        |        |                    |
| AXIOM MCP server (31 tools) |        | LEXICON MCP server (9)      |
+-----------------------------+        +-----------------------------+
            |                                       |
            v                                       v
     corpus + graph reads/writes           enrichment lookups
            |                                       |
            +------------ Claude Desktop -----------+
                              |
                       GRAPH PROPOSAL
                              |
                              v
                       user review/approval
                              |
                              v
                       axiom_graph.db (writes)
```

Concrete components:

* Corpus at `E:/data/literature/{pdf,markdown}/`. PDFs and markdown live in flat sibling directories; papers and books co-locate. The `source_type` column distinguishes them.
* Corpus index at `E:/bin/axiom/Python/lib/data/axiom.db` (sources, chunks, pubtator_entities). Rebuildable from corpus.
* Curated graph at `E:/bin/axiom/Python/lib/data/axiom_graph.db` (nodes, node_aliases, node_observations, edges, edge_conditions, edge_evidence). Not rebuildable from any other source.
* AXIOM MCP server at `E:/bin/axiom/mcp/server.py`, project-attached, uses `E:/bin/axiom/Python/venv`.
* LEXICON MCP server at `E:/bin/mcp/lexicon/server.py`, standalone, project-agnostic, uses shared venv at `E:/bin/mcp/.venv`.
* NCBI MCP server at `E:/bin/mcp/ncbi/server.py`, standalone. PubMed search/fetch/DOI-lookup/citation via NCBI E-utilities. Used for citation verification and corpus-candidate discovery; not in the AXIOM read path.

## MCP collection root

`E:/bin/mcp/` is the project-agnostic root for MCP servers. Manifest at `E:/bin/mcp/servers.toml` is the source of truth for `claude_desktop_config.json`'s `mcpServers` block.

```
E:/bin/mcp/
  .venv/                # shared venv for standalone servers
  requirements.txt      # shared deps (mcp, fastmcp, httpx, pydantic, lxml)
  servers.toml          # manifest
  generate_config.py    # manifest -> claude_desktop_config.json
  filesystem/           # standalone
  ncbi/                 # standalone
  lexicon/              # standalone
```

`servers.toml` distinguishes `kind = "standalone"` (uses `python = "shared"`) from `kind = "project_attached"` (uses an explicit project venv). AXIOM is the only project-attached server currently registered.

**Important behavior of `generate_config.py`:** it does `config["mcpServers"] = new_servers` (assignment, not merge). It preserves the rest of the JSON (preferences, Chrome pairing, etc.), but the entire `mcpServers` block is overwritten on every run. Anything hand-added to the JSON gets silently wiped on next regeneration. **Add new servers via the manifest, never directly to the JSON.**

## Workflow

Stages are independent and idempotent. Each numbered script can be re-run safely. Convenience batch files (`run_0X_*.bat`) live at the project root.

### Stage 01: PDF to markdown (`01_pdf_to_markdown.py`)

Walks `E:/data/literature/pdf/`, skips PDFs that already have markdown, converts via Marker (vision OCR via Surya), writes atomically (`.md.tmp` -> `.md`). Loads model dictionary once per batch. Handles `Ctrl+C` gracefully (single press finishes current file; second press exits).

### Stage 02: PubMed lookup, book classification, NER (`02_PMID_lookup.py`)

For each markdown not yet in `sources`:

1. Read the PDF page count via pypdf. Pages >= 100: classify as book, insert with `source_type = 'book'`, `metadata_source = 'filename'`, skip PubMed and PubTator. Otherwise: paper path.
2. Paper path: search PubMed by title (year as optional narrower), insert source row with full bibliography on hit (or filename-only on miss; either way it isn't retried). On PubMed hit, fetch PubTator3 NER and insert into `pubtator_entities`.

Per-source try/except. A missing PDF returns `None` from the page-count helper and falls through to the paper path.

### Stage 03: chunking and embedding (`03_chunk_and_embed.py`)

Per source: skip if chunks exist (override with `--force`); read markdown; walk paragraphs; drop the first heading as title; drop subsequent title-like headings via Jaccard similarity (>= 0.8) against the database title; track most-recent heading as `section`; drop paragraphs under 200 chars; split paragraphs over 1500 chars at sentence boundaries with one-sentence overlap; prepend section heading to chunk content for embedding context; embed batch via `BAAI/bge-base-en-v1.5`; store as float32 BLOB.

CLI: `--source-id`, `--source-type`, `--limit`, `--batch-size`, `--force`, `--dry-run`.

Known imperfections (mitigated, not blocking):
* **Over-cap chunks (8.25% of total).** Paragraphs without sentence-boundary regex matches fall through as oversized blocks. bge truncates embedding input to 512 tokens; stored `content` keeps full text. A future token-aware fallback splitter would fix this.
* **Reference-section chunks (12.9% of total).** Roughly 10,640 chunks are reference-list content. The MCP search tools default `exclude_references = True` and screen these out via a precomputed mask, so they are absent from default retrieval.

### Stage 04: graph export (`04_graph_export.py`)

Reads `axiom_graph.db` directly, joins evidence and observations to `axiom.db` for citations, emits `cytoscape-js` (with companion `viewer.html`), `graphml`, or `tsv` (nodes.tsv + edges.tsv + observations.tsv). Filters: `--node-type`, `--edge-type`, `--min-coverage`, `--year-min`, `--year-max`. Conversation metadata is scrubbed by default; `--include-conversation-metadata` retains it for audit dumps.

The viewer template at `Python/templates/viewer.html` loads cytoscape.js 3.30 from a CDN. Serve the export directory with `python -m http.server` for local viewing (CORS blocks `file://` for the `graph.json` fetch).

### Books

Wired through Stage 02's page-count classifier as a deliberately minimal first cut. Books and papers share the filename convention `(YYYY) Title.md` and the same Stage 01/03 pipelines. Book metadata enrichment (OpenLibrary/Google Books), publisher/ISBN columns, chapter/subsection chunk hierarchy, and `--use_llm` for scanned-book quality are all deferred until a real book has been processed end to end and the gaps are concrete.

## Conversation protocol

When an answer asserts mechanistic content or per-entity findings worth capturing in the graph:

1. **Pre-flight existing matches.** Call `graph_find_nodes` for each candidate node. For every match, also call `graph_get_observations(node_id)` so the accumulated dossier is in scope before composing the answer.
2. **Enrich new candidates via LEXICON.** For new nodes, call the appropriate `lexicon_lookup_*` tool. For small molecules, try `lookup_drug` first (DrugBank, richer); fall back to `lookup_compound` (PubChem) on miss. For genes, `lookup_gene` is the workhorse; `lookup_hgnc` if a legacy symbol is suspected. To inventory existing pharmacology around a candidate target, use `lexicon_find_drugs_by_target` (reverse-direction DrugBank lookup; an empty result is a meaningful negative finding, not a tool failure).
3. **Emit a `GRAPH PROPOSAL` block** at the end of the response. Sections:
   * Existing matches (annotate with `observation_count`).
   * New nodes (with `cross_references` and `notes_prefix`).
   * New edges (with conditions and per-evidence chunk citations).
   * Additions to existing edges (with coverage delta).
   * New observations (text + chunk citation, anchored to existing or newly-proposed node).
   * Rewrites of existing observations (before / after / preserved chunk_ids).
   * Cross-reference additions or updates on existing nodes.
4. **User reviews** freeform: approve all, approve some, edit, reject.
5. **Commit via `graph_apply_proposal`** in one atomic batch. The single call accepts the full proposal payload (any subset of 10 sections; see the AXIOM MCP server section below) and returns a structured report with assigned IDs, a `created` vs `matched` flag per item, a sorted rollback recipe for any additions, and captured pre-edit values for any in-place edits. For very small commits (one or two writes) the per-call `graph_add_*` and `graph_update_*` tools remain available for explicit visibility. Either way, restate what committed and report new `graph_stats`.

## Quality gates

Behavioral, applied by Claude before proposing.

1. No edge or observation without citable grounding. Valid grounding sources: (a) corpus chunks retrieved during the current conversation; (b) inline-cited statements within a corpus chunk (the chunk grounds the proposal; the inline reference is recorded as upstream provenance); (c) LEXICON returns; (d) Claude's background knowledge. Background knowledge that is not common-knowledge biomedical fact is flagged on the record as weakest grounding. Common-knowledge background (textbook-grade, multiple independent sources agree) is not flagged.
2. Pre-flight existing matches and pull observations before answering.
3. Provenance always populated on writes, source-type appropriate. Corpus: `source_filename`, `chunk_id`. LEXICON: source name and identifier (e.g., DrugBank DB00001, UniProt P04637), retrieval date. Background: labeled as common-knowledge or weakest-grounding with brief justification. `conversation_date` and `conversation_question` are always present regardless of source.
4. Strict node identity: same `(canonical_name, node_type)` is the same node. Alternate names are aliases, attached only after approval.
5. No auto-merging of edge types. "A activates B" and "A binds B" are separate edges. The multigraph by edge type is the intended shape.
6. Conditions scope WHEN an edge holds (`edge_conditions`); cell_system on evidence records WHERE it was demonstrated (`edge_evidence`). Don't conflate.
7. LEXICON enrichment is advisory. The user may override `suggested_node_type` and `canonical_name` at review.
8. Observations are paraphrases, not quotes. Verbatim chunk text never goes in the `observation` field.
9. Reference data and curated-database returns are valid evidence with source labeling. DrugBank drug-target relationships, UniProt features, PubChem records, and similar can ground graph edges and observations when they directly assert the relationship. Provenance records the source name and identifier. Null results from LEXICON (e.g., empty `lexicon_find_drugs_by_target` for a TF target) typically belong in `node_observations` as a source-labeled negative finding rather than in `edge_evidence`, because they assert absence of a relationship rather than its presence.

## Editorial discipline for observations

Append-by-default. Propose a rewrite only when one of:
* The new observation supersedes an old one with strictly greater precision.
* Two existing entries can be losslessly consolidated into a more readable single entry.
* A direct contradiction needs resolution and leaving both in place would mislead a future reader.

Always preserve every `chunk_id` from rewritten entries. Always show before / after / preserved chunks in the proposal block before the user approves. Provenance fields are immutable; only `observation` and `notes` are editable via `update_observation`.

## Schemas (high level)

### `axiom.db` (corpus index)

* `sources`: one row per source. `source_type` in {`journal_article`, `book`, `preprint`, `other`}. `metadata_source` in {`pubmed`, `openlibrary`, `google_books`, `filename`, `manual`}. Books currently always end up `metadata_source = 'filename'`.
* `chunks`: paragraph-level retrievable spans. `embedding` is a raw float32 BLOB. `ON DELETE CASCADE` on `source_id`. `UNIQUE(source_id, chunk_index)`. `chapter` and `subsection` columns exist but are NULL today (flat `section` heading model).
* `pubtator_entities`: NER from PubTator3 (papers only). `UNIQUE(source_id, mention, entity_type, normalized_id)`.

### `axiom_graph.db` (curated graph)

* `nodes`: `canonical_name` UNIQUE with `node_type`. Node types are open-ended (suggested: `gene`, `protein`, `miRNA`, `PTM_state`, `complex`, `process`, `phenotype`, `compartment`, `condition`, `small_molecule`, `other`). `cross_references` is a TEXT JSON column. Species is not stored: for gene and protein nodes it is derived at read time from `cross_references` (via `mcp/species.py`) and surfaced through `graph_find_nodes_by_species` and the `graph_stats` species histogram.
* `node_aliases`: alternate names, UNIQUE per node.
* `node_observations`: per-node corpus-derived findings. One row per supporting chunk. Provenance fields immutable post-creation; only `observation` and `notes` editable. Carries `grounding_type` (one of `corpus_primary`, `corpus_inline_cited`, `lexicon`, `common_knowledge`, `background_weak`) and `provenance_extra` (TEXT JSON) for non-corpus provenance.
* `edges`: subject -> object directed multigraph keyed by `edge_type`. UNIQUE on `(subject_id, object_id, edge_type)`.
* `edge_conditions`: free-form `(condition_type, condition_value)` pairs scoping when the edge holds.
* `edge_evidence`: per-observation provenance. Two layers: corpus (`source_filename`, `source_doi`, `source_pmid`, `chunk_id`, `method`, `cell_system`) and conversation (`conversation_date`, `conversation_question`). Carries `grounding_type` and `provenance_extra` matching `node_observations`.

Cross-DB references on `edge_evidence` and `node_observations` are validated by `GraphAccessor` at write time, not by SQLite. Coverage of an edge is `COUNT(*)` of its evidence rows. `observation_count` for a node is `COUNT(*)` of its `node_observations`. Both are derived; nothing is stored.

Schema migrations land via `_apply_migrations` in `AxiomGraphDatabase.initialize()`, idempotent on every startup.

### LEXICON databases

* `E:/bin/mcp/lexicon/data/cache.db`: HTTP response cache for MyGene, HGNC, UniProt, PubChem, QuickGO. 7-day TTL. Key: `<source>:<sha256(sorted-params)[:24]>`.
* `E:/bin/mcp/lexicon/data/drugbank.db`: parsed DrugBank index. Tables: `drugs` (primary, with calc chemistry, indication, MoA, etc.), `drug_aliases`, `drug_categories`, `drug_targets` (kind in {target, enzyme, transporter, carrier}; indexed on `drugbank_id`, `uniprot_id`, and `gene_name`), `drug_xrefs`, `meta`. Re-runnable, drop-and-recreate. DrugBank lookups bypass the HTTP cache.

Detailed column lists live in `lib/axiom_db.py`, `lib/axiom_graph_db.py`, and `E:/bin/mcp/lexicon/load_drugbank.py`. Refer to source rather than duplicating columns here.

## AXIOM MCP server

33 tools across 7 corpus + 26 graph categories.

**Corpus tools:** `semantic_search`, `keyword_search`, `hybrid_search` (RRF, default search), `get_source`, `get_source_chunks`, `get_chunk`, `find_entity`. All search tools accept `source_type`, `year_min`, `year_max`, `exclude_references` (default `True`). Results include `content`, `source filename / title / year / section`, rank, score, and pre-formatted APA citation, so Claude can cite without follow-up calls.

**Graph reads:** `graph_get_node`, `graph_find_nodes`, `graph_find_nodes_batch`, `graph_find_nodes_by_species`, `graph_get_edges`, `graph_get_edge`, `graph_neighbors`, `graph_get_observations`. Every node read returns `observation_count`; `graph_get_node` also returns full observations. `graph_find_nodes_batch` resolves many name lookups in one call, closing the former per-paper pre-flight bottleneck. `graph_find_nodes_by_species` lists gene and protein nodes by species; species is derived at read time from `cross_references` via `mcp/species.py` and is never stored (pass `unknown` to audit nodes that will export unclassified). Cross-DB citation enrichment happens in the MCP layer at read time (not SQL): evidence and observation rows store only `chunk_id`, and APA / DOI / PMID / title / year are inlined when retrieved.

**Graph writes (per-call):** `graph_add_node` (with optional aliases and cross_references in same call), `graph_add_alias`, `graph_add_edge` (with conditions and evidence in same call), `graph_add_evidence`, `graph_add_condition`, `graph_add_observation`, `graph_update_node`, `graph_update_edge`, `graph_update_observation`, `graph_set_cross_references`.

**Graph bulk:** `graph_apply_proposal`. Accepts a single payload with up to 10 optional sections (`new_nodes`, `new_edges`, `new_observations`, `alias_additions`, `evidence_additions`, `condition_additions`, `observation_rewrites`, `cross_reference_updates`, `node_updates`, `edge_updates`) and applies the entire batch in one SQLite transaction. Three properties matter:

* **Forward references by name and type.** A new edge or observation may reference a node being created in the same payload via `{"name": str, "node_type": str}`. The validator builds an in-payload index during the new_nodes pass and resolves refs against it before the DB.
* **Idempotent match-and-merge.** A new_node whose `(canonical_name, node_type)` already exists is matched, not duplicated. Existing aliases are preserved; new aliases from the payload are appended; cross_references are merged with existing keys kept and new keys added. A new_edge whose `(subject, object, edge_type)` already exists is matched and the payload's conditions and evidence are appended to it. Each item in the report carries a `created` vs `matched` flag.
* **Two-phase commit with rollback recipe.** Validation runs against the whole payload first, returns all errors at once with no writes. The write phase runs every change through the existing `GraphAccessor` write methods with `commit=False`, then commits the entire batch in a single transaction. Any exception during writes or commit triggers `rollback()` and the batch is rejected with no partial state. The success report carries `rollback_additions` (a sorted list of delete-tool calls that would undo every addition) and `previous_values_for_in_place_edits` (pre-edit values for observation rewrites, cross_reference updates, node updates, and edge updates) so the user can manually restore any in-place edit via the existing per-call update tools.

To enable atomic batches, the 10 low-level write methods in `axiom_graph_db.py` (`add_node`, `add_alias`, `add_edge`, `add_condition`, `add_evidence`, `add_observation`, `set_cross_references`, `update_node`, `update_edge`, `update_observation`) gained a `commit=True` keyword parameter, and the matching 10 `GraphAccessor` methods plumb it through. Existing per-call callers see no behavior change; `apply_proposal` invokes everything with `commit=False`.

**Deletes:** `graph_delete_node` (cascades to incident edges and observations), `graph_delete_edge` (cascades to conditions and evidence), `graph_delete_alias`, `graph_delete_evidence`, `graph_delete_condition`, `graph_delete_observation`.

**Stats:** `graph_stats` (counts, type histograms, a derived species histogram over gene and protein nodes, top-cited papers, top-observed nodes).

Performance: warm startup ~3-4 seconds (model + embedding cache load); resident memory ~239 MB embedding matrix + ~440 MB model on RTX 2080 Ti; per-query latency ~30-200 ms depending on mode.

## LEXICON MCP server

11 tools, project-agnostic. All `lookup_*` tools return the same envelope: `found`, `query`, `canonical_name`, `suggested_node_type`, `aliases`, `summary`, `notes_prefix`, `cross_references`, `raw`, `_provenance`. Source-specific extras (UniProt PTMs and subcellular; PubChem formula/InChIKey; QuickGO aspect; DrugBank targets, MoA, indication, ATC, groups) populated where available.

| Tool | Source | Best for |
|---|---|---|
| `lexicon_lookup_gene` | MyGene.info | Genes (workhorse; aggregates IDs across sources) |
| `lexicon_lookup_hgnc` | HGNC | Legacy/previous-symbol normalization |
| `lexicon_lookup_protein` | UniProt | Protein-typed nodes; PTMs, subcellular |
| `lexicon_lookup_compound` | PubChem | Small molecules not in DrugBank |
| `lexicon_lookup_drug` | DrugBank (local SQLite) | Clinical drugs; targets, MoA, indication |
| `lexicon_find_drugs_by_target` | DrugBank (local SQLite) | Reverse-direction lookup: drugs targeting a given gene/UniProt |
| `lexicon_find_drugs_by_targets` | DrugBank (local SQLite) | Batched reverse lookup over many targets in one call |
| `lexicon_lookup_batch` | multiple | Batched `lookup_*` in one call (per-query hint: gene/protein/drug/compound/go_term) |
| `lexicon_lookup_go_term` | QuickGO | Process and compartment nodes |
| `lexicon_cache_stats` | local | HTTP cache observability |
| `lexicon_cache_clear` | local | HTTP cache reset (per-source or all) |

`notes_prefix` format is `[source source_id YYYY-MM-DD]: body`, designed to drop into a node's `notes` field with provenance visible at a glance. Empty body produces empty string (safe to use unconditionally).

`cross_references` is the structured machine-readable identity grounding (NCBI gene, Ensembl, UniProt, HGNC, OMIM, PubChem CID, GO ID, InChIKey, DrugBank ID, etc.) and lands in the dedicated `nodes.cross_references` JSON column. `notes` is for human-readable narrative; the two are populated together but consumed separately.

DrugBank specifics: served from local SQLite at `E:/bin/mcp/lexicon/data/drugbank.db`, built once by `load_drugbank.py` from DrugBank's full-database XML (academic license; user supplies `E:/data/drugbank/full_database.xml`). The loader pre-filters the XML at the byte level (strips `<reactions>`, `<drug-interactions>`, `<products>`, `<pathways>`, `<patents>`, `<dosages>`, etc.) before lxml.etree.iterparse streams the rest into SQLite. The pre-filter is the load-bearing step: without it, drugs with massive `<reactions>` blocks (e.g., DB03994 ethanolamine, ~105k lines) hang lxml entirely. Total runtime ~46 seconds on V5.1.

`lexicon_find_drugs_by_target` accepts gene symbol or UniProt accession (case-insensitive on both `drug_targets.gene_name` and `drug_targets.uniprot_id`); filters AND-combine on `organism_id` (default 9606 -> "Humans"; 0 disables; map covers 9606 / 10090 / 10116), `target_kind`, `action`, `groups`, `exclude_withdrawn` (default `True`). Action and groups filters apply in Python over parsed JSON lists; SQL handles only organism + target_kind. Results sort approved-first, then target_kind primacy (target -> enzyme -> transporter -> carrier), then alphabetical. The empty result for a TF target is itself meaningful and typically lands as a `node_observation` rather than a tool retry.

## Network control analyses

A read-only analysis layer over `axiom_graph.db` that mines the curated graph for actionable intervention targets using network control theory. It is separate from the extraction pipeline: it consumes the graph, never modifies it, and writes TSV artifacts plus a JSON build report into `Python/export/`. Everything runs from the AXIOM venv (adds `networkx` and `scipy`, both already resident; the reporting pass adds only standard-library `urllib`).

As of V16 the layer is multi-outcome. Where it previously routed everything to two organismal endpoints (organismal aging and maximum lifespan), it now targets the full curated outcome panel: those two anchors plus the twelve hallmarks of aging and a 35-node age-related-disease panel. The passes remain configured by node-id lists at the top of each script; the outcome taxonomy that groups those ids is centralized in `graph_common`.

**Shared foundation (`lib/graph_common.py`).** All analysis scripts import one module so the edge sign map, the outcome taxonomy, the breadth-floor policy, and the graph read have a single authoritative definition. It holds:

* `EDGE_SIGN`: the one editorial-judgment layer. Each edge type maps to +1 (subject increases object), -1 (decreases), or 0 (not determinable from the type alone). Borderline calls (`encodes`, `recruits`, `detoxifies`, `cleaves`, `catalyzes`, `displaces`) are marked REVIEW in-line. Changing a sign here changes every downstream result. Edge types absent from the map are treated as sign-indeterminate and warned about on stderr, so new edge types entering the graph surface immediately. All 29 edge types currently in the graph are mapped.
* **Outcome taxonomy.** `ANCHOR_OUTCOME_ID` (14, organismal aging, the outcome every lead is gated on), `SECONDARY_ANCHOR_ID` (171, maximum lifespan, reported but not gating), `HALLMARK_OUTCOME_IDS` (the twelve hallmark nodes), and `DISEASE_OUTCOME_IDS` (the 35-node disease panel). Membership is curated and explicit because `node_type` does not distinguish a hallmark process from a disease phenotype.
* **Breadth-floor policy.** A disease outcome is "well fed" when its direct in-degree (curated incoming edges) is at least `WELL_FED_INDEGREE_K` (currently 5). `breadth_floor_for(well_fed_count)` returns the disease-breadth floor a lead must clear: base 2, plus one per `BREADTH_FLOOR_STEP` (8) well-fed diseases, capped at `BREADTH_FLOOR_CAP` (6). This ties the lead bar to disease-side curation depth, so it ratchets only as diseases are genuinely wired in, not as the graph grows overall, and keeps "lead" comparable across builds. Six diseases are well fed at K=5 today, so the floor is 2.
* `load_graph_data(db_path)`: one read of `nodes` and `edges` into `{id: (name, type)}` and `[(subject, object, edge_type, sign)]`, plus an unmapped-type tally.
* Structure builders: `build_reverse_adj` (backward walk from a sink), `build_digraph` (networkx DiGraph, structure only), `resolve_pair_signs` (one sign per ordered pair; a real +/- conflict resolves to None, a neutral-only pair to 0), and `count_well_fed_diseases(edges)` for the breadth-floor input.
* `EXPORT_DIR = Python/export`. Outputs land here, deliberately not in `export_public/`, because analysis TSVs can carry DrugBank-derived node names verbatim.

**The five passes.**

* **`signed_path_net_effect.py`.** Backward signed-reachability from every outcome node (the two anchors, the twelve hallmarks, and the 35 diseases). For each upstream node it reports whether increasing it raises or lowers the outcome, as the product of edge signs along each path; any path crossing a neutral edge is reported indeterminate rather than guessed. A per-node `TARGET_POLARITY` map turns the raw sign into a recommended increase or decrease; polarity is set from each node's canonical semantics, so the ten damage-named hallmarks and every disease read -1 (less is better) while proteostasis and macroautophagy read +1 (the canonical name is the salutary state). Structural signed-reachability, not a simulation: direction is trustworthy, path-count reflects curation density not effect magnitude. Writes `signed_path_net_effect.tsv`, one row per (outcome, source) pair.
* **`cycle_analysis.py`.** The gating diagnostic. It decomposes the graph into strongly connected components: if every SCC is a trivial singleton the graph is a DAG apart from self-loops and the attractor / feedback-vertex-set family is formally degenerate; a multi-node feedback core means those methods apply, restricted to it. Each enumerated cycle is labeled positive (amplifying), negative (homeostatic), indeterminate (neutral edge), or conflicting (a pair carrying both signs) via `resolve_pair_signs`. Its `OUTCOME_NODE_IDS` annotation now spans the full panel, flagging for each outcome whether it sits inside a feedback core. Writes `cycle_analysis.tsv`.
* **`target_control.py`.** A structural target-control pass toward the outcome panel: restrict to the union of the targets' ancestor subgraphs, compute a Liu maximum-matching full-control reference, and report one matched-path-head driver per target, joined to the signed-path direction. Labeled throughout as a structural heuristic, not a paper-faithful Gao 2014 reconstruction and not a controllability guarantee. Writes `target_control.tsv`.
* **`feedback_control_targets.py`.** The intersection of feedback structure and direction, now judged across the whole outcome panel rather than a single outcome. It computes the exact minimum feedback vertex set over the core (an integer program via `scipy.optimize.milp` / HiGHS, rank formulation, so no cycle enumeration and no length cap), the minimum hitting set over the positive cycles (break amplifying loops while sparing the homeostatic brakes; this one inherits the length cap), and per-node positive-cycle participation as the stable centrality ranking. These structural objects are outcome-independent and computed once. It then joins the signed-path direction for every outcome and, per core node, derives a coherent action (increase, decrease, or none), the favorable outcome set under that action, separate hallmark-breadth and disease-breadth counts, and a strict cross-outcome conflict flag (a clean opposite recommendation across outcomes, not a mixed or indeterminate one). Central nodes are labeled `clean_leverage`, `cross_outcome_conflict`, or `central_ambiguous`. Writes two files: `feedback_control_targets.tsv` (node-level summary, with breadth, conflict, aging-favorability, and coherent action) and `feedback_direction_by_outcome.tsv` (long format, one row per node-outcome pair).
* **`build_report.py`.** The reporting pass, added in V15 and made multi-outcome in V16. Consumes `cycle_analysis.tsv`, `feedback_control_targets.tsv`, and `feedback_direction_by_outcome.tsv` (never `target_control.tsv`, whose driver identities are matching artifacts) plus the graph database, adds grounding metrics (degree normalization to flag single-file pass-through chokepoints, evidence depth, single-source dominance) and an external PubMed / Open Targets cross-check over the public HTTP APIs, and assigns each candidate a provisional lead / watch_item / curation_priority / cross_outcome_conflict / discard label. The lead gate is multi-outcome: a candidate must be central, cleanly favor the anchor outcome, cleanly favor at least `breadth_floor` distinct diseases, carry no cross-outcome conflict, and pass the evidence-depth, single-source, and external gates. The breadth floor is computed each build from the well-fed disease count via the `graph_common` policy and logged, alongside the count and the analyzable-outcome inventory, into an `outcomes` block in `build_report.json`. It reads no free-text fields, so the output is DrugBank-clean by construction. Writes `build_report.json` plus a dated archive under `export/build_reports/`. See the V16 and V15 change notes.

**Method discipline.** The analyses are structural over curated topology and edge signs, not dynamical simulations. Direction is the trustworthy signal; magnitude and path-count are not. Minimum FVS and hitting sets are non-unique, so participation is the stable ranker. Positive-cycle enumeration is length-capped (`MAX_CYCLE_LENGTH`, default 8), while the all-cycle minimum FVS is exact and uncapped. Breadth is measured against the analyzable outcomes (those with at least one reachable source in the current build); a disease with no reachable sources is a curation gap, not a negative finding. Hallmark breadth and disease breadth are reported separately, because the hallmarks are facets of aging and folding them into one count would restate aging-favorability as independent reach. The disease-breadth floor for a lead is not a fixed constant; it rises with disease-side curation depth via the breadth-floor policy. Exact integer programs use `scipy.optimize.milp`; structure uses `networkx`. Each script is config-driven via constants at its top and can be run directly.

**Runner.** `run_analysis.bat` activates the venv once and runs all five in order: signed_path first so the directional joins (in target_control and feedback_control_targets) read a fresh TSV, then cycle_analysis as the gating diagnostic, then target_control, then feedback_control_targets, and finally build_report to produce `build_report.json`. A non-zero exit from any pass stops the run. `run_feedback_control_targets.bat` runs the fourth pass alone.

## Current state

| | |
|---|---|
| Corpus markdown documents | 961 |
| Corpus chunks | 93,480 (~8% over 512-token cap; ~13% reference-section, filtered at retrieval) |
| Corpus PubTator entities | 4,243 |
| Books in corpus | 0 |
| Graph nodes | 783 (589 with cross_references) |
| Graph node aliases | 3,057 |
| Graph edges | 1,210 |
| Graph edge conditions | 1,421 |
| Graph edge evidence | 1,852 |
| Graph node observations | 1,301 |
| LEXICON DrugBank index | 19,871 drugs / 35,030 targets / 95,614 cross-references |
| AXIOM MCP tools | 33 |
| LEXICON MCP tools | 11 |

The graph has grown roughly ninefold in nodes since the V14 snapshot (91 nodes / 85 edges / 127 observations) and now spans well beyond the original progeria/UPR seed into senescence, glycation, NAD+ metabolism, immune surveillance, and, as of V16, an explicit panel of the twelve hallmarks of aging and 35 age-related diseases wired in as outcome nodes for the multi-outcome control analyses. Top node types by count: `gene` (316), `small_molecule` (158), `process` (120), `phenotype` (74), `protein` (40), `other` (25), `complex` (21), `condition` (16), `compartment` (6), `PTM_state` (5), `miRNA` (2). Top edge types: `suppresses` (348), `induces` (188), `promotes` (188), `contributes_to` (147), `activates` (77), `inhibits` (74), `part_of` (31), `binds` (27); the long tail covers `matures_to` (19), `causes` (17), `produces` (17), `degrades`, `cleaves`, `encodes`, `supports`, `transcribes`, `transports`, `synthesizes`, `increases`, `detoxifies`, `deacetylates`, `phosphorylates`, `recruits`, `catalyzes`, `regulates`, `s_nitrosylates`, `denitrosylates`, `displaces`, and `stabilizes`. All 29 edge types are mapped in `EDGE_SIGN`. The most-cited corpus source is "(2013) The Hallmarks of Aging.md" at 83 evidence rows, followed by "(2025) Immune surveillance of senescent cells in aging and disease.md" (43), "(2024) Mitophagy curtails cytosolic mtDNA-dependent activation of cGAS-STING inflammation during aging.md" (32), "(2023) Hallmarks of aging - An expanding universe.md" (29), and "(2009) Healing and Hurting - Molecular Mechanisms, Functions, and Pathologies of Cellular Senescence.md" (25). The most-observed node is `cellular senescence` (30 observations), followed by `carnosine` (25), `dietary glycation compound intake` (22), `maximum lifespan` (20), and a cluster at 18 (`organismal aging`, `progerin`, and others). 589 of 783 nodes carry cross_references; the disease and hallmark outcome nodes added for the analysis panel are largely unenriched so far, a near-term curation target.

## Recent change in V17: technical specialist report

V15 shipped a single funder-facing report: a short, lay-audience `report.md` that states one lead, written by `SOP_build_insights_report.md` from `build_report.json`. V17 adds a technical companion, `specialist_report.md`, written from the same JSON by a new sibling SOP, `SOP_build_specialist_report.md`, and published to the same place.

Where the insights report deliberately withholds most of what `build_report.json` contains (one lead, no counts, no jargon, methods behind a link), the specialist report surfaces the rest: the full ranked candidate set with every label and metric, the structural objects (the strongly connected feedback core, the exact minimum feedback vertex set, the positive-cycle hitting set, and participation as the stable ranker), the multi-outcome breadth and cross-outcome conflict signals stated against their analyzable denominators, the external cross-check, the build-over-build persistence, and a closing Discussion. The Discussion is the one place either report synthesizes rather than reports; it is bounded to what is derivable from `build_report.json`, introduces no biology the map does not already contain, and never crosses into interventional or dosage framing. The report is self-contained: every technical term, biological and network-theoretic alike, is defined on first use, with a glossary, for a reader with no prior knowledge of the project.

The sibling SOP reuses Steps 0 through 6 of `SOP_build_insights_report.md` verbatim (reading the JSON is identical) and diverges only on audience and voice, on what to include, on the sourcing guard, and on the verification pass. The sourcing guard is the load-bearing constraint: the prose derives only from canonical node names, the structural and grounding numbers, and the external counts, never from the `notes`, `observation`, or `edge_evidence` free-text fields. Because `build_report.json` is DrugBank-clean by construction, a report written from it alone drops into `export_public/` without passing the redaction filter, on the same basis as `report.md`.

Two pieces of wiring accompany the report. `AXIOM_Custom_Instructions.md` registers the new SOP and adds a combined report trigger: "Build the report" or "Build the reports", or any request for the report without naming which, fires both report SOPs in sequence, insights first so the single featured lead is settled, then specialist so its lead-in-depth section matches. `run_publish_website.bat` now copies `specialist_report.md` alongside `report.md` to the live Maypop Labs data folder, guarded the same way, so an absent specialist report is skipped rather than fatal.

Still deferred: the hallmark-by-disease influence matrix rendered as its own artifact. The specialist report summarizes breadth and conflict but does not render the full matrix.

## Earlier change in V16: multi-outcome control and a curation-scaled lead bar

V15's network-control layer routed every pass to two organismal endpoints, organismal aging and maximum lifespan. V16 widens the target set to the full curated outcome panel (the two anchors, the twelve hallmarks of aging, and a 35-node age-related-disease panel), and makes the feedback pass and the build report reason across all of them at once.

The mechanical change is small because the structural objects (feedback core, minimum FVS, positive-cycle hitting set, participation) are outcome-independent and computed once; only the direction join is per-outcome. `feedback_control_targets.py` now loads the signed direction for every outcome and, per core node, derives a coherent push direction, separate hallmark and disease breadth, and a strict cross-outcome conflict flag (a clean opposite recommendation across outcomes). Nodes that help some outcomes and harm others are labeled `cross_outcome_conflict`, a first-class output that captures the real geroscience tradeoffs (the cellular-senescence-and-cancer case) rather than a lead. It emits a node-level summary plus a long-format `feedback_direction_by_outcome.tsv`.

`build_report.py` gains the `cross_outcome_conflict` label and a multi-outcome lead gate: a lead must be central, cleanly favor organismal aging (the anchor), cleanly favor at least a breadth floor of distinct diseases with no conflict, and pass the existing grounding and external gates. Disease breadth is the translational signal and is featured in the lead; hallmark breadth is a secondary, mechanistic signal kept off the headline, because the hallmarks are facets of aging and would otherwise restate aging-favorability as independent reach.

The lead's disease-breadth floor is not a fixed number. It is computed each build from disease-side curation depth: a disease counts as "well fed" at a direct in-degree of `WELL_FED_INDEGREE_K` (5), and the floor is base 2 plus one per eight well-fed diseases, capped at six (`graph_common.breadth_floor_for`). Six diseases are well fed today, so the floor is 2; it ratchets only as more diseases are genuinely wired in, and both the well-fed count and the floor are logged in the `outcomes` block of `build_report.json` so the step is deliberate and visible across builds. `SOP_build_insights_report.md` documents the policy and adds a verification step that re-derives the floor and flags any build-over-build change, because a lead certified under a higher floor is a stronger claim than one under a lower floor.

The outcome taxonomy (anchor, secondary anchor, hallmark ids, disease ids) and the breadth-floor policy live in `graph_common.py` alongside `EDGE_SIGN`, so the multi-outcome definitions have one authoritative home. Polarity for each outcome is set in `signed_path_net_effect.py` from the node's canonical semantics; the two salutary-named hallmarks (proteostasis, macroautophagy) invert to +1 while the damage-named hallmarks and all diseases read -1.

## Earlier change in V15: public reporting and publishing pipeline

The network-control layer gained a fifth pass and a route to the public website.

`build_report.py` (pass 5 of `run_analysis.bat`) consumes the four analysis TSVs and the graph database, computes per-candidate grounding metrics (degree normalization to catch pass-through chokepoints, evidence depth, and single-source dominance), runs an external cross-check over the public PubMed (NCBI E-utilities) and Open Targets GraphQL APIs, assigns a provisional lead / watch_item / curation_priority / discard label, diffs against the prior build, and writes `build_report.json` plus a dated archive under `export/build_reports/`. It reads no free-text fields, so the JSON is DrugBank-clean by construction. The external calls use the public HTTP APIs directly (stdlib `urllib`), not the bio-research MCP connectors, so the automated pipeline needs no authorization; the script degrades cleanly to a JSON-only artifact if the network is unavailable and never promotes a candidate to `lead` without the external leg.

The prose report is a separate human-in-the-loop step, governed by `claude/SOP_build_insights_report.md`. A writer reads `build_report.json`, applies the judgment the script cannot (which single lead to feature, lay phrasing, honest caveats, the funding ask), and writes a short funder-facing `report.md` directly into `Python/export_public/`, overwriting the previous build's report. `run_publish_website.bat` then copies `report.md` alongside the redacted graph artifacts to the live Maypop Labs data folder; the `report.md` copy is guarded, so an absent report is skipped rather than fatal.

The split is deliberate. Everything mechanical (metrics, external counts, labels, persistence) is scripted and reproducible in the JSON; everything requiring judgment stays with the writer, who never sources a public sentence from `notes` or `observation` free text. The SOP encodes the two mistakes caught during its design (confirming the FVS against the hitting set, and re-counting any "only X" or "most Y" superlative) as a mandatory verification step, run as a subagent for public-facing builds.

## Earlier change in V14: bulk graph proposal tool

V13's GRAPH PROPOSAL protocol required one MCP tool call per write: a separate call for each new node, each new edge, each evidence record, each observation, each condition. A medium paper-extraction commit could easily run 20-40 round trips. V14 collapses that to one.

The new tool, `graph_apply_proposal(proposal: dict) -> dict`, accepts a single structured payload with up to 10 optional sections (see the AXIOM MCP server section) and applies the entire batch in one SQLite transaction. The other graph write tools (`graph_add_*`, `graph_update_*`, `graph_set_cross_references`) are unchanged and remain available for one-off small commits and for explicit visibility.

Three properties were not achievable with per-call writes:

1. **Forward references by `(canonical_name, node_type)`.** A new edge or observation can reference a node being created in the same payload by its name and type. The validator builds an in-payload index during the new_nodes pass and resolves refs against it before the DB, so a single round trip can express an entire sub-graph including its referenced nodes.
2. **Idempotent match-and-merge.** A new_node whose `(canonical_name, node_type)` already exists is matched, not duplicated. Existing aliases are preserved and new aliases appended; cross_references are merged with existing keys preserved and new keys added. A new_edge whose `(subject, object, edge_type)` already exists is matched and its payload conditions and evidence are appended. Each item carries a `created` vs `matched` result flag.
3. **Atomic two-phase commit with rollback metadata.** Validation runs against the whole payload first, reporting all errors at once with no writes (so the caller fixes everything in one pass). The write phase then runs each change through the existing `GraphAccessor` methods with `commit=False`, followed by a single `commit()` covering the batch. Any exception during writes or commit triggers `rollback()` and the batch is rejected with no partial state. The success report carries `rollback_additions` (a sorted list of delete-tool calls that would undo every addition) and `previous_values_for_in_place_edits` (pre-edit values for observation rewrites, cross_reference updates, and node/edge updates) so the user can manually restore any in-place edit through the existing per-call update tools if needed.

Enabling all of this required the 10 low-level write methods in `axiom_graph_db.py` (`add_node`, `add_alias`, `add_edge`, `add_condition`, `add_evidence`, `add_observation`, `set_cross_references`, `update_node`, `update_edge`, `update_observation`) and their matching `GraphAccessor` wrappers to gain a `commit=True` keyword parameter. Existing callers see no behavior change; `apply_proposal` calls everything with `commit=False` and commits once at the end.

The protocol implication: step 5 of the conversation protocol now collapses to one call. The proposal review step still operates on the same logical "GRAPH PROPOSAL" block; the difference is that approval flows into a single bulk commit rather than a sequence of writes. Tool count goes from 30 to 31.

## Tooling gaps surfaced this session

* **Bulk LEXICON / DrugBank lookups (shipped in V15).** `lexicon_lookup_batch(queries: [{query, hint?}])` and `lexicon_find_drugs_by_targets(targets, **filters)` now collapse the per-paper LEXICON pre-flight (formerly 5-15 single lookups) to 1-2 calls.
* **Bulk `graph_find_nodes` (shipped in V15).** `graph_find_nodes_batch(queries)` closes the symmetric per-paper name-lookup sequence against the curated graph.
* **No PMC full-text download tool.** Carried over from V13. The gap is between NCBI search (which yields PMIDs and PMC IDs) and the AXIOM Stage 01 pipeline (which consumes PDFs from the corpus inbox). A new `ncbi_download_pmc(pmid_or_pmcid, dest_dir)` would close it: hit `efetch.fcgi` with `db=pmc&rettype=full`, save NXML or PDF to a configured inbox, return the file path. Stages 01/03 then run on whatever lands there. Build only when corpus-expansion-via-conversation is wanted; manual download remains a viable alternative.
* **`generate_config.py` is overwrite-not-merge with no warning.** Carried over from V11 and V13; still true. Adding a `--check` mode that diffs JSON-only servers against the manifest would warn before overwriting.

## Pending

* `ncbi_download_pmc` (see Tooling gaps).
* Enrichment of the V16 outcome panel: most of the twelve hallmark and 35 disease outcome nodes still lack `cross_references`, and several diseases are wired too thinly (one to four incoming edges) to support disease-breadth claims. Deepening disease-side curation is what raises the computed breadth floor over time.
* `AXIOM_Custom_Instructions.md` and SOP updates to switch the proposal protocol to commit-by-default (with the `graph_apply_proposal` report standing in for the prior approve-then-commit round trip) and to require a "considered but skipped" section so Claude's self-curation during extraction is visible and correctable rather than invisible.
* Reference-section filter at chunk time (currently only at retrieval).
* Token-aware fallback splitter for the 8.25% over-cap chunks.
* `graph_path(node_a, node_b)` MCP tool when graph density makes multi-hop interesting.
* Sibling UPR-sensor expansion to the original V12 seed: PERK (`EIF2AK3`) and IRE1α (`ERN1`) as gene nodes, with their tool compounds (GSK2606414, ISRIB, 4μ8c) as small-molecule nodes and edges to ER UPR and cellular senescence. The same Pluquet 2015 review supplies loss-of-function evidence for IRE1α (DN-IRE1α, XBP1 siRNA) and PERK (GSK2606414) in HRas-driven senescence.

## Deferred

* **Books.** Scan a real book end to end before deciding on `--use_llm`, OpenLibrary metadata, chapter/subsection hierarchy, or `publisher`/`isbn` columns.
* **LEXICON Tier 2.** Ensembl REST, Reactome, STRING, Open Targets, KEGG, miRBase, InterPro.
* **FTS5 / BM25.** Current `keyword_search` ranks by raw whole-word count. BM25 via SQLite FTS5 on a virtual table would be the natural upgrade.
* **Hallmark-by-disease influence matrix as its own artifact.** The V17 specialist report surfaces the cross-outcome conflict set and the full candidate breadth in prose, but neither report renders the hallmark-by-disease influence matrix as a standalone artifact. That is deferred until the disease panel is curated deeply enough to carry it.
* **Public publication of curated graph snapshots** via Maypop Labs. Partially implemented in V15: `run_publish_website.bat` pushes the redacted graph artifacts plus the build-report prose (`report.md`) to the live site data folder. Remaining work is publication cadence and the public-facing presentation of successive builds.

## Design principles

1. **Curation is the moat.** Hand-selected corpus, hand-approved graph entries.
2. **Markdown is the source of truth.** The corpus index DB is rebuildable; the graph DB is not.
3. **No local LLMs.** Embeddings (sentence-transformers) and PDF layout (Marker/Surya) are deterministic. Generation goes through Claude.
4. **Hybrid retrieval, not pure semantic.** Embeddings catch concepts, keywords catch exact symbols, RRF fuses.
5. **Source-type-aware retrieval.** Books and journals get filtered separately when the question warrants.
6. **Citations are first-class.** APA on every retrieval, evidence row, observation, and LEXICON envelope.
7. **Grounding discipline.** Every claim that goes on the page is anchored to a citable source: corpus, LEXICON, or labeled background. Background knowledge is welcome; non-common-knowledge background gets the weakest-grounding flag so future readers can weight it appropriately.
8. **Curation discipline at the graph layer.** Every edge and observation traces to a citable source (corpus chunk, LEXICON return, or labeled background). No node without explicit user approval. Non-common-knowledge background and other weak or indirect evidence is flagged on the record, not absorbed into the next stronger source.
9. **Enrichment is separate from retrieval.** LEXICON is its own MCP, project-agnostic, with its own caches and lifecycle.
10. **Provenance survives the full chain.** Tagged `notes_prefix` for human-readable; `cross_references` JSON for machine-readable identity. They don't get conflated.
11. **The model can be swapped.** Architecture does not couple to a single LLM vendor.
12. **Stages are independent and idempotent.** Re-runs are safe. Failure in a later stage does not require redoing earlier ones.
13. **Coverage and observation_count are derived, not asserted.** No "confidence" column. Counts of independent evidence rows and observation rows fall out of the underlying tables.
14. **One Graph storage; corpus, background, and LEXICON are sources.** Node observations are a per-node attribute table inside the graph DB, not a parallel store.
15. **Source labeling on every grounding entry.** Corpus chunks, LEXICON returns, and labeled background are all valid grounding for graph writes. Provenance records the source type and identifier. Null results (e.g., empty LEXICON pharmacology for a TF target) belong in observations with the source labeled.
16. **The manifest is the source of truth for MCP registration.** `claude_desktop_config.json` is downstream of `servers.toml`. Direct JSON edits are time bombs.
17. **Bulk graph writes are atomic.** `graph_apply_proposal` either commits the entire batch or rolls back with no partial state. Validation reports all errors at once before any write happens. The per-call tools remain available for small commits and explicit visibility.
18. **Analysis is structural, and its judgment layers are explicit and shared.** The network-control passes read the graph and never write it. Edge signs, the outcome taxonomy, and the breadth-floor policy are curator-controlled definitions in `graph_common.py`, so a change lands in one place and propagates to every pass. Direction is trusted; magnitude and path-count are not. Thresholds that gate a public "lead" scale with curation depth rather than being asserted.

## Tech stack

Python 3.x. Marker (PDF -> markdown via vision OCR). bge-base-en-v1.5 (sentence-transformers, 768 dim). SQLite (corpus index, graph, LEXICON cache, DrugBank index). pypdf (Stage 02 page-count classifier). lxml (DrugBank XML parser; iterparse with byte-level pre-filter). httpx async (LEXICON HTTP clients, NCBI E-utilities client). FastMCP via the `mcp` Python SDK (all servers). networkx + scipy (network-control analyses). cytoscape.js 3.30 (graph viewer, CDN-loaded). Hardware: RTX 2080 Ti, 11 GB VRAM.

## Directory layout

```
E:/data/literature/{pdf,markdown}/    corpus (papers and books co-located)
E:/data/drugbank/full_database.xml    DrugBank source XML

E:/bin/axiom/                         AXIOM project root
  Python/
    01_pdf_to_markdown.py             Stage 01
    02_PMID_lookup.py                 Stage 02 (with book classifier)
    03_chunk_and_embed.py             Stage 03
    04_graph_export.py                Stage 04
    signed_path_net_effect.py         network analysis: signed-path net effect
    cycle_analysis.py                 network analysis: feedback / SCC census
    target_control.py                 network analysis: structural target control
    feedback_control_targets.py       network analysis: FVS + multi-outcome direction
    build_report.py                   network analysis: JSON build report (pass 5)
    export/                           network-analysis TSVs + build_report.json
      feedback_direction_by_outcome.tsv  long-format (node, outcome) directions
      build_reports/                  dated build_report.json archives
    export_public/                    redacted, publishable artifacts + report.md + specialist_report.md
    venv/                             AXIOM-owned venv
    templates/viewer.html             cytoscape.js viewer template
    lib/
      axiom_db.py                     corpus index DB layer
      axiom_graph_db.py               graph DB layer (write methods accept commit=)
      graph_common.py                 network-analysis shared layer (EDGE_SIGN,
                                      outcome taxonomy, breadth-floor policy, readers)
      pubmed.py, pubtator.py          API clients
      data/{axiom.db, axiom_graph.db}
  mcp/
    server.py                         AXIOM MCP entry (33 tools)
    retrieval.py                      AxiomRetriever (embedding cache + search + RRF)
    graph.py                          GraphAccessor (graph DB + cross-DB enrichment;
                                      hosts apply_proposal and its validators)
    species.py                        derive_species (gene/protein species from cross_references)
  claude/                             versioned project overviews + SOPs
  run_0X_*.bat                        convenience launchers
  run_analysis.bat                    analysis + report launcher (5 passes)
  run_feedback_control_targets.bat    standalone launcher for the 4th pass
  run_publish_website.bat             publish redacted artifacts + report.md to site

E:/bin/mcp/                           generic MCP collection root
  servers.toml                        manifest (source of truth)
  generate_config.py                  manifest -> claude_desktop_config.json (overwrite)
  .venv/                              shared venv for standalone servers
  filesystem/                         standalone
  ncbi/                               standalone (PubMed search/fetch/DOI/citation)
  lexicon/                            standalone (project-agnostic)
    server.py, base.py, cache.py
    mygene.py, hgnc.py, uniprot.py, pubchem.py, quickgo.py, drugbank.py
    load_drugbank.py
    data/{cache.db, drugbank.db, drugbank_filtered.xml}
```
