# SOP: Dataset ingestion

## Purpose

Standard procedure for proposing graph additions from a structured dataset rather than a corpus paper. A dataset is a table (CSV, TSV, database export, or similar) whose grain is one row per record, not one row per paragraph: an interventions-outcomes table (for example the NIA ITP Data Coordinating Center export), a per-genome comparative table, a curated trait table. The procedure inventories the table, fixes its grain and join keys, maps columns to graph constructs, resolves entities against the graph, and emits a GRAPH PROPOSAL in reviewable batches.

It exists because `SOP_paper_extraction` assumes a single ingested source with chunks and sections, and every one of its steps binds to a `chunk_id`. A dataset has no chunks, no sections, and no single `source_id`. The failure is at Step 0 of paper extraction, not at its margins, so a dataset needs its own procedure rather than a shim.

## The provenance gate (read before running)

This SOP has a hard external dependency it cannot satisfy from inside this project.

Every graph write carries a `grounding_type`, and the schema validates it against a closed set: `corpus_primary`, `corpus_inline_cited`, `lexicon`, `common_knowledge`, `background_weak`. **None of these describes a dataset row.** A row from the ITP DCC is not a corpus chunk, is not a LEXICON return, is not common-knowledge background, and is emphatically not `background_weak` (a rigorous multi-cohort survival dataset is among the strongest grounding the graph can hold, and tagging it weakest would invert any downstream weighting).

Two things follow, and both are load-bearing:

1. **Do not repurpose an existing `grounding_type` to force a dataset write.** Mislabeling a dataset row as `lexicon` or `background_weak` corrupts provenance silently and permanently. This is worse than not writing at all.
2. **The fix is a schema change, and schema changes live in AXIOM Developer, not here.** The likely shape: a new `grounding_type` (working name `dataset_primary`) plus a `provenance_extra` JSON convention carrying dataset name, snapshot date or version, source URL or DOI, and the row identifier. The storage is already partly present, since `node_observations` and `edge_evidence` both carry a `provenance_extra` TEXT JSON column for non-corpus provenance; what is missing is only the enum discriminator and its validation branch.

**Behavior of this SOP under the gate:** everything up to and including entity resolution and column mapping (Steps 0 through 5) is useful now and can be run today, because it neither writes nor assumes a provenance model. At Step 6, if the dataset `grounding_type` does not yet exist in the schema, **stop before composing any write** and hand the schema decision to AXIOM Developer as a Manual-handling item. Present the completed mapping and entity resolution so the developer-side change is fully specified, but do not commit. Resume from Step 6 once the enum value exists.

Confirm the current state of the enum by asking Neil or by checking with AXIOM Developer; do not assume it has or has not been added.

**The second gate closed in V18.** This SOP previously carried a hidden dependency alongside the provenance gate: even with a `dataset_primary` grounding type there was no way to record "tested and found null" that the analysis layer read correctly, and no way to scope one evidence row differently from another on the same edge. Both now exist (`assertion_status` on evidence and observations, `evidence_id` on conditions). The provenance gate is the only remaining blocker on the ITP fixture.

## Trigger

Invoked explicitly by the user. Examples:

- "Ingest the ITP DCC table into the graph"
- "Run dataset ingestion on [file or export]"
- "Load this comparative genome table as graph nodes"

Do not auto-invoke. A dataset mentioned in passing during another task is not a trigger.

## Step 0: Identify the dataset and confirm it is loadable

Resolve the user's reference to a concrete file or export and establish what it is:

1. Locate the file (filesystem tools) and read enough of it to see the header row and the first several data rows. Do not read the whole table yet if it is large; the header and a sample fix the shape.
2. Record dataset identity in one line at the top of the response: dataset name, source (issuing body and URL or DOI if known), snapshot date or version, file path, format, and approximate row count.

**Trust check (the dataset analog of the sibling-hit check).** A dataset is a snapshot, and snapshots go stale, truncate, and get re-issued with changed values. Before treating the file as authoritative:

