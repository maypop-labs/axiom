# SOP: Connections dossier



## Purpose



Standard procedure for mechanistic-connection questions. Assembles what is known from the graph, what the corpus says, what LEXICON grounds, what background knowledge contributes, and optionally what PubMed offers as next reading. Output is a dossier with strict source demarcation, ending in a GRAPH PROPOSAL block.



## Trigger



Invoked explicitly by the user. Examples:

- "Run a connections dossier on X" (entity-centric)

- "Run a connections dossier on X and Y" (pair-centric)

- "What do I know about X's role in Y?"

- "What's the evidence for X to Y?"



Do not auto-invoke. Mechanistic questions outside this trigger get a normal answer per the conversation protocol.



## Step 0: Scope



Before retrieval, settle these explicitly and state them in one line at the top of the dossier:



1. **Entity-centric or pair-centric?** Single entity (general connections) versus a specific pair.

2. **Knowledge mode or literature mode?**

&#x20;  - Knowledge mode ("what do I know", "what's in the graph"): graph-first ordering.

&#x20;  - Literature mode ("what does the literature say", "what's published"): corpus-first ordering.

&#x20;  - Ambiguous: default to corpus-first and state the assumption.

3. **Filters?** year range, source_type, tissue or condition restriction.



## Step 1: Primary retrieval



### Graph-first variant (knowledge mode)



1. `graph_find_nodes` on X (and Y if pair-centric); try common aliases.

2. For each match: `graph_get_node`, `graph_get_observations`, `graph_neighbors` at depth 1.

3. Pair-centric: `graph_get_edges` between matched node IDs.



### Corpus-first variant (literature mode)



1. `hybrid_search` on the entity name (or both names for pair-centric).

2. Apply year and source_type filters from scope.

3. For top-N relevant hits: `get_chunk` for full content when the snippet is insufficient.



## Step 2: Cross-pass



Run the other source from Step 1.



If Step 1 was graph-first: corpus pass via `hybrid_search` to verify and extend. For each returned chunk_id, check whether it already appears in graph evidence and flag as already-cited versus novel.



If Step 1 was corpus-first: graph pass via `graph_find_nodes` for every entity surfaced in the corpus chunks. Pull observations and neighbors for matches. This tells the user what is already curated in this neighborhood.



## Step 3: LEXICON enrichment



For new candidate entities (those not matched in graph):

- Genes: `lexicon_lookup_gene`. Fall back to `lexicon_lookup_hgnc` for legacy symbols.

- Proteins: `lexicon_lookup_protein`.

- Small molecules: `lexicon_lookup_drug` first; `lexicon_lookup_compound` on miss.

- GO terms (process or compartment nodes): `lexicon_lookup_go_term`.



For X if it is a target and pharmacology relevance is plausible: `lexicon_find_drugs_by_target`. An empty result is a meaningful negative finding to record as an observation, not a tool failure.



For existing graph nodes that lack `cross_references`: consider proposing an update from LEXICON results.



## Step 4: PubMed gap-fill (optional)



Run only if explicitly invited or if Steps 1 and 2 returned thin coverage relative to the question.



`ncbi_search` for relevant terms. Surface PMIDs with one-line summaries and PMC IDs where present. Tag the section as candidate corpus additions. Do not download or ingest. This is human triage; ingestion belongs in AXIOM Developer once `ncbi_download_pmc` exists.



## Step 5: Compose the dossier



Section order in the response:



1. **Scope.** One line: question, entity-centric or pair-centric, knowledge or literature mode, filters applied.

2. **Graph state.** Matched nodes with `observation_count`, relevant edges with coverage, edge_type, and conditions, existing observations paraphrased. If empty, say so explicitly.

3. **Corpus findings.** Mechanistic claims grounded in retrieved chunks. Each with APA citation and chunk_id. Flag overlap with existing graph evidence versus novel.

4. **LEXICON identity.** `notes_prefix` and `cross_references` for new candidates, plus any null findings worth recording.

5. **Background knowledge.** Claude's own, clearly labeled. Common-knowledge biomedical facts (textbook-grade, multiple independent sources agree) can license proposals. Non-common background is allowed but flagged on the record as weakest grounding.

6. **Candidate next papers.** Only if Step 4 ran. PMIDs with summaries.

7. **GRAPH PROPOSAL.** Per the format in the project's custom instructions.



## Failure modes



- **Empty corpus retrieval.** State explicitly. Offer one retry with broadened terms before escalating to PubMed.

- **Empty graph retrieval.** State explicitly. All candidates are new; LEXICON enrichment becomes the identity-grounding step rather than a follow-up.

- **Corpus and graph conflict.** Surface in the dossier with both positions cited. Suggest the edge-audit SOP if resolution is warranted.

- **Corpus and background knowledge conflict.** Corpus wins on the page; flag the discrepancy in the Background section.

- **Reference-list chunks leak through `exclude_references = True`.** Skip silently unless the user is auditing references.



## Stop conditions



Stop after one cross-pass plus enrichment plus (if invited) one PubMed pass. Compose and propose. Do not recurse on broader search terms past the single retry budget. If the question still cannot be answered, say so.



## Out of scope



- Piecemeal graph writes during the dossier. The commit happens once at SOP completion via graph_apply_proposal per the custom-instructions conversation protocol.

- Pipeline stage runs.

- Paper ingestion. PubMed candidates are listed for human triage only.

