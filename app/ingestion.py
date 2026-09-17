"""
Policy ingestion and chunking.

Design (see expected_outcomes_ground_truth.md for the full rationale):
- The policy has a small number of major sections with consistent headers:
  DEFINITIONS, Critical Illness, SCOPE OF COVER / WHAT WE COVER,
  WHAT WE EXCLUDE, EXTENSIONS, CLAIMS PROCEDURE,
  STANDARD TERMS AND CONDITIONS (numbered 1-21).
- Within DEFINITIONS, chunk PER DEFINED TERM (bold term followed by "means").
- Within WHAT WE EXCLUDE and STANDARD TERMS AND CONDITIONS, chunk PER
  NUMBERED ITEM.
- Everything else (Scope of Cover, Extensions, Claims Procedure) chunks
  per paragraph/bullet-group, capped at ~400 words, never mid-sentence.
- The Ombudsman directory (pp.16-17) is administrative, not
  decision-relevant -- it is excluded from the index by default so it
  doesn't pollute retrieval, but the raw text is still preserved in
  case you want it later.

This module intentionally avoids naive fixed-size chunking per
Section 4.1 of the assignment.
"""

import re
from pathlib import Path
from typing import List

from app.models import PolicyChunk

try:
    import fitz  # PyMuPDF -- preferred, faster
    _PDF_BACKEND = "pymupdf"
except ImportError:
    import pdfplumber  # fallback if PyMuPDF isn't installed
    _PDF_BACKEND = "pdfplumber"


SECTION_HEADERS = [
    "DEFINITIONS",
    "Critical Illness",
    "SCOPE OF COVER",
    "WHAT WE COVER",
    "WHAT WE EXCLUDE",
    "EXTENSIONS",
    "CLAIMS PROCEDURE",
    "STANDARD TERMS AND CONDITIONS",
]

# Section where administrative content (ombudsman addresses) begins --
# excluded from the index by default.
ADMIN_SECTION_MARKER = "AHMEDABAD"  # first ombudsman city heading in the doc


def extract_pages(pdf_path: str) -> List[str]:
    """Returns a list of page texts, index 0 == page 1."""
    if _PDF_BACKEND == "pymupdf":
        doc = fitz.open(pdf_path)
        pages = [page.get_text("text") for page in doc]
        doc.close()
        return pages
    else:
        with pdfplumber.open(pdf_path) as pdf:
            return [page.extract_text() or "" for page in pdf.pages]


def _find_section_for_line(line: str, current_section: str) -> str:
    for header in SECTION_HEADERS:
        if header.lower() in line.lower():
            return header
    return current_section


def chunk_definitions(text: str, page: int, chunk_id_prefix: str) -> List[PolicyChunk]:
    """Split the Definitions section into one chunk per defined term.

    Heuristic: a new term starts at a line where a capitalized phrase is
    immediately followed by "means" (the policy's consistent phrasing,
    e.g. "Accident means a sudden unforeseen...").
    """
    term_pattern = re.compile(r"([A-Z][A-Za-z /\-]{2,40}?)\s+means\s")
    chunks = []
    matches = list(term_pattern.finditer(text))
    for i, m in enumerate(matches):
        term = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) < 5:
            continue
        chunks.append(
            PolicyChunk(
                chunk_id=f"{chunk_id_prefix}_def_{i:03d}",
                text=body,
                page=page,
                section="Definitions",
                subsection=term,
            )
        )
    return chunks


def chunk_numbered_list(text: str, page: int, section_name: str, chunk_id_prefix: str) -> List[PolicyChunk]:
    """Split a numbered-list section (exclusions, standard terms) into one
    chunk per top-level numbered item, e.g. "1. Pre-existing diseases ...".

    NOTE: some sections contain NESTED numbering -- e.g. "11. Free Look-up
    period" has its own sub-points labeled "1." and "2." again, and page 8's
    "Additional Benefits" sub-list restarts at "1." under "Note". The regex
    below matches any line starting with a number, so it will match both the
    top-level items AND these nested restarts. That's fine for citation
    granularity (each is still a meaningful, citable unit) -- but it means
    the captured item number is NOT guaranteed unique within a block. Chunk
    IDs therefore use a running enumerate index, never the captured number,
    to avoid ID collisions (this was the source of a real DuplicateIDError
    bug found during testing).
    """
    item_pattern = re.compile(r"^\s*(\d{1,2})\.\s+", re.MULTILINE)
    chunks = []
    matches = list(item_pattern.finditer(text))
    for i, m in enumerate(matches):
        item_no = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) < 5:
            continue
        chunks.append(
            PolicyChunk(
                chunk_id=f"{chunk_id_prefix}_{section_name.lower().replace(' ', '_')}_{i:03d}",
                text=body,
                page=page,
                section=section_name,
                subsection=f"Item {item_no}",
            )
        )
    return chunks


