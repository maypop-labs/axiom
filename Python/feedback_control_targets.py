#!/usr/bin/env python3
"""
feedback_control_targets.py

Find the intervention targets that both break the amplifying feedback in the
AXIOM graph and have a clean directional call toward slowing aging. This is
the intersection of two analyses:

  1. Feedback structure. The graph's aging dynamics live in a feedback core
     (the nontrivial strongly connected components). Two control objects are
     computed over it:
       a. The exact minimum feedback vertex set (FVS): the smallest set of
          nodes whose removal makes the core acyclic, breaking ALL feedback.
          Solved exactly as an integer program (scipy.optimize.milp / HiGHS)
          via the rank formulation, so it needs no cycle enumeration and is
          not length-capped.
       b. The minimum positive-cycle hitting set: the smallest set of nodes
          that breaks every POSITIVE (amplifying) cycle while leaving the
          negative (homeostatic) loops intact. This is the goal-aligned
          object, because we want to break vicious cycles, not the built-in
          brakes. It is computed over positive cycles enumerated up to
          MAX_CYCLE_LENGTH, so it is a tight upper bound on the true
          positive-FVS rather than a proven global minimum.
     Positive-cycle participation per node is also reported as a robust
     centrality ranking, since minimum sets are non-unique.

  2. Directionality. The signed-path net-effect verdicts (read from
     signed_path_net_effect.tsv) say, for each node, whether increasing it
     raises or lowers the aging outcome. A node is "clean" only if that
     verdict is unambiguous (not mixed, not indeterminate).

The intersection keeps nodes that are central to the positive feedback AND
clean-directional (the high-leverage set), and separately flags nodes that
are central but ambiguous (loop-breakers with no safe push direction).

INTERPRETATION AND LIMITS
-------------------------
This is a structural analysis over curated topology and edge signs, not a
dynamical simulation. Participation reflects loop density in the curation,
not effect magnitude. Minimum sets are non-unique; the participation ranking
is the stable signal. The positive-cycle hitting set inherits the length cap;
the all-cycle FVS does not.

Reads axiom_graph.db and (optionally) signed_path_net_effect.tsv from the
export directory. Requires networkx and scipy.
"""

import sys
import csv
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

import numpy as np
import networkx as nx
from scipy.optimize import milp, LinearConstraint, Bounds

from graph_common import (
    DB_PATH,
    load_graph_data,
    build_digraph,
    resolve_pair_signs,
    report_unmapped,
    EXPORT_DIR,
    ANCHOR_OUTCOME_ID,
    SECONDARY_ANCHOR_ID,
    HALLMARK_OUTCOME_IDS,
    DISEASE_OUTCOME_IDS,
    WELL_FED_INDEGREE_K,
    breadth_floor_for,
    count_well_fed_diseases,
)


# ----------------------------- configuration -----------------------------

# Direction is judged against the full outcome set (anchor + secondary anchor +
# hallmarks + diseases), imported from graph_common. The anchor (organismal
# aging) is the gate a lead must pass; hallmark breadth and disease breadth are
# counted separately. The disease-breadth floor a lead must clear is computed
# from disease-side curation depth (graph_common.breadth_floor_for) and applied
# in build_report.py; this pass only reports it for context.

MAX_CYCLE_LENGTH = 8                # length bound for positive-cycle enumeration
MAX_CYCLES = 200_000               # safety cap on enumerated cycles
PARTICIPATION_PCT_FLOOR = 8.0       # a node is "central" at or above this share
                                    # of positive cycles (or if in a min set)
MILP_TIME_LIMIT = 120.0             # seconds per integer program before fallback

SIGNED_PATH_TSV = "signed_path_net_effect.tsv"   # read from EXPORT_DIR
OUTPUT_TSV = "feedback_control_targets.tsv"        # node-level summary (EXPORT_DIR)
OUTPUT_DIRECTION_TSV = "feedback_direction_by_outcome.tsv"  # long format (EXPORT_DIR)


# ------------------------- feedback structure ----------------------------

def nontrivial_sccs(g):
    """Return SCCs that carry feedback: size >= 2, or a singleton self-loop."""
    out = []
    for comp in nx.strongly_connected_components(g):
        if len(comp) >= 2:
            out.append(comp)
        else:
            (only,) = tuple(comp)
            if g.has_edge(only, only):
                out.append(comp)
    return out


