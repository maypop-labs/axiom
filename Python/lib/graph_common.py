#!/usr/bin/env python3
"""
graph_common.py  (lib/)

Shared foundations for the AXIOM graph-analysis scripts:
signed_path_net_effect.py, cycle_analysis.py, and target_control.py all
import from here so the edge sign map and the database read have a single
authoritative definition.

THE ONE JUDGMENT LAYER
----------------------
EDGE_SIGN is the only place editorial judgment enters any of the analyses.
+1 means the subject increases the object's level or activity, -1 means it
decreases it, and 0 means the sign is not determinable from the edge type
alone. Paths or cycles crossing a 0-signed edge are reported as
sign-indeterminate rather than assigned a false sign. The borderline calls
are flagged with REVIEW; they are defensible defaults, not settled facts.
Changing a sign here changes every downstream analysis, which is the point:
it is explicit and curator-controlled.
"""

import sqlite3
from collections import defaultdict
from pathlib import Path


DB_PATH = r"E:/bin/axiom/Python/lib/data/axiom_graph.db"

# Directory for internal analysis artifacts (the TSV outputs of the graph-
# analysis scripts). This is NOT export_public/, which is reserved for the
# DrugBank-redacted, license-compliant artifacts published to the website.
PYTHON_DIR = Path(__file__).resolve().parent.parent   # .../Python
EXPORT_DIR = PYTHON_DIR / "export"


# ---- outcome taxonomy for the multi-outcome control analyses ---------------
# The anchor is the organismal outcome every lead is gated on; the secondary
# anchor is reported but does not gate. The hallmark and disease id lists drive
# the two separate breadth counts in feedback_control_targets.py and
# build_report.py. Membership is curated and explicit: node_type does not
# distinguish a hallmark process from a disease phenotype, so it must be stated
# here. Add a new outcome node's id to the appropriate list to fold it in.
ANCHOR_OUTCOME_ID = 14          # organismal aging (the gate)
SECONDARY_ANCHOR_ID = 171       # maximum lifespan (reported, not a gate)

HALLMARK_OUTCOME_IDS = [
    67, 53, 766, 75, 84, 767, 100, 11, 104, 768, 109, 142,
]

DISEASE_OUTCOME_IDS = [
    305, 353, 37, 769, 770, 771, 772, 773, 774,   # cardiovascular
    775, 434, 504, 480, 503,                       # cancer
    239, 614, 411, 776, 777,                       # neurological
    591, 778,                                      # metabolic
    613, 309, 313, 164,                            # musculoskeletal
    271, 194, 779, 780, 781,                       # sensory
    782, 783,                                      # respiratory
    38, 784,                                       # renal / urologic
    466,                                           # multisystem: frailty
]


# ---- breadth-floor policy (multi-outcome lead gate) ------------------------
# A disease outcome is "well fed" when its direct in-degree (curated incoming
# edges) is at least WELL_FED_INDEGREE_K. The lead disease-breadth floor rises
# by one for every BREADTH_FLOOR_STEP well-fed diseases, from BREADTH_FLOOR_BASE
# up to BREADTH_FLOOR_CAP. Tying the floor to disease-side curation depth makes
# it ratchet only as diseases are genuinely wired in, not as the graph grows
# overall, and keeps "lead" comparable across builds. Calibrated 2026-07 against
# the disease panel: 6 diseases well fed at K=5, so the floor sits at 2 today.
WELL_FED_INDEGREE_K = 5
BREADTH_FLOOR_BASE = 2
BREADTH_FLOOR_STEP = 8
BREADTH_FLOOR_CAP = 6


