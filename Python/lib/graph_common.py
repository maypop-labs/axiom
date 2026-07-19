#!/usr/bin/env python3
"""
graph_common.py  (lib/)

Shared foundations for the AXIOM graph-analysis scripts:
signed_path_net_effect.py, cycle_analysis.py, and target_control.py all
import from here so the edge sign map and the database read have a single
authoritative definition.

ASSERTION POLARITY (V18)
------------------------
Evidence rows carry assertion_status ('asserting' or 'refuting'). The
edge-level rollup is DERIVED here, never stored: an edge is 'refuted' when
it has evidence and all of it refutes, 'contested' when it has both kinds,
'asserted' otherwise (including edges with no evidence). load_graph_data
excludes refuted edges by default, so every pass that imports it inherits
the exclusion without changing its own logic. Pass include_refuted=True to
audit them.

This matters because edge_conditions are invisible to this module: the read
is over the edges table alone. Before V18 the SOPs told curators to record a
negative result as a condition-scoped edge, which every pass then counted as
a positive assertion at full sign. Polarity had to live somewhere the loader
actually looks.

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

# PANEL GRANULARITY RULE: a disease is counted exactly once, at the level where
# mechanism is actually curated. The other level stays in the graph as a node and
# as a subtype_of edge, but is not an outcome: counting both would double-count a
# single mechanism in disease_breadth, which is the lead gate's translational
# signal. Applied 2026-07-12, when subtype_of gained a +1 sign:
#   - dementia (776) EXCLUDED. Pure roll-up: its only two incoming edges are
#     subtype_of from Alzheimer's (239) and vascular dementia (777), both panel
#     members carrying the real mechanism. Counted, it would hand a free breadth
#     point to every lever that helps Alzheimer's.
#   - brunescent cataract (194) EXCLUDED. Subtype with in-degree 0, so it can
#     never be reached; its umbrella, age-related cataract (779), carries what
#     mechanism exists.
#   - cancer (775) KEPT, with its subtypes. 775 carries direct incoming mechanism,
#     so the umbrella IS the curated level here. Hepatocellular carcinoma (434)
#     and melanoma (480) each have in-degree 1 and are kept on that basis.
#     WATCH: prostate cancer (504) has in-degree 0 and is the same phantom shape
#     as 194. It is left in only because nothing feeds it either way today; drop
#     it if 775 stays the curated level, or wire it.
DISEASE_OUTCOME_IDS = [
    305, 353, 37, 769, 770, 771, 772, 773, 774,   # cardiovascular
    775, 434, 504, 480, 503,                       # cancer
    239, 614, 411, 777,                            # neurological (776 excluded)
    591, 778,                                      # metabolic
    613, 309, 313, 164,                            # musculoskeletal
    271, 779, 780, 781,                            # sensory (194 excluded)
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
    "subtype_of": +1,       # subsumption: the subject is a kind, form, or arm of
                            # the object, and more subject necessarily means more
                            # object (Alzheimer's -> dementia; folate cycle -> one
                            # carbon metabolism; macroautophagy -> proteostasis).
                            # Split out of part_of on 2026-07-12: part_of was
                            # carrying both this and plain composition, and its 0
                            # sign was silently cutting umbrella outcomes off from
                            # the mechanisms feeding their subtypes.

    # clearly negative (subject decreases object)
    "suppresses": -1,
    "inhibits": -1,
    "degrades": -1,
    "detoxifies": -1,       # REVIEW: removes/neutralizes the object.

    # sign-indeterminate (PTMs, structural, transport, context-dependent)
    "binds": 0,
    "part_of": 0,           # composition ONLY: the subject is a component of a
                            # discrete assembly (RICTOR -> mTORC2; glucosepane ->
                            # lipofuscin). No monotone magnitude relation is
                            # asserted, so the sign stays indeterminate. If more
                            # subject necessarily means more object, the edge is
                            # subtype_of, not part_of.
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


def _has_assertion_column(conn):
    """True when the database has been migrated to V18."""
    cursor = conn.execute("PRAGMA table_info(edge_evidence)")
    return any(row[1] == "assertion_status" for row in cursor.fetchall())


def edge_assertion_status(conn):
    """Derive per-edge assertion status from its evidence rows.

    Returns (status_by_edge, census). Status is one of:
      'refuted'   at least one evidence row, every one of them refuting
      'contested' both asserting and refuting evidence present
      'asserted'  everything else, including edges carrying no evidence

    A pre-V18 database has no assertion_status column. Rather than fail,
    every edge is reported 'asserted', which is exactly what such a
    database means: no refutation had ever been recordable. This keeps the
    analysis scripts runnable against a graph the MCP server has not yet
    reopened and migrated.
    """
    if not _has_assertion_column(conn):
        ids = [row[0] for row in conn.execute("SELECT id FROM edges")]
        status = {i: "asserted" for i in ids}
        return status, {
            "asserted": len(ids), "contested": 0, "refuted": 0,
            "refuted_edge_ids": [], "contested_edge_ids": [],
            "column_present": False,
        }

    status = {}
    refuted, contested = [], []
    for edge_id, n_refuting, n_asserting in conn.execute(
        "SELECT e.id,"
        " SUM(CASE WHEN ev.assertion_status = 'refuting'"
        "          THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN ev.id IS NOT NULL"
        "          AND ev.assertion_status <> 'refuting'"
        "          THEN 1 ELSE 0 END)"
        " FROM edges e LEFT JOIN edge_evidence ev ON ev.edge_id = e.id"
        " GROUP BY e.id"
    ):
        n_refuting = n_refuting or 0
        n_asserting = n_asserting or 0
        if n_refuting and not n_asserting:
            status[edge_id] = "refuted"
            refuted.append(edge_id)
        elif n_refuting and n_asserting:
            status[edge_id] = "contested"
            contested.append(edge_id)
        else:
            status[edge_id] = "asserted"

    return status, {
        "asserted": sum(1 for v in status.values() if v == "asserted"),
        "contested": len(contested),
        "refuted": len(refuted),
        "refuted_edge_ids": sorted(refuted),
        "contested_edge_ids": sorted(contested),
        "column_present": True,
    }


def refuted_edge_ids(conn):
    """Just the refuted edge id set. For callers holding their own conn."""
    status, _ = edge_assertion_status(conn)
    return {eid for eid, s in status.items() if s == "refuted"}


def count_well_fed_diseases_from_db(conn, disease_ids=None,
                                    k=WELL_FED_INDEGREE_K):
    """SQL counterpart of count_well_fed_diseases, for callers that hold a
    connection rather than an edges list (build_report.py).

    Excludes refuted edges from the in-degree, so a relation that was tested
    and found absent cannot push a disease over the well-fed threshold and
    thereby raise the public lead bar. The two implementations agree because
    both count every non-refuted incoming edge exactly once.
    """
    if disease_ids is None:
        disease_ids = DISEASE_OUTCOME_IDS
    excluded = refuted_edge_ids(conn)
    marks = ",".join("?" for _ in disease_ids)
    indeg = {}
    for edge_id, object_id in conn.execute(
        f"SELECT id, object_id FROM edges WHERE object_id IN ({marks})",
        list(disease_ids),
    ):
        if edge_id in excluded:
            continue
        indeg[object_id] = indeg.get(object_id, 0) + 1
    return sum(1 for d in disease_ids if indeg.get(d, 0) >= k)


def load_graph_data(db_path=DB_PATH, include_refuted=False):
    """Read the graph once.

    Returns (nodes, edges, unmapped, assertions):
      nodes:      {node_id: (canonical_name, node_type)}
      edges:      list of (subject_id, object_id, edge_type, sign),
                  self-loops included, sign resolved via EDGE_SIGN.
                  Refuted edges are EXCLUDED unless include_refuted=True.
      unmapped:   {edge_type: count} for edge types absent from EDGE_SIGN.
      assertions: the census from edge_assertion_status, plus
                  refuted_excluded (how many edges this call dropped),
                  contested_pairs (ordered (subject, object) tuples whose
                  edges are contested), and include_refuted.

    The tuple shape of `edges` is deliberately unchanged from V17 so that
    build_reverse_adj, resolve_pair_signs, build_digraph and
    count_well_fed_diseases keep unpacking four elements. The return arity
    grew instead, which is a visible break at each call site rather than a
    silent one inside the unpacking loops.
    """
    conn = sqlite3.connect(db_path)
    try:
        nodes = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT id, canonical_name, node_type FROM nodes"
            )
        }
        status_by_edge, census = edge_assertion_status(conn)
        edges = []
        unmapped = defaultdict(int)
        contested_pairs = set()
        refuted_excluded = 0
        for edge_id, subj, obj, etype in conn.execute(
            "SELECT id, subject_id, object_id, edge_type FROM edges"
        ):
            edge_status = status_by_edge.get(edge_id, "asserted")
            if edge_status == "refuted" and not include_refuted:
                refuted_excluded += 1
                continue
            if edge_status == "contested":
                contested_pairs.add((subj, obj))
            sign, mapped = sign_of(etype)
            if not mapped:
                unmapped[etype] += 1
            edges.append((subj, obj, etype, sign))
    finally:
        conn.close()

    assertions = dict(census)
    assertions["refuted_excluded"] = refuted_excluded
    assertions["contested_pairs"] = sorted(contested_pairs)
    assertions["include_refuted"] = include_refuted
    return nodes, edges, dict(unmapped), assertions


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


def report_assertions(assertions, stream):
    """Print the edge assertion census.

    Quiet when there is nothing to say: a graph with no refuted and no
    contested edges prints nothing, so this can be called unconditionally
    at the top of every pass without adding noise to routine runs.
    """
    if not assertions.get("column_present"):
        print(
            "NOTE: this database predates the V18 assertion_status column. "
            "Every edge is being treated as asserted, which is what a "
            "pre-V18 graph means. Reopen it through the MCP server to "
            "migrate.",
            file=stream,
        )
        print("", file=stream)
        return

    refuted = assertions.get("refuted", 0)
    contested = assertions.get("contested", 0)
    if not refuted and not contested:
        return

    print("Edge assertion census:", file=stream)
    if refuted:
        verb = ("retained (include_refuted=True)"
                if assertions.get("include_refuted")
                else f"excluded from this run ({assertions['refuted_excluded']})")
        print(f"  refuted: {refuted}, {verb}", file=stream)
        print(f"    edge ids: {assertions['refuted_edge_ids']}", file=stream)
    if contested:
        print(
            f"  contested: {contested}, traversed as asserted and flagged. "
            f"These carry both asserting and refuting evidence; treat any "
            f"result routed through them as provisional.",
            file=stream,
        )
        print(f"    edge ids: {assertions['contested_edge_ids']}", file=stream)
    print("", file=stream)


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
