"""RetrofitProof AI: Streamlit UI. All logic lives in core/; this file only displays and edits."""
from pathlib import Path
import pandas as pd
import streamlit as st

from core.schema import CompType, Circuit, Component, Connection, IOPoint, Requirements
from core.extract import extract_circuit, parse_io_list, load_case
from core.pipeline import analyze
from core.graph import to_dot
from core.export import build_workbook, BANNER
from core.roi import DEFAULTS

st.set_page_config(page_title="RetrofitProof AI", layout="wide")
CASES = Path(__file__).parent / "data" / "cases"

st.title("RetrofitProof AI")
st.caption("Brownfield pneumatic circuit → reviewable Festo retrofit options → test-backed BOM and ROI")
st.warning(BANNER)


def to_df(models, cols=None):
    df = pd.DataFrame([m.model_dump() for m in models])
    return df if len(df) else pd.DataFrame(columns=cols or [])


def from_df(df, Model):
    df = df.astype(object).where(pd.notna(df), None)
    keep = [r for r in df.to_dict("records") if any(v not in (None, "") for v in r.values())]
    return [Model(**r) for r in keep]


# ------------------------------------------------------------------ sidebar: input
with st.sidebar:
    st.header("1. Input")
    mode = st.radio("Source", ["Demo case", "Upload diagram"])
    if mode == "Demo case":
        names = sorted(d.name for d in CASES.iterdir() if (d / "extracted.json").exists())
        case = st.selectbox("Case", names)
        if st.session_state.get("loaded") != case:
            st.session_state.circuit = load_case(CASES / case)
            st.session_state.loaded = case
        img = next(iter((CASES / case).glob("diagram.*")), None)
        st.caption("Demo extraction is SIMULATED with deliberate errors for the correction step.")
    else:
        img_file = st.file_uploader("Diagram image", type=["png", "jpg", "jpeg"])
        io_file = st.file_uploader("I/O list (CSV/XLSX)", type=["csv", "xlsx"])
        img = None
        if img_file and st.button("Extract with AI"):
            with st.spinner("Reading diagram..."):
                try:
                    circ = extract_circuit(img_file.getvalue(), img_file.type or "image/png")
                    if io_file:
                        circ.io = parse_io_list(io_file)
                    st.session_state.circuit = circ
                    st.session_state.loaded = "upload"
                except Exception as e:
                    st.error(f"Extraction failed: {e}. Use a cached demo case.")
        if img_file:
            st.image(img_file)
    if img:
        st.image(str(img))

if "circuit" not in st.session_state:
    st.info("Choose a demo case or upload a diagram in the sidebar.")
    st.stop()

circuit: Circuit = st.session_state.circuit
tabs = st.tabs(["1 Review extraction", "2 Risk flags & graph", "3 Sizing", "4 Alternatives",
                "5 Tests & I/O", "6 ROI & export"])

# ------------------------------------------------------------------ tab 1: technician correction
with tabs[0]:
    st.subheader("Technician review")
    st.caption("Fix anything the AI misread. Rows with confidence < 0.7 are flagged below. "
               "Engineers (only) may set safety_status to a confirmed value.")
    low = [c.id for c in circuit.components if c.confidence < 0.7]
    if low:
        st.warning(f"Low-confidence components: {', '.join(low)}")
    comp_cfg = {
        "type": st.column_config.SelectboxColumn(options=list(CompType.__args__)),
        "safety_status": st.column_config.SelectboxColumn(options=[
            "unknown", "potentially_safety_related", "engineer_confirmed_safety_related",
            "engineer_confirmed_not_safety_related"]),
        "confidence": st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.05)}
    key = st.session_state.get("loaded", "x")
    cdf = st.data_editor(to_df(circuit.components), num_rows="dynamic", column_config=comp_cfg,
                         key=f"comp_{key}", use_container_width=True)
    kdf = st.data_editor(to_df(circuit.connections), num_rows="dynamic", key=f"conn_{key}",
                         column_config={"connection_type": st.column_config.SelectboxColumn(options=["pneumatic", "signal"])},
                         use_container_width=True)
    idf = st.data_editor(to_df(circuit.io), num_rows="dynamic", key=f"io_{key}",
                         column_config={"direction": st.column_config.SelectboxColumn(options=["DI", "DO", "AI", "AO"])},
                         use_container_width=True)
    c1, c2 = st.columns(2)
    p = c1.number_input("Supply pressure (bar), 0 = unknown",
                        value=float(circuit.requirements.supply_pressure_bar or 0.0))
    tgt = c2.text_input("Target controller", circuit.requirements.target_controller or "")
    try:
        confirmed = circuit.model_copy(update={
            "components": from_df(cdf, Component), "connections": from_df(kdf, Connection),
            "io": from_df(idf, IOPoint),
            "requirements": circuit.requirements.model_copy(update={
                "supply_pressure_bar": p or None, "target_controller": tgt or None})})
    except Exception as e:
        st.error(f"Fix the table: {e}")
        st.stop()