def breadth_floor_for(well_fed_count):
    """The lead disease-breadth floor for a given well-fed disease count."""
    return min(BREADTH_FLOOR_CAP,
               BREADTH_FLOOR_BASE + well_fed_count // BREADTH_FLOOR_STEP)


def count_well_fed_diseases(edges, disease_ids=None, k=WELL_FED_INDEGREE_K):
    """Count disease outcomes whose direct in-degree (incoming edges) is at
    least k, computed from the edges list returned by load_graph_data. Counts
    every incoming edge the same way the SQL in-degree does, so the two agree."""
    if disease_ids is None:
        disease_ids = DISEASE_OUTCOME_IDS
    wanted = set(disease_ids)
    indeg = {}
    for _subj, obj, _etype, _sign in edges:
        if obj in wanted:
            indeg[obj] = indeg.get(obj, 0) + 1
    return sum(1 for d in wanted if indeg.get(d, 0) >= k)


EDGE_SIGN = {
    # clearly positive (subject increases object)
    "activates": +1,
    "promotes": +1,
    "induces": +1,
    "increases": +1,
    "produces": +1,
    "synthesizes": +1,
    "stabilizes": +1,
    "supports": +1,
    "contributes_to": +1,
    "causes": +1,
    "transcribes": +1,      # TF -> gene product present. functional-flow positive.
    "encodes": +1,          # REVIEW: identity/flow (gene present -> protein present).
    "recruits": +1,         # REVIEW: borderline; increases object's local activity.

    # clearly negative (subject decreases object)
    "suppresses": -1,
    "inhibits": -1,
    "degrades": -1,
    "detoxifies": -1,       # REVIEW: removes/neutralizes the object.

    # sign-indeterminate (PTMs, structural, transport, context-dependent)
    "binds": 0,
    "part_of": 0,
    "matures_to": 0,
    "transports": 0,
    "regulates": 0,
    "phosphorylates": 0,    # sign depends on whether the PTM activates the target
    "deacetylates": 0,
    "s_nitrosylates": 0,
    "denitrosylates": 0,
    "catalyzes": 0,         # REVIEW: could be production (+) or degradation (-)
    "cleaves": 0,           # REVIEW: can inactivate or activate the substrate
    "displaces": 0,         # REVIEW: context-dependent
}


def sign_of(edge_type):
    """Return (sign, is_mapped). Unmapped edge types resolve to (0, False)
    so callers can both treat them as sign-indeterminate and report them."""
    if edge_type in EDGE_SIGN:
        return EDGE_SIGN[edge_type], True
    return 0, False


def load_graph_data(db_path=DB_PATH):
    """Read the graph once.

    Returns (nodes, edges, unmapped):
      nodes:    {node_id: (canonical_name, node_type)}
      edges:    list of (subject_id, object_id, edge_type, sign) for EVERY
                edge, self-loops included, sign resolved via EDGE_SIGN.
      unmapped: {edge_type: count} for any edge type absent from EDGE_SIGN.
    """
    conn = sqlite3.connect(db_path)
    try:
        nodes = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT id, canonical_name, node_type FROM nodes"
            )
        }
        edges = []
        unmapped = defaultdict(int)
        for subj, obj, etype in conn.execute(
            "SELECT subject_id, object_id, edge_type FROM edges"
        ):
            sign, mapped = sign_of(etype)
            if not mapped:
                unmapped[etype] += 1
            edges.append((subj, obj, etype, sign))
    finally:
        conn.close()
    return nodes, edges, dict(unmapped)


def build_reverse_adj(edges, skip_self_loops=True):
    """Reverse adjacency keyed by object_id: {obj: [(subj, sign), ...]}.

    Used to walk backward from a sink toward its upstream sources.
    Multi-edges are preserved as separate entries.
    """
    radj = defaultdict(list)
    for subj, obj, _etype, sign in edges:
        if skip_self_loops and subj == obj:
            continue
        radj[obj].append((subj, sign))
    return radj


def resolve_pair_signs(edges):
    """Collapse multi-edges to one sign per ordered (subject, object) pair.

    Returns {(subj, obj): resolved} where resolved is:
      +1 or -1 if the pair has exactly one non-neutral sign among its edges
               (neutral edges alongside a single signed edge do not erase it),
      None     if the pair carries BOTH a +1 and a -1 edge (a real conflict),
      0        if the pair carries only neutral edges.
    Used to label cycle parity; conflicts and neutrals are reported honestly
    rather than guessed.
    """
    by_pair = defaultdict(set)
    for subj, obj, _etype, sign in edges:
        by_pair[(subj, obj)].add(sign)
    resolved = {}
    for pair, signs in by_pair.items():
        nonzero = {s for s in signs if s != 0}
        if len(nonzero) > 1:
            resolved[pair] = None
        elif len(nonzero) == 1:
            resolved[pair] = next(iter(nonzero))
        else:
            resolved[pair] = 0
    return resolved


def build_digraph(nodes, edges, include_self_loops=True):
    """Build a networkx DiGraph carrying structure only.

    Node attributes: name, ntype. Edges are collapsed (a DiGraph keeps one
    edge per ordered pair); this is correct for reachability, strongly
    connected components, cycle existence, and matching, none of which
    depend on edge multiplicity. Sign labeling uses resolve_pair_signs
    separately, which sees the full multi-edge set.
    """
    import networkx as nx

    g = nx.DiGraph()
    for nid, (name, ntype) in nodes.items():
        g.add_node(nid, name=name, ntype=ntype)
    for subj, obj, _etype, _sign in edges:
        if not include_self_loops and subj == obj:
            continue
        g.add_edge(subj, obj)
    return g


def report_unmapped(unmapped, stream):
    """Print a warning listing edge types absent from EDGE_SIGN."""
    if not unmapped:
        return
    print(
        "WARNING: edge types not in EDGE_SIGN (treated as sign-indeterminate):",
        file=stream,
    )
    for etype, count in sorted(unmapped.items(), key=lambda kv: -kv[1]):
        print(f"  {etype}: {count}", file=stream)
    print("", file=stream)
