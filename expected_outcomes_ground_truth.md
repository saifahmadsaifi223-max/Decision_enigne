# Expected Outcomes — Ground Truth Mapping (Public Cases PUB-001 to PUB-012)

This maps each supplied case to the exact policy clause(s) that determine its outcome. Use this as your **evaluation ground truth** (Section 9 requires you to "explain how the expected outcome is established" — this document IS that explanation) and as a build/debug reference for the Coverage & Exclusion Agent.

Policy reference: CSC – Individual Health Insurance, Universal Sompo, UNIHLIP18004V011718.

---

## PUB-001 — Baseline admissible claim
- **Facts:** Inpatient, appendicitis/appendectomy, 96hrs admission, network hospital, 14 months continuous coverage, no PED, no experimental treatment.
- **Governing clauses:** "What We Cover" (Scope of Cover, p.7) — standard hospitalization expenses. 30-day waiting period clause (p.9, item 2) — satisfied since coverage started well before claim and no break. No excluded disease from the "first year" list (p.9, item 3) applies to appendicitis.
- **Sub-limits that apply:** Room rent capped at 1% of Sum Insured/day; doctor fees capped at 25% of Sum Insured; misc (anesthesia/OT/medicines) capped at 40% of Sum Insured — check room ₹30,000 vs 1% of ₹500,000 = ₹5,000/day cap. **Room rent will be capped**, so expect **ADMISSIBLE_WITH_LIMITS**, not plain ADMISSIBLE.
- **Expected decision:** `ADMISSIBLE_WITH_LIMITS` — capped on room rent sub-limit.
- **Key citation:** p.7 "Sub limits... Normal Room expenses: 1.0% of Basic Sum Insured."

## PUB-002 — Initial 30-day waiting period
- **Facts:** Policy started 2026-01-01, claim 2026-01-20 (20 days in), 0 months continuous coverage, viral fever.
- **Governing clause:** p.9, item 2, "30 days Waiting Period" — applies to all claims unless continuously insured previously without break. Here continuous_coverage_months = 0 and prior_insurer_continuous_years = 0, so the exception doesn't apply.
- **Expected decision:** `NOT_ADMISSIBLE` — claim falls squarely within the 30-day initial waiting period with no continuity credit.
- **Key citation:** p.9, "A waiting period of 30 days will apply to all claims unless..."

## PUB-003 — Pre-existing disease (PED) waiting period
- **Facts:** PED (thyroid disorder with complications), 27 months continuous coverage under this policy (started 2024-01-01, claim 2026-04-10 ≈ 27 months).
- **Governing clause:** p.8, "What We Exclude — 1. Pre-existing diseases" — PED excluded until **48 months** of continuous coverage have elapsed since inception, unless the insured had continuous prior coverage with another Indian insurer (reduces waiting period). `prior_insurer_continuous_years: 0` here, so no reduction applies.
- **27 months < 48 months required** → PED waiting period not yet satisfied.
- **Expected decision:** `NOT_ADMISSIBLE` — PED exclusion still in force (needs 48 months, only has 27).
- **Key citation:** p.8, "Pre-existing diseases will not be covered until 48 months of continuous coverage have elapsed, since inception of the first Policy with Us."

## PUB-004 — Domiciliary treatment sub-limit
- **Facts:** Domiciliary treatment, hospital room unavailable (qualifying circumstance), patient_cannot_be_moved: false, non-network provider, 28 months coverage.
- **Governing clauses:** Definitions p.2 "Domiciliary Treatment" — qualifies if either (a) patient's condition prevents hospital transfer OR (b) non-availability of room. Case satisfies (b). p.9 NB2: "Expenses incurred for Domiciliary Hospitalization will be paid up to a maximum aggregate sub-limit of **20% of the Basic Sum Insured**." Also note exclusion list item 17 mentions "Any expense under Domiciliary Hospitalisation for" — this appears to be a truncated/incomplete clause in the source policy (likely a formatting error inherited from the original scan) — flag this as a genuine ambiguity your Validation Agent should catch rather than silently resolve.
- **Expected decision:** `ADMISSIBLE_WITH_LIMITS` — capped at 20% of Sum Insured (₹100,000 cap on ₹500,000 SI); actual claimed expenses (₹25,000 doctor + ₹80,000 meds = ₹105,000) exceed the cap slightly.
- **Key citation:** p.9, "Expenses incurred for Domiciliary Hospitalization will be paid up to a maximum aggregate sub-limit of 20% of the Basic Sum Insured."
- **Note:** This is a good failure-analysis candidate — the exclusion list's item 17 is ambiguous/truncated in the source PDF, which is a realistic "messy real-world document" scenario worth documenting.