def cycle_parity(cycle_nodes, pair_signs):
    """positive_feedback / negative_feedback / indeterminate / conflicting."""
    product = 1
    saw_neutral = False
    n = len(cycle_nodes)
    for i in range(n):
        s = pair_signs.get((cycle_nodes[i], cycle_nodes[(i + 1) % n]))
        if s is None:
            return "conflicting"
        if s == 0:
            saw_neutral = True
        else:
            product *= s
    if saw_neutral:
        return "indeterminate"
    return "positive_feedback" if product > 0 else "negative_feedback"


# ------------------------- integer programs ------------------------------

def _greedy_fvs(sub):
    """Fallback FVS: repeatedly remove the node on the most simple cycles."""
    h = sub.copy()
    removed = set()
    while not nx.is_directed_acyclic_graph(h):
        counts = Counter()
        for cyc in nx.simple_cycles(h, length_bound=MAX_CYCLE_LENGTH):
            for node in cyc:
                counts[node] += 1
        if not counts:  # only long cycles beyond the bound remain
            for node in list(h.nodes()):
                if h.has_edge(node, node):
                    counts[node] += 1
            if not counts:
                # remove an arbitrary node from a remaining SCC
                for comp in nx.strongly_connected_components(h):
                    if len(comp) >= 2:
                        counts[next(iter(comp))] += 1
                        break
        victim = counts.most_common(1)[0][0]
        removed.add(victim)
        h.remove_node(victim)
    return removed


def exact_min_fvs(g):
    """Exact minimum feedback vertex set of g, computed per SCC.

    Per SCC integer program (rank formulation): binary x_v (1 if removed) and
    continuous rank r_v in [0, k]; for every arc u->v of the SCC,
        (k+1) * (x_u + x_v) + r_v - r_u >= 1.
    If both endpoints are kept the constraint forces r_v >= r_u + 1, so a
    consistent ordering exists iff the kept subgraph is acyclic. Minimizing
    sum(x_v) yields a minimum FVS. Self-loops force their node in.
    """
    fvs = set()
    for comp in nontrivial_sccs(g):
        nodes = sorted(comp)
        k = len(nodes)
        if k == 1:
            fvs.add(nodes[0])  # singleton self-loop
            continue
        idx = {n: i for i, n in enumerate(nodes)}
        sub = g.subgraph(nodes)
        edges = list(sub.edges())
        m = len(edges)
        big_m = k + 1
        a = np.zeros((m, 2 * k))
        for r, (u, v) in enumerate(edges):
            iu, iv = idx[u], idx[v]
            a[r, iu] += big_m
            a[r, iv] += big_m
            a[r, k + iv] += 1.0
            a[r, k + iu] -= 1.0
        cost = np.concatenate([np.ones(k), np.zeros(k)])
        integ = np.concatenate([np.ones(k), np.zeros(k)])
        lower = np.zeros(2 * k)
        upper = np.concatenate([np.ones(k), np.full(k, float(k))])
        con = LinearConstraint(a, lb=np.ones(m), ub=np.inf)
        res = milp(
            cost, constraints=[con], integrality=integ,
            bounds=Bounds(lower, upper),
            options={"time_limit": MILP_TIME_LIMIT},
        )
        if res.x is None:
            print(f"  FVS solver did not return for an SCC of size {k}; "
                  f"using greedy fallback.", file=sys.stderr)
            fvs |= _greedy_fvs(sub)
        else:
            fvs |= {nodes[i] for i in range(k) if res.x[i] > 0.5}
    return fvs


def _greedy_hitting_set(cycles):
    remaining = [set(c) for c in cycles]
    chosen = set()
    while remaining:
        counts = Counter()
        for c in remaining:
            for n in c:
                counts[n] += 1
        pick = counts.most_common(1)[0][0]
        chosen.add(pick)
        remaining = [c for c in remaining if pick not in c]
    return chosen


