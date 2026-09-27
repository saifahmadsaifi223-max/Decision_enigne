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
| 5 | Engineering | Correct, well-grounded conclusion wrongly flagged unsupported | Validator only saw the LLM's self-reported citation subset, incomplete for multi-hop reasoning | Attach full retrieved evidence set to each finding, not just self-reported citations; also tightened dimension-scoping to reduce overreach |
| 6 | Engineering | Correct percentage-to-rupee calculation flagged unsupported | Validator required the exact rupee figure to appear verbatim in the policy text, not recognizing correct arithmetic on an explicitly-stated percentage rule | Validation prompt now explicitly distinguishes "hallucinated fact" from "correct derivation from a stated rule + a known case fact" |

---

## Failure 6: Correct percentage-based calculation wrongly failed validation

**What happened:** A sub-limit finding correctly stated: *"Normal Room
expenses limit at 1% of Basic Sum Insured (₹5,000)..."* for a case with a
₹500,000 Sum Insured. 1% of 500,000 is, correctly, 5,000. The Validation
Agent flagged this as unsupported anyway.

**Root cause:** The policy excerpt states the rule as a percentage
("1.0% of Basic Sum Insured"), never as a precomputed rupee figure --
naturally, since the source policy is written once for all policyholders
regardless of their individual Sum Insured. The Decision Agent correctly
combined this general rule with the specific case's Sum Insured to
compute a concrete number. But the Validation Agent's strictness, phrased
as "does the excerpt directly support this," didn't distinguish between
*hallucinating* a number not grounded in anything, and *correctly deriving*
a number from a rule that IS explicitly stated, combined with a case fact
that's also explicitly given. Both looked the same to a validator that
was only checking for verbatim presence.

