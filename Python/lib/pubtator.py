#!/usr/bin/env python3
"""
AXIOM Project - PubTator3 API Module

Fetches pre-annotated entity annotations from NCBI PubTator3 for papers
already in the AXIOM corpus (by PMID).

PubTator3 annotates six entity types:
  - Gene (normalized to NCBI Gene IDs)
  - Disease (normalized to MeSH IDs)
  - Chemical (normalized to MeSH IDs)
  - Species (normalized to NCBI Taxonomy IDs)
  - Mutation (normalized to dbSNP IDs or tmVar format)
  - CellLine (normalized to Cellosaurus IDs)

API documentation:
    https://www.ncbi.nlm.nih.gov/research/pubtator3/api

Usage:
    from pubtator import fetch_pubtator_annotations

    # Fetch annotations for one or more PMIDs
    annotations = fetch_pubtator_annotations(["23746838", "10647931"])
    # Returns list of dicts: {pmid, mention, entity_type, normalized_id, normalized_name}
"""

import json
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

PUBTATOR_API_URL = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"

# Rate limiting: PubTator3 allows ~30 req/min; we stay conservative
REQUEST_DELAY = 0.5  # seconds between requests

# How many PMIDs to request per API call
BATCH_SIZE = 50

# User agent
USER_AGENT = "AXIOM-Project/1.0 (Biomedical Literature Analysis)"

# Track last request time
_last_request_time = 0

# -----------------------------------------------------------------------------
# Rate Limiting
# -----------------------------------------------------------------------------

def _rate_limit():
    """Ensure minimum delay between API requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_request_time = time.time()


def _make_request(url):
    """Make HTTP request with rate limiting and error handling."""
    _rate_limit()

    request = Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")
    except HTTPError as e:
        print(f"    PubTator HTTP Error {e.code}: {e.reason}")
        return None
    except URLError as e:
        print(f"    PubTator URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"    PubTator request failed: {e}")
        return None


# -----------------------------------------------------------------------------
# Response Parsing
# -----------------------------------------------------------------------------

def _parse_biocjson(raw_json):
    """
    Parse BioC JSON response into a flat list of entity annotations.

    PubTator3 returns a structure like:
        {"PubTator3": [{"_id": "12345", "passages": [{"annotations": [...]}]}]}

    Each annotation has:
        - text: the mention string
        - infons.type: entity type (Gene, Disease, Chemical, etc.)
        - infons.identifier: normalized ID(s), sometimes semicolon-separated
        - infons.normalized (optional): list of dicts with "id" and "name"

    Returns:
        list of dicts: {pmid, mention, entity_type, normalized_id, normalized_name}
    """
    results = []

    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return results

    # Handle both list-at-top-level and {"PubTator3": [...]} wrapper
    articles = data
    if isinstance(data, dict):
        articles = data.get("PubTator3", [])
    if not isinstance(articles, list):
        return results

    for article in articles:
        # Extract PMID from _id field (format: "PMID" or "PMID|PMCID")
        raw_id = article.get("_id", "")
        pmid = raw_id.split("|")[0] if raw_id else None
        if not pmid:
            continue

        passages = article.get("passages", [])
        for passage in passages:
            annotations = passage.get("annotations", [])
            for ann in annotations:
                mention = ann.get("text", "").strip()
                if not mention:
                    continue

                infons = ann.get("infons", {})
                entity_type = infons.get("type", "Unknown")
                normalized_id = infons.get("identifier", "")

                # Get canonical name from infons
                normalized_name = infons.get("name", "")

                # Skip annotations without any identifier
                if not normalized_id or normalized_id == "-":
                    continue

                results.append({
                    "pmid": pmid,
                    "mention": mention,
                    "entity_type": entity_type,
                    "normalized_id": str(normalized_id),
                    "normalized_name": normalized_name or "",
                })

    return results


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def fetch_pubtator_annotations(pmids):
    """
    Fetch entity annotations from PubTator3 for a list of PMIDs.

    Batches requests to stay within API limits.

    Args:
        pmids: List of PMID strings

    Returns:
        list of dicts: {pmid, mention, entity_type, normalized_id, normalized_name}
    """
    if not pmids:
        return []

    all_annotations = []

    # Process in batches
    for i in range(0, len(pmids), BATCH_SIZE):
        batch = pmids[i:i + BATCH_SIZE]
        pmid_str = ",".join(batch)
        url = f"{PUBTATOR_API_URL}?pmids={pmid_str}"

        raw = _make_request(url)
        if raw:
            annotations = _parse_biocjson(raw)
            all_annotations.extend(annotations)

    return all_annotations


# -----------------------------------------------------------------------------
# Main (for testing)
# -----------------------------------------------------------------------------

def main():
    """Test PubTator3 lookup with a known paper."""
    print("=" * 70)
    print("AXIOM - PubTator3 API Test")
    print("=" * 70)
    print()

    # The Hallmarks of Aging (Lopez-Otin et al., 2013)
    test_pmid = "23746838"
    print(f"Fetching annotations for PMID {test_pmid}...")
    print()

    annotations = fetch_pubtator_annotations([test_pmid])

    if annotations:
        # Group by type for display
        by_type = {}
        for ann in annotations:
            t = ann["entity_type"]
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(ann)

        for entity_type, anns in sorted(by_type.items()):
            print(f"{entity_type} ({len(anns)} annotations):")
            # Show unique mentions
            seen = set()
            for a in anns:
                key = (a["mention"], a["normalized_id"])
                if key not in seen:
                    seen.add(key)
                    name_part = f" -> {a['normalized_name']}" if a["normalized_name"] else ""
                    print(f"  {a['mention']} [{a['normalized_id']}]{name_part}")
            print()

        print(f"Total annotations: {len(annotations)}")
    else:
        print("No annotations returned.")


if __name__ == "__main__":
    main()
