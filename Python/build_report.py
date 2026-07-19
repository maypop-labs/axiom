#!/usr/bin/env python3
"""
build_report.py

Fifth pass. Turns the four analysis TSVs plus the graph database into one
machine-written artifact, build_report.json: the mechanical substrate that the
public prose report is written from, and the archived record that makes
persistence claims possible across builds.

WHAT THIS DOES NOT DO
---------------------
It does not write the public report. Choosing the single lead to feature,
glossing the biology for a lay reader, and phrasing the honest claim are
judgment calls made by a human following SOP_build_insights_report.md. This
script hands that writer a decision-ready table; it does not decide.

WHAT IT COMPUTES
----------------
1. Structural frame from cycle_analysis.tsv (core size, loop parity counts,
   length distribution, and how many loops sit exactly at the enumeration cap,
   which is the truncation signal).
2. The candidate set from feedback_control_targets.tsv (the clean_leverage,
   cross_outcome_conflict, and central_ambiguous rows), carrying participation,
   per-node breadth, and conflict. It never reads target_control.tsv, whose
   driver identities are matching artifacts.
3. Grounding metrics per candidate, computed against the database:
     - degree (incident edge count) and the participation-to-degree ratio, which
       flags single-file pass-through nodes that only inherit a busy corridor's
       traffic (the HMGB1 case);
     - evidence rows, distinct source documents, and the largest single-source
       share, which flags a candidate resting on one ingested paper.
4. External cross-check over public HTTP APIs (no MCP, no auth):
     - PubMed field footprint via NCBI E-utilities esearch (candidate-and-aging
       hit count against a reference term), the external basis for any
       "understudied" framing;
     - Open Targets association via GraphQL for gene and protein candidates, the
       external basis for "independently aging-linked".
   Network is optional. If it is disabled or unreachable, external fields are
   null, a status flag is set, and no candidate is promoted to "lead" (a lead
   requires the external leg per the SOP).
5. A provisional label per candidate (lead / watch_item / curation_priority /
   cross_outcome_conflict / discard) applied mechanically from the thresholds in
   the configuration block.
6. Persistence deltas against the most recent archived build_report.json.

Writes build_report.json to the export directory and archives a dated copy under
export/build_reports/. All fields derive from canonical names, topology, and
counts only; the notes and observation free-text fields are never read, so the
output is DrugBank-clean by construction.

Reads axiom_graph.db, cycle_analysis.tsv, feedback_control_targets.tsv, and
feedback_direction_by_outcome.tsv. Requires only the standard library.
"""

import sys
import csv
import json
import time
import sqlite3
import datetime
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from graph_common import (
    DB_PATH,
    EXPORT_DIR,
    ANCHOR_OUTCOME_ID,
    SECONDARY_ANCHOR_ID,
    HALLMARK_OUTCOME_IDS,
    DISEASE_OUTCOME_IDS,
    WELL_FED_INDEGREE_K,
    breadth_floor_for,
    edge_assertion_status,
    count_well_fed_diseases_from_db,
)


# ----------------------------- configuration -----------------------------

CYCLE_TSV = "cycle_analysis.tsv"
FEEDBACK_TSV = "feedback_control_targets.tsv"
FEEDBACK_DIRECTION_TSV = "feedback_direction_by_outcome.tsv"
OUTPUT_JSON = "build_report.json"
ARCHIVE_SUBDIR = "build_reports"

# Must match MAX_CYCLE_LENGTH in cycle_analysis.py and
# feedback_control_targets.py. Used only to detect truncation, not to enumerate.
CYCLE_LENGTH_CAP = 8

# A node counts as central if it is on this share of positive cycles or is in
# the positive-cycle hitting set. Matches feedback_control_targets.py.
PARTICIPATION_PCT_FLOOR = 8.0

# Participation-to-degree ratio above which a node is treated as a pass-through
# chokepoint rather than an independent hub. HMGB1 (51/2) is far above this;
# methylglyoxal (57/19) is well below it.
PASS_THROUGH_RATIO = 5.0

# Minimum distinct source documents for a candidate to qualify as a lead rather
# than a watch-item. This is a judgment threshold; adjust after review.
EVIDENCE_DEPTH_FLOOR = 10