- **Completeness.** Does the row count match what the source claims? A silently truncated export (partial download, row cap on a query) is the dataset equivalent of a bad bibliography row: every write inherits the omission. If the source publishes a record count, compare.
- **Currency.** When was this exported versus when was the source last updated? State the lag. For the ITP specifically, the NIA publications page and the DCC are updated on different cadences, and cohorts reported after the export date are simply absent. An absent cohort is a curation gap, not a negative result, and must not be recorded as "not tested."
- **Schema match.** Do the columns match the expected schema for this dataset? An unexpected or renamed column means the mapping in Step 2 cannot be assumed from a prior run.

If the file fails completeness or currency, note it explicitly and proceed only on the rows present, scoping every downstream claim to the snapshot. Do not extrapolate beyond the rows in hand.

## Step 1: Inventory the structure

Before any mapping, fix the table's shape:

1. **Grain.** State in one line what one row is: "one row per (compound, sex, cohort, survival-test verdict)" or "one row per species genome." The grain determines what a row can and cannot assert.
2. **Columns.** List every column with its apparent type (identifier, categorical, quantitative, free text) and a one-line meaning.
3. **Join keys.** Identify the column(s) that identify the primary entity (compound name, species name, gene symbol) and any that link to controlled vocabularies. These drive entity resolution in Step 3.
4. **Verdict multiplicity.** Flag any case where the same primary entity appears in multiple rows with different outcomes, and identify the column that discriminates them. The ITP is the canonical example: one compound carries different lifespan verdicts under different statistical tests (log-rank versus Gehan). That discriminator is a condition, not a contradiction (Step 4).

Present a brief inventory summary before mapping: dataset identity, grain, column list, join keys, row count, and any verdict-multiplicity columns.

## Step 2: Semantic column mapping

This is the core step with no paper analog. Decide, per column, which graph construct it feeds. Present the mapping as a table for review before resolving entities.

- **Entity columns to nodes.** A compound name maps to a `small_molecule` node; a species name to a `species` node (node_type is open-ended, so new descriptive types do not require a schema migration, unlike `grounding_type`); a gene symbol to a `gene` node. State the target node_type per column.
- **Relationship columns to edges.** An outcome column (extends lifespan: yes or no; direction of effect) maps to an edge from the intervention node to an outcome node. Name the edge_type explicitly and write the causal sentence in plain English first, per guideline 3, so the subject is the driver and not the marker.
- **Scope columns to conditions.** Sex, statistical test, dose, cohort, site, organism map to `edge_conditions` (when the edge holds), not to the edge itself. The verdict-discriminator column from Step 1 lands here.
- **Demonstration columns to cell_system on evidence.** Where a result was shown (strain, tissue) is cell_system on the evidence row, not a condition. Do not conflate with the scope columns above.
- **Quantitative columns.** A bare effect size, p-value, or coefficient with no causal claim is a standalone measurement and is a default-reject per guideline 2, unless it grounds a causal edge. When it does ground one, it belongs in the evidence note or observation text, not as a node.
- **Provenance columns to provenance_extra.** The row identifier, source URL, and snapshot date feed `provenance_extra`, under the gated `grounding_type` from the provenance gate above.

Anything that maps to nothing is logged in Considered-but-skipped at compose time, with the matching guideline reason.

## Step 3: Entity resolution

For every distinct primary entity in the table:

1. `graph_find_nodes_batch` on all entity names in one call (the batch tool exists precisely for this; a dataset naming dozens of compounds is its intended load). Include obvious aliases.
2. For matches: note node_id, node_type, observation_count, and any existing edges relevant to the outcome being ingested, so a dataset row that duplicates an existing curated edge becomes an evidence addition rather than a new edge.
3. For non-matches: hold for LEXICON enrichment in Step 4.

Most well-known interventions (rapamycin, acarbose, metformin, canagliflozin) will already exist. Treat a match as the default and a new node as the exception for any dataset covering established entities.

Where identity is ambiguous (a compound named differently than its graph canonical form, a suspected duplicate), hand that single entity to `SOP_entity_reconciliation` as a sub-procedure and fold its finding back here, exactly as paper extraction does.

## Step 4: LEXICON enrichment

For each new candidate entity:

