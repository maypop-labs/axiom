# SOP: Entity reconciliation



## Purpose



Standard procedure for resolving ambiguous entity identity: deciding whether a candidate entity is already in the graph as an existing node, and if so, what (if any) updates are needed (alias, cross_references, canonical_name change), or whether it warrants a new node. Also handles detection of suspected duplicates already in the graph.



## Trigger



Invoked explicitly by the user, or as a sub-procedure called from another SOP when entity resolution is non-obvious. Examples:

- "Is X the same as Y in the graph?"

- "Reconcile [name 1] with [name 2]"

- "I think [name] might already be in the graph as something else"

- "Audit identity for [name]"



Routine entity resolution (clean match via `graph_find_nodes` or clean miss followed by LEXICON enrichment) is handled inline by the calling SOP. This procedure is for the ambiguous middle and for suspected-duplicate cases.



When invoked from another SOP, the reconciliation findings feed back into the parent SOP's GRAPH PROPOSAL rather than producing an independent one.



## Step 0: Frame the question



State which shape applies in one line:



- **Candidate vs existing.** One new candidate to reconcile against possibly-matching graph nodes.

- **Suspected duplicate.** Investigate whether the graph already contains two or more nodes for the same entity.

- **Canonical-name update.** Existing node's canonical_name is suspected suboptimal (legacy symbol, deprecated name).



## Step 1: Pre-flight comprehensively



`graph_find_nodes` is the workhorse. Call it broadly:



1. Primary name as given.

2. Known variants and legacy symbols.

3. Suspected aliases from prior context.



For every match: `graph_get_node`, `graph_get_observations`. Tabulate:

- canonical_name

- node_type

- aliases (full list)

- cross_references (JSON, expanded)

- observation_count

- coverage on incident edges (brief: a heavily-cited duplicate is a bigger problem than a lightly-used one)



Read the observations themselves, not just the count. Notes may be aspirational, stale, or describe intent that was never realized; observations are the actual usage record, grounded in chunks. When notes and observations disagree about what a node represents, observations win — they record what the node has been used for in practice.



If multiple matches arise, list all of them. Do not pick one yet.



## Step 2: LEXICON identity grounding



Run the appropriate lookup on the candidate:

- Genes: `lexicon_lookup_gene`. Try `lexicon_lookup_hgnc` if the candidate looks like a legacy symbol.

- Proteins: `lexicon_lookup_protein`.

- Small molecules: `lexicon_lookup_drug` first; `lexicon_lookup_compound` on miss.

- GO terms: `lexicon_lookup_go_term`.



Capture from the LEXICON envelope:

- canonical_name (LEXICON's suggestion).

- suggested_node_type.

- aliases.

- cross_references.

- notes_prefix.



LEXICON output is advisory; the user may override at review.



## Step 3: Cross-reference comparison



Cross-references are the strongest identity signal because they are stable, structured, and source-grounded. Compare LEXICON's `cross_references` against each graph match's `cross_references`:



- **Identifier match.** At least one identifier in common (e.g., NCBI gene ID, UniProt accession, DrugBank ID): same entity. Name differences are alias matters, not identity.

- **Disjoint identifiers.** No overlap on identifiers despite name similarity: different entities. Common causes: gene-vs-protein with similar names, paralogs sharing a name fragment, namesake compounds in different chemical classes.

- **Partial or absent comparison.** Existing node has no cross_references, or LEXICON has none, or only some identifier types overlap: judgment call. Default to surfacing the comparison and asking the user, not guessing.



Strict node identity per project rule (same `(canonical_name, node_type)` is the same node) applies on top of this. Two graph nodes with the same canonical_name but different node_type are distinct by design (e.g., `("TP53", "gene")` and `("p53", "protein")` are separate by intent).



## Step 4: Decision



Walk to one outcome:



- **A. Same node, no change.** Candidate matches an existing node, name is already canonical or aliased, cross_references already grounded. No proposal needed beyond noting the match.

- **B. Same node, add alias.** Canonical name matches an existing node; candidate name is a variant not yet aliased. Propose `graph_add_alias`.

- **C. Same node, update cross_references.** Match by name or alias; existing node lacks cross_references or has incomplete identity grounding. Propose `graph_set_cross_references` from LEXICON output.

- **D. Same node, canonical_name should change.** Existing node uses a legacy or suboptimal canonical_name; LEXICON or chunk evidence supports a current standard. Propose `graph_update_node` to swap the canonical_name; if the legacy name has citation value, add it as an alias via `graph_add_alias` in the same proposal.

- **E. Distinct new node.** Cross-references show the candidate is a different entity despite name similarity. Propose `graph_add_node` with appropriate node_type, aliases, cross_references, notes_prefix.

- **F. Suspected duplicate in graph.** Two or more existing nodes appear to refer to the same entity. List as a Manual-handling item (no merge MCP tool). Recommend resolving via AXIOM Developer before any further graph writes touch the affected nodes, to avoid compounding the duplication.

- **G. Cross-references conflict.** LEXICON cross_references conflict with the existing node's (e.g., the node points to a different NCBI gene than LEXICON does). Surface explicitly; ask the user. Do not silently overwrite cross_references.



## Step 5: Compose the proposal



Section order in the response:



1. **Reconciliation question.** One line, per Step 0.

2. **Graph matches.** Pre-flight results with tabulated fields from Step 1.

3. **LEXICON grounding.** Envelope summary from Step 2.

4. **Cross-reference comparison.** Per-match comparison: identifier match, disjoint, or partial.

5. **Decision.** One of A through G with brief reasoning.

6. **GRAPH PROPOSAL.** Additive items per the format in the project's custom instructions.

7. **Manual-handling items.** If Decision is D, F, or G, what the user resolves outside the MCP, with one-line reasons.



## Failure modes



- **No graph matches and LEXICON miss.** Identity ungroundable from current sources. Propose either (a) creating the node with notes flagging the ungroundedness, or (b) deferring until a chunk citation provides better grounding. The user decides; default to deferring when this SOP is invoked standalone.

- **Multiple plausible matches in graph.** List all. Apply Step 3's cross-reference comparison to each. If still ambiguous, ask the user.

- **LEXICON returns conflicting hits across tools** (e.g., `lookup_gene` and `lookup_protein` both return non-empty but inconsistent entries). Report both; the user picks the appropriate node_type framing.

- **Suspected duplicate confirmed mid-procedure (in service of another SOP).** Halt the calling SOP and prioritize duplicate resolution via AXIOM Developer before further writes. Compounding writes onto a duplicate makes the eventual merge harder.



## Stop conditions



One pre-flight sweep (Step 1), one LEXICON grounding (Step 2), one cross-reference comparison (Step 3), one decision (Step 4). Compose and propose. Do not iterate on related entities; reconcile one at a time.



## Out of scope



- Multi-entity batch reconciliation (iterate one at a time).

- Merging duplicate nodes (deferred MCP capability; handled in AXIOM Developer).

- Piecemeal graph writes during the reconciliation. When invoked standalone the commit happens once at SOP completion via graph_apply_proposal per the custom-instructions conversation protocol; when invoked as a sub-procedure the findings feed back to the parent SOP for commit there.

