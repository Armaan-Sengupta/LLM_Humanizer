"""Local web UI.  Run with:  streamlit run app.py"""
from __future__ import annotations

import streamlit as st

from rewriter.checker import analyze, change_ratio, word_diff_html
from rewriter.pipeline import Options, rewrite

st.set_page_config(page_title="Local Rewriter", layout="wide")
st.title("Local Rewriter")
st.caption("Everything runs on this machine. Nothing is uploaded.")

with st.sidebar:
    intensity = st.slider("Intensity", 0.0, 1.0, 0.5, 0.05)
    seed = st.number_input("Seed (0 = random)", min_value=0, value=0, step=1)
    phrases = st.checkbox("Phrase bank (wordy phrases, AI-marker words)", True)
    synonyms = st.checkbox("Synonym swaps (riskier: review the diff)", True)
    structure = st.checkbox("Sentence restructuring", True)
    latex = st.checkbox("LaTeX-aware", False)
    protect_quotes = st.checkbox("Never touch direct quotes", True)
    protected = st.text_area("Protected terms (comma-separated)", "")
    st.divider()
    llm_mode = st.selectbox("Stages 4-5 engine", ["auto (Claude Haiku if available)", "claude CLI", "Anthropic API", "off (local rules)"])
    swap_every = st.slider("LLM: one word swap per N words", 6, 25, 12)
    restructure_every = st.slider("LLM: restructure one sentence in N", 2, 8, 4)

from rewriter.llm import LLMConfig

_backend = {"auto": "auto", "claude": "cli", "Anthropic": "api"}.get(llm_mode.split()[0])
llm_cfg = LLMConfig(backend=_backend, swap_every=swap_every, restructure_every=restructure_every) if _backend else None

opts = Options(
    intensity=intensity, seed=int(seed) or None, phrases=phrases, synonyms=synonyms,
    structure=structure, latex=latex, protect_quotes=protect_quotes,
    protected_terms=[t for t in protected.split(",") if t.strip()], llm=llm_cfg,
)

tab_text, tab_docx, tab_check = st.tabs(["Text", "Word (.docx)", "Checker"])

with tab_text:
    src = st.text_area("Input", height=260)
    if st.button("Rewrite", type="primary") and src.strip():
        res = rewrite(src, opts)
        st.session_state["last"] = (src, res)
    if "last" in st.session_state:
        src0, res = st.session_state["last"]
        st.subheader("Output")
        st.text_area("Rewritten", res.text, height=260)
        st.download_button("Download .txt", res.text, file_name="rewritten.txt")
        for w in res.warnings:
            st.warning(w)
        st.markdown(f"**{len(res.changes)} changes · {change_ratio(src0, res.text):.0%} of words differ**")
        with st.expander("Diff", expanded=True):
            st.markdown(word_diff_html(src0, res.text), unsafe_allow_html=True)
        with st.expander("Change log"):
            st.dataframe([c.__dict__ for c in res.changes], width="stretch")

with tab_docx:
    up = st.file_uploader("Upload .docx", type=["docx"])
    scrub = st.checkbox("Clear author/comments metadata", True)
    if up and st.button("Rewrite document", type="primary"):
        from rewriter.docx_io import rewrite_docx

        data, changes, _warn = rewrite_docx(up.getvalue(), opts, scrub=scrub)
        st.success(f"{len(changes)} changes")
        st.download_button("Download rewritten .docx", data, file_name=up.name.replace(".docx", ".rewritten.docx"))
        st.dataframe([c.__dict__ for c in changes], width="stretch")

with tab_check:
    txt = st.text_area("Text to analyse", height=220)
    if st.button("Analyse") and txt.strip():
        r = analyze(txt)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ZipPy", f"{r.zippy_label or 'n/a'}", f"conf {r.zippy_confidence}" if r.zippy_confidence else None)
        c2.metric("AI markers / 1k words", r.markers_per_1k)
        c3.metric("Burstiness", r.burstiness)
        c4.metric("Lexical diversity", r.mattr)
        st.write("Most frequent markers:", r.top_markers)
        st.caption("Rough local signals only. They do not predict Turnitin, GPTZero or any other commercial detector.")
