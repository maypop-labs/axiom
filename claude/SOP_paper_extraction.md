# SOP: Paper extraction



## Purpose



Standard procedure for proposing graph additions from a single newly-added corpus paper. Inventories the paper, identifies mechanistic claims, pre-flights entities against the graph, enriches new candidates via LEXICON, and emits a GRAPH PROPOSAL grouped by chunk so each proposed addition is auditable against its grounding text.



## Trigger



Invoked explicitly by the user. Examples:

- "I just added [paper]. What should land in the graph?"

- "Run paper extraction on [paper title or filename]"

- "Walk me through what's graph-worthy in [paper]"



The paper must already be ingested through Stage 03 (chunked and embedded). If chunks do not exist, stop and direct to AXIOM Developer; do not attempt extraction on a paper without chunks.



## Step 0: Identify the paper and confirm ingestion



Resolve the user's reference to a source_id:

- Filename or title: narrow `keyword_search` or `hybrid_search`.

- PMID: search by PMID.



Confirm:

- `get_source` returns the source row (title, year, authors, source_type, metadata_source).

- `get_source_chunks` returns at least one chunk; otherwise stop and flag.



If `source_type = book`, note that PubTator entities are absent and the chunk inventory will likely be larger; otherwise proceed normally.



**Metadata trust check.** Stage 02 resolves bibliography by PubMed title search and accepts the top hit with no confirmation that the returned record is the same paper. A sibling (same year, same issue, similar title) can be written into the sources row. The chunk text stays correct; only the bibliography is wrong. Because graph evidence joins chunk_id to source, every citation resolving through a bad row renders as the wrong paper. Verify the row before extracting against it.

Run after get_source, before stating paper identity:

1. Scope by metadata_source.
   - pubmed: at risk. Run the full check.
   - manual: already vetted. Note and proceed.
   - filename: PubMed miss, no derived bibliography to be wrong. Citations are filename-grade. Note and proceed.
2. Filename title is ground truth. Stage 02 never renames the file. Parse the filename with the "(YYYY) Title.md" convention (year, title) and compare against stored title and year. Material divergence from sources.title is the primary tell.
3. DOI confirmation when available. If the markdown prints a DOI (check the first one or two chunks via get_chunk), compare it to sources.doi. A DOI mismatch is decisive.
4. Author sanity. If the first chunk carries an author line, it should be consistent with sources.authors.

If the anchors agree, proceed to Step 1 as normal.

If the filename title and stored title diverge materially, or DOI or authors disagree, treat the row as a suspected sibling hit. Stop normal extraction and go to the "Correction: sibling-hit metadata mismatch" section below. Do not extract graph content against a source whose bibliography is unconfirmed, because every evidence row inherits the wrong citation.

State paper identity in one line at the top of the response.



## Step 1: Inventory



Build a structured inventory before extraction:



1. `get_source_chunks` for the full chunk list with sections.

2. Group chunks by section. Mechanistically substantive sections are typically Results, Discussion, and Conclusion; Introduction for context-setting claims; Methods rarely useful for graph content.

3. `find_entity` if PubTator entities exist; surface the NER inventory as an entity hint list, not as a substitute for chunk-level reading.

4. Estimate scope: total chunks, approximate count of substantive chunks, paper type (primary research, review, methods, perspective).



Present a brief inventory summary before deep-diving:

- Paper identity, year, source_type, paper type.

- Section breakdown: chunk counts per section.

- Substantive section list.

- PubTator entity types and counts if present.



## Step 2: Pass-one triage (optional checkpoint)



For papers with many substantive chunks (rough threshold: >15), surface the substantive-chunk inventory with one-line summaries and ask the user which to deep-dive on. For shorter or focused papers, proceed directly to Step 3.



The default budget is full extraction across all substantive chunks. The triage step exists to let the user scope down on long papers without forcing the full sweep.



## Step 3: Mechanistic claim identification



Walk substantive chunks. For each, identify candidate claims:



- **Relational claims** (subject, relationship, object): "X activates Y", "loss of A increases B", "C binds D". Map to candidate edges.