def exact_min_hitting_set(cycles):
    """Minimum set of nodes intersecting every cycle in `cycles` (set cover)."""
    if not cycles:
        return set()
    nodes = sorted({n for c in cycles for n in c})
    idx = {n: i for i, n in enumerate(nodes)}
    k = len(nodes)
    m = len(cycles)
    a = np.zeros((m, k))
    for r, c in enumerate(cycles):
        for n in set(c):
            a[r, idx[n]] = 1.0
    con = LinearConstraint(a, lb=np.ones(m), ub=np.inf)
    res = milp(
        np.ones(k), constraints=[con], integrality=np.ones(k),
        bounds=Bounds(np.zeros(k), np.ones(k)),
        options={"time_limit": MILP_TIME_LIMIT},
    )
    if res.x is None:
        print("  hitting-set solver did not return; using greedy fallback.",
              file=sys.stderr)
        return _greedy_hitting_set(cycles)
    return {nodes[i] for i in range(k) if res.x[i] > 0.5}


# ------------------------------- join ------------------------------------

def load_signed_all(path):
    """Read the signed-path TSV once into a nested map:
        {source_id: {target_id: (verdict, recommended_direction)}}.
    Also returns the set of outcome (target) ids that appear at least once,
    which is the data-driven inventory of analyzable outcomes for this build.
    Returns ({}, set()) if the file is absent.
    """
    lookup = {}
    outcomes_present = set()
    if not path.exists():
        return lookup, outcomes_present
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                tid = int(row["target_id"])
                sid = int(row["source_id"])
            except (KeyError, ValueError):
                continue
            outcomes_present.add(tid)
            lookup.setdefault(sid, {})[tid] = (
                row.get("net_sign_verdict", ""),
                row.get("recommended_source_direction", ""),
            )
    return lookup, outcomes_present


def summarize_directions(per_outcome, hallmark_ids, disease_ids):
    """Collapse one node's per-outcome directions into a coherent-action summary.

    per_outcome: {target_id: (verdict, direction)} for a single source node.
    Returns the clean-increase / clean-decrease outcome sets, the coherent
    action (or None on conflict or no clean call), the favorable set under that
    action, hallmark and disease breadth, the conflict flag and its outcomes,
    and the unresolved count. Conflict is strict: it requires a clean opposite
    recommendation across outcomes, not a mixed or indeterminate one.
    """
    inc, dec, unresolved = set(), set(), set()
    for tid, (verdict, direction) in per_outcome.items():
        dclass = direction_class(verdict, direction)
        if dclass == "clean_increase":
            inc.add(tid)
        elif dclass == "clean_decrease":
            dec.add(tid)
        else:
            unresolved.add(tid)

    conflict = bool(inc and dec)
    if conflict:
        action, favorable, conflict_ids = None, set(), sorted(inc | dec)
    elif inc:
        action, favorable, conflict_ids = "increase", inc, []
    elif dec:
        action, favorable, conflict_ids = "decrease", dec, []
    else:
        action, favorable, conflict_ids = None, set(), []

    hset, dset = set(hallmark_ids), set(disease_ids)
    return {
        "action": action,
        "favorable": favorable,
        "favorable_hallmarks": sorted(favorable & hset),
        "favorable_diseases": sorted(favorable & dset),
        "hallmark_breadth": len(favorable & hset),
        "disease_breadth": len(favorable & dset),
        "conflict": conflict,
        "conflict_ids": conflict_ids,
        "unresolved_count": len(unresolved),
    }


def direction_class(verdict, direction):
    """Collapse a signed-path verdict into a control-relevant class."""
    if verdict == "increase_lowers_target":
        return "clean_increase" if direction == "increase" else "clean_decrease"
    if verdict == "increase_raises_target":
        return "clean_decrease" if direction == "decrease" else "clean_increase"
    if verdict == "mixed":
        return "ambiguous"
    if verdict == "indeterminate":
        return "indeterminate"
    return "none"


# ------------------------------- main ------------------------------------