## PUB-005 — Day-care / less-than-24-hour treatment (cataract)
- **Facts:** Day-care cataract surgery, 8 hours admission, 29 months continuous coverage, network hospital.
- **Governing clauses:** Definitions p.2, "Day Care Treatment" — qualifies since undertaken under anesthesia in <24hrs due to technological advancement, and would otherwise require >24hr hospitalization. p.7 NB4 explicitly lists **Eye Surgery** as a qualifying day-care procedure where the 24-hour minimum stay is waived.
- **BUT:** p.9, item 3(i) — cataract is in the **first-year exclusion list** (hospitalization expense in the first year of operation of insurance cover). Case has 29 months coverage, well past the first policy year, so this exclusion does NOT apply here.
- **Expected decision:** `ADMISSIBLE` (or `ADMISSIBLE_WITH_LIMITS` if you want to apply the general expense sub-limits from p.7) — day-care treatment properly covered, first-year cataract exclusion doesn't apply since >12 months elapsed.
- **Key citation:** p.7, NB4 "...Eye Surgery... shall also be covered under the Policy," combined with p.9 first-year exclusion not being triggered.

## PUB-006 — Insufficient evidence (deliberate abstention case #1)
- **Facts:** `evidence_context.hospital_registered: null`, `medical_necessity_confirmed: null`. Only 2 documents supplied (claim_form, discharge_summary) — missing itemized bill.
- **Governing clause:** Definitions p.3, "Hospital" — requires registration under Clinical Establishments Act OR compliance with minimum criteria (beds, staff, OT, etc.). This case explicitly cannot confirm either.
- **Expected decision:** `NEEDS_REVIEW` / `INSUFFICIENT_EVIDENCE` — cannot establish the case meets the policy's "Hospital" definition, and medical necessity is unconfirmed.
- **Key citation:** p.3, "Hospital means any institution established for in-patient care... registered... OR complies with all minimum criteria..."

## PUB-007 — Category-specific sub-limits (cancer)
- **Facts:** Cancer, ₹1,000,000 sum insured, very high expenses (doctor fees ₹300,000, meds/diagnostics ₹500,000).
- **Governing clause:** p.7, item 2 — doctor/surgeon fees capped at **25% of Sum Insured** = ₹250,000 (claimed ₹300,000, exceeds cap). Item 3 — medicines/diagnostics/etc. capped at **40% of Sum Insured** = ₹400,000 (claimed ₹500,000, exceeds cap). Also NB3: hospitalization for "Any One Illness" under package charges restricted to 75% of SI or actual, whichever is less.
- **Expected decision:** `ADMISSIBLE_WITH_LIMITS` — multiple category sub-limits bite; payable amount is materially less than billed amount.
- **Key citation:** p.7, "Medical Practitioner/Anesthetist, Consultant fees, Surgeons fees... subject to a limit of 25% of Sum Assured" + "...subject to a limit of 40% Sum Insured."

## PUB-008 — Cosmetic surgery exclusion
- **Facts:** "Cosmetic condition," cosmetic surgery.
- **Governing clause:** p.9, item 5 — excludes "cosmetic or aesthetic treatment of any description (including any complications arising thereof), plastic surgery except those relating to treatment of Injury or Disease."
- **Expected decision:** `NOT_ADMISSIBLE` — squarely excluded as cosmetic treatment, not linked to injury/disease correction.
- **Key citation:** p.9, "vaccination, inoculation, cosmetic or aesthetic treatment of any description... plastic surgery except those relating to treatment of Injury or Disease."

