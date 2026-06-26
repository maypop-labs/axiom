# SOP: Edge audit



## Purpose



Standard procedure for reviewing the support for a single curated edge against the current corpus. Re-resolves each evidence chunk, validates conditions, cross-checks cell_system, identifies missing observations and identity gaps on endpoint nodes, and surfaces additional supporting or contradicting chunks in the broader corpus. Output is an audit report ending in a GRAPH PROPOSAL for the additive changes (evidence, conditions, observations, aliases, cross_references) that the current MCP supports, plus a separate Manual-handling section for subtractive changes that require AXIOM Developer.



## Trigger



Invoked explicitly by the user. Examples:

- "Audit the X --activates--> Y edge"

- "Run an edge audit on edge_id 7"

- "Is the support for X to Y still solid?"



Multi-edge audits (all edges between a pair, all edges incident on a node) are just iteration of this SOP, one edge at a time.



## Step 0: Identify the edge



Resolve the user's reference to a single edge_id:

- Triple given (subject, edge_type, object): `graph_find_nodes` for each endpoint, then `graph_get_edges` filtered by edge_type to pin the edge_id.

- edge_id given: proceed directly.

- Loose reference ("the YAP edge"): list candidates via `graph_get_edges` on the matched endpoint and ask the user to pick. Do not guess.



State edge identity in one line at the top of the response: subject --edge_type--> object (edge_id N).



## Step 1: Pull edge context



1. `graph_get_edge(edge_id)` for full edge content: metadata, evidence rows, conditions.

2. `graph_get_node` for both endpoints; note observation_count, aliases, cross_references.

3. `graph_get_observations` for both endpoints; note any observations relevant to the edge.



Establish the baseline:

- Coverage (count of evidence rows).

- Conditions list (condition_type, condition_value pairs).

- Per-evidence cell_system distribution.

- Endpoint observation counts and cross_references status.



## Step 2: Re-resolve each evidence chunk



For every evidence row, retrieve current content via `get_chunk(chunk_id)` and categorize:



- **Holds.** Chunk still supports the claim as recorded, including cell_system.

- **Holds with refinement.** Chunk supports the claim but at narrower or broader scope than the recorded conditions imply.

- **Does not hold.** Chunk no longer supports the claim. Possible causes: re-chunking shifted content, original interpretation was off, content drift.

- **Unresolvable.** chunk_id does not resolve to current content.



Each row gets one category and a one-line note. Do not silently skip unresolvable rows; flag them.



## Step 3: Validate conditions



For each `edge_conditions` row:

- Is the condition supported by at least one evidence chunk?

- Conversely, do evidence chunks consistently imply a condition not currently recorded? Example: all evidence is in a specific cell context but no `cell_type` condition exists. These become candidate new conditions via `graph_add_condition`.



Distinguish conditions (when the edge holds) from cell_system (where it was demonstrated). "In HRas-driven OIS" is a condition; "in IMR-90 fibroblasts" is cell_system on the evidence row. Do not promote one into the other during the audit.



Unsupported conditions are removal candidates and go to the Manual-handling section (see deferred-tools note).



## Step 4: Identify missing observations and identity gaps



Walk the evidence chunks. For each:

- Does it ground a per-entity finding about subject or object that is not currently recorded as an observation on that node? Examples: subcellular localization, expression context, phosphorylation state, induced phenotype. Candidate observations via `graph_add_observation`, each tracing to a chunk_id.

- Does it use a name for either endpoint that is not currently an alias on the node? Candidate aliases via `graph_add_alias`.



Additionally, check endpoint nodes for missing `cross_references`. For any endpoint without them, run the appropriate `lexicon_lookup_*` tool and propose `graph_set_cross_references` if a hit is found.



## Step 5: Broader corpus cross-check



Two bounded searches against the full corpus:



1. **Confirming evidence.** `hybrid_search` on (subject, object) terms plus the relationship verb when specific. Filter out chunk_ids already in evidence. Remaining results are candidate new evidence via `graph_add_evidence`.

2. **Contradicting evidence.** Same query intent but scan for chunks asserting the opposite or a scope-incompatible claim. Surface contradictions explicitly; do not silently suppress.



Bound to top-N per search, where N is small (5 to 10). Goal is sanity check, not exhaustive mining.



## Step 6: Compose the audit report



Section order in the response:



1. **Edge identity.** subject --edge_type--> object (edge_id), coverage, conditions, endpoint observation_counts and cross_references status.

2. **Evidence row status.** Per-row category from Step 2, with chunk_id, cell_system, and one-line note.

3. **Conditions assessment.** Supported, unsupported, implied-but-not-recorded.

4. **Missing observations and identity gaps.** Per-endpoint candidate observations, candidate aliases, missing cross_references.

5. **Confirming evidence in corpus.** Candidate new evidence rows from Step 5, with citations.

6. **Contradictions in corpus.** Chunks that conflict with the edge as recorded, with citations and one-line descriptions.

7. **GRAPH PROPOSAL.** Additive changes only: new evidence, new conditions, new observations, new aliases, new cross_references. Per the format in the project's custom instructions.

8. **Manual-handling items.** Subtractive corrections (evidence rows that no longer hold, conditions to drop) listed with chunk_ids or condition tuples and one-line reasons; user resolves via AXIOM Developer until deletion MCP tools land.



## Deferred-tools note



The current AXIOM MCP has `graph_delete_edge` and `graph_delete_node` (both cascading), but lacks `graph_delete_evidence`, `graph_delete_condition`, and `graph_delete_observation`. The DB methods exist; the MCP wrapping is in the Pending/Deferred backlog.



This SOP proposes additive corrections via existing MCP tools. Subtractive corrections live in the Manual-handling section until those tools land.



If the audit determines the entire edge is unsound (most or all evidence in the "does not hold" category, no salvageable interpretation, no confirming evidence in broader corpus), the appropriate action is `graph_delete_edge`. Include this as a GRAPH PROPOSAL item but flag it explicitly because of the cascade to conditions and evidence.



## Failure modes



- **edge_id not found.** Stop and flag.

- **Endpoint nodes missing.** Schema invariant violated; stop and flag for AXIOM Developer.

- **Evidence chunk_id does not resolve.** Category is "unresolvable"; do not silently skip.

- **All evidence holds, no missing items, no contradictions.** Audit report is brief; GRAPH PROPOSAL may be empty or limited to cross_references additions. This is the success case and is reported as such.

- **High-volume contradictions surfaced.** If Step 5's contradicting search returns many chunks, stop short of full triage and recommend a deeper review session. The edge may need a substantive rethink rather than a patch.



## Stop conditions



One pass through evidence (Step 2), one pass through conditions (Step 3), one pass for missing observations and identity gaps (Step 4), one bounded corpus cross-check (Step 5). Compose and propose. Do not iterate on broader searches; the audit is a checkpoint, not exhaustive corpus mining.



## Out of scope



- Pipeline stage runs.

- Multiple edges in a single invocation (iterate one at a time).

- Node-centric reviews (use a connections dossier instead).

- Subtractive MCP writes (deferred).

- Piecemeal graph writes during the audit. The commit happens once at SOP completion via graph_apply_proposal per the custom-instructions conversation protocol.

