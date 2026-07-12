# SOP: Build specialist report

## Purpose

Standard procedure for turning one graph build into a longer, technical
companion to the lay `report.md`. Where the insights report states a single lead
for a funder with no domain knowledge, the specialist report exposes the full
analytic picture the insights report deliberately withholds: the complete ranked
candidate set, the structural objects (feedback core, minimum feedback vertex
set, positive-cycle hitting set, participation), the multi-outcome breadth and
conflict signals, the external cross-check, and the build-over-build persistence.
It closes with a Discussion section, an informed synthesis written by the drafter.

This SOP is a sibling of `SOP_build_insights_report.md`, not a replacement. It
reuses that SOP's reading of `build_report.json` verbatim (Steps 0 through 6
there) and diverges only on audience, on what to include, on the sourcing guard,
and on the verification pass. Read the insights SOP first; this document assumes it.

## What this SOP does and does not do

It presents the whole candidate set with every label and metric, explains the
structural method in defined terms, lays out the multi-outcome breadth matrix and
the cross-outcome conflicts, and writes a closing Discussion. It does not
recompute anything: every number comes from `build_report.json`, exactly as the
insights report does. It does not reach into the corpus, the graph free-text
fields, or LEXICON. The technical depth here is depth of analysis, not depth of
biology sourced from free text.

## Audience and voice

The reader is assumed to be numerate and willing to follow a technical argument,
but to have no prior knowledge of this project, of network control theory, or of
the biology. Accordingly:

- Every technical term, biological and network-theoretic alike, is defined in
  plain language on first use. This includes project vocabulary (participation,
  well-fed, breadth floor, coherent action, cross-outcome conflict) and method
  vocabulary (strongly connected component, feedback vertex set, positive-cycle
  hitting set, signed reachability). A consolidated glossary closes the document
  so a reader can look any term up without hunting.
- Length is not constrained. Completeness and clarity win over brevity. The
  insights report's one-lead discipline does not apply here; the point of this
  report is to show the whole board.
- Numbers are welcome, unlike in the lay report, but each is given with its
  denominator and its meaning. Disease breadth is always stated against
  `outcomes.disease_outcomes_analyzable`, never the full disease list; hallmark
  breadth against `outcomes.hallmark_outcomes_analyzable`. A dropped outcome is a
  curation gap, never a finding that a lever does not affect it.
- Honesty over excitement, exactly as in the lay report. Structural centrality is
  not discovery. Direction is the trustworthy signal; magnitude and path-count are
  not. The persistence of known hubs across builds is a legitimate meta-analytic
  result and is stated as such, not dressed up as novelty.
- No interventional or dosage framing. The report describes where causal
  influence concentrates on the curated map, never what to take or change.
- No species-resolved claims. The loop analysis collapses species; nothing here is
  human-specific unless a species-aware pass establishes it.

## Trigger

Invoked explicitly by the user, for example "write the specialist report" or
"generate the technical report for tonight's build." It runs against the same
`build_report.json` the insights report uses, so the two can be written from one
build without rerunning anything.

## Inputs

Primary input, and the source of every fact and number:

- `Python/export/build_report.json`, written by pass 5 of `run_analysis.bat`.

Fallback and verification sources, used only when a JSON value is missing or looks
wrong, per the insights SOP appendix:

- `Python/export/cycle_analysis.tsv`, `feedback_control_targets.tsv`, and
  `feedback_direction_by_outcome.tsv`.
- `Python/lib/data/axiom_graph.db`.

Never used: `target_control.tsv` (matching artifacts, excluded by design), and the
free-text `notes`, `observation`, and `edge_evidence` narrative fields (see the
sourcing guard).

## Step 0 through Step 6: Read the build

Perform Steps 0 through 6 of `SOP_build_insights_report.md` without change. Those
steps confirm the JSON is present and fresh, read the structural frame, read the
candidate set and its labels, read the breadth and conflict signal, run the
judgment review of each candidate, read the external cross-check, and read the
persistence delta. The specialist report consumes the same reads; it simply keeps
more of them.

