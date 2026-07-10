# SOP: Build insights report

## Purpose

Standard procedure for turning one graph build into a short, honest,
lay-audience report of the tentative leads the analysis surfaces.

As of the five-pass pipeline, the mechanical work is already done before this
SOP begins. The fifth pass, `build_report.py`, reads the analysis TSVs and the
database, computes every grounding metric, runs the external cross-check over
the public PubMed and Open Targets APIs, assigns a provisional label to each
candidate, and writes it all to `build_report.json` plus a dated archive. This
SOP is therefore no longer a compute procedure. It is a judgment-and-writing
procedure that consumes that JSON and produces prose.

## What this SOP does and does not do

It decides which single lead to feature, glosses the biology for a lay reader,
phrases the honest claim, and writes the funding ask. It also reads the
multi-outcome breadth and conflict signals to choose that lead and to surface any
cross-outcome tradeoff. It does not recompute the
metrics; those come from `build_report.json`. The by-hand database queries and
MCP lookups that earlier versions of this SOP performed are retained only as a
fallback, in the appendix, for when a value in the JSON looks wrong and must be
checked against source.

## Audience and voice

The reader is assumed to be a prospective funder with no domain knowledge who
wants to know whether the project is finding anything worthwhile. Accordingly:

- No node counts, edge counts, or graph jargon in the prose. Those are
  meaningless to this reader and invite eye-glaze.
- One lead stated plainly, at most one watch-item. Not a digest. The
  multi-outcome analysis strengthens the single lead rather than expanding it
  into a list: the lead is the node with the broadest clean, conflict-free
  favorable footprint, and its reach is stated as "favorable across N of the M
  analyzable diseases, and no outcome it moves the wrong way," never as node
  counts.
- Every biological term gets a plain-language gloss on first use (for example,
  glycation described as the sugar-and-protein reaction that browns toast).
- Honesty over excitement. Never imply discovery where there is only
  structural centrality. The persistence of well-known hubs (cellular
  senescence, NAD+) across builds is itself a legitimate meta-analytic result
  and may be stated as such.
- Methods and all numbers live behind a link, not on the front page.

## Trigger

Invoked explicitly by the user, for example "write the build report" or
"generate the public summary for tonight's build." The build itself
(`run_analysis.bat`, which runs the five passes and produces
`build_report.json`) is a separate step the user runs first.

## Inputs

Primary input:

- `Python/export/build_report.json`, written by pass 5 of `run_analysis.bat`.
  This is the source of every fact and metric used below.

Fallback and verification sources (used only when the JSON is missing a value or
a value looks wrong, per the appendix):

- `Python/export/cycle_analysis.tsv`, `feedback_control_targets.tsv`, and
  `feedback_direction_by_outcome.tsv`.
- `Python/lib/data/axiom_graph.db`.

Never used: `target_control.tsv`. Its driver identities are matching artifacts,
excluded from `build_report.json` by design and excluded from the report here.

## Step 0: Confirm the report is present and fresh

1. Open `build_report.json`. Confirm `build_date` matches the build you intend
   to write about. If it is older, the batch was not rerun; stop and rerun it.
2. Read `external_status`. If `pubmed` or `open_targets` is `unavailable`, the
   batch ran without part of the cross-check. No candidate will carry a "lead"
   label in that case (the script enforces this). Do not manufacture a lead by
   hand; either rerun the batch with network available or write a prose-deferred
   report per the degrade rule below.
3. Note `structure.truncation_suspected`. If true, the loop counts are bound by
   the enumeration cap and must never be published as a trend without the
   caveat. This belongs in the methods page regardless.
4. Read the `outcomes` block. It records the breadth denominator for this build:
   which hallmark and disease outcomes were analyzable (`*_analyzable`) and which
   were dropped for having no reachable sources (`*_dropped`), the
   `anchor_outcome_id`, the `well_fed_disease_count` (diseases wired to depth
   `well_fed_indegree_k`), and the `breadth_floor` computed from it. A dropped
   outcome is a curation
   gap, not a negative finding, and must never be written as "X does not affect
   disease Y."

## Step 1: Read the structural frame

From the `structure` block of the JSON, take the loop parity counts and
`amplifying_to_damping_ratio`. The amplifying-to-damping ratio is the one
structural fact worth stating in lay terms, expressed as a ratio, not a raw
count. Do not recompute it; it is already there. (If you doubt it, the appendix
gives the recount.)

## Step 2: Read the candidate set and its labels

The `candidates` array is already sorted by participation and each entry already
carries a `label` (lead / watch_item / curation_priority /
cross_outcome_conflict / discard) assigned
mechanically from the thresholds in `build_report.py`. Read them. The labels are
the script's proposal, not the final word; Step 4 is where you confirm or
override with a stated reason.

The `curation_priority` entries (high centrality, unresolved direction, for
example AGER) are a first-class output, not a leftover. They are the project's
most valuable open question and double as the funding ask.

