# Failure Analysis

Four failures discovered during development, each with root cause and fix.
Three are engineering/system bugs found while building and testing against
the real policy PDF and live LLM calls; the fourth is a genuine ambiguity
in the source policy document itself, surfaced by the evaluation cases.
All four directly informed the reliability scenarios listed in Section 10
of the assignment.

---

## Failure 1: Duplicate chunk IDs crashed ingestion (Chroma `DuplicateIDError`)

**What happened:** Running `python -m app.ingestion` against the real
policy PDF crashed with:
```
chromadb.errors.DuplicateIDError: Expected IDs to be unique, found
duplicates of: p8_what_we_exclude_item_1, p13_standard_terms_and_conditions_item_2
```

**Root cause:** The policy document contains **nested numbering**. For
example, page 13's "Free Look-up period" is item **11** in the main
numbered list of Standard Terms and Conditions, but its own sub-points are
independently labeled "1." and "2." again. The chunker's regex matched
*any* line starting with a digit and a period, so it matched both the
top-level items and these nested restarts. Chunk IDs were built directly
from the captured item number (e.g. `..._item_1`), so the nested "1." on
page 13 collided with the real top-level item 1 elsewhere in that block.
The same pattern occurred on page 8, where "Additional Benefits"
restarts its own sub-list at "1." under a "Note" heading.

**Fix:** Two changes in `app/ingestion.py`:
1. Chunk IDs now use a running `enumerate()` index rather than the
   captured item number, while the actual item number is preserved as
   human-readable `subsection` metadata (e.g. `subsection="Item 11"`) for
   citation display.
2. Added a global deduplication safety net at the end of
   `build_policy_index()` that guarantees unique chunk IDs regardless of
   what any individual chunking function produces, appending a `_dupN`
   suffix on collision. This is defense-in-depth: even if another section
   of the policy (or a different policy entirely, if this pipeline is
   reused) has an unexpected numbering quirk, ingestion can no longer
   crash on this class of bug.

**Verified:** Re-ran ingestion against the real PDF: 107 chunks, 107
unique IDs, zero duplicates.

---

## Failure 2: Validation Agent always failed, even on correct decisions

**What happened:** Every single decision, including ones with clearly
accurate, well-grounded findings, was flagged `validation.status: FAIL`
with all key findings listed as "unsupported."

**Root cause:** The Validation Agent was checking the Decision Agent's
final `key_findings` (short paraphrased bullets like *"30-day waiting
period satisfied due to continuous coverage"*) against `citations`
(`claim` field = the full original per-dimension conclusion sentence,
e.g. *"The 30-day waiting period does not apply because the insured has
been continuously covered..."*). Matching was done by substring
containment. A paraphrase is, by construction, essentially never a
literal substring of the sentence it paraphrases -- so the match almost
always failed, and the agent treated "no substring match" as "no
supporting citation exists," even when a citation manifestly did exist
and did support the claim.

**Fix:** Restructured the pipeline so validation happens **before**
paraphrasing, not after. Each `DimensionFinding.conclusion` is checked
against its own `DimensionFinding.evidence` -- the exact pairing the
Decision Agent already established internally, with no string-matching
required. Findings that fail this check are replaced with an honest
"could not verify this dimension" placeholder (not silently dropped),
which naturally lets a case still be evaluated on its supported
dimensions rather than throwing away the whole decision. See
`app/agents/validation.py` docstring for the full before/after
architecture.

**Why this mattered for the assignment specifically:** This is exactly
the class of bug Section 10's last reliability scenario names -- "The LLM
attempts to make a claim that cannot be supported by the policy" -- except
the actual failure mode was the reverse: the *validator* itself was
unreliable, which is arguably worse, since it would have silently
downgraded every correct decision to NEEDS_REVIEW, making the system
look far less capable than it actually was.

---

## Failure 3: Wrong/retired LLM model name caused every request to 500

**What happened:** Every `/analyze` request failed with `500 Internal
Server Error`. The uvicorn log showed:
```
KeyError: 'GROQ_API_KEY'
```
and, after fixing that, once the key loaded correctly:
the model `llama-3.3-70b-versatile` is no longer available on the
account's Groq tier.

**Root cause:** Two compounding issues:
1. `python-dotenv` was listed in `requirements.txt` but `load_dotenv()`
   was never actually called anywhere in the code, so `.env` contents
   (including `GROQ_API_KEY`) were never loaded into the process
   environment in the first place.
2. Separately, the hardcoded default model name had been retired from
   Groq's available lineup.

**Fix:**
1. Added `from dotenv import load_dotenv; load_dotenv()` as the first
   lines of `app/api.py` (the actual entrypoint) and, as a safety net for
   any module run standalone (e.g. `eval/evaluate.py`), inside
   `app/llm_client.py` too.
2. Switched the default model to `openai/gpt-oss-120b`, confirmed present
   in the account's actual available-models list (checked via the Groq
   console's Limits page rather than assumed from memory/training data).

**Takeaway documented for the design note:** LLM provider model
availability changes over time and should never be hardcoded from
memory -- always verify against the provider's live console/API before
relying on a specific model string.

---

## Failure 4 (content, not code): Ambiguous/truncated exclusion clause in the source policy

**What happened:** While tracing PUB-004 (domiciliary treatment) against
the actual policy text to establish ground truth for evaluation, item 17
of the "What We Exclude" list reads only:

> "17. Any expense under Domiciliary Hospitalisation for"

This sentence is incomplete in the source PDF -- it has no object. It's
unclear whether this is a scanning/formatting artifact in the original
document or a genuine drafting error in the policy wording itself.

**Root cause:** This is not a bug in our system -- it's a real ambiguity
in the authoritative source document itself, which Section 1 of the
assignment designates as the "authoritative source for policy decisions."

**How the system handles it:** The chunker preserves this clause exactly
as written rather than attempting to silently "fix" or complete it --
silently repairing ambiguous source text would be worse than surfacing
the ambiguity, since it would fabricate a policy provision that doesn't
actually exist in the source. When retrieval surfaces this chunk for a
domiciliary-treatment case, the Decision Agent's grounding requirement
means it cannot confidently assert what the truncated clause excludes --
at most, the general Domiciliary Hospitalization sub-limit (p.9, "up to
20% of the Basic Sum Insured," which IS complete and unambiguous) is
applied, and the truncated item 17 is treated as inconclusive rather than
as grounds for an outright exclusion.

**What we did NOT do:** We deliberately did not use outside insurance
knowledge to guess what item 17 was "supposed to" say, per the
assignment's explicit instruction in Section 2: "Do not use external
medical or insurance knowledge to invent a policy conclusion."

---

## Summary table

| # | Type | Symptom | Root cause | Fix |
|---|---|---|---|---|
| 1 | Engineering | Ingestion crash (DuplicateIDError) | Nested numbering in source doc broke ID uniqueness assumption | Enumerate-based IDs + global dedup safety net |
| 2 | Engineering | Validation always FAILs, even when correct | Comparing paraphrased text to original text via substring match | Validate before paraphrasing, against the original evidence pairing |
| 3 | Engineering | Every request 500s | `.env` never loaded + retired model name | Added `load_dotenv()`; switched to a verified-available model |
| 4 | Content/data | Truncated exclusion clause (p.9, item 17) | Ambiguity in the source policy document itself | Preserved as-is; treated as inconclusive rather than fabricated |
