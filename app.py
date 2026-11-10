"""BESS SCADA & Protection Design Agent - Phase 1: SLD reading & verification."""
from __future__ import annotations

import io
import json
from datetime import datetime

import pandas as pd
import streamlit as st

from core.auth import current_user, login_gate, logout
from core.checks import run_checks
from core.schema import EQUIPMENT_COLUMNS, EQUIPMENT_TYPES

st.set_page_config(page_title="BESS SCADA Agent", page_icon="⚡", layout="wide")

if not login_gate():
    st.stop()

# ---------------------------------------------------------------- config
def secret(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default))
    except FileNotFoundError:
        return default

API_KEY = secret("ANTHROPIC_API_KEY")
MODEL = secret("ANTHROPIC_MODEL", "claude-sonnet-4-5")
ss = st.session_state

with st.sidebar:
    st.markdown(f"**Signed in:** {current_user()}")
    if st.button("Sign out", width="stretch"):
        logout()
    st.divider()
    detail = st.radio("SLD reading detail", ["Standard", "High", "Very high"], index=1,
                      help="High/Very high split each sheet into 2x2 / 3x3 zoomed tiles so small "
                           "ratings stay legible. More tiles = more accurate, slower, higher cost.")
    st.caption(f"Model: `{MODEL}`")
    if not API_KEY:
        st.error("ANTHROPIC_API_KEY missing in secrets.")
    st.divider()
    st.caption("Phase 1 · SLD reading\n\nPhase 2 · SCADA network\n\nPhase 3 · Protection design")

st.title("⚡ BESS SCADA & Protection Design Agent")

# ---------------------------------------------------------------- helpers
def eq_to_df(eqs: list[dict]) -> pd.DataFrame:
    rows = []
    for e in eqs:
        r = {c: e.get(c) for c in EQUIPMENT_COLUMNS}
        r["ansi_functions"] = ", ".join(e.get("ansi_functions") or [])
        rows.append(r)
    return pd.DataFrame(rows, columns=EQUIPMENT_COLUMNS)