## Step 3: Read the breadth and conflict signal

The analysis judges each candidate against the full outcome set: the anchor
(organismal aging), the secondary anchor (maximum lifespan), the twelve
hallmarks, and the disease panel. Each candidate carries `coherent_action`
(increase, decrease, or empty), `aging_favorable`, `lifespan_favorable`,
`hallmark_breadth`, `disease_breadth`, `favorable_disease_ids`, `conflict`, and
`conflict_outcome_ids`. Read these before deciding what to feature.

- `aging_favorable` is the gate. A lead must cleanly favor the anchor outcome.
  `lifespan_favorable` is reported for context but does not gate.
- `disease_breadth` is the translational signal and the reason a lead earns a
  funder's attention: one lever moving many age-related diseases the right way.
  Read it against `outcomes.disease_outcomes_analyzable`, never against the full
  disease list and never against the dropped outcomes. The script requires it to
  reach `outcomes.breadth_floor` for a lead. That floor is not fixed: it is
  computed from how many diseases are wired to depth
  (`outcomes.well_fed_disease_count`) and rises as curation deepens, so each
  build's floor reflects that build's disease-side maturity.
- `hallmark_breadth` is a secondary, mechanistic signal, not the headline. The
  hallmarks are facets of aging, so a high hallmark breadth largely restates
  aging-favorability and must not be presented as independent reach.
- `conflict` true means the node cleanly helps some outcomes and cleanly harms
  others (the cellular-senescence-and-cancer case). The script labels these
  `cross_outcome_conflict` and never `lead`; do not override that. These nodes
  are first-class output, not noise: they are the honest tradeoff and often the
  strongest funding ask. Report the tradeoff plainly and name the outcomes on
  each side from `conflict_outcome_ids`.

## Step 4: Judgment review of each candidate

For each candidate the script proposed as `lead` or `watch_item`, confirm the
label against the fields the script computed:

- `pass_through_flag` true means the node is a single-file chokepoint that only
  inherits a busy corridor's traffic (the HMGB1 case). The script already
  labels these `discard`; confirm you agree and do not resurrect them.
- `distinct_sources` below the evidence floor, or `single_source_dominant_flag`
  true, means the candidate rests on too little independent literature to be a
  headline lead. The script caps these at `watch_item`; confirm.
- `external_ok` false means the external leg is missing; the candidate cannot be
  a lead. Confirm.
- `conflict` true means the script labeled the node `cross_outcome_conflict`. It
  is never a lead; confirm, and carry it into the tradeoff paragraph rather than
  discarding it.
- `aging_favorable` not "yes" means the node fails the anchor gate and is capped
  at `watch_item` even if its disease breadth is wide; confirm.
- `disease_breadth` below `outcomes.breadth_floor` means the node is not a lead
  even when every other gate passes; confirm against the analyzable denominator,
  not the full disease list.

You may override a label, but only with a one-line reason recorded in the
working notes, and only downward toward more caution (promoting a
script-labeled watch-item to lead requires re-running the checks by hand via the
appendix, not a judgment call). If any field looks implausible, verify it
against the DB using the appendix queries before trusting it.

## Step 5: Read the external cross-check

From each candidate, take `pubmed_count`, `pubmed_footprint_ratio` (the
candidate's field footprint relative to `reference_pubmed_count` for
`reference_term`), and, for gene and protein candidates, `ot_top_diseases`. A
low footprint ratio is the external basis for an "understudied" framing; state
it as the external number it is, not as an assertion. A present Open Targets
association is the external basis for "independently aging-linked." A
conspicuously absent one is worth noting honestly, not hiding.

Degrade rule. If `external_status` shows either source unavailable, write the
`build_report.json` summary of structure and candidates but defer any prose that
characterizes a lead as understudied or independently validated until the batch
is rerun with the cross-check live. Publish nothing the missing check would have
backed.

## Step 6: Read the persistence delta

From the `persistence` block, note which candidates are `new`, `persisting`,
`strengthened`, `weakened`, or `dropped` relative to the prior archived build.
Persistence across builds and added papers is a stronger claim than any single
build supports and is the honest way to convey that an incrementally growing
graph is producing results over time. If `compared_to` is null, this is the
first archived build and there is nothing to compare yet; say nothing about
trends.

## Step 7: Compose

