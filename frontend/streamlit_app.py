"""
Streamlit reviewer frontend (Section 8).

Run with: streamlit run frontend/streamlit_app.py

Set API_BASE_URL env var to point at your deployed FastAPI backend, or
leave default for local dev (http://localhost:8000).
"""

import json
import os

import requests
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Aptino Claim Decision Engine", layout="wide")
st.title("Policy-Aware Multi-Agent RAG Claim Decision Engine")

with st.sidebar:
    st.subheader("Load a case")
    source = st.radio("Source", ["Upload / paste JSON", "Pick a public test case"])

    case_json = None
    if source == "Pick a public test case":
        try:
            with open("data/public_test_cases.json") as f:
                cases = json.load(f)
            case_ids = [c["case_id"] for c in cases]
            selected = st.selectbox("Case", case_ids)
            case_json = next(c for c in cases if c["case_id"] == selected)
            st.json(case_json, expanded=False)
        except FileNotFoundError:
            st.warning("data/public_test_cases.json not found.")
    else:
        uploaded = st.file_uploader("Upload case JSON", type=["json"])
        pasted = st.text_area("...or paste case JSON here", height=200)
        if uploaded:
            case_json = json.load(uploaded)
        elif pasted.strip():
            try:
                case_json = json.loads(pasted)
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

    analyze_clicked = st.button("Analyze claim", type="primary", disabled=case_json is None)

if analyze_clicked and case_json is not None:
    with st.spinner("Running multi-agent analysis..."):
        try:
            resp = requests.post(f"{API_BASE_URL}/analyze", json={"case": case_json}, timeout=120)
            # NOTE: adjust the payload shape above to match whatever
            # AnalyzeRequest actually expects once you finalize the API
            # contract -- kept flexible here since it's the most likely
            # place a schema mismatch bites during integration.
            resp.raise_for_status()
            result = resp.json()["result"]
        except requests.exceptions.RequestException as e:
            st.error(f"API request failed: {e}")
            result = None

    if result:
        decision = result["decision"]
        confidence = result["confidence"]

        # --- Headline decision ---
        color = {
            "ADMISSIBLE": "green",
            "ADMISSIBLE_WITH_LIMITS": "orange",
            "PARTIALLY_ADMISSIBLE": "orange",
            "NOT_ADMISSIBLE": "red",
            "NEEDS_REVIEW": "gray",
        }.get(decision, "gray")

        if decision == "NEEDS_REVIEW":
            st.warning(f"⚠️ **NEEDS_REVIEW / INSUFFICIENT EVIDENCE** — the system is abstaining "
                       f"rather than guessing (confidence: {confidence:.0%})")
        else:
            st.markdown(f"### Decision: :{color}[{decision}]  (confidence: {confidence:.0%})")

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Key findings")
            for kf in result.get("key_findings", []):
                st.markdown(f"- {kf}")

            st.subheader("Applicable limits / deductions")
            if result.get("applicable_limits"):
                for lim in result["applicable_limits"]:
                    st.markdown(f"- {lim}")
            else:
                st.caption("None identified.")

            st.subheader("Missing evidence")
            if result.get("missing_evidence"):
                for m in result["missing_evidence"]:
                    st.markdown(f"- ⚠️ {m}")
            else:
                st.caption("None.")

        with col2:
            st.subheader("Policy citations")
            for c in result.get("citations", []):
                st.markdown(
                    f"> {c['claim']}\n\n"
                    f"— *{c['source']}, page {c['page']}, section: {c['section']}"
                    f"{', chunk: ' + c['chunk_id'] if c.get('chunk_id') else ''}*"
                )

            st.subheader("Validation")
            v = result.get("validation", {})
            v_status = v.get("status", "UNKNOWN")
            if v_status == "PASS":
                st.success("PASS — all material claims are evidence-supported.")
            else:
                st.error(f"FAIL — unsupported claims: {v.get('unsupported_claims', [])}")

        st.subheader("Execution trace")
        trace_rows = result.get("trace", [])
        if trace_rows:
            st.dataframe(
                [
                    {
                        "Agent": t["agent"],
                        "Action": t["action"],
                        "Detail": t.get("detail", ""),
                        "Retrieval count": t.get("retrieval_result_count", ""),
                        "Elapsed (ms)": round(t["elapsed_ms"], 1) if t.get("elapsed_ms") else "",
                    }
                    for t in trace_rows
                ],
                use_container_width=True,
            )
else:
    st.info("Select or paste a claim case in the sidebar, then click **Analyze claim**.")