def main():
    nodes, edges, unmapped = load_graph_data(DB_PATH)
    report_unmapped(unmapped, sys.stderr)

    g = build_digraph(nodes, edges, include_self_loops=True)
    pair_signs = resolve_pair_signs(edges)

    cores = nontrivial_sccs(g)
    core_nodes = set().union(*cores) if cores else set()
    multi = [c for c in cores if len(c) >= 2]

    print("=" * 78)
    print("AXIOM feedback-control targets")
    print("=" * 78)
    if not core_nodes:
        print("No feedback core: the graph is acyclic apart from self-loops. "
              "Nothing to break.")
        return
    sizes = sorted((len(c) for c in multi), reverse=True)
    print(f"feedback core: {len(cores)} nontrivial SCC(s), "
          f"{len(core_nodes)} nodes; multi-node SCC sizes {sizes}")

    # Exact minimum FVS over the whole core (all cycles, not length-capped).
    fvs = exact_min_fvs(g)
    print(f"exact minimum feedback vertex set (all cycles): {len(fvs)} nodes")

    # Positive cycles within the core (length-capped enumeration).
    core_sub = g.subgraph(core_nodes)
    pos_cycles = []
    total = 0
    truncated = False
    for cyc in nx.simple_cycles(core_sub, length_bound=MAX_CYCLE_LENGTH):
        if len(cyc) < 2:
            continue
        total += 1
        if cycle_parity(cyc, pair_signs) == "positive_feedback":
            pos_cycles.append(tuple(cyc))
        if total >= MAX_CYCLES:
            truncated = True
            break

    participation = Counter()
    for c in pos_cycles:
        for n in set(c):
            participation[n] += 1
    n_pos = len(pos_cycles)
    hitting = exact_min_hitting_set(pos_cycles)
    print(f"positive cycles (length <= {MAX_CYCLE_LENGTH}): {n_pos}"
          f"{' (cap hit; partial)' if truncated else ''}")
    print(f"minimum positive-cycle hitting set: {len(hitting)} nodes")

    signed, outcomes_present = load_signed_all(EXPORT_DIR / SIGNED_PATH_TSV)
    if signed:
        n_hall = len([h for h in HALLMARK_OUTCOME_IDS if h in outcomes_present])
        n_dis = len([d for d in DISEASE_OUTCOME_IDS if d in outcomes_present])
        anchor_name = nodes.get(ANCHOR_OUTCOME_ID, ("?", "?"))[0]
        print(f"signed-path loaded: {len(signed)} source nodes; analyzable "
              f"outcomes present: {n_hall}/{len(HALLMARK_OUTCOME_IDS)} hallmarks, "
              f"{n_dis}/{len(DISEASE_OUTCOME_IDS)} diseases")
        well_fed = count_well_fed_diseases(edges)
        floor = breadth_floor_for(well_fed)
        print(f"anchor outcome: [{ANCHOR_OUTCOME_ID}] {anchor_name}; "
              f"well-fed diseases (in-degree >= {WELL_FED_INDEGREE_K}): "
              f"{well_fed}; disease breadth floor "
              f"(applied in build_report): {floor}")
    else:
        print(f"(no {SIGNED_PATH_TSV} in the export dir; run "
              f"signed_path_net_effect.py first for the directional join)")
    print("-" * 78)

    # Assemble one summary row per core node, plus long-format (node, outcome)
    # rows for audit and for the report's pivot.
    hset, dset = set(HALLMARK_OUTCOME_IDS), set(DISEASE_OUTCOME_IDS)
    rows = []
    long_rows = []
    for nid in core_nodes:
        name, ntype = nodes.get(nid, ("?", "?"))
        scc_size = next((len(c) for c in cores if nid in c), 1)
        part = participation.get(nid, 0)
        pct = (100.0 * part / n_pos) if n_pos else 0.0

        per_outcome = signed.get(nid, {})
        summ = summarize_directions(per_outcome, HALLMARK_OUTCOME_IDS,
                                    DISEASE_OUTCOME_IDS)

        for tid, (verdict, direction) in sorted(per_outcome.items()):
            if tid == ANCHOR_OUTCOME_ID:
                oclass = "anchor"
            elif tid == SECONDARY_ANCHOR_ID:
                oclass = "secondary_anchor"
            elif tid in hset:
                oclass = "hallmark"
            elif tid in dset:
                oclass = "disease"
            else:
                oclass = "other"
            long_rows.append({
                "node_id": nid,
                "node_name": name,
                "outcome_id": tid,
                "outcome_name": nodes.get(tid, ("?", "?"))[0],
                "outcome_class": oclass,
                "signed_verdict": verdict,
                "recommended_direction": direction,
                "direction_class": direction_class(verdict, direction),
            })

        aging_verdict, aging_dir = per_outcome.get(ANCHOR_OUTCOME_ID, ("", ""))
        aging_dclass = direction_class(aging_verdict, aging_dir)
        aging_favorable = ANCHOR_OUTCOME_ID in summ["favorable"]
        life_favorable = SECONDARY_ANCHOR_ID in summ["favorable"]

        central = (nid in hitting) or (pct >= PARTICIPATION_PCT_FLOOR)
        if not central:
            priority = ""
        elif summ["conflict"]:
            priority = "cross_outcome_conflict"
        elif summ["action"] is not None:
            priority = "clean_leverage"
        else:
            priority = "central_ambiguous"

        rows.append({
            "node_id": nid, "node_name": name, "node_type": ntype,
            "scc_size": scc_size, "pos_participation": part,
            "pos_participation_pct": round(pct, 1),
            "in_min_fvs": "yes" if nid in fvs else "no",
            "in_pos_hitting_set": "yes" if nid in hitting else "no",
            "coherent_action": summ["action"] or "",
            "aging_favorable": "yes" if aging_favorable else "no",
            "aging_direction_class": aging_dclass,
            "lifespan_favorable": "yes" if life_favorable else "no",
            "hallmark_breadth": summ["hallmark_breadth"],
            "disease_breadth": summ["disease_breadth"],
            "favorable_hallmark_ids": ";".join(str(i) for i in summ["favorable_hallmarks"]),
            "favorable_disease_ids": ";".join(str(i) for i in summ["favorable_diseases"]),
            "conflict": "yes" if summ["conflict"] else "no",
            "conflict_outcome_ids": ";".join(str(i) for i in summ["conflict_ids"]),
            "unresolved_count": summ["unresolved_count"],
            "priority": priority,
        })

    rows.sort(key=lambda r: (
        r["priority"] == "clean_leverage", r["disease_breadth"],
        r["in_pos_hitting_set"] == "yes", r["pos_participation"]), reverse=True)

    def _named(ids):
        return ", ".join(f"[{i}] {nodes.get(i, ('?','?'))[0]}"
                         for i in sorted(ids))

    print(f"minimum FVS nodes: {_named(fvs)}")
    print(f"minimum positive-cycle hitting set: {_named(hitting)}")
    print("")

    leverage = [r for r in rows if r["priority"] == "clean_leverage"]
    conflicted = [r for r in rows if r["priority"] == "cross_outcome_conflict"]
    ambig = [r for r in rows if r["priority"] == "central_ambiguous"]

    print("CLEAN LEVERAGE (central, coherent action, no cross-outcome conflict):")
    print(f"  {'part%':>6}  {'action':>8}  {'aging':>5}  {'hall':>4} {'dis':>4}  name")
    for r in leverage:
        print(f"  {r['pos_participation_pct']:>5}%  {r['coherent_action']:>8}  "
              f"{r['aging_favorable']:>5}  "
              f"{r['hallmark_breadth']:>4} {r['disease_breadth']:>4}  "
              f"[{r['node_id']}] {r['node_name']}")
    print("")
    print("CROSS-OUTCOME CONFLICT (central; favors some outcomes, harms others):")
    for r in conflicted:
        print(f"  {r['pos_participation_pct']:>5}%  conflict on outcomes "
              f"{r['conflict_outcome_ids']}  [{r['node_id']}] {r['node_name']}")
    print("")
    print("CENTRAL BUT AMBIGUOUS (loop-breakers with no clean direction anywhere):")
    for r in ambig:
        print(f"  {r['pos_participation_pct']:>5}%  [{r['node_id']}] {r['node_name']}")
    print("")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXPORT_DIR / OUTPUT_TSV
    fieldnames = ["node_id", "node_name", "node_type", "scc_size",
                  "pos_participation", "pos_participation_pct", "in_min_fvs",
                  "in_pos_hitting_set", "coherent_action", "aging_favorable",
                  "aging_direction_class", "lifespan_favorable",
                  "hallmark_breadth", "disease_breadth", "favorable_hallmark_ids",
                  "favorable_disease_ids", "conflict", "conflict_outcome_ids",
                  "unresolved_count", "priority"]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} core nodes to {out_path}")

    long_path = EXPORT_DIR / OUTPUT_DIRECTION_TSV
    long_fields = ["node_id", "node_name", "outcome_id", "outcome_name",
                   "outcome_class", "signed_verdict", "recommended_direction",
                   "direction_class"]
    with open(long_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=long_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(long_rows)
    print(f"Wrote {len(long_rows)} (node, outcome) rows to {long_path}")


if __name__ == "__main__":
    main()