Write the prose per the Audience and voice section: one lead, at most one
watch-item, the curation priority as the ask, the amplifying-to-damping ratio as
the one structural hook, methods and numbers behind a link. State the lead's
reach as its disease breadth ("favorable across N of the M analyzable diseases,
and no outcome it moves the wrong way"), not as a node count. If a
`cross_outcome_conflict` node is present, it is the one tradeoff paragraph and
frequently the funding ask, standing in for or joining the `curation_priority`.

Save. Write the finished prose directly to `Python/export_public/report.md`,
overwriting the previous build's report. This is a public-folder write with no
separate review gate, so the prose must be delivery-ready before it is written.
The filename is fixed, not dated; build-over-build history lives in the
`build_report.json` archive under `export/build_reports/`, not in this file.

DrugBank and provenance guard. The prose must derive only from canonical names,
the structural and grounding numbers in `build_report.json`, and the external
counts. It must never quote or paraphrase the `notes` or `observation` free-text
fields, which can carry licensed DrugBank content. `build_report.json` is
DrugBank-clean by construction (it holds no free text), so a report written from
it alone can be placed into `export_public/` without passing the redaction
filter. If you fall back to the DB for a check, do not carry any free-text field
into the prose.

## Step 8: Verification pass

Before delivery, re-derive every number and every superlative in the prose from
`build_report.json`, and re-count any claim of the form "the only X," "the most
Y," or "more than anything else" against the underlying TSVs using the appendix
queries. Re-check any breadth claim against a candidate's `favorable_disease_ids`
and the `outcomes.disease_outcomes_analyzable` denominator, and re-check any "no
conflict" or tradeoff claim against `conflict` and `conflict_outcome_ids`. Confirm
`outcomes.breadth_floor` matches the policy value for
`outcomes.well_fed_disease_count` (base 2, plus one per eight well-fed diseases,
capped at six), and if the floor changed from the prior archived build, say so,
since a lead certified under a higher floor is a stronger claim than one under a
lower floor. These are the claims most likely to be wrong: during this SOP's design,
a superlative about the graph's single brake and a swap of two column meanings
both slipped through and were caught only by recounting. For a public-facing
build, run this pass as a separate subagent against the JSON, the TSVs, and the
DB so the check does not inherit the drafter's assumptions.

## Failure modes

- `build_report.json` missing or stale. Batch not run or not rerun; stop and run
  `run_analysis.bat`.
- `external_status` unavailable. No leads certifiable; defer prose per the
  degrade rule, or rerun with network.
- `structure.truncation_suspected` true. Loop counts are cap-bound; disclose in
  methods, never trend.
- A JSON field looks implausible. Verify against the DB via the appendix before
  writing anything from it.
- No candidate labeled `lead`. Valid and reportable. Feature the top
  `curation_priority` and the persistence of known hubs; do not manufacture a
  lead to fill space.

## Stop conditions

One read of the JSON (Steps 0 to 6), one judgment review (Step 4), compose and
verify. Do not re-mine the corpus or re-run the external searches by hand unless
a specific value fails verification; the batch already did that work.

## Out of scope

- Running the pipeline. `run_analysis.bat` (five passes) is a prior step.
- Recomputing metrics. That is `build_report.py`, not this SOP.
- `target_control.tsv` (excluded from public reporting by design).
- The interactive graph viewer (a separate credibility artifact).
- Any interventional or dosage framing. The report describes where the map
  concentrates causal influence, never what to take or change.
- Species-resolved claims. The loop analysis collapses species; do not imply a
  finding is human-specific unless a species-aware pass establishes it.

## Appendix: verification and fallback queries

These reproduce by hand what `build_report.py` computes, for use only when a JSON
value must be checked against source. They are not part of the normal flow.

Structural recount, against `cycle_analysis.tsv`: count rows by the `parity`
column for the loop totals, and count rows whose `length` equals the cap
(currently 8) for the truncation figure.

Degree of a node, against the DB:

```sql
SELECT COUNT(*) FROM edges WHERE subject_id = :id OR object_id = :id;
```

Evidence depth and source diversity of a node, against the DB:

```sql
SELECT COALESCE(NULLIF(ev.source_doi,''), NULLIF(ev.source_pmid,''), NULLIF(ev.source_filename,'')) AS k
FROM edge_evidence ev JOIN edges e ON ev.edge_id = e.id
WHERE e.subject_id = :id OR e.object_id = :id
UNION ALL
SELECT COALESCE(NULLIF(source_doi,''), NULLIF(source_pmid,''), NULLIF(source_filename,''))
FROM node_observations WHERE node_id = :id;
```

Count distinct non-null keys for `distinct_sources`, and the largest single-key
share for the single-source-dominance check.

External counts by hand, if the connectors are authorized in the session: the
PubMed MCP (`search_articles`) for the candidate-and-aging hit count against the
reference term, and the Open Targets MCP (`search_entities`, then a
target-disease association query) for gene and protein candidates. Note that
`build_report.py` reaches the same two services over their public HTTP APIs and
needs no connector; the MCP path is only for manual spot-checks.

Breadth and conflict recount, against `feedback_direction_by_outcome.tsv`: for a
candidate node, its disease breadth is the count of distinct `outcome_id` rows
with `outcome_class = disease` whose `direction_class` matches the node's
`coherent_action` (clean_increase for an increase action, clean_decrease for a
decrease action). A conflict is present when the node carries both a
clean_increase and a clean_decrease row across its anchor and disease outcomes.
These reproduce the `disease_breadth` and `conflict` fields in
`build_report.json`.
