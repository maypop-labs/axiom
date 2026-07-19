#!/usr/bin/env python3
"""
cycle_analysis.py

Feedback-structure census of the AXIOM graph. Its job is narrow: decide
whether the attractor-family control methods (feedback vertex set control,
stable-motif control, attractor analysis) have anything to act on.

The gating object is the strongly connected component (SCC) structure.
Feedback lives only inside an SCC of size two or more, or inside a single
node carrying a self-loop. If every SCC is a trivial singleton, the graph
is a DAG apart from self-loops, its minimum feedback vertex set is empty,
and the attractor-family methods are FORMALLY DEGENERATE on this graph.

Structure uses every directed edge, because direction, not sign, defines a
cycle. Edge signs (from graph_common.EDGE_SIGN) are used only to LABEL each
enumerated feedback loop: a product of +1 is positive feedback (amplifying,
enables multistability), a product of -1 is negative feedback (homeostatic
or oscillatory), a neutral edge on the loop makes the parity indeterminate,
and a sign-conflicting pair makes it conflicting. None of these are guessed.

Reads axiom_graph.db. Requires networkx.
"""

import sys
import csv
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import networkx as nx
from graph_common import (
    DB_PATH,
    load_graph_data,
    build_digraph,
    resolve_pair_signs,
    report_unmapped,
    report_assertions,
    EXPORT_DIR,
)


# ----------------------------- configuration -----------------------------

# Outcome nodes flagged for whether they sit inside any feedback core.
OUTCOME_NODE_IDS = [
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

MAX_CYCLE_LENGTH = 8                # length bound for simple-cycle enumeration
MAX_CYCLES = 200_000               # hard cap on enumerated cycles; warns if hit
OUTPUT_TSV = "cycle_analysis.tsv"   # per-cycle catalog; set to None to skip


# ------------------------------- analysis --------------------------------

def classify_sccs(g):
    """Split SCCs into trivial (singleton, no self-loop) and nontrivial
    (size >= 2, or singleton carrying a self-loop)."""
    sccs = list(nx.strongly_connected_components(g))
    trivial, nontrivial = [], []
    for comp in sccs:
        if len(comp) >= 2:
            nontrivial.append(comp)
        else:
            (only,) = tuple(comp)
            if g.has_edge(only, only):
                nontrivial.append(comp)
            else:
                trivial.append(comp)
    return sccs, trivial, nontrivial


def cycle_parity(cycle_nodes, pair_signs):
    """Label a node-cycle's feedback sign from its ordered-pair signs."""
    product = 1
    saw_neutral = False
    n = len(cycle_nodes)
    for i in range(n):
        a = cycle_nodes[i]
        b = cycle_nodes[(i + 1) % n]
        s = pair_signs.get((a, b))
        if s is None:
            return "conflicting"
        if s == 0:
            saw_neutral = True
        else:
            product *= s
    if saw_neutral:
        return "indeterminate"
    return "positive_feedback" if product > 0 else "negative_feedback"


def main():
    nodes, edges, unmapped, assertions = load_graph_data(DB_PATH)
    report_unmapped(unmapped, sys.stderr)
    report_assertions(assertions, sys.stderr)

    g = build_digraph(nodes, edges, include_self_loops=True)
    pair_signs = resolve_pair_signs(edges)

    self_loops = [(u, v) for u, v in nx.selfloop_edges(g)]
    sccs, trivial, nontrivial = classify_sccs(g)

    print("=" * 78)
    print("AXIOM cycle / feedback-structure census")
    print("=" * 78)
    print(f"nodes: {g.number_of_nodes()}   "
          f"directed edges (collapsed pairs): {g.number_of_edges()}   "
          f"self-loops: {len(self_loops)}")
    print(f"strongly connected components: {len(sccs)}   "
          f"trivial: {len(trivial)}   "
          f"nontrivial (feedback-bearing): {len(nontrivial)}")
    print("-" * 78)

    multi_node = [c for c in nontrivial if len(c) >= 2]
    if not nontrivial:
        print("VERDICT: the graph is a DAG apart from self-loops. The minimum")
        print("feedback vertex set is empty and there are no multi-node")
        print("attractors. FVS control, stable-motif control, and attractor")
        print("analysis are FORMALLY DEGENERATE on this graph.")
    elif not multi_node:
        print("VERDICT: the only feedback is self-loops; there are no")
        print("multi-node cycles. The attractor-family methods remain")
        print("effectively degenerate.")
    else:
        sizes = sorted((len(c) for c in multi_node), reverse=True)
        largest = max(multi_node, key=len)
        print(f"VERDICT: {len(multi_node)} multi-node feedback component(s) "
              f"exist; sizes {sizes}.")
        print(f"Largest feedback core has {len(largest)} nodes. Attractor-"
              f"family methods are viable, restricted to that core.")
    print("")

    for oid in OUTCOME_NODE_IDS:
        name = nodes.get(oid, ("<unknown>", "?"))[0]
        inside = any(oid in c for c in nontrivial)
        loc = "inside a feedback core" if inside else "outside every feedback core"
        extra = " (has a self-loop)" if g.has_edge(oid, oid) else ""
        print(f"outcome node [{oid}] {name}: {loc}{extra}")
    print("")

    if self_loops:
        sign_label = {1: "+", -1: "-", 0: "neutral", None: "conflicting"}
        print(f"self-loops ({len(self_loops)}):")
        for u, _v in self_loops:
            nm = nodes.get(u, ("<unknown>", "?"))[0]
            s = pair_signs.get((u, u))
            print(f"  [{u}] {nm}  (sign {sign_label.get(s, '?')})")
        print("")

    rows = []
    parity_counts = {
        "positive_feedback": 0, "negative_feedback": 0,
        "indeterminate": 0, "conflicting": 0,
    }
    total = 0
    truncated = False
    for cycle in nx.simple_cycles(g, length_bound=MAX_CYCLE_LENGTH):
        if len(cycle) < 2:
            continue  # self-loops reported separately above
        parity = cycle_parity(cycle, pair_signs)
        parity_counts[parity] += 1
        names = [nodes.get(n, ("<unknown>", "?"))[0] for n in cycle]
        rows.append({
            "length": len(cycle),
            "parity": parity,
            "node_ids": ">".join(str(n) for n in cycle),
            "node_names": " > ".join(names),
        })
        total += 1
        if total >= MAX_CYCLES:
            truncated = True
            break

    print(f"multi-node simple cycles (length <= {MAX_CYCLE_LENGTH}): {total}")
    if truncated:
        print(f"  NOTE: cycle cap {MAX_CYCLES} hit; counts are partial. Lower "
              f"MAX_CYCLE_LENGTH or raise MAX_CYCLES.")
    print(f"  positive feedback (amplifying):    {parity_counts['positive_feedback']}")
    print(f"  negative feedback (homeostatic):   {parity_counts['negative_feedback']}")
    print(f"  sign-indeterminate (neutral edge): {parity_counts['indeterminate']}")
    print(f"  sign-conflicting (mixed edges):    {parity_counts['conflicting']}")
    print("")

    if OUTPUT_TSV and rows:
        rows.sort(key=lambda r: (r["length"], r["parity"]))
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = EXPORT_DIR / OUTPUT_TSV
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["length", "parity", "node_ids", "node_names"],
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} cycles to {out_path}")


if __name__ == "__main__":
    main()