def df_to_eq(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.to_dict("records"):
        r = {k: (None if (isinstance(v, float) and pd.isna(v)) or v == "" else v) for k, v in r.items()}
        if not r.get("id"):
            continue
        r["ansi_functions"] = [a.strip() for a in str(r.get("ansi_functions") or "").split(",") if a.strip()]
        if r.get("quantity") is not None:
            r["quantity"] = int(r["quantity"])
        out.append(r)
    return out


def to_excel(sld: dict, checks: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([sld.get("project", {})]).T.rename(columns={0: "value"}).to_excel(xw, sheet_name="Project")
        eq_to_df(sld["equipment"]).to_excel(xw, sheet_name="Equipment", index=False)
        pd.DataFrame(sld["buses"]).to_excel(xw, sheet_name="Buses", index=False)
        pd.DataFrame(checks["findings"]).to_excel(xw, sheet_name="Checks", index=False)
        pd.DataFrame(checks["derived"]).to_excel(xw, sheet_name="Derived values", index=False)
        pd.DataFrame(sld["missing_inputs"]).to_excel(xw, sheet_name="Missing inputs", index=False)
    return buf.getvalue()


# ---------------------------------------------------------------- step 1: upload
st.header("1 · Upload project SLD")
c1, c2 = st.columns([3, 2])
with c1:
    up = st.file_uploader("Single line diagram", type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "dxf"],
                          help="Vector PDF or DXF gives the best accuracy. For DWG, export to DXF first.")
with c2:
    hint = st.text_area("Optional project context (hints only - the drawing governs)",
                        placeholder="e.g. 100 MW / 400 MWh BESS, POI 220 kV, Rajasthan, PCS: Sungrow",
                        height=110)

if up is not None and ss.get("file_name") != up.name:
    from core.sld_loader import load_sld
    try:
        with st.spinner("Loading drawing..."):
            ss["payload"] = load_sld(up.name, up.getvalue())
        ss["file_name"] = up.name
        for k in ("sld", "checks", "confirmed"):
            ss.pop(k, None)
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        st.stop()

payload = ss.get("payload")
if payload:
    for n in payload.notes:
        st.info(n)
    if payload.pages:
        with st.expander(f"Preview ({len(payload.pages)} sheet(s))", expanded=False):
            for i, p in enumerate(payload.pages, 1):
                st.image(p, caption=f"Sheet {i}", width="stretch")
    if st.button("🔍 Read & understand SLD", type="primary", disabled=not API_KEY):
        from core.extractor import extract_sld
        with st.spinner("SLD agent is reading the drawing - typically 1-3 minutes for large sheets..."):
            try:
                ss["sld"] = extract_sld(payload, API_KEY, MODEL, detail, hint)
                ss["checks"] = run_checks(ss["sld"])
                ss.pop("confirmed", None)
            except Exception as exc:
                st.error(f"Extraction failed: {exc}")

# ---------------------------------------------------------------- step 2: summary
sld = ss.get("sld")
if sld:
    checks = ss["checks"]
    st.header("2 · SLD summary")
    p = sld["project"]
    m = st.columns(6)
    m[0].metric("Capacity", f"{p.get('capacity_mw') or '–'} MW")
    m[1].metric("Energy", f"{p.get('capacity_mwh') or '–'} MWh")
    m[2].metric("POI voltage", f"{p.get('poi_voltage_kv') or '–'} kV")
    m[3].metric("Equipment rows", len(sld["equipment"]))
    m[4].metric("Errors", checks["counts"]["error"])
    m[5].metric("Warnings", checks["counts"]["warning"])
    st.markdown(sld.get("summary") or "_No summary returned._")
    if p.get("name") or p.get("drawing_number"):
        st.caption(f"Project: {p.get('name') or '–'} · Drawing: {p.get('drawing_number') or '–'} "
                   f"rev {p.get('revision') or '–'} · Grid: {p.get('grid_operator') or '–'}")

    tabs = st.tabs(["Equipment (editable)", "Buses", f"Engineering checks ({len(checks['findings'])})",
                    "Derived values", "Missing inputs & unreadable", "Raw JSON"])

    with tabs[0]:
        st.caption("Correct any misread values here, then click **Apply edits & re-check**. "
                   "Evidence shows exactly what the AI read and where.")
        edited = st.data_editor(
            eq_to_df(sld["equipment"]), num_rows="dynamic", width="stretch", key="eq_editor",
            column_config={
                "type": st.column_config.SelectboxColumn("type", options=EQUIPMENT_TYPES, required=True),
                "confidence": st.column_config.SelectboxColumn("confidence", options=["high", "medium", "low"]),
                "quantity": st.column_config.NumberColumn("quantity", min_value=0, step=1),
                "evidence": st.column_config.TextColumn("evidence", width="large"),
            })
    with tabs[1]:
        edited_bus = st.data_editor(pd.DataFrame(sld["buses"], columns=["id", "name", "voltage_kv", "configuration"]),
                                    num_rows="dynamic", width="stretch", key="bus_editor")
    with tabs[2]:
        icon = {"error": "🔴", "warning": "🟠", "info": "🔵"}
        if not checks["findings"]:
            st.success("No issues found by the rule checks.")
        for f in checks["findings"]:
            ref = f" — *{f['reference']}*" if f.get("reference") else ""
            st.markdown(f"{icon[f['severity']]} **{f['rule']}**: {f['message']}{ref}")
    with tabs[3]:
        st.caption("Every derived value shows its formula and reference.")
        st.dataframe(pd.DataFrame(checks["derived"]), width="stretch", hide_index=True)
    with tabs[4]:
        st.subheader("Inputs needed that are not on the SLD")
        st.dataframe(pd.DataFrame(sld["missing_inputs"]), width="stretch", hide_index=True)
        st.subheader("Could not be read")
        st.dataframe(pd.DataFrame(sld["unreadable_items"]), width="stretch", hide_index=True)
        if sld.get("observations"):
            st.subheader("Observations")
            for o in sld["observations"]:
                st.markdown(f"- {o}")
    with tabs[5]:
        st.json(sld, expanded=False)

    if st.button("Apply edits & re-check"):
        sld["equipment"] = df_to_eq(edited)
        sld["buses"] = [{k: (None if pd.isna(v) else v) for k, v in r.items()}
                        for r in edited_bus.to_dict("records") if r.get("id")]
        ss["checks"] = run_checks(sld)
        ss.pop("confirmed", None)
        st.rerun()

    # ------------------------------------------------------------ step 3: confirm
    st.header("3 · Engineer confirmation")
    errors = checks["counts"]["error"]
    if errors:
        st.warning(f"{errors} error(s) remain. Fix them in the tables above, or confirm only if you have "
                   "verified the drawing is correct as read.")
    agree = st.checkbox("I have reviewed the extracted SLD data against the drawing and confirm it is correct.")
    if st.button("✅ Confirm SLD data", type="primary", disabled=not agree):
        ss["confirmed"] = {"by": current_user(), "at": datetime.now().isoformat(timespec="seconds"),
                           "file": ss.get("file_name")}
        st.rerun()

    record = {**sld, "_checks": checks, "_confirmation": ss.get("confirmed")}
    d1, d2 = st.columns(2)
    stem = (ss.get("file_name") or "sld").rsplit(".", 1)[0]
    d1.download_button("Download SLD data (JSON)", json.dumps(record, indent=2, default=str),
                       file_name=f"{stem}_sld.json", mime="application/json", width="stretch")
    d2.download_button("Download SLD data (Excel)", to_excel(sld, checks), file_name=f"{stem}_sld.xlsx",
                       width="stretch")

    if ss.get("confirmed"):
        c = ss["confirmed"]
        st.success(f"Confirmed by {c['by']} at {c['at']}.")
        st.header("4 · Design")
        b1, b2 = st.columns(2)
        b1.button("🌐 Build SCADA Network", disabled=True, width="stretch",
                  help="Phase 2 - SCADA architecture, network drawings, IP/VLAN plan, I/O list.")
        b2.button("🛡️ Design Protection System", disabled=True, width="stretch",
                  help="Phase 3 - IEC 60909 short circuit, relay selection, CT sizing, coordination.")
        st.caption("Design agents are delivered in Phase 2 and Phase 3.")
