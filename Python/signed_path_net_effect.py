#!/usr/bin/env python3
"""
signed_path_net_effect.py

Signed-path net-effect analysis over the AXIOM curated knowledge graph.

WHAT THIS DOES
--------------
For a configurable set of outcome ("target") nodes, this script finds every
other node that can reach a target through directed edges within a depth
limit, and for each such source node it reports how a change in that source
propagates to the target along the graph's edge signs.

The net sign of a path is the product of its edge signs, so two suppressions
compose to a net increase, and so on. A path that crosses any sign-
indeterminate edge is reported as indeterminate rather than assigned a false
net sign.

INTERPRETATION AND LIMITS
-------------------------
The net sign of a source with respect to a target answers: "if the source's
level or activity goes up, which direction does the target move." This is a
STRUCTURAL signed-reachability summary, not a dynamical prediction. It treats
each path as independent, assumes monotone propagation, does NOT model
combination logic at nodes (the graph carries none), does NOT weight by
evidence coverage, and is sensitive to MAX_DEPTH. Read it as a ranked set of
hypotheses grounded in the curated edge signs, not as a simulation.

The edge sign map lives in lib/graph_common.py (EDGE_SIGN) and is shared with
cycle_analysis.py and target_control.py. Review it there.

Pure standard library plus the shared lib/graph_common module.
"""

import sys
import csv
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from graph_common import (
    load_graph_data,
    build_reverse_adj,
    report_unmapped,
    report_assertions,
    EXPORT_DIR,
)


# ----------------------------- configuration -----------------------------

DB_PATH = r"E:/bin/axiom/Python/lib/data/axiom_graph.db"

# Target (outcome) node selection. A node is a target if it matches ANY of
# these selectors. Leave a selector empty to disable it.
TARGET_NODE_IDS = [
    14, 171,                                    # organismal outcomes: aging, maximum lifespan
    67, 53, 766, 75, 84, 767,                   # hallmarks of aging (1 of 2)
    100, 11, 104, 768, 109, 142,                # hallmarks of aging (2 of 2)
    305, 353, 37, 769, 770, 771, 772, 773, 774, # cardiovascular
    775, 434, 504, 480, 503,                    # cancer
    239, 614, 411, 776, 777,                    # neurological
    591, 778,                                   # metabolic
    613, 309, 313, 164,                         # musculoskeletal
    271, 194, 779, 780, 781,                    # sensory
    782, 783,                                   # respiratory
    38, 784,                                    # renal / urologic
    466,                                        # multisystem: frailty
]
TARGET_NODE_TYPES = []             # e.g. ["phenotype"] sweeps all phenotype nodes
TARGET_NAME_CONTAINS = []          # case-insensitive substrings, e.g. ["alzheimer"]

# Optional desired-direction per target node id:
#   -1 => a LOWER target value is the desired outcome (e.g. organismal aging)
#   +1 => a HIGHER target value is the desired outcome (e.g. maximum lifespan)
# Targets absent from this dict get raw net sign only, with no good/bad label.
TARGET_POLARITY = {
    14: -1,     # organismal aging: less is better
    171: +1,    # maximum lifespan: more is better

    # hallmarks of aging. 10 name a damage/dysfunction, so less is better;
    # proteostasis and macroautophagy name the salutary state, so more is better.
    67: -1,     # genomic instability
    53: -1,     # telomere attrition
    766: -1,    # epigenetic alterations   (REVIEW: neutral-sounding name, deleterious in aging framing)
    75: +1,     # proteostasis   (salutary state, NOT "loss of proteostasis")
    84: +1,     # macroautophagy   (salutary process, NOT "disabled macroautophagy")
    767: -1,    # deregulated nutrient sensing
    100: -1,    # mitochondrial dysfunction
    11: -1,     # cellular senescence   (organism-level reading; protective in the cancer context)
    104: -1,    # stem cell exhaustion
    768: -1,    # altered intercellular communication   (REVIEW: neutral-sounding name, deleterious in aging framing)
    109: -1,    # inflammaging
    142: -1,    # gut dysbiosis

    # age-related diseases and syndromes: all pathology, so less is better.
    305: -1,    # atherosclerosis
    353: -1,    # hypertension
    37: -1,     # vascular calcification
    769: -1,    # coronary artery disease
    770: -1,    # heart failure
    771: -1,    # stroke
    772: -1,    # atrial fibrillation
    773: -1,    # calcific aortic stenosis
    774: -1,    # peripheral artery disease
    775: -1,    # cancer
    434: -1,    # hepatocellular carcinoma
    504: -1,    # prostate cancer
    480: -1,    # melanoma
    503: -1,    # cancer mortality
    239: -1,    # Alzheimer's disease
    614: -1,    # Parkinson's disease
    411: -1,    # age-related cognitive decline
    776: -1,    # dementia
    777: -1,    # vascular dementia
    591: -1,    # age-related metabolic dysfunction
    778: -1,    # type 2 diabetes mellitus
    613: -1,    # osteoarthritis
    309: -1,    # senile osteoporosis
    313: -1,    # bone fragility
    164: -1,    # sarcopenia
    271: -1,    # age-related macular degeneration
    194: -1,    # brunescent cataract
    779: -1,    # age-related cataract
    780: -1,    # glaucoma
    781: -1,    # presbycusis
    782: -1,    # chronic obstructive pulmonary disease
    783: -1,    # idiopathic pulmonary fibrosis
    38: -1,     # chronic kidney disease
    784: -1,    # benign prostatic hyperplasia
    466: -1,    # frailty
}