def chunk_generic(text: str, page: int, section_name: str, chunk_id_prefix: str, max_words: int = 350) -> List[PolicyChunk]:
    """Fallback chunker for prose sections (Scope of Cover intro, Extensions,
    Claims Procedure). Splits on blank-line paragraph boundaries, then
    merges small paragraphs up to max_words, never splitting mid-sentence.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    buffer = ""
    idx = 0
    for para in paragraphs:
        candidate = (buffer + "\n\n" + para).strip() if buffer else para
        if len(candidate.split()) > max_words and buffer:
            chunks.append(
                PolicyChunk(
                    chunk_id=f"{chunk_id_prefix}_{section_name.lower().replace(' ', '_')}_{idx:03d}",
                    text=buffer,
                    page=page,
                    section=section_name,
                )
            )
            idx += 1
            buffer = para
        else:
            buffer = candidate
    if buffer:
        chunks.append(
            PolicyChunk(
                chunk_id=f"{chunk_id_prefix}_{section_name.lower().replace(' ', '_')}_{idx:03d}",
                text=buffer,
                page=page,
                section=section_name,
            )
        )
    return chunks


def build_policy_index(pdf_path: str) -> List[PolicyChunk]:
    """Main entry point: parse the PDF, split by section per page, and
    apply the appropriate chunking strategy per section.

    NOTE: This is a first pass tuned to the known structure of the
    UNIHLIP18004V011718 policy wording. If you swap in a different policy
    PDF, re-verify the SECTION_HEADERS list and regex patterns still match.
    """
    pages = extract_pages(pdf_path)
    all_chunks: List[PolicyChunk] = []
    current_section = "Prospectus"

    for page_num, page_text in enumerate(pages, start=1):
        if ADMIN_SECTION_MARKER in page_text:
            # Stop indexing once we hit the ombudsman directory.
            break

        # Detect section header(s) present on this page and split the page
        # text into sub-blocks per section if more than one header appears.
        lines = page_text.split("\n")
        section_starts = []
        for i, line in enumerate(lines):
            for header in SECTION_HEADERS:
                if header.lower() in line.lower() and len(line.strip()) < 60:
                    section_starts.append((i, header))

        if not section_starts:
            # No new section header on this page -- continue current_section
            blocks = [(current_section, page_text)]
        else:
            blocks = []
            # IMPORTANT: capture the section active BEFORE this page started.
            # Pre-header text on this page belongs to that section, not to
            # whatever new header appears first on this page (a page can
            # contain the tail of one section and the start of the next,
            # e.g. p.8 has both the end of "What We Cover" and the start of
            # "What We Exclude").
            section_before_this_page = current_section
            for idx, (line_no, header) in enumerate(section_starts):
                start_line = line_no
                end_line = section_starts[idx + 1][0] if idx + 1 < len(section_starts) else len(lines)
                block_text = "\n".join(lines[start_line:end_line])
                blocks.append((header, block_text))
                current_section = header
            if section_starts[0][0] > 0:
                pre_text = "\n".join(lines[: section_starts[0][0]])
                blocks.insert(0, (section_before_this_page, pre_text))

        for section_name, block_text in blocks:
            prefix = f"p{page_num}"
            if "DEFINITIONS" in section_name.upper():
                all_chunks.extend(chunk_definitions(block_text, page_num, prefix))
            elif "EXCLUDE" in section_name.upper() or "STANDARD TERMS" in section_name.upper():
                all_chunks.extend(chunk_numbered_list(block_text, page_num, section_name, prefix))
            else:
                all_chunks.extend(chunk_generic(block_text, page_num, section_name, prefix))

    # Safety net: guarantee globally unique chunk_ids no matter what
    # upstream chunking logic does. This is a defense-in-depth measure --
    # a DuplicateIDError from Chroma is much harder to debug than a
    # deduplication happening here with a visible suffix.
    seen: dict[str, int] = {}
    for chunk in all_chunks:
        if chunk.chunk_id in seen:
            seen[chunk.chunk_id] += 1
            chunk.chunk_id = f"{chunk.chunk_id}_dup{seen[chunk.chunk_id]}"
        else:
            seen[chunk.chunk_id] = 0

    return all_chunks


if __name__ == "__main__":
    import sys
    import json

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "data/USGIC-CSCIndividualHealthInsurance_2017-2018.pdf"
    chunks = build_policy_index(pdf_path)
    print(f"Extracted {len(chunks)} chunks")
    for c in chunks[:10]:
        print(f"[{c.chunk_id}] p.{c.page} {c.section}/{c.subsection or ''}: {c.text[:80]!r}")

    out_path = Path("data/policy_chunks.json")
    out_path.write_text(json.dumps([c.model_dump() for c in chunks], indent=2))
    print(f"Saved to {out_path}")