- **Per-entity findings** (no second entity required): "X is upregulated in senescence", "Y localizes to the ER under stress". Map to candidate node observations.

- **Conditions and cell_system**: extract scope. "In HRas-driven OIS in melanocytes" is a condition (when the edge holds); "demonstrated in IMR-90 fibroblasts" is a cell_system anchored on evidence (where it was shown).

- **Negative findings**: "X does not affect Y", "loss of A fails to rescue B". Often valuable as observations or condition-scoped negative edges.



Distinguish what the paper itself shows from what it cites. When the paper demonstrates a finding from its own data, the chunk grounds the proposal directly. When the paper makes a claim that carries an inline citation to another work, the chunk still grounds the proposal under V13's broadened policy; record the inline-cited reference in the evidence note as upstream provenance.



For review papers, bias toward author framing and synthetic conclusions. Cited mechanisms in a review can be promoted to the graph if the chunk asserts them clearly; record the inline citation as upstream provenance. When the upstream paper is itself in the corpus, prefer retrieving and grounding directly from it.

Catalog and dossier-style papers (reviews or meta-analyses that enumerate many genes or proteins with a documented role in aging or age-related disease, each with a manipulation-to-phenotype fact) are a distinct case. Here the per-entity enumeration is the graph-worthy content, not a reproduction risk: promote each named gene as a node plus its manipulation-to-phenotype fact as an edge or observation, with the inline citation as upstream provenance. This is the gene dossier the project wants (custom-instructions Purpose and guideline 2), and it legitimately produces a high entry count from one paper. What is not promoted from such a paper, logged in Considered-but-skipped with the matching reason: (a) a bulk list referenced but not actually present in the retrieved chunks (a supplement table not in the corpus text cannot be grounded); (b) aggregate set-level statistics with no single intervention point (for example, "N% of inputs change expression with age"); (c) biomarker-overlap or model-comparison methodology. A named gene carrying a clean manipulation-to-phenotype fact is never skipped for volume.



## Step 4: Entity resolution



For every entity surfaced as candidate subject or object:



1. `graph_find_nodes` with the canonical name and any aliases visible in the chunk.

2. For matches: `graph_get_node`, `graph_get_observations`. Note observation_count and any conflicts with the new claim.

3. For non-matches: hold for LEXICON enrichment in Step 5.



Cross-reference the PubTator NER inventory from Step 1 against the candidate list. PubTator's normalization may suggest an alternate canonical name or alias worth searching.



## Step 5: LEXICON enrichment



For each new candidate entity:

- Genes: `lexicon_lookup_gene`. Fall back to `lexicon_lookup_hgnc` for legacy symbols.

- Proteins: `lexicon_lookup_protein`.

- Small molecules: `lexicon_lookup_drug` first; `lexicon_lookup_compound` on miss.

- GO terms (process, compartment): `lexicon_lookup_go_term`.



For graph-matched targets where pharmacology relevance is plausible: `lexicon_find_drugs_by_target`. Empty results for non-druggable targets (transcription factors, scaffolds) are themselves observations to record.



For matched nodes lacking cross_references, propose an update if LEXICON returns identifiers.

DrugBank-derived returns (`lexicon_lookup_drug` prose, `lexicon_find_drugs_by_target` relationships) carry export-compliance obligations; see DrugBank attribution suffix below before writing any of that content into a field.



## DrugBank attribution suffix (export compliance)

DrugBank is licensed for internal research only; its curated content cannot be redistributed in a public export. Any text written into a node, edge, observation, or evidence field that reproduces DrugBank-restricted content gets the literal suffix ` [Source: DrugBank]` appended at curation time. This suffix is the signal the Stage 04 redactor keys on (`04_graph_export.py --redact-drugbank`): tagged text is stripped from the public `.graphml`, `.json`, and `.tsv` artifacts while the curated DB keeps the full text. Tagging at write time is load-bearing, because the redactor cannot infer DrugBank provenance from free text; the accession-id and `[drugbank ...]`-prefix detector is only a backstop for text that happens to carry those signals.