MAX_DEPTH = 4                       # max number of edges in an enumerated path
MAX_PATHS_PER_TARGET = 3_000_000    # safety cap per target; warns if exceeded
TOP_N_PRINT = 40                    # rows per target printed to stdout
OUTPUT_TSV = "signed_path_net_effect.tsv"   # full results; set to None to skip

# If True, print every phenotype/process/condition node (id, name) and exit,
# so you can choose which to treat as hallmark or disease targets.
LIST_CANDIDATE_TARGETS = False
CANDIDATE_TARGET_TYPES = ["phenotype", "process", "condition"]


# ------------------------------ selection --------------------------------

def select_targets(nodes):
    """Resolve the configured selectors to a set of target node ids."""
    wanted = set()
    id_set = set(TARGET_NODE_IDS)
    type_set = {t.lower() for t in TARGET_NODE_TYPES}
    name_subs = [s.lower() for s in TARGET_NAME_CONTAINS]

    for nid, (name, ntype) in nodes.items():
        if nid in id_set:
            wanted.add(nid)
            continue
        if ntype and ntype.lower() in type_set:
            wanted.add(nid)
            continue
        low = (name or "").lower()
        if any(sub in low for sub in name_subs):
            wanted.add(nid)
    return wanted


# ------------------------------ analysis ---------------------------------

def analyze_target(target_id, reverse_adj, max_depth, per_target_cap):
    """Backward DFS from a target over reversed edges.

    Returns (agg, path_count, truncated) where agg maps
        source_id -> {"pos": int, "neg": int, "indet": int, "shortest": int}
    counting, for that source, how many enumerated paths to the target are
    net-positive, net-negative, or sign-indeterminate, plus the shortest
    path length in edges.
    """
    agg = {}
    path_count = 0
    truncated = False

    # Stack frames: (node, depth, running_sign, indet_flag, visited_frozenset)
    stack = [(target_id, 0, 1, False, frozenset((target_id,)))]

    while stack:
        node, depth, sign, indet, visited = stack.pop()
        if depth >= max_depth:
            continue
        for subj, esign in reverse_adj.get(node, ()):
            if subj in visited:
                continue
            new_indet = indet or (esign == 0)
            new_sign = sign if new_indet else sign * esign
            new_depth = depth + 1

            rec = agg.get(subj)
            if rec is None:
                rec = {"pos": 0, "neg": 0, "indet": 0, "shortest": None}
                agg[subj] = rec
            if new_indet:
                rec["indet"] += 1
            elif new_sign > 0:
                rec["pos"] += 1
            else:
                rec["neg"] += 1
            if rec["shortest"] is None or new_depth < rec["shortest"]:
                rec["shortest"] = new_depth

            path_count += 1
            if path_count > per_target_cap:
                truncated = True
                stack = []
                break

            stack.append(
                (subj, new_depth, new_sign, new_indet, visited | {subj})
            )

    return agg, path_count, truncated


def verdict(rec):
    """Net-sign verdict for a source with respect to a target."""
    p, n = rec["pos"], rec["neg"]
    if p > 0 and n == 0:
        return "increase_raises_target"
    if n > 0 and p == 0:
        return "increase_lowers_target"
    if p > 0 and n > 0:
        return "mixed"
    return "indeterminate"


def recommended_direction(v, polarity):
    """Given a verdict and an optional target polarity, recommend whether to
    increase or decrease the source to move the target the desired way."""
    if polarity is None:
        return "unknown"
    if v == "mixed":
        return "ambiguous"
    if v == "indeterminate":
        return "indeterminate"
    net = +1 if v == "increase_raises_target" else -1
    return "increase" if (polarity * net) > 0 else "decrease"