## PUB-009 — Pre/post-hospitalization time windows
- **Facts:** Pre-hospitalization expenses incurred 30 days before admission; post-hospitalization 60 days after discharge; same condition confirmed.
- **Governing clause:** p.9, "Note" — "Pre-Hospitalisation up to a maximum of **30 days** immediately preceding Hospitalisation and Post Hospitalisation expenses up to a maximum of **60 days** immediately following Hospitalisation will also be reimbursed." Both windows are at the maximum boundary (30 and 60 days) — i.e., exactly at the limit, not exceeding it.
- **Expected decision:** `ADMISSIBLE_WITH_LIMITS` (or `ADMISSIBLE` if you treat "up to 30/60 days" as inclusive) — pre/post expenses admissible since within the exact windows; general expense sub-limits from p.7 still apply to the underlying hospitalization amounts.
- **Key citation:** p.9, "Pre-Hospitalisation up to a maximum of 30 days... and Post Hospitalisation expenses up to a maximum of 60 days..."
- **Note:** Good test of boundary-condition handling — this is exactly at the limit, not beyond it. A naive system might wrongly reject it as "exceeding" 30/60 days if it uses `>` instead of `>=`/inclusive logic.

## PUB-010 — Portability / continuity from prior insurer
- **Facts:** Policy started 2026-01-01 (only 8 months with this insurer), but 1 year continuous prior coverage with another Indian insurer, database/claim history received, cataract day-care.
- **Governing clauses:** p.9, item 2, 30-day waiting period exception — "You were insured continuously and without interruption for at least 1 year under any other Indian insurer's individual health insurance Policy..." → 30-day waiting period is waived. Also p.9, item 3 first-year cataract exclusion — waived if "insured continuously and without interruption for at least 1 year under Our or any other Indian insurer's...Policy," which is satisfied here (1 year prior + evidence received per the NB conditions).
- **Expected decision:** `ADMISSIBLE` (or `ADMISSIBLE_WITH_LIMITS` if general sub-limits reduce payable amount) — both the 30-day and first-year cataract exclusions are waived due to verified prior continuous coverage.
- **Key citation:** p.9, "a waiting period of 1 year will not apply if You were insured continuously and without interruption for at least 1 year under Our or any other Indian insurer's individual health insurance Policy..." + NB conditions on database/claim history being received (satisfied here).

## PUB-011 — Insufficient evidence (deliberate abstention case #2)
- **Facts:** Non-network, unnamed/unverified facility ("Unknown Care Facility"), only 2 documents (claim_form, itemized_bill — no discharge summary), `hospital_registered: null`, `hospital_minimum_criteria_documented: false`.
- **Governing clause:** Definitions p.3, "Hospital" — same as PUB-006, but here it's explicit that minimum criteria are NOT documented (`false`, not just unknown), which is arguably even more clear-cut evidence of a policy-definition failure — though the case explicitly frames it as "cannot be finally decided," implying the safe move is still abstention rather than an outright rejection, since a genuinely registered small hospital might still qualify under the Act even without documented minimum criteria.
- **Expected decision:** `NEEDS_REVIEW` / `INSUFFICIENT_EVIDENCE` — cannot confirm facility meets the "Hospital" definition either via registration or minimum criteria.
- **Key citation:** p.3, "Hospital means any institution... registered... under the Clinical Establishments (Registration and Regulation) Act, 2010... OR complies with all minimum criteria as under..."
- **Design note:** This case is a good test that your Decision Agent doesn't over-confidently jump to NOT_ADMISSIBLE just because criteria are undocumented — undocumented ≠ proven absent.

