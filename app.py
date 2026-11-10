"""Local web UI.  Run with:  streamlit run app.py"""
from __future__ import annotations

import streamlit as st

from text_improver.checker import analyze, change_ratio, word_diff_html
from text_improver.pipeline import Options, rewrite
from text_improver.windows import DEFAULT_PATTERN, Phase, parse_pattern

st.set_page_config(page_title="Improve text", layout="wide")
st.title("Improve text")
st.caption("Everything runs on this machine. Nothing is uploaded.")

with st.sidebar:
    intensity = st.slider("Intensity", 0.0, 1.0, 0.5, 0.05)
    seed = st.number_input("Seed (0 = random)", min_value=0, value=0, step=1)
    phrases = st.checkbox("Light phrasing adjustments", True)
    synonyms = st.checkbox("Word adjustments (review the diff)", True)
    structure = st.checkbox("Sentence adjustments", True)
    latex = st.checkbox("LaTeX-aware", False)
    protect_quotes = st.checkbox("Never touch direct quotes", True)
    protected = st.text_area("Protected terms (comma-separated)", "")
    st.divider()
    llm_mode = st.selectbox("How to edit", ["auto", "helper A", "saved key", "local"])
    swap_every = st.slider("How light the edit is", 6, 25, 12)
    restructure_every = st.slider("How often a sentence changes", 2, 8, 4)
    st.divider()
    use_rotation = st.checkbox("Vary the edit across the document", True)
    _keep, _haiku, _gemini = parse_pattern(DEFAULT_PATTERN)
    keep_words = st.number_input("Words left unchanged", min_value=0, value=_keep, step=10, disabled=not use_rotation)
    haiku_words = st.number_input("Words for helper A", min_value=0, value=_haiku, step=10, disabled=not use_rotation)
    gemini_words = st.number_input("Words for helper B", min_value=0, value=_gemini, step=10, disabled=not use_rotation)

from text_improver.llm import LLMConfig

_backend = {"auto": "auto", "helper": "cli", "saved": "api", "local": None}[llm_mode.split()[0]]
llm_cfg = LLMConfig(backend=_backend, swap_every=swap_every, restructure_every=restructure_every) if _backend else None

rotation = None
if use_rotation and llm_cfg is None:
    st.sidebar.warning("That option needs a helper, so it is off.")
elif use_rotation and haiku_words + gemini_words == 0:
    st.sidebar.warning("Give a helper some words to vary the edit.")
elif use_rotation:
    rotation = [
        Phase(int(keep_words)),
        Phase(int(haiku_words), llm_cfg, "haiku"),
        Phase(int(gemini_words), LLMConfig(provider="gemini", swap_every=swap_every), "gemini"),
    ]

opts = Options(
    intensity=intensity, seed=int(seed) or None, phrases=phrases, synonyms=synonyms,
    structure=structure, latex=latex, protect_quotes=protect_quotes,
    protected_terms=[t for t in protected.split(",") if t.strip()], llm=llm_cfg, rotation=rotation,
)

tab_text, tab_docx, tab_check = st.tabs(["Text", "Word (.docx)", "Readings"])

with tab_text:
    src = st.text_area("Input", height=260)
    if st.button("Improve", type="primary") and src.strip():
        res = rewrite(src, opts)
        if res.failures:
            st.session_state.pop("last", None)
            st.error("Could not improve the text: a model was unavailable or couldn't improve its part.")
        else:
            st.session_state["last"] = (src, res)
    if "last" in st.session_state:
        src0, res = st.session_state["last"]
        st.subheader("Output")
        st.text_area("Improved", res.text, height=260)
        st.download_button("Download .txt", res.text, file_name="improved.txt")
        st.markdown(f"**improved ({len(res.changes)}) · {change_ratio(src0, res.text):.0%} of words improved**")
        with st.expander("Diff", expanded=True):
            st.markdown(word_diff_html(src0, res.text), unsafe_allow_html=True)
        with st.expander("Improvements"):
            st.dataframe([c.__dict__ for c in res.changes], width="stretch")

with tab_docx:
    up = st.file_uploader("Upload .docx", type=["docx"])
    scrub = st.checkbox("Tidy the file", True)
    if up and st.button("Improve document", type="primary"):
        from text_improver.docx_io import rewrite_docx

        data, changes, failures = rewrite_docx(up.getvalue(), opts, scrub=scrub)
        if failures:
            st.error("Could not improve the text: a model was unavailable or couldn't improve its part.")
            st.stop()
        st.success(f"improved ({len(changes)})")
        st.download_button("Download improved .docx", data, file_name=up.name.replace(".docx", ".improved.docx"))
        st.dataframe([c.__dict__ for c in changes], width="stretch")

with tab_check:
    txt = st.text_area("Text to analyse", height=220)
    if st.button("Analyse") and txt.strip():
        r = analyze(txt)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Reading", f"{r.reading or 'n/a'}", f"weight {r.weight}" if r.weight else None)
        c2.metric("Notes / 1k words", r.notes_per_1k)
        c3.metric("Spread", r.spread)
        c4.metric("Variety", r.variety)
        st.write("Most frequent notes:", r.frequent)