Two emphases specific to this report:

1. Keep the entire `candidates` array, not just the lead. Every entry, including
   the `discard` and `cross_outcome_conflict` labels, appears in the specialist
   report's candidate table. A `discard` is informative here: it shows the method
   demoting a node it could have inflated (the pass-through chokepoint case).
2. Read the `outcomes` block closely, because the specialist report states the
   breadth denominators explicitly rather than hiding them: how many hallmarks and
   diseases were analyzable, how many were dropped and why, the well-fed disease
   count, and the computed breadth floor.

## Step 7: Compose

Write the report to the structure below. Section order is a default, not a
mandate; adjust when a build's shape warrants, but keep every section present.

1. Orientation. What the causal map is (a directed graph of curated cause-and-
   effect statements from the literature, one arrow per asserted influence), what
   the control analysis asks of it, and why a blind structural method saying
   anything is worth attention. Define "map," "arrow/edge," "outcome," and
   "leverage" here.
2. Structural frame. The strongly connected feedback core, the exact minimum
   feedback vertex set, the positive-cycle hitting set, participation as the
   stable ranker, and the amplifying-to-damping ratio. State the ratio as a ratio
   and disclose the loop-length cap and the share of loops at the cap. Define
   every one of these terms in place.
3. Outcome panel and breadth denominator. The anchor, the secondary anchor, the
   analyzable hallmarks and diseases, the dropped ones as curation gaps, the
   well-fed count, and the breadth floor with the reason it sits where it does.
4. The candidate table. The full ranked set with participation, label, centrality
   membership (minimum FVS, hitting set), coherent action, aging and lifespan
   favorability, hallmark and disease breadth, evidence depth, source diversity,
   and the external footprint. A reader should be able to see every node the
   analysis surfaced and why it landed where it did.
5. Leads in depth. Each `lead` node in full: its participation, its centrality
   membership, its clean favorable footprint across the analyzable diseases and
   hallmarks, its evidence depth, and its external footprint. Name the diseases
   from `favorable_disease_ids` where it aids readability.
6. Cross-outcome conflicts. Each `cross_outcome_conflict` node, with the outcomes
   it helps and the outcomes it harms named from `conflict_outcome_ids`. This is
   the honest-tradeoff section and frequently the most valuable content.
7. Curation priorities and open questions. High-centrality nodes whose anchor
   direction the map cannot yet resolve (`aging_direction_class` ambiguous), and
   thinly-wired or dropped outcomes. These are the project's open questions and
   double as the funding ask.
8. Persistence. What is new, persisting, strengthened, weakened, or dropped versus
   the prior archived build. If `compared_to` is null, say there is no comparison
   yet and make no trend claim.
9. Methods and caveats. The full version of the lay report's caveats: the loop-
   length cap and truncation, the curation-limited breadth denominators, the
   structural-not-dynamical nature of the analysis, the species collapse, and the
   no-intervention scope.
10. Discussion. The drafter's informed synthesis. See the next section for its
    boundaries.

Save. Write the finished prose directly to
`Python/export_public/specialist_report.md`, overwriting the previous build's
specialist report. The filename is fixed, not dated; build-over-build history
lives in the `build_report.json` archive under `export/build_reports/`. This is a
public-folder write with no separate review gate, so the prose must be delivery-
ready before it is written.

## The Discussion section

The Discussion is the one place in either report where the drafter synthesizes
rather than reports. It is an informed reading of what the build, taken as a
whole, is saying: which patterns cohere, what the method's convergence on known
biology implies about its trustworthiness, where the genuine tradeoffs sit, and
which open question most deserves the next unit of curation effort.

It is bounded, and the bounds are load-bearing:

- It interprets only what is in `build_report.json`. It introduces no biological
  claim, mechanism, or citation that is not derivable from the structural results,
  the canonical node names, and the external counts already in the JSON. It is
  synthesis of the build, not new literature.
- It never crosses into intervention. "The map concentrates influence here" is in
  scope; "therefore raise or lower X," or any dose, agent, or treatment framing,
  is not.