# If one source accounts for more than this share of a candidate's evidence, the
# candidate is treated as single-source-driven and capped at watch-item.
SINGLE_SOURCE_MAX_SHARE = 0.5

# The disease-breadth floor a lead must clear is not a fixed constant: it is
# computed each build from disease-side curation depth (the count of well-fed
# diseases) via graph_common.breadth_floor_for, and logged into the outcomes
# block. See WELL_FED_INDEGREE_K and the breadth-floor policy in graph_common.

# External cross-check.
ENABLE_EXTERNAL = True
REFERENCE_TERM = "cellular senescence"   # PubMed denominator for the footprint
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_TOOL = "AXIOM"
NCBI_EMAIL = ""                          # optional; NCBI requests one on heavy use
OPEN_TARGETS_GRAPHQL = "https://api.platform.opentargets.org/api/v4/graphql"
HTTP_TIMEOUT = 20                        # seconds
NCBI_PAUSE = 0.34                        # seconds between esearch calls (3/sec)
OT_TARGET_NODE_TYPES = {"gene", "protein"}
OT_TOP_DISEASES = 5


# ------------------------------- tsv reads -------------------------------

def read_tsv(path):
    """Read a TSV written by the analysis passes into a list of dict rows.
    Opened with newline='' so the csv module handles the CRLF line endings the
    Windows-written TSVs carry."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def read_outcome_inventory(path):
    """From the long-format direction TSV, return the set of analyzable outcome
    ids (those that appeared for at least one node), keyed by outcome_class.
    Returns empty sets if the file is absent, so the report degrades cleanly."""
    present = {"anchor": set(), "secondary_anchor": set(),
               "hallmark": set(), "disease": set(), "other": set()}
    if not path.exists():
        return present
    for row in read_tsv(path):
        oclass = row.get("outcome_class", "other")
        try:
            oid = int(row["outcome_id"])
        except (KeyError, ValueError):
            continue
        present.setdefault(oclass, set()).add(oid)
    return present


def structural_frame(cycle_rows):
    """Loop counts by parity, length distribution, and cap-proximity."""
    parity_counts = {}
    length_counts = {}
    for r in cycle_rows:
        parity_counts[r["parity"]] = parity_counts.get(r["parity"], 0) + 1
        length = int(r["length"])
        length_counts[length] = length_counts.get(length, 0) + 1
    total = len(cycle_rows)
    loops_at_cap = length_counts.get(CYCLE_LENGTH_CAP, 0)
    max_len = max(length_counts) if length_counts else 0
    pos = parity_counts.get("positive_feedback", 0)
    neg = parity_counts.get("negative_feedback", 0)
    return {
        "loops_total": total,
        "loops_by_parity": parity_counts,
        "amplifying_to_damping_ratio": (round(pos / neg, 2) if neg else None),
        "length_distribution": {str(k): length_counts[k]
                                for k in sorted(length_counts)},
        "cycle_length_cap": CYCLE_LENGTH_CAP,
        "loops_at_cap": loops_at_cap,
        "loops_at_cap_share": (round(loops_at_cap / total, 3) if total else 0.0),
        "max_loop_length_observed": max_len,
        "truncation_suspected": bool(max_len >= CYCLE_LENGTH_CAP
                                     and loops_at_cap > 0),
    }


# --------------------------- database grounding --------------------------

def open_db():
    return sqlite3.connect(DB_PATH)


def graph_stats(conn):
    q = conn.execute
    nodes = q("SELECT COUNT(*) FROM nodes").fetchone()[0]
    edges = q("SELECT COUNT(*) FROM edges").fetchone()[0]
    ev = q("SELECT COUNT(*) FROM edge_evidence").fetchone()[0]
    obs = q("SELECT COUNT(*) FROM node_observations").fetchone()[0]
    distinct_sources = q(
        "SELECT COUNT(DISTINCT k) FROM ("
        " SELECT COALESCE(NULLIF(source_doi,''),NULLIF(source_pmid,''),"
        "  NULLIF(source_filename,'')) AS k FROM edge_evidence"
        " UNION ALL"
        " SELECT COALESCE(NULLIF(source_doi,''),NULLIF(source_pmid,''),"
        "  NULLIF(source_filename,'')) FROM node_observations"
        ") WHERE k IS NOT NULL"
    ).fetchone()[0]
    return {
        "nodes": nodes, "edges": edges, "evidence_records": ev + obs,
        "distinct_sources": distinct_sources,
    }


def node_degree(conn, node_id, refuted=frozenset()):
    """Incident edge count, excluding refuted edges (V18).

    Degree is the denominator of participation_degree_ratio, which decides
    the pass_through_flag and hence the `discard` label. A relation that was
    tested and found absent must not change that judgement.
    """
    rows = conn.execute(
        "SELECT id FROM edges WHERE subject_id=? OR object_id=?",
        (node_id, node_id),
    ).fetchall()
    return sum(1 for (edge_id,) in rows if edge_id not in refuted)


def contested_incident_nodes(conn, status_by_edge):
    """Node ids touching at least one contested edge.

    A contested edge carries both asserting and refuting evidence, so any
    conclusion routed through it is provisional. Used to withhold the
    `lead` label. This is deliberately the conservative approximation:
    incidence, not path-crossing. Catching the precise case (a candidate
    whose favourable path to an outcome crosses a contested edge anywhere
    upstream) needs the flag threaded through signed_path_net_effect and
    feedback_control_targets, which is a larger change than the safety gate
    warrants today. Incidence over-blocks rather than under-blocks, which is
    the correct direction to err for a public label.
    """
    contested = {eid for eid, s in status_by_edge.items() if s == "contested"}
    if not contested:
        return set()
    touched = set()
    for edge_id, subject_id, object_id in conn.execute(
        "SELECT id, subject_id, object_id FROM edges"
    ):
        if edge_id in contested:
            touched.add(subject_id)
            touched.add(object_id)
    return touched


def node_evidence(conn, node_id, refuted=frozenset(), has_assertion=True):
    """Return (evidence_rows, distinct_sources, top_source_share) over the
    node's incident-edge evidence and its own observations.

    V18 exclusions, both of which matter because distinct_sources gates
    promotion to `lead` through EVIDENCE_DEPTH_FLOOR:
      - evidence sitting on a refuted edge;
      - any individually refuting row, wherever it sits.
    The gate measures how well SUPPORTED a candidate is. A row recording
    that something was tested and found absent is not support, and must not
    help clear the bar.
    """
    ev_status = "ev.assertion_status" if has_assertion else "'asserting'"
    obs_status = "assertion_status" if has_assertion else "'asserting'"
    rows = conn.execute(
        f"SELECT e.id, {ev_status},"
        "  COALESCE(NULLIF(ev.source_doi,''),NULLIF(ev.source_pmid,''),"
        "  NULLIF(ev.source_filename,'')) AS k"
        " FROM edge_evidence ev JOIN edges e ON ev.edge_id=e.id"
        " WHERE e.subject_id=? OR e.object_id=?"
        " UNION ALL"
        f" SELECT NULL, {obs_status},"
        "  COALESCE(NULLIF(source_doi,''),NULLIF(source_pmid,''),"
        "  NULLIF(source_filename,'')) FROM node_observations WHERE node_id=?",
        (node_id, node_id, node_id),
    ).fetchall()
    total = 0
    counts = {}
    for edge_id, status, k in rows:
        if edge_id is not None and edge_id in refuted:
            continue
        if status == "refuting":
            continue
        total += 1
        if k:
            counts[k] = counts.get(k, 0) + 1
    distinct = len(counts)
    top_share = (max(counts.values()) / total) if (total and counts) else 0.0
    return total, distinct, round(top_share, 3)


# ------------------------------- external --------------------------------

def pubmed_count(term):
    """Total PubMed hits for a term via E-utilities esearch. None on failure."""
    params = {
        "db": "pubmed", "term": term, "retmode": "json", "retmax": "0",
        "tool": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL
    url = NCBI_ESEARCH + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return int(data["esearchresult"]["count"])
    except Exception as exc:  # network, parse, or key error
        print(f"  pubmed lookup failed for {term!r}: {exc}", file=sys.stderr)
        return None


def _ot_post(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        OPEN_TARGETS_GRAPHQL, data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def open_targets_associations(symbol):
    """Resolve a gene/protein symbol to an Ensembl target and return its top
    associated diseases. Returns (ensembl_id, [{name, score}, ...]) or
    (None, None) on failure."""
    search_q = (
        "query($q:String!){search(queryString:$q,entityNames:[\"target\"],"
        "page:{index:0,size:1}){hits{id entity}}}"
    )
    assoc_q = (
        "query($id:String!,$size:Int!){target(ensemblId:$id){"
        "associatedDiseases(page:{index:0,size:$size}){"
        "rows{disease{name} score}}}}"
    )
    try:
        hits = (_ot_post(search_q, {"q": symbol})
                .get("data", {}).get("search", {}).get("hits", []))
        target_hits = [h for h in hits if h.get("entity") == "target"]
        if not target_hits:
            return None, None
        ensembl_id = target_hits[0]["id"]
        rows = (_ot_post(assoc_q, {"id": ensembl_id, "size": OT_TOP_DISEASES})
                .get("data", {}).get("target", {})
                .get("associatedDiseases", {}).get("rows", []))
        diseases = [{"name": r["disease"]["name"], "score": round(r["score"], 3)}
                    for r in rows]
        return ensembl_id, diseases
    except Exception as exc:
        print(f"  open targets lookup failed for {symbol!r}: {exc}",
              file=sys.stderr)
        return None, None


# ------------------------------- labeling --------------------------------

def is_central(pct, in_hitting_set):
    return pct >= PARTICIPATION_PCT_FLOOR or in_hitting_set


def classify(cand, breadth_floor):
    """Assign one provisional label from the grounding and breadth metrics.

    Order matters: a pass-through chokepoint is discarded first; a cross-outcome
    conflict is surfaced as its own class rather than a lead; a node with no
    clean direction anywhere is a curation priority. Only a central node with a
    coherent favorable action, favorable for the anchor outcome, broad enough
    across diseases, well-grounded, and externally corroborated becomes a lead.
    """
    if cand["pass_through_flag"]:
        return "discard"
    if cand["conflict"] == "yes":
        return "cross_outcome_conflict"
    if cand["source_priority"] == "central_ambiguous":
        return "curation_priority"
    # Remaining candidates are central with a coherent favorable action.
    if cand["aging_favorable"] != "yes":
        return "watch_item"
    # V18: a candidate touching a contested edge (one carrying both
    # asserting and refuting evidence) is capped at watch_item. The
    # underlying relation is unsettled, so the claim is not lead-grade
    # however well the rest of the metrics read.
    if cand.get("contested_incident_flag"):
        return "watch_item"
    gates_ok = (cand["distinct_sources"] >= EVIDENCE_DEPTH_FLOOR
                and not cand["single_source_dominant_flag"])
    if (cand["disease_breadth"] >= breadth_floor
            and gates_ok and cand["external_ok"]):
        return "lead"
    return "watch_item"


# ------------------------------ persistence ------------------------------

def latest_prior_archive(archive_dir, today_iso):
    if not archive_dir.exists():
        return None
    candidates = sorted(archive_dir.glob("build_report_*.json"))
    prior = [p for p in candidates if today_iso not in p.name]
    return prior[-1] if prior else None


def persistence_delta(current, prior_path):
    """Compare current candidates to a prior archived report by node_id."""
    if prior_path is None:
        return {"compared_to": None}
    try:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  could not read prior archive {prior_path}: {exc}",
              file=sys.stderr)
        return {"compared_to": None}

    prior_by_id = {c["node_id"]: c for c in prior.get("candidates", [])}
    cur_by_id = {c["node_id"]: c for c in current}
    new, dropped, strengthened, weakened, persisting = [], [], [], [], []
    for nid, c in cur_by_id.items():
        if nid not in prior_by_id:
            new.append(c["node_name"])
            continue
        before = prior_by_id[nid].get("pos_participation", 0)
        after = c["pos_participation"]
        if after > before:
            strengthened.append(c["node_name"])
        elif after < before:
            weakened.append(c["node_name"])
        else:
            persisting.append(c["node_name"])
    for nid, c in prior_by_id.items():
        if nid not in cur_by_id:
            dropped.append(c["node_name"])
    return {
        "compared_to": prior_path.name,
        "new": new, "dropped": dropped, "strengthened": strengthened,
        "weakened": weakened, "persisting": persisting,
    }


# --------------------------------- main ----------------------------------

def main():
    cycle_path = EXPORT_DIR / CYCLE_TSV
    feedback_path = EXPORT_DIR / FEEDBACK_TSV
    for p in (cycle_path, feedback_path):
        if not p.exists():
            print(f"ERROR: required input not found: {p}", file=sys.stderr)
            return 1

    cycle_rows = read_tsv(cycle_path)
    feedback_rows = read_tsv(feedback_path)
    frame = structural_frame(cycle_rows)

    # Candidate set: the clean_leverage, cross_outcome_conflict, and
    # central_ambiguous rows.
    candidate_rows = [r for r in feedback_rows
                      if r["priority"] in ("clean_leverage", "cross_outcome_conflict", "central_ambiguous")]

    conn = open_db()
    try:
        gstats = graph_stats(conn)

        # V18 assertion polarity. Derived once and threaded through every
        # grounding metric below, because each of them feeds a gate:
        # degree -> pass_through_flag -> discard; distinct_sources ->
        # EVIDENCE_DEPTH_FLOOR -> lead; disease in-degree -> breadth floor
        # -> lead. A refuted relation must not move any of them.
        status_by_edge, assertion_census = edge_assertion_status(conn)
        refuted = {eid for eid, s in status_by_edge.items() if s == "refuted"}
        has_assertion = assertion_census["column_present"]
        contested_nodes = contested_incident_nodes(conn, status_by_edge)

        well_fed = count_well_fed_diseases_from_db(
            conn, DISEASE_OUTCOME_IDS, WELL_FED_INDEGREE_K,
        )
        breadth_floor = breadth_floor_for(well_fed)

        ext_pubmed_ok = False
        ext_ot_ok = False
        reference_count = None
        if ENABLE_EXTERNAL:
            reference_count = pubmed_count(f'"{REFERENCE_TERM}" AND aging')
            ext_pubmed_ok = reference_count is not None
            if ext_pubmed_ok:
                time.sleep(NCBI_PAUSE)

        candidates = []
        for r in candidate_rows:
            nid = int(r["node_id"])
            name = r["node_name"]
            ntype = r["node_type"]
            participation = int(r["pos_participation"])
            pct = float(r["pos_participation_pct"])

            degree = node_degree(conn, nid, refuted)
            ev_rows, distinct_sources, top_share = node_evidence(
                conn, nid, refuted=refuted, has_assertion=has_assertion,
            )
            ratio = (participation / degree) if degree else float("inf")

            pmid_count = None
            footprint_ratio = None
            ot_id = None
            ot_diseases = None
            if ENABLE_EXTERNAL and ext_pubmed_ok:
                pmid_count = pubmed_count(f'"{name}" AND aging')
                time.sleep(NCBI_PAUSE)
                if pmid_count is not None and reference_count:
                    footprint_ratio = round(pmid_count / reference_count, 4)
                if ntype in OT_TARGET_NODE_TYPES:
                    ot_id, ot_diseases = open_targets_associations(name)
                    if ot_id is not None:
                        ext_ot_ok = True

            # A lead needs the external leg. For a gene/protein that means both
            # the PubMed footprint and an Open Targets resolution; for other
            # node types the PubMed footprint alone.
            if ntype in OT_TARGET_NODE_TYPES:
                external_ok = pmid_count is not None and ot_id is not None
            else:
                external_ok = pmid_count is not None

            cand = {
                "node_id": nid,
                "node_name": name,
                "node_type": ntype,
                "pos_participation": participation,
                "pos_participation_pct": pct,
                "in_min_fvs": r["in_min_fvs"],
                "in_pos_hitting_set": r["in_pos_hitting_set"],
                "coherent_action": r.get("coherent_action", ""),
                "aging_favorable": r.get("aging_favorable", "no"),
                "aging_direction_class": r.get("aging_direction_class", ""),
                "lifespan_favorable": r.get("lifespan_favorable", "no"),
                "hallmark_breadth": int(r.get("hallmark_breadth", 0) or 0),
                "disease_breadth": int(r.get("disease_breadth", 0) or 0),
                "favorable_disease_ids": r.get("favorable_disease_ids", ""),
                "conflict": r.get("conflict", "no"),
                "conflict_outcome_ids": r.get("conflict_outcome_ids", ""),
                "source_priority": r["priority"],
                "degree": degree,
                "participation_degree_ratio": (round(ratio, 2)
                                               if degree else None),
                "pass_through_flag": bool(degree and ratio >= PASS_THROUGH_RATIO),
                "contested_incident_flag": nid in contested_nodes,
                "evidence_rows": ev_rows,
                "distinct_sources": distinct_sources,
                "top_source_share": top_share,
                "single_source_dominant_flag": top_share > SINGLE_SOURCE_MAX_SHARE,
                "pubmed_count": pmid_count,
                "pubmed_footprint_ratio": footprint_ratio,
                "ot_ensembl_id": ot_id,
                "ot_top_diseases": ot_diseases,
                "external_ok": external_ok,
            }
            cand["label"] = classify(cand, breadth_floor)
            candidates.append(cand)
    finally:
        conn.close()

    candidates.sort(key=lambda c: c["pos_participation"], reverse=True)

    today_iso = datetime.date.today().isoformat()
    archive_dir = EXPORT_DIR / ARCHIVE_SUBDIR
    prior = latest_prior_archive(archive_dir, today_iso)
    persistence = persistence_delta(candidates, prior)

    # Analyzable-outcome inventory: the breadth denominator, logged so the
    # report writer knows exactly what "breadth" was measured against. An
    # outcome with no reachable sources this build never appears in the long
    # file and lands in the dropped lists.
    present = read_outcome_inventory(EXPORT_DIR / FEEDBACK_DIRECTION_TSV)
    analyzable_hall = sorted(present.get("hallmark", set()))
    analyzable_dis = sorted(present.get("disease", set()))
    outcomes_block = {
        "anchor_outcome_id": ANCHOR_OUTCOME_ID,
        "secondary_anchor_id": SECONDARY_ANCHOR_ID,
        "well_fed_indegree_k": WELL_FED_INDEGREE_K,
        "well_fed_disease_count": well_fed,
        "breadth_floor": breadth_floor,
        "hallmark_outcomes_total": len(HALLMARK_OUTCOME_IDS),
        "hallmark_outcomes_analyzable": analyzable_hall,
        "hallmark_outcomes_dropped": sorted(set(HALLMARK_OUTCOME_IDS)
                                            - set(analyzable_hall)),
        "disease_outcomes_total": len(DISEASE_OUTCOME_IDS),
        "disease_outcomes_analyzable": analyzable_dis,
        "disease_outcomes_dropped": sorted(set(DISEASE_OUTCOME_IDS)
                                           - set(analyzable_dis)),
    }

    report = {
        "build_date": today_iso,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "db_path": str(DB_PATH),
        "graph": gstats,
        "assertions": assertion_census,
        "structure": frame,
        "outcomes": outcomes_block,
        "reference_term": REFERENCE_TERM,
        "reference_pubmed_count": reference_count,
        "external_status": {
            "pubmed": "ok" if ext_pubmed_ok else "unavailable",
            "open_targets": "ok" if ext_ot_ok else "unavailable",
        },
        "candidates": candidates,
        "persistence": persistence,
    }

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EXPORT_DIR / OUTPUT_JSON
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"build_report_{today_iso}.json"
    archive_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    labels = {}
    for c in candidates:
        labels[c["label"]] = labels.get(c["label"], 0) + 1
    print("=" * 78)
    print("AXIOM build report")
    print("=" * 78)
    print(f"candidates: {len(candidates)}   "
          + "   ".join(f"{k}: {v}" for k, v in sorted(labels.items())))
    print(f"external: pubmed {report['external_status']['pubmed']}, "
          f"open_targets {report['external_status']['open_targets']}")
    if not assertion_census["column_present"]:
        print("NOTE: pre-V18 database; every edge treated as asserted.")
    elif assertion_census["refuted"] or assertion_census["contested"]:
        print(f"assertions: {assertion_census['refuted']} refuted edge(s) "
              f"excluded from all grounding metrics, "
              f"{assertion_census['contested']} contested edge(s) capped at "
              f"watch_item")
    if frame["truncation_suspected"]:
        print(f"NOTE: {frame['loops_at_cap']} of {frame['loops_total']} loops "
              f"are at the length cap {CYCLE_LENGTH_CAP}; counts are truncation-"
              f"bound and must not be published as a trend without the caveat.")
    print(f"Wrote {out_path}")
    print(f"Archived {archive_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