Suffix this (DrugBank-restricted content):
- Verbatim or near-verbatim DrugBank prose copied from a `lexicon_lookup_drug` return: mechanism-of-action, pharmacodynamics, indication, or description text.
- DrugBank-curated relational data: a specific drug-target relationship, a list of drugs from `lexicon_find_drugs_by_target`, an action annotation (inhibitor, activator, modulator, and so on), a regulatory-group classification (approved, investigational, experimental, withdrawn, nutraceutical), an ATC code, a DrugBank accession (`DB#####`), or a target id (`BE#######`).
- A field where DrugBank content is only one clause inside otherwise-original curation still gets the suffix. The redactor drops the whole tagged field from the export today, so keep DrugBank-derived clauses out of fields whose surrounding curation you want to survive export, or accept wholesale redaction of that field.

Do not suffix this:
- Negative findings: "no drugs in DrugBank", "undrugged", "zero hits", "null find_drugs_by_target". These assert absence and reproduce nothing.
- Bare attribution naming DrugBank as the source without reproducing its data: "DrugBank-curated drug-target relationship", "grounding anchors on DrugBank's drug_targets record".
- General characterizations that name no specific datum: "these enzymes have inhibitors in DrugBank" with no drug, action, or id given.
- Plain drug names (brand or generic) used in original prose. The accession is DrugBank's identifier; the common name is not.

Placement and idempotence: append the suffix at the end of the offending text, and never double-tag text that already ends in ` [Source: DrugBank]`. While the redactor tags at field granularity, the suffix goes at the end of the field; if clause-level redaction is added later, it moves to the end of the clause.

Provenance is unchanged. The suffix is an export-compliance marker, not provenance. `grounding_type` stays `lexicon` and `provenance_extra` keeps the DrugBank source and identifier as before; those remain how the row records DrugBank as its source.



## Step 6: Edge candidates



For each (subject, edge_type, object) tuple from Step 3 with both entities resolved or proposed as new:



1. `graph_get_edges` between matched node IDs (when both already exist).

2. If an edge of the same edge_type exists: this paper supplies new evidence for an existing edge. Extract conditions and cell_system; add to the proposal as additions to existing edges.

3. If no matching edge exists: candidate new edge.

4. A different edge_type between the same nodes is a separate edge. Do not merge.



## Step 7: Compose the proposal



Section order in the response:



1. **Paper identity** — title, year, source_type, source_id, total chunks, sections deep-dived.

2. **Existing matches** — graph nodes mentioned with observation_count.

3. **New nodes** — canonical_name, node_type, aliases, cross_references, notes_prefix.

4. **New edges** — subject, edge_type, object, conditions, per-evidence chunk citation.

5. **Additions to existing edges** — edge_id, new evidence rows, coverage delta.

6. **New observations** — target node, paraphrased observation, chunk citation.

7. **Cross-reference additions on existing nodes**.

8. **Conflicts with existing graph** — if any. Edge contradictions, observation contradictions; suggest edge-audit SOP for resolution.

9. **Considered but skipped**. Required if Step 3 identified any candidate claim that was not promoted. Per claim: the chunk_id, the candidate claim in one line, and a one-line reason. Use only the valid reasons from guideline 6 of the custom instructions (fails guideline 1, fails guideline 2, fails QG1, handoff to SOP_entity_reconciliation, handoff to SOP_edge_audit). If skipping for an invalid reason ("enough proposed already", "will come up in a future paper", "low confidence" without grounding rationale) is tempting, promote the claim instead and let the user filter at review.



Group items 4-6 by source chunk where possible. Every proposed item must trace to one or more specific chunk_ids.

Apply the DrugBank attribution suffix (see the dedicated section after Step 5) to any composed node, edge, observation, or evidence text that reproduces DrugBank-restricted content, before the proposal is committed.



## Idempotence



Re-running this SOP on a paper already extracted should surface mostly Existing matches with high observation_count and largely empty New sections. This is correct behavior, and it is also useful: re-running after the graph has grown elsewhere can catch newly-relevant cross-references and edges that previously had no other endpoint in the graph.



## Correction: sibling-hit metadata mismatch

Trigger: the Step 0 metadata trust check flags a sources row whose bibliography does not match the paper the markdown actually is.

