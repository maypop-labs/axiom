#!/usr/bin/env python3
"""
AXIOM Project - PubMed API Module

Handles PubMed/NCBI E-utilities API calls for fetching bibliographic metadata.

The E-utilities API is free and does not require authentication for reasonable
usage (< 3 requests/second without API key, < 10 requests/second with key).

Usage:
    from pubmed import search_pubmed, fetch_pubmed_metadata
    
    # Search by title
    pmid = search_pubmed("The Hallmarks of Aging")
    
    # Fetch full metadata
    metadata = fetch_pubmed_metadata(pmid)
    print(metadata["authors"], metadata["journal"], metadata["doi"])

API Documentation:
    https://www.ncbi.nlm.nih.gov/books/NBK25500/
"""

import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# NCBI E-utilities base URLs
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# Be respectful to NCBI servers
REQUEST_DELAY = 0.4  # seconds between requests (< 3/sec without API key)

# User agent for requests (NCBI requests this for tracking)
USER_AGENT = "AXIOM-Project/1.0 (Biomedical Literature Analysis)"

# Track last request time for rate limiting
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
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except HTTPError as e:
        print(f"    HTTP Error {e.code}: {e.reason}")
        return None
    except URLError as e:
        print(f"    URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"    Request failed: {e}")
        return None


# -----------------------------------------------------------------------------
# Title Cleaning
# -----------------------------------------------------------------------------

def clean_title_for_search(title):
    """
    Clean a title string for PubMed search.
    
    Removes special characters that interfere with search,
    normalizes whitespace, and handles common issues.
    
    Args:
        title: Raw title string
        
    Returns:
        str: Cleaned title suitable for search query
    """
    if not title:
        return ""
    
    # Replace em-dashes, en-dashes with regular hyphens
    title = re.sub(r"[–—]", "-", title)
    
    # Remove content in parentheses that might be annotations
    # e.g., "(Review)" or "(2023)"
    title = re.sub(r"\s*\([^)]*\)\s*", " ", title)
    
    # Remove special characters except hyphens and basic punctuation
    title = re.sub(r"[^\w\s\-.,:]", "", title)
    
    # Normalize whitespace
    title = " ".join(title.split())
    
    # Truncate very long titles (PubMed search has limits)
    if len(title) > 200:
        title = title[:200]
    
    return title.strip()


# -----------------------------------------------------------------------------
# PubMed Search
# -----------------------------------------------------------------------------

def search_pubmed(title, year=None):
    """
    Search PubMed for a paper by title.
    
    Args:
        title: Paper title to search for
        year: Optional publication year to narrow results
        
    Returns:
        str: PMID if found, None otherwise
    """
    cleaned_title = clean_title_for_search(title)
    if not cleaned_title:
        return None
    
    # Build search query
    query = f"{cleaned_title}[Title]"
    if year:
        query += f" AND {year}[Publication Date]"
    
    # URL encode the query
    encoded_query = quote_plus(query)
    url = f"{ESEARCH_URL}?db=pubmed&term={encoded_query}&retmode=xml&retmax=5"
    
    response = _make_request(url)
    if not response:
        return None
    
    # Parse XML response
    try:
        root = ET.fromstring(response)
        
        # Check for errors
        error = root.find(".//ErrorList/PhraseNotFound")
        if error is not None:
            return None
        
        # Get ID list
        id_list = root.find(".//IdList")
        if id_list is None:
            return None
        
        ids = [id_elem.text for id_elem in id_list.findall("Id")]
        
        if not ids:
            return None
        
        # Return first (best) match
        return ids[0]
        
    except ET.ParseError as e:
        print(f"    XML parse error: {e}")
        return None


# -----------------------------------------------------------------------------
# PubMed Fetch
# -----------------------------------------------------------------------------

def fetch_pubmed_metadata(pmid):
    """
    Fetch full metadata for a PubMed article.
    
    Args:
        pmid: PubMed ID
        
    Returns:
        dict: Metadata dictionary with keys:
            - pmid, title, authors, journal, year, volume, issue, pages
            - doi, abstract, pub_date
            Returns None if fetch fails.
    """
    if not pmid:
        return None
    
    url = f"{EFETCH_URL}?db=pubmed&id={pmid}&retmode=xml"
    
    response = _make_request(url)
    if not response:
        return None
    
    try:
        root = ET.fromstring(response)
        article = root.find(".//PubmedArticle")
        
        if article is None:
            return None
        
        metadata = {"pmid": pmid}
        
        # Title
        title_elem = article.find(".//ArticleTitle")
        if title_elem is not None and title_elem.text:
            metadata["title"] = title_elem.text.strip()
        
        # Authors
        authors = []
        for author in article.findall(".//Author"):
            last_name = author.find("LastName")
            initials = author.find("Initials")
            if last_name is not None and last_name.text:
                if initials is not None and initials.text:
                    authors.append(f"{last_name.text}, {initials.text}")
                else:
                    authors.append(last_name.text)
        metadata["authors"] = "; ".join(authors) if authors else None
        
        # Journal
        journal_elem = article.find(".//Journal/Title")
        if journal_elem is not None and journal_elem.text:
            metadata["journal"] = journal_elem.text.strip()
        
        # Also try ISOAbbreviation as fallback
        if not metadata.get("journal"):
            journal_abbrev = article.find(".//Journal/ISOAbbreviation")
            if journal_abbrev is not None and journal_abbrev.text:
                metadata["journal"] = journal_abbrev.text.strip()
        
        # Year
        year_elem = article.find(".//PubDate/Year")
        if year_elem is not None and year_elem.text:
            try:
                metadata["year"] = int(year_elem.text)
            except ValueError:
                pass
        
        # MedlineDate fallback (some articles use this format)
        if not metadata.get("year"):
            medline_date = article.find(".//PubDate/MedlineDate")
            if medline_date is not None and medline_date.text:
                year_match = re.search(r"(\d{4})", medline_date.text)
                if year_match:
                    metadata["year"] = int(year_match.group(1))
        
        # Volume
        volume_elem = article.find(".//JournalIssue/Volume")
        if volume_elem is not None and volume_elem.text:
            metadata["volume"] = volume_elem.text.strip()
        
        # Issue
        issue_elem = article.find(".//JournalIssue/Issue")
        if issue_elem is not None and issue_elem.text:
            metadata["issue"] = issue_elem.text.strip()
        
        # Pages
        pages_elem = article.find(".//Pagination/MedlinePgn")
        if pages_elem is not None and pages_elem.text:
            metadata["pages"] = pages_elem.text.strip()
        
        # DOI
        for article_id in article.findall(".//ArticleId"):
            if article_id.get("IdType") == "doi":
                metadata["doi"] = article_id.text.strip()
                break
        
        # Abstract
        abstract_parts = []
        for abstract_text in article.findall(".//AbstractText"):
            if abstract_text.text:
                label = abstract_text.get("Label")
                if label:
                    abstract_parts.append(f"{label}: {abstract_text.text}")
                else:
                    abstract_parts.append(abstract_text.text)
        if abstract_parts:
            metadata["abstract"] = " ".join(abstract_parts)
        
        return metadata
        
    except ET.ParseError as e:
        print(f"    XML parse error: {e}")
        return None


# -----------------------------------------------------------------------------
# Citation Formatting
# -----------------------------------------------------------------------------

def format_citation_apa(metadata):
    """
    Generate APA 7th edition citation from metadata.
    
    Format: Author, A. A., & Author, B. B. (Year). Title. Journal, Volume(Issue), Pages. https://doi.org/xxx
    """
    if not metadata:
        return None
    
    parts = []
    
    # Authors - APA uses "&" before last author
    if metadata.get("authors"):
        author_list = metadata["authors"].split("; ")
        if len(author_list) > 1:
            parts.append(", ".join(author_list[:-1]) + ", & " + author_list[-1])
        else:
            parts.append(author_list[0])
    else:
        parts.append("[Authors unknown]")
    
    # Year
    if metadata.get("year"):
        parts.append(f"({metadata['year']}).")
    else:
        parts.append("(n.d.).")
    
    # Title (sentence case in APA, but we keep original)
    if metadata.get("title"):
        title = metadata["title"].rstrip(".")
        parts.append(f"{title}.")
    
    # Journal, Volume(Issue), Pages
    journal_part = []
    if metadata.get("journal"):
        journal_part.append(metadata["journal"])  # Italicized in rendered form
    if metadata.get("volume"):
        journal_part.append(f", {metadata['volume']}")
        if metadata.get("issue"):
            journal_part.append(f"({metadata['issue']})")
    if metadata.get("pages"):
        journal_part.append(f", {metadata['pages']}")
    if journal_part:
        parts.append("".join(journal_part) + ".")
    
    # DOI
    if metadata.get("doi"):
        doi = metadata["doi"]
        if not doi.startswith("http"):
            doi = f"https://doi.org/{doi}"
        parts.append(doi)
    
    return " ".join(parts)


def format_citation_mla(metadata):
    """
    Generate MLA 9th edition citation from metadata.
    
    Format: Author(s). "Title." Journal, vol. #, no. #, Year, pp. #-#. DOI.
    """
    if not metadata:
        return None
    
    parts = []
    
    # Authors - MLA uses "and" before last author
    if metadata.get("authors"):
        author_list = metadata["authors"].split("; ")
        if len(author_list) > 1:
            parts.append(", ".join(author_list[:-1]) + ", and " + author_list[-1] + ".")
        else:
            parts.append(author_list[0] + ".")
    else:
        parts.append("[Authors unknown].")
    
    # Title in quotes
    if metadata.get("title"):
        title = metadata["title"].rstrip(".")
        parts.append(f'"{title}."')
    
    # Journal, vol., no., Year, pp.
    journal_parts = []
    if metadata.get("journal"):
        journal_parts.append(metadata["journal"])  # Italicized in rendered form
    if metadata.get("volume"):
        journal_parts.append(f"vol. {metadata['volume']}")
    if metadata.get("issue"):
        journal_parts.append(f"no. {metadata['issue']}")
    if metadata.get("year"):
        journal_parts.append(str(metadata["year"]))
    if metadata.get("pages"):
        journal_parts.append(f"pp. {metadata['pages']}")
    if journal_parts:
        parts.append(", ".join(journal_parts) + ".")
    
    # DOI
    if metadata.get("doi"):
        doi = metadata["doi"]
        if not doi.startswith("http"):
            doi = f"https://doi.org/{doi}"
        parts.append(doi)
    
    return " ".join(parts)


# -----------------------------------------------------------------------------
# High-Level Functions
# -----------------------------------------------------------------------------

def lookup_paper(title, year=None):
    """
    Complete lookup: search PubMed by title and fetch full metadata.
    
    Args:
        title: Paper title
        year: Optional publication year
        
    Returns:
        dict: Full metadata including formatted citations, or None if not found
    """
    print(f"    Searching PubMed for: {title[:60]}...")
    
    pmid = search_pubmed(title, year)
    if not pmid:
        print(f"    No PubMed match found.")
        return None
    
    print(f"    Found PMID: {pmid}")
    
    metadata = fetch_pubmed_metadata(pmid)
    if not metadata:
        print(f"    Failed to fetch metadata for PMID {pmid}")
        return None
    
    # Add formatted citations
    metadata["citation_apa"] = format_citation_apa(metadata)
    metadata["citation_mla"] = format_citation_mla(metadata)
    
    print(f"    Retrieved: {(metadata.get('authors') or 'Unknown')[:40]}... ({metadata.get('year') or '?'})")
    
    return metadata


# -----------------------------------------------------------------------------
# Main (for testing)
# -----------------------------------------------------------------------------

def main():
    """Test PubMed lookup with a known paper."""
    print("=" * 70)
    print("AXIOM - PubMed API Test")
    print("=" * 70)
    print()
    
    # Test with a well-known aging paper
    test_title = "The Hallmarks of Aging"
    test_year = 2013
    
    metadata = lookup_paper(test_title, test_year)
    
    if metadata:
        print()
        print("-" * 70)
        print("METADATA RETRIEVED")
        print("-" * 70)
        for key, value in metadata.items():
            if value and key != "abstract":  # Skip abstract for brevity
                print(f"{key}: {value}")
        print()
        print("-" * 70)
        print("APA CITATION")
        print("-" * 70)
        print(metadata.get("citation_apa"))
        print()
        print("-" * 70)
        print("MLA CITATION")
        print("-" * 70)
        print(metadata.get("citation_mla"))
    else:
        print("Lookup failed.")


if __name__ == "__main__":
    main()
