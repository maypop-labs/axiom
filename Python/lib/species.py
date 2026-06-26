"""Species classification from node cross_references.

Shared by the Stage 04 graph export and the AXIOM MCP graph accessor so
there is a single definition of how a gene/protein node's species is
derived. Classification is by cross_reference key presence first (the
per-organism nomenclature databases), then by NCBI taxonomy id, so a
node is classifiable whenever LEXICON supplied either signal.
"""
from __future__ import annotations

# Cross-reference keys whose presence implies a species. Lowercase to match
# the snake_case keys LEXICON writes into nodes.cross_references.
SPECIES_KEYS = {
    "hgnc": "human",
    "mgi": "mouse",
    "rgd": "rat",
    "wormbase": "worm",
}

# NCBI taxonomy ids mapped to the same species labels. Used as a fallback
# when no per-organism nomenclature key is present. Stored as strings to
# match the cross_references["taxid"] value LEXICON writes.
TAXID_SPECIES = {
    "9606": "human",
    "10090": "mouse",
    "10116": "rat",
    "6239": "worm",
}

# Only these node types carry a meaningful species classification. Everything
# else is treated as species-agnostic regardless of cross_references content.
SPECIES_RELEVANT_TYPES = {"gene", "protein"}


def derive_species(node_type, cross_refs):
    """Classify a node by species based on cross_references content.

    Returns one of: 'human', 'mouse', 'rat', 'worm', 'species_agnostic',
    'unknown'. Non-gene/protein node types always return 'species_agnostic'.

    A per-organism nomenclature key (SPECIES_KEYS) wins when present; failing
    that, a recognized NCBI taxonomy id under 'taxid' (TAXID_SPECIES) is used.
    """
    if node_type not in SPECIES_RELEVANT_TYPES:
        return "species_agnostic"
    if not cross_refs:
        return "unknown"
    for key, species in SPECIES_KEYS.items():
        if key in cross_refs:
            return species
    taxid = cross_refs.get("taxid")
    if taxid is not None:
        species = TAXID_SPECIES.get(str(taxid))
        if species is not None:
            return species
    return "unknown"