result = analyze(confirmed, st.session_state.get("roi_inputs"))
c = result["circuit"]

# ------------------------------------------------------------------ tab 2: flags + graph
with tabs[1]:
    n_crit = sum(f["severity"] == "critical" for f in result["flags"])
    m1, m2, m3 = st.columns(3)
    m1.metric("Critical", n_crit)
    m2.metric("Warnings", sum(f["severity"] == "warning" for f in result["flags"]))
    m3.metric("Info", sum(f["severity"] == "info" for f in result["flags"]))
    for f in result["flags"]:
        msg = f"**{f['component_id']}** · {f['code']}: {f['message']}"
        (st.error if f["severity"] == "critical" else st.warning if f["severity"] == "warning" else st.info)(msg)
    st.subheader("Circuit graph")
    st.caption("Red = potential safety function (never substituted). Yellow = flagged. Dashed = signal.")
    st.graphviz_chart(to_dot(c, result["flags"]))

# ------------------------------------------------------------------ tab 3: sizing
with tabs[2]:
    st.caption("Screening-level calculation only. Final sizing requires engineering verification.")
    for cid, s in result["sizing"].items():
        with st.expander(f"{cid}: required bore {s['required_bore_mm']} mm · flow {s['required_flow_lpm']} l/min",
                         expanded=True):
            st.json(s["inputs"])
            for n in s["notes"]:
                st.warning(n)
            st.markdown("**Assumptions:** " + "; ".join(s["assumptions"]))
            st.markdown("**Must still be verified:** " + "; ".join(s["verify"]))

# ------------------------------------------------------------------ tab 4: alternatives
with tabs[3]:
    sub = st.tabs([a["title"] for a in result["alternatives"]])
    for t, a in zip(sub, result["alternatives"]):
        with t:
            for n in a["notes"]:
                st.info(n)
            bom = pd.DataFrame([{k: v for k, v in l.items() if k not in ("checks", "alt")} for l in a["bom"]])
            st.dataframe(bom, use_container_width=True)
            st.markdown("**Evidence per line** (what passed, what is still unknown)")
            for l in a["bom"]:
                if l["checks"]:
                    with st.expander(f"{l['legacy_id']} → {l['part_number']} · {l['status']}"):
                        st.write(l["rationale"])
                        st.dataframe(pd.DataFrame(l["checks"]), use_container_width=True)

# ------------------------------------------------------------------ tab 5: tests + I/O
with tabs[4]:
    names = [a["name"] for a in result["alternatives"]]
    pick = st.selectbox("Alternative", names, index=1)
    a = next(x for x in result["alternatives"] if x["name"] == pick)
    st.subheader("I/O migration")
    st.dataframe(pd.DataFrame(a["io"]), use_container_width=True)
    st.subheader(f"FAT/SAT proposals ({len(a['tests'])})")
    st.dataframe(pd.DataFrame(a["tests"]), use_container_width=True)

# ------------------------------------------------------------------ tab 6: ROI + export
with tabs[5]:
    st.caption("Defaults are placeholders. Replace with customer figures.")
    inputs = {}
    cols = st.columns(3)
    for i, (k, v) in enumerate(DEFAULTS.items()):
        inputs[k] = cols[i % 3].number_input(k, value=float(v))
    st.session_state.roi_inputs = inputs
    pick2 = st.selectbox("Alternative to export", names, index=1, key="exp")
    r = analyze(confirmed, inputs)
    a2 = next(x for x in r["alternatives"] if x["name"] == pick2)["roi"]
    m = st.columns(3)
    m[0].metric("Hardware (illustrative)", f"₹{a2['hardware_cost_inr']:,.0f}")
    m[1].metric("Payback (months)", f"{a2['payback_months']:.1f}" if a2["payback_months"] else "n/a")
    m[2].metric("Engineering hours saved / yr", f"{a2['eng_hours_saved_per_year']:.0f}")
    st.download_button("Download Excel report", build_workbook(r, pick2),
                       file_name=f"retrofit_{pick2}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