## PUB-012 — Experimental treatment exclusion
- **Facts:** "Experimental condition," "experimental therapy," `experimental: true`.
- **Governing clause:** Definitions p.6, "Unproven/Experimental Treatment means a treatment... which is not based on established medical practice in India, is treatment experimental or unproven." This definition itself signals exclusion; cross-reference with general "What We Cover" scope, which only covers standard medical/surgical treatment — experimental treatment falls outside intended scope.
- **Expected decision:** `NOT_ADMISSIBLE` — experimental/unproven treatment, excluded by definition and scope.
- **Key citation:** p.6, "Unproven/Experimental Treatment means a treatment, including drug Experimental therapy, which is not based on established medical practice in India, is treatment experimental or unproven."
- **Note:** The policy's "What We Exclude" numbered list doesn't explicitly restate "experimental treatment" as its own line item — the exclusion is implied through the definition + scope of cover rather than a direct exclusion clause. This is a **good failure-analysis / reliability scenario**: your Validation Agent should confirm this indirect reasoning chain is still evidence-grounded rather than assumed, and your citation should point to the definition clause plus the "What We Cover" scope statement, not an exclusion bullet that doesn't exist.

---

## Summary table

| Case | Expected Decision | Primary Clause Location |
|---|---|---|
| PUB-001 | ADMISSIBLE_WITH_LIMITS | p.7 room rent sub-limit |
| PUB-002 | NOT_ADMISSIBLE | p.9 30-day waiting period |
| PUB-003 | NOT_ADMISSIBLE | p.8 PED 48-month exclusion |
| PUB-004 | ADMISSIBLE_WITH_LIMITS | p.9 domiciliary 20% sub-limit |
| PUB-005 | ADMISSIBLE | p.7 NB4 day-care eye surgery |
| PUB-006 | NEEDS_REVIEW | p.3 Hospital definition unconfirmed |
| PUB-007 | ADMISSIBLE_WITH_LIMITS | p.7 25%/40% category caps |
| PUB-008 | NOT_ADMISSIBLE | p.9 cosmetic exclusion |
| PUB-009 | ADMISSIBLE_WITH_LIMITS | p.9 pre/post 30/60-day windows |
| PUB-010 | ADMISSIBLE | p.9 portability waiting-period waiver |
| PUB-011 | NEEDS_REVIEW | p.3 Hospital definition unconfirmed |
| PUB-012 | NOT_ADMISSIBLE | p.6 experimental treatment definition |

**Abstention count check:** PUB-006 and PUB-011 = 2 NEEDS_REVIEW cases ✓ (assignment requires ≥2, satisfied by the public set alone).

## Notable ambiguities worth flagging in your failure analysis (Section 9)
1. **PUB-004 domiciliary exclusion (p.9 item 17)** appears truncated in the source PDF ("Any expense under Domiciliary Hospitalisation for" — incomplete sentence). This is a real-world messy-document scenario; document how your system handles a clause it can't fully parse.
2. **PUB-009 boundary condition** (exactly 30/60 days) — tests inclusive vs. exclusive interpretation of "up to a maximum of."
3. **PUB-012 indirect exclusion** — experimental treatment is excluded via definition + scope, not a direct exclusion bullet, testing whether your agents chain evidence correctly instead of hallucinating a nonexistent exclusion line.

## Chunking implications from this read
The policy has clean, consistent section headers you can split on:
`DEFINITIONS` → `Critical Illness` → `SCOPE OF COVER` (`WHAT WE COVER`) → `WHAT WE EXCLUDE` → `EXTENSIONS` → `CLAIMS PROCEDURE` → `STANDARD TERMS AND CONDITIONS` (numbered 1–21) → Ombudsman directory (pp.16–17, not policy-substantive — consider excluding from the index or tagging as "administrative/non-decision-relevant" so it doesn't pollute retrieval).

Definitions should probably be chunked **per-term** (one chunk per defined term, e.g., "Hospital," "Domiciliary Treatment," "Pre-Existing Diseases") rather than as one giant block — this gives you precise citations and much better retrieval precision, since a query like "does this facility qualify as a Hospital" should retrieve exactly the Hospital definition, not the entire definitions section.

"What We Exclude" and "Standard Terms and Conditions" should be chunked **per numbered item** for the same reason — clean citation granularity, e.g. `chunk_id: exclude_item_1` → PED, `exclude_item_5` → cosmetic/plastic surgery.