- It respects the same trust ordering as the rest of the report: direction is
  trustworthy, magnitude and path-count are not, centrality is not discovery.
- It stays DrugBank-clean, exactly as the body does. No free-text field feeds it.
- It is labeled as interpretation, so a reader never mistakes the drafter's
  synthesis for a computed result.

## Sourcing guard

The prose must derive only from canonical node names, the structural and grounding
numbers in `build_report.json`, the `outcomes` block, and the external counts.
It must never quote or paraphrase the `notes`, `observation`, or `edge_evidence`
free-text fields, which can carry licensed DrugBank content. `build_report.json`
is DrugBank-clean by construction (it holds no free text), so a specialist report
written from it alone can be placed into `export_public/` without passing the
redaction filter, on the same basis as the lay report. If a value is checked
against the DB via the fallback queries, no free-text field is carried into the
prose. This guard is what keeps the specialist report public-safe; it is not
optional.

## Step 8: Verification pass

Before delivery, re-derive every number and every superlative in the prose from
`build_report.json`, and re-count any claim of the form "the only X," "the most
Y," or "more than anything else" against the underlying TSVs using the insights
SOP appendix queries. Re-check each breadth claim against a candidate's
`favorable_disease_ids` and the `outcomes.disease_outcomes_analyzable` denominator,
and each conflict or tradeoff claim against `conflict` and `conflict_outcome_ids`.
Confirm `outcomes.breadth_floor` matches the policy value for
`outcomes.well_fed_disease_count` (base 2, plus one per eight well-fed diseases,
capped at six), and if the floor changed from the prior archived build, say so.
Confirm the amplifying-to-damping ratio equals the positive-over-negative loop
counts. Confirm no candidate promoted to `lead` in the prose lacks an
`external_ok` of true or an `aging_favorable` of yes. Finally, scan the whole
document for any sentence that could only have come from a free-text field, and
for any interventional phrasing, and remove it. For a public-facing build, run
this pass as a separate subagent against the JSON and the TSVs so the check does
not inherit the drafter's assumptions.

## Failure modes

- `build_report.json` missing or stale. Batch not run or not rerun; stop and run
  `run_analysis.bat`.
- `external_status` unavailable. No candidate is certifiable as a lead; carry the
  degrade rule from the insights SOP into this report and defer any "understudied"
  or "independently validated" phrasing until the batch is rerun with the cross-
  check live.
- `structure.truncation_suspected` true. Loop counts are cap-bound; disclose in the
  methods and Discussion, and never present a raw loop count as a trend.
- A JSON field looks implausible. Verify against the DB via the insights SOP
  appendix before writing anything from it.
- No candidate labeled `lead`. Valid and reportable. Present the full candidate
  table, feature the top curation priority, and note the persistence of known
  hubs; do not manufacture a lead to fill space.

## Stop conditions

One read of the JSON (the shared Steps 0 to 6), compose the ten sections including
the Discussion, verify. Do not re-mine the corpus or re-run the external searches
by hand unless a specific value fails verification; the batch already did that work.

## Out of scope

- Running the pipeline. `run_analysis.bat` (five passes) is a prior step.
- Recomputing metrics. That is `build_report.py`, not this SOP.
- `target_control.tsv` (excluded from public reporting by design).
- The interactive graph viewer (a separate credibility artifact).
- Any interventional or dosage framing, in the body or the Discussion.
- Species-resolved claims.
- The hallmark-by-disease influence matrix as its own artifact. That is a separate,
  deferred report type; the specialist report summarizes breadth and conflict but
  does not render the full matrix.

## Relationship to the insights report

The two reports are written from one build and one JSON. The insights report is
the front door: one lead, lay funder, methods behind a link. The specialist report
is the full account behind that door: every candidate, every structural object,
the breadth matrix, the conflicts, and a synthesis. They must not disagree on any
shared fact. When both are produced for a build, write the insights report first
so the single featured lead is settled, then write the specialist report so its
lead-in-depth section matches the front door's choice.