**Fix:** Two prompt-based attempts (allowing "honest uncertainty," then
explicitly allowing "correct arithmetic on a stated rule") reduced but
did not eliminate the false-failure rate -- the same conclusion pattern
(sub-limits stated correctly, admission that the breakdown needed to
check them isn't available) was flagged unsupported on repeated,
near-identical test runs despite explicit prompt instructions and worked
examples. This showed prompt engineering alone wasn't a reliable enough
guarantee for a safety-relevant check. The decisive fix moved the "honest
uncertainty" case out of the LLM's hands entirely: `_validate_finding()`
now short-circuits deterministically -- any finding where the Decision
Agent already reported `supports_admissibility=None` (i.e. it already
admitted it couldn't determine the answer either way) is automatically
treated as supported, with no second LLM call at all. There is nothing to
hallucinate in an honest "I don't know," so there is nothing for a second
model to inconsistently mis-judge. The LLM validator is now only invoked
for findings making a *definite* claim (`true`/`false`), which is exactly
the class of claim that actually needs auditing.

**Broader note for future work:** the most robust long-term fix for this
entire class of problem is a **deterministic calculation tool** -- a
plain Python function that applies percentage/cap rules to claimed
amounts, rather than asking an LLM to both compute *and* self-verify
arithmetic. This removes the ambiguity entirely rather than teaching a
validator to be more lenient about correct math. Not implemented in this
build due to time constraints; documented here as the natural next step.

**Takeaway:** when a probabilistic check (an LLM call) proves
inconsistent even after prompt refinement, the more reliable fix is often
to identify the subset of cases that can be handled deterministically in
code and remove them from the probabilistic path entirely, rather than
continuing to iterate on prompt wording and hoping for consistency.

---

## Failure 5: Correct multi-hop conclusion wrongly failed validation

**What happened:** Custom test case CUST-006 (an adventure-sports injury
claim, designed specifically to test the LLM fallback dimension detector
from Failure 3's follow-up work) produced a fully correct finding:

> *"The injury occurred [during] bungee jumping, which is defined as an
> adventure sport, and any expense related to injury sustained whilst
> engaging in adventure sports is excluded, so the claim is not
> admissible."*

This is exactly right -- correctly grounded, correctly reasoned. But the
Validation Agent flagged it as **unsupported**, and the case wrongly fell
back to `NEEDS_REVIEW` instead of the correct `NOT_ADMISSIBLE`.

**Root cause:** The conclusion required combining two separate policy
facts from two different retrieved chunks: (1) the Definitions section
explicitly names "bungee jumping" as an example of an Adventure Sport
(p.2), and (2) the exclusion clause states adventure-sport injuries
aren't covered (p.9, item 14). The Decision Agent's LLM correctly reasoned
across both chunks -- but when asked to self-report which chunk_ids it
cited, it named only one of the two. The Validation Agent then checked
the conclusion against only that one incomplete chunk and, correctly
given what it was shown, could not verify that "bungee jumping" and
"adventure sport" were the same thing -- so it flagged the finding as
unsupported, even though the underlying reasoning was entirely correct.

This is a case where every individual component behaved reasonably given
its inputs, but the overall pipeline still produced a wrong outcome
because of an information-passing gap between two steps.

**Fix:** Two changes in `app/agents/decision.py` and
`app/agents/validation.py`:
1. `assess_dimension()` now attaches the **full retrieved evidence set**
   to each `DimensionFinding`, not just the subset of chunk_ids the
   drafting LLM self-reported as "cited." This trades a slightly longer
   citation list for a validation check that has access to everything
   the reasoning could plausibly have drawn on, closing the multi-hop
   evidence gap directly.
2. Tightened the Decision Agent's prompt to keep each dimension's
   conclusion narrowly scoped to that one dimension (e.g. "not on this
   specific exclusion list" rather than the broader, unverifiable "not
   excluded overall") -- reducing a separate, related overreach issue
   observed on the same test case (`first_year_disease_exclusion`
   incorrectly concluding the claim wasn't excluded "at all," when it
   could only speak to one specific exclusion list, not the policy's
   other exclusion clauses).
3. Added a concrete worked example to the Validation Agent's prompt
   clarifying that a conclusion honestly admitting uncertainty (e.g. "the
   expense breakdown isn't provided, so it's unclear whether a sub-limit
   is exceeded") should be marked *supported*, not failed -- this was
   already the intended rule but was being followed inconsistently by
   the validation model without a concrete example to anchor it.

**Why this is worth documenting rather than hiding:** it demonstrates the
system's own safety mechanism (the Validation Agent) erring on the side
of caution rather than letting an unverifiable claim through -- which is
the correct failure direction for an insurance-adjudication context, even
though the specific trigger here was a false positive rather than a true
hallucination. Catching and fixing the root cause (incomplete evidence
attachment) rather than loosening the validator's strictness preserves
that safety property while eliminating the false positive.

---

## Additional table rows (7-8)

| # | Type | Symptom | Root cause | Fix |
|---|---|---|---|---|
| 7 | Content/reasoning | PUB-010 wrongly decided NOT_ADMISSIBLE | Two similarly-themed waiver clauses (item 2's 30-day general waiver, item 3's first-year disease-list waiver) have DIFFERENT conditions attached, and the system conflated them | Split the portability-continuity investigation question into two explicitly separate sub-questions, one per clause, with their differing conditions spelled out |
| 8 | Engineering | PUB-004 wrongly decided PARTIALLY_ADMISSIBLE | LLM fallback dimension detector proposed "is this really day-care/outpatient" questions that second-guessed an already-established treatment type (domiciliary), rather than proposing genuinely new considerations | Fallback prompt now explicitly forbids re-classifying a claim's already-given `treatment.type` |

---

## Failure 7: Two similarly-worded waiver clauses conflated (PUB-010)

**What happened:** PUB-010 (cataract surgery, 8 months with current insurer,
but 1 continuous prior year with another Indian insurer) was decided
`NOT_ADMISSIBLE`. My own original ground truth (see
`expected_outcomes_ground_truth.md`) called this `ADMISSIBLE` -- but a
closer re-read of the policy during debugging revealed my own ground
truth was itself incomplete.

**Root cause:** The policy contains two separate waiting-period waiver
provisions that both key off "1 continuous prior year with another Indian
insurer," but with **different conditions attached**:
- Item 3 (first-year, disease-specific exclusion list -- covers cataract):
  waived by 1 continuous prior year alone. No further condition.
- Item 2 (general 30-day waiting period), when waived via the *same*
  1-year-prior-insurer route, **additionally** requires establishing that
  the insured "was unaware of and had not taken any advice or medication"
  for the specific condition.

The system's single, undifferentiated investigation question asked about
"waiting-period reduction" generically, and the resulting finding
incorrectly concluded the cataract-specific exclusion (item 3) was
*not* waived -- when it should have been, since item 3 carries no
awareness requirement at all. The system appears to have applied item
2's stricter condition to item 3's simpler one.

**Fix:** Rewrote the `portability_continuity` investigation question in
`app/agents/case_analysis.py` to explicitly separate the two clauses and
state their differing conditions, rather than asking one generic question
and hoping the model keeps the two straight on its own.

**Also revised:** my own ground truth for PUB-010. Given the case data
never addresses whether the insured was aware of the cataract condition
beforehand, item 2's specific 30-day waiver condition is genuinely
unresolved by the supplied facts -- while item 3's cataract-specific
exclusion is cleanly waived regardless. A fully rigorous answer likely
depends on whether item 2's general 30-day rule is read as an independent
hurdle stacked on top of item 3, or as effectively superseded once the
more specific, on-point exclusion (item 3) is waived. The policy text
does not explicitly resolve this stacking question. Documented here as a
genuine content ambiguity (alongside Failure 4) rather than papered over
with an artificially confident ground truth label.

---

## Failure 8: LLM fallback dimension detector second-guessed an established fact (PUB-004)

**What happened:** PUB-004 (a domiciliary treatment claim) was decided
`PARTIALLY_ADMISSIBLE` instead of the expected `ADMISSIBLE_WITH_LIMITS`.
The findings included *"Day-care treatment is not recognised as covered"*
and *"Outpatient-only expenses are excluded"* -- neither of which has any
bearing on a domiciliary claim.

**Root cause:** The LLM fallback dimension detector added in response to
the CUST-006 generalization work (see the case_analysis.py module
docstring) is deliberately open-ended, so it can catch claim shapes the
deterministic rules didn't anticipate. But its prompt didn't forbid it
from *re-questioning facts already given in the claim* -- `treatment.type`
is `"domiciliary"`, a fact from the input data, not something to
re-derive. The fallback proposed checking whether the treatment might
"really" be day-care or outpatient instead, and the Decision Agent, faced
with negative findings on those two invented dimensions alongside a
positive domiciliary finding, split the claim into partially-admissible
pieces that don't actually exist.

**Fix:** Added an explicit rule to `FALLBACK_DIMENSION_SYSTEM_PROMPT`:
the claim's already-established `treatment.type` is a given fact, not
something to question, and the detector should only propose genuinely
*additional* policy considerations, never alternate framings of facts
already in the claim data.

**Why this is worth documenting:** it's a direct, concrete illustration
of a real risk in any "LLM proposes what to investigate" design --
without an explicit boundary, the model's open-endedness can work against
you as easily as for you. The fix that made CUST-006 (adventure sports)
work correctly needed an equally explicit *constraint* to stop it from
overreaching on a different, more straightforward case.

