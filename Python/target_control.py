#!/usr/bin/env python3
"""
target_control.py

Structural target-control pass over the AXIOM graph for a chosen set of
outcome (target) nodes, default {14 organismal aging, 171 maximum lifespan}.

HONEST FRAMING
--------------
Target control (Gao, Liu, D'Souza, Barabasi 2014) was derived for linear
time-invariant dynamics. AXIOM carries no dynamics, so this computes a
STRUCTURAL heuristic, not a controllability guarantee, and minimal target
control is NP-hard, so the driver sets here are heuristic, not proven
minima. Read the output as structurally justified candidate control points
to cross-read against the signed-path net-effect results, not as proof.

METHOD
------
1. Ancestor restriction. Only a node that can reach a target can control it,
   so the analysis runs on the union of each target's ancestors plus the
   targets themselves (networkx.ancestors, exact, no depth cap).
2. Full-control reference (Liu, Slotine, Barabasi 2011). Maximum bipartite
   matching on the ancestor subgraph; the unmatched in-side nodes are the
   drivers needed to control the ENTIRE subgraph. Reported as an upper
   reference only.
3. Target-control backbone. From the same matching, follow matched in-edges
   backward from each target to the head of its matched path. Those heads
   are the target-control drivers: controlling a path head propagates
   control along the matched path to the target. This is the honest,
   matching-based backbone, not a line-for-line reproduction of Gao's
   greedy; it is labeled a heuristic throughout.
4. Signed-path join. If signed_path_net_effect.tsv is present, each driver
   is annotated with its net-effect verdict and recommended direction toward
   each target, so a driver that is also directionally favorable stands out
   as the actionable intersection.

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
    report_unmapped,
    EXPORT_DIR,
)


# ----------------------------- configuration -----------------------------

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

# Signed-path results to join against, if present (written by
# signed_path_net_effect.py into the export directory).
SIGNED_PATH_TSV = "signed_path_net_effect.tsv"

OUTPUT_TSV = "target_control.tsv"


# ------------------------------- matching --------------------------------

def maximum_matching(sub):
    """Maximum bipartite matching of the directed subgraph.

    Each node u contributes an out-copy ("o", u) and an in-copy ("i", v);
    every directed edge u -> v becomes a bipartite edge. Returns
    matched_in: {v: u} meaning edge u -> v is in the matching (each v has at
    most one matched in-edge), and the raw matching size.
    """
    b = nx.Graph()
    out_nodes = set()
    for u in sub.nodes():
        b.add_node(("o", u))
        b.add_node(("i", u))
        out_nodes.add(("o", u))
    for u, v in sub.edges():
        b.add_edge(("o", u), ("i", v))

    raw = nx.bipartite.maximum_matching(b, top_nodes=out_nodes)

    matched_in = {}
    for a, c in raw.items():
        # keep only ("o", u) -> ("i", v) orientation to avoid double counting
        if a[0] == "o" and c[0] == "i":
            matched_in[c[1]] = a[1]
    return matched_in, len(matched_in)


def path_head(target, matched_in):
    """Walk matched in-edges backward from target to its matched-path head.

    Returns the head node. Guards against cycles in the matched set by
    stopping if a node repeats.
    """
    cur = target
    seen = {cur}
    while cur in matched_in:
        pred = matched_in[cur]
        if pred in seen:
            break  # matched cycle; treat current as head
        seen.add(pred)
        cur = pred
    return cur


# ------------------------------- join ------------------------------------

def load_signed_path(path):
    """Load {(target_id, source_id): (verdict, direction)} from the
    signed-path TSV, or {} if the file is absent."""
    lookup = {}
    if not path.exists():
        return lookup
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            try:
                key = (int(row["target_id"]), int(row["source_id"]))
            except (KeyError, ValueError):
                continue
            lookup[key] = (
                row.get("net_sign_verdict", ""),
                row.get("recommended_source_direction", ""),
            )
    return lookup


# ------------------------------- main ------------------------------------

def main():
    nodes, edges, unmapped = load_graph_data(DB_PATH)
    report_unmapped(unmapped, sys.stderr)

    g = build_digraph(nodes, edges, include_self_loops=True)

    targets = [t for t in TARGET_NODE_IDS if t in g]
    missing = [t for t in TARGET_NODE_IDS if t not in g]
    for t in missing:
        print(f"WARNING: target id {t} not in graph; skipping.", file=sys.stderr)
    if not targets:
        print("No valid target nodes; nothing to do.", file=sys.stderr)
        return

    ancestor_set = set(targets)
    for t in targets:
        ancestor_set |= nx.ancestors(g, t)
    sub = g.subgraph(ancestor_set).copy()

    matched_in, matching_size = maximum_matching(sub)

    # Full-control driver set (Liu): in-side nodes with no matched in-edge.
    full_drivers = sorted(n for n in sub.nodes() if n not in matched_in)

    # Target-control drivers: heads of the matched paths reaching each target.
    target_drivers = {}   # target_id -> head driver id
    for t in targets:
        target_drivers[t] = path_head(t, matched_in)
    driver_set = sorted(set(target_drivers.values()))

    signed = load_signed_path(EXPORT_DIR / SIGNED_PATH_TSV)

    print("=" * 78)
    print("AXIOM structural target-control pass  (heuristic, not a guarantee)")
    print("=" * 78)
    tnames = ", ".join(f"[{t}] {nodes.get(t, ('?','?'))[0]}" for t in targets)
    print(f"targets: {tnames}")
    print(f"ancestor subgraph: {sub.number_of_nodes()} nodes, "
          f"{sub.number_of_edges()} edges")
    print(f"maximum matching size: {matching_size}")
    print(f"full-control driver set (Liu, upper reference): "
          f"{len(full_drivers)} nodes")
    print("-" * 78)

    print("target-control drivers (one matched-path head per target):")
    for t in targets:
        d = target_drivers[t]
        tname = nodes.get(t, ("?", "?"))[0]
        dname, dtype = nodes.get(d, ("?", "?"))
        note = " (target is its own driver: no matched in-edge)" if d == t else ""
        print(f"  target [{t}] {tname}  <-  driver [{d}] {dname} ({dtype}){note}")
        if signed:
            verdict, direction = signed.get((t, d), ("", ""))
            if verdict:
                print(f"      signed-path: {verdict}; recommended direction "
                      f"{direction}")
            else:
                print("      signed-path: no entry for this driver-target pair")
    print("")
    print(f"distinct target-control drivers: {len(driver_set)}")
    print("")

    if not signed:
        print("(No signed_path_net_effect.tsv found next to this script; run "
              "signed_path_net_effect.py first to enable the directional join.)")
        print("")

    if OUTPUT_TSV:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = EXPORT_DIR / OUTPUT_TSV
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, delimiter="\t")
            writer.writerow([
                "target_id", "target_name", "driver_id", "driver_name",
                "driver_node_type", "driver_is_target",
                "signed_path_verdict", "recommended_driver_direction",
            ])
            for t in targets:
                d = target_drivers[t]
                tname = nodes.get(t, ("?", "?"))[0]
                dname, dtype = nodes.get(d, ("?", "?"))
                verdict, direction = signed.get((t, d), ("", "")) if signed else ("", "")
                writer.writerow([
                    t, tname, d, dname, dtype, "yes" if d == t else "no",
                    verdict, direction,
                ])
        print(f"Wrote target-control drivers to {out_path}")


if __name__ == "__main__":
    main()