1. Establish true identity from the markdown, not the stored row: filename title and year, plus any DOI or PMID printed in the body (get_chunk on the first one or two chunks).
2. Resolve the authoritative record.
   - DOI present in markdown: ncbi_lookup_doi on it.
   - Otherwise: ncbi_search by the filename title (year as narrower), confirm the returned title matches the paper, then ncbi_fetch for the full record.
   - ncbi_citation for the formatted citation.
   Confirm the resolved record is the same paper before proposing any write. If NCBI returns nothing confident, stop and hand to Neil. A filename-only row is better than a confidently wrong one.
3. Present the correction for confirmation. Show source_id, filename, the wrong stored values, and the corrected values for title, authors, journal, year, volume, issue, pages, doi, pmid, citation_apa. This is a corpus-metadata write, separate from any GRAPH PROPOSAL, and it rewrites every citation resolving through this source, so it gets explicit confirmation before apply.
4. On confirmation, apply via filesystem:sqlite_query against E:/bin/axiom/Python/lib/data/axiom.db, with the confirmed values inlined into the statement and metadata_source set to manual:

   ```sql
   UPDATE sources
   SET title = ..., authors = ..., journal = ..., year = ..., volume = ...,
       issue = ..., pages = ..., doi = ..., pmid = ..., citation_apa = ...,
       metadata_source = 'manual', updated_at = CURRENT_TIMESTAMP
   WHERE id = <source_id>;
   ```

5. Re-confirm with get_source that the row now matches, then resume from Step 1.

Note: metadata_source = 'manual' only protects the correction if Stage 02 skips manual rows on re-run. Confirm Stage 02 honors that skip; if it does not, a future re-run can re-introduce the sibling hit. Pipeline concern, fix belongs in Stage 02.

Known-good fixture: source_id 894, "(2022) Advanced Glycation End Products in Health and Disease.md". Correct: Reddy VP, Aryal P, Darkwah EK; Microorganisms 2022, 10(9), 1848; doi 10.3390/microorganisms10091848; pmid 36144449. Stage 02 had written Rabbani N, Thornalley PJ (doi 10.3390/ijms232113053, pmid 36361833), a different 2022 glycation paper. Already corrected and set to manual; use to validate the check.

## Failure modes



- **Paper not in corpus.** Stop and direct to AXIOM Developer for ingestion.

- **Chunks missing or empty.** Stop and flag; likely Stage 03 did not complete for this source.

- **No PubTator entities (book or PubMed-miss paper).** Proceed without the NER hint list.

- **Paper contradicts existing graph.** Surface in the Conflicts section. Recommend edge-audit SOP if resolution is needed before commit.

- **Review paper with high citation density.** Bias toward author framing. Cited mechanisms can be promoted under V13's broadened policy with the inline citation as upstream provenance, but prefer retrieving from the upstream paper directly when it is in the corpus.

- **Massive paper or book chapter.** Use the Step 2 triage checkpoint to scope down.



## Stop conditions



Complete one full pass across substantive chunks (or the user-scoped subset from Step 2). Compose and propose. Do not loop back to broaden retrieval; if entities or claims are unclear, leave them in "Skipped or unclear claims" for user direction.



## Out of scope



- Pipeline stage runs.

- Re-ingestion.

- Multi-paper synthesis or comparison.

- Updates to other papers' evidence rows, even if this paper would have provided better grounding.

- Piecemeal graph writes during the SOP. The commit happens once at SOP completion via graph_apply_proposal per the custom-instructions conversation protocol.

## Scope (age-related disease papers)

A paper being framed around a specific age-related disease is not grounds for rejection; insight into age-related disease is insight into aging. If Neil hands a disease paper over for processing, it is in scope. Default-reject applies only to content with no mechanism and no intervention. Curation judgment continues to operate on grounding, mechanism/intervention presence, duplication (guideline 1), and edge-subject correctness, never on disease framing. Any residual scope reservation is recorded inside the GRAPH PROPOSAL (condition, observation, or Considered-but-skipped note), never raised as a blocking question before extraction.