def sort_key(item):
    """Order sources: consistent-sign first, then shortest path, then more
    determinate paths, then more indeterminate paths."""
    _sid, rec, v = item
    consistent = 0 if v in ("increase_lowers_target", "increase_raises_target") else 1
    shortest = rec["shortest"] if rec["shortest"] is not None else 999
    determinate = rec["pos"] + rec["neg"]
    return (consistent, shortest, -determinate, -rec["indet"])


# ------------------------------- output ----------------------------------

def main():
    nodes, edges, unmapped, assertions = load_graph_data(DB_PATH)
    reverse_adj = build_reverse_adj(edges, skip_self_loops=True)
    report_unmapped(unmapped, sys.stderr)
    report_assertions(assertions, sys.stderr)

    if LIST_CANDIDATE_TARGETS:
        wanted_types = {t.lower() for t in CANDIDATE_TARGET_TYPES}
        rows = [
            (nid, name, ntype)
            for nid, (name, ntype) in nodes.items()
            if ntype and ntype.lower() in wanted_types
        ]
        rows.sort(key=lambda r: (r[2], r[1].lower()))
        print(f"Candidate target nodes (types: {', '.join(CANDIDATE_TARGET_TYPES)}):")
        for nid, name, ntype in rows:
            print(f"  [{nid}] ({ntype}) {name}")
        print(f"\n{len(rows)} candidate nodes. "
              f"Set TARGET_NODE_IDS / TARGET_NODE_TYPES / TARGET_NAME_CONTAINS "
              f"and rerun with LIST_CANDIDATE_TARGETS = False.")
        return

    targets = select_targets(nodes)
    if not targets:
        print("No target nodes matched the configured selectors.", file=sys.stderr)
        return

    tsv_rows = []
    for target_id in sorted(targets):
        if target_id not in nodes:
            print(f"WARNING: target id {target_id} not in graph; skipping.",
                  file=sys.stderr)
            continue
        tname, ttype = nodes[target_id]
        polarity = TARGET_POLARITY.get(target_id)

        agg, path_count, truncated = analyze_target(
            target_id, reverse_adj, MAX_DEPTH, MAX_PATHS_PER_TARGET
        )

        scored = [(sid, rec, verdict(rec)) for sid, rec in agg.items()]
        scored.sort(key=sort_key)

        pol_label = {None: "unspecified", -1: "lower is better",
                     1: "higher is better"}[polarity]
        print("=" * 78)
        print(f"TARGET [{target_id}] {tname}  ({ttype})   "
              f"desired direction: {pol_label}")
        print(f"reachable sources: {len(agg)}   paths enumerated: {path_count}"
              f"   depth cap: {MAX_DEPTH}")
        if truncated:
            print("  NOTE: path cap hit; results are partial. Lower MAX_DEPTH "
                  "or raise MAX_PATHS_PER_TARGET.")
        print("-" * 78)
        print(f"{'src_id':>6}  {'d':>1}  {'pos':>4} {'neg':>4} {'ind':>4}  "
              f"{'verdict':<24} {'do':<10} name")

        for sid, rec, v in scored[:TOP_N_PRINT]:
            name, _ntype = nodes.get(sid, ("<unknown>", "?"))
            direction = recommended_direction(v, polarity)
            print(f"{sid:>6}  {rec['shortest']:>1}  "
                  f"{rec['pos']:>4} {rec['neg']:>4} {rec['indet']:>4}  "
                  f"{v:<24} {direction:<10} {name}")

        for sid, rec, v in scored:
            name, ntype = nodes.get(sid, ("<unknown>", "?"))
            direction = recommended_direction(v, polarity)
            tsv_rows.append({
                "target_id": target_id,
                "target_name": tname,
                "source_id": sid,
                "source_name": name,
                "source_node_type": ntype,
                "shortest_edges": rec["shortest"],
                "pos_paths": rec["pos"],
                "neg_paths": rec["neg"],
                "indeterminate_paths": rec["indet"],
                "net_sign_verdict": v,
                "recommended_source_direction": direction,
            })
        print("")

    if OUTPUT_TSV and tsv_rows:
        fieldnames = ["target_id", "target_name", "source_id", "source_name",
                      "source_node_type", "shortest_edges", "pos_paths",
                      "neg_paths", "indeterminate_paths", "net_sign_verdict",
                      "recommended_source_direction"]
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = EXPORT_DIR / OUTPUT_TSV
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(tsv_rows)
        print(f"Wrote {len(tsv_rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