- Small molecules: `lexicon_lookup_drug` first, `lexicon_lookup_compound` on miss.
- Genes: `lexicon_lookup_gene`, `lexicon_lookup_hgnc` for legacy symbols.
- Species: LEXICON has no species tool; ground identity from the dataset's own taxonomy column and note it. Cross-reference enrichment for species nodes is a known gap, not a tool failure.

Apply the **DrugBank attribution suffix** exactly as specified in `SOP_paper_extraction` (the dedicated section after its Step 5). A dataset of pharmacological interventions is a high-risk surface for this: any DrugBank-curated action, group classification, accession, target id, or verbatim mechanism text written into a field gets the literal ` [Source: DrugBank]` suffix at write time so the Stage 04 redactor strips it from public export. Plain drug names in original prose do not get suffixed. Negative findings ("no drugs in DrugBank for this target") do not get suffixed.

## Step 5: Edge and observation construction

For each row (or each group of rows sharing a primary entity), build the graph items from the Step 2 mapping:

- **Outcome edges.** One edge per (intervention, outcome) with the edge_type from the mapping. Scope columns that apply to the whole edge become plain `edge_conditions`. Scope columns that discriminate one row's verdict from another's become **evidence-scoped conditions** (V18): pass `evidence_id` on `graph_add_condition`, or `evidence_addition_index` inside a `graph_apply_proposal` payload to point at an evidence row being created in the same batch. Before V18 this was not expressible, because a condition attached only to the edge, so both rows inherited both conditions and the discriminator was lost. A single compound with a log-rank null and a Gehan positive is therefore **one edge with two evidence rows, each carrying its own evidence-scoped `test` condition**, never a silent overwrite of one verdict by the other. Both verdicts are first-class; recording only the headline verdict discards the finding the retrodiction depends on.
- **Negative outcomes.** A tested-and-did-not-extend result is a first-class recordable outcome. Record it as an evidence row with `assertion_status: "refuting"` (V18), scoped by evidence-level conditions to the test and sex under which it was null, and carrying `method` naming that test (enforced at write time). The rollup then does the right thing without further intervention: an edge whose evidence is entirely refuting derives as `refuted` and is excluded from all five analysis passes and from every grounding metric in the build report, while an edge carrying both kinds derives as `contested`, stays traversable, and caps its candidate at `watch_item` so it can never be published as a lead. That is the correct treatment of a compound whose verdict depends on which survival test you run. This is distinct from an absent cohort (Step 0), which is recorded nowhere as an outcome. "Tested and failed" and "not tested" must never collapse into the same graph state, and after V18 they no longer can: the first is a refuting row, the second is the absence of any row.
- **Observations.** Per-entity findings that are not relational (a compound's tested dose range, a species trait) map to observations on the entity node.

Every constructed item carries the gated dataset `grounding_type` and a `provenance_extra` naming the dataset, snapshot, row id, and source. If the gate is unresolved, this is where the SOP stops (see the provenance gate).

## Step 6: Batch for review pacing

Datasets are inherently larger than papers, and the one hard constraint from guideline 5 is review pacing: a commit too large to present for review is split into reviewable batches. Volume itself is not a reason to drop a groundable, engineering-relevant row (guideline 5 forbids that), but it is a reason to sequence.

- Propose a batching key that produces coherent, independently reviewable units: by intervention class, by outcome, by cohort year, or by alphabetical block. State the key.
- Present the first batch as a full GRAPH PROPOSAL. On approval, proceed to the next batch in the same shape. Each batch commits atomically via `graph_apply_proposal`.
- Track and report progress across batches (rows ingested, rows remaining, batches committed) so a multi-session ingestion is resumable.

## Step 7: Compose the proposal

Section order in the response, per batch:

1. **Dataset identity and batch scope.** Dataset name, snapshot, batching key, which batch this is, rows covered.
2. **Existing matches.** Entity nodes already in the graph, with observation_count.
3. **New nodes.** canonical_name, node_type, aliases, cross_references, notes_prefix.
4. **New edges.** subject, edge_type, object, conditions (including the verdict discriminator), per-row provenance.
5. **Additions to existing edges.** edge_id, new evidence rows, coverage delta, for dataset rows that corroborate an already-curated edge.
6. **New observations.** target node, paraphrased finding, per-row provenance.
7. **Cross-reference additions on existing nodes.**
8. **Conflicts with existing graph.** A dataset row that contradicts a curated edge (opposite direction, incompatible scope). Surface with both positions; recommend `SOP_edge_audit` for resolution. Do not silently overwrite curated content with dataset content.
9. **Considered but skipped.** Required if Step 2 mapped any column or row to nothing. Per item: the column or row, and a one-line reason from the valid set (fails guideline 1, fails guideline 2, fails QG1, handoff to reconciliation, handoff to edge audit). Absent cohorts and bare quantitative columns are logged here, not written.
10. **Manual-handling items.** The provenance-gate schema decision if unresolved; any suspected duplicate deferred to AXIOM Developer; any species cross-reference gap. One line each.

Apply the DrugBank suffix to any field reproducing DrugBank-restricted content before the batch is committed.

## Idempotence

Re-running this SOP on an already-ingested dataset should surface mostly existing matches and evidence additions that already exist, with largely empty New sections, exactly as paper extraction does. `graph_apply_proposal` match-and-merge handles this for additions.

The dataset-specific wrinkle: a **re-issued snapshot with changed values** is not a clean re-run. If a later export changes a verdict on a row already ingested (a reanalysis flips a null to a positive, or refines a dose), that is an in-place edit, not an addition, and match-and-merge will not catch it because the edge already exists. Handle a changed verdict as an `observation_rewrite` or a new condition-scoped evidence row that records the reanalysis, preserving the original verdict and its provenance. Never delete the superseded verdict silently; the history of what was found under which analysis is itself content. When a snapshot changes materially, state the snapshot delta at the top of the response before proposing.

## Failure modes

- **Provenance gate unresolved.** Stop at Step 6. Present the full mapping and resolution as a Manual-handling item specifying the needed `grounding_type`. Do not write.
- **Dataset not locatable or unreadable.** Stop and flag.
- **Truncated or stale export.** Proceed on rows present, scope every claim to the snapshot, and record the gap. Do not extrapolate.
- **Grain ambiguous.** If one row cannot be described in a single plain sentence, the mapping is not yet safe. Stop and clarify with the user before mapping.
- **Verdict multiplicity mistaken for contradiction.** Two rows differing only by statistical test or sex are condition-scoped facts, not a conflict. Resolve as conditions, not as an edge audit.
- **Dataset contradicts curated graph.** Surface in Conflicts (Step 7, item 8). Recommend `SOP_edge_audit`. Do not overwrite.
- **Batch too large to review.** Reduce batch granularity (Step 6). Never drop groundable rows to fit a size target.

## Stop conditions

Complete one full mapping (Steps 1 and 2), one entity-resolution pass (Step 3), enrichment (Step 4), and construction (Step 5) for the batch in scope. Compose and propose the batch. On multi-batch datasets, stop after each batch for review rather than committing the whole table in one call. Do not iterate on broader retrieval; a dataset is self-contained.

## Out of scope

- Corpus papers (use `SOP_paper_extraction`).
- The schema change the provenance gate requires. Specifying it is in scope; making it is AXIOM Developer.
- Pipeline stage runs and re-exports of the source dataset.
- Merging duplicate nodes (AXIOM Developer).
- Piecemeal graph writes during the SOP. Each batch commits once via `graph_apply_proposal` per the custom-instructions conversation protocol.

## Known-target fixture (not yet ingested)

The NIA ITP is the motivating dataset and the intended first target. Its outcome ground truth is the Data Coordinating Center export at `https://phenome.jax.org/projects/ITP1`, one record per compound-cohort, which is the authoritative outcome layer; the ITP publications supply the mechanistic layer (why a compound was proposed) and are ingested as papers, joined to the dataset on compound. The verdict-multiplicity case is live here: the 2024 Gehan reanalysis (GeroScience, doi 10.1007/s11357-024-01161-9) reports five compounds (metformin, enalapril, 17-DMAG, caffeic acid phenethyl ester, green tea extract) as lifespan-extending under the Gehan test that are null under the log-rank test the original cohort papers used. Both verdicts are recorded, condition-scoped to the test. This fixture is documented here for when the provenance gate opens; it is not yet ingested.
