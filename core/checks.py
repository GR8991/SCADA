"""Deterministic engineering checks on the extracted SLD.

The AI reads; this module verifies. Nothing here is AI-generated: every rule is
plain code, every derived value shows its formula and reference, and every
non-standard judgement is labelled "Engineering practice" so the engineer can
accept or override it.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from .schema import TWO_TERMINAL_TYPES

SQRT3 = math.sqrt(3)

# IEC 60038 / IS 12360 nominal system voltages (kV) + common BESS/PV PCS AC voltages
STANDARD_KV = [0.23, 0.4, 0.415, 0.433, 0.48, 0.55, 0.6, 0.63, 0.66, 0.69, 0.8, 0.9,
               3.3, 6.6, 11, 22, 33, 66, 110, 132, 220, 230, 400, 765]
# IEC 62271-1 preferred rated normal currents (R10 series), A
STD_BREAKER_A = [400, 630, 800, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300]

KEY_RATINGS = {
    "power_transformer": [("rated_power_mva", "load flow, CT sizing, 87T/51 settings"),
                          ("voltage_kv", "HV rated current"), ("secondary_voltage_kv", "LV rated current"),
                          ("impedance_percent", "short-circuit study (IEC 60909)"),
                          ("vector_group", "87T phase compensation, earthing")],
    "inverter_duty_transformer": [("rated_power_mva", "CT sizing / settings"),
                                  ("impedance_percent", "short-circuit study (IEC 60909)"),
                                  ("vector_group", "earthing & protection")],
    "auxiliary_transformer": [("rated_power_mva", "aux load capacity"), ("vector_group", "earthing")],
    "circuit_breaker": [("rated_current_a", "continuous duty check (IEC 62271-100)"),
                        ("breaking_current_ka", "fault duty check vs IEC 60909 result")],
    "current_transformer": [("ct_ratio", "relay/metering settings"), ("ct_class", "accuracy/saturation (IEC 61869-2)")],
    "voltage_transformer": [("vt_ratio", "relay/metering settings")],
    "pcs_inverter": [("rated_power_mva", "PCS current & fault contribution"), ("voltage_kv", "AC side current")],
    "battery_container": [("energy_mwh", "plant energy / SOC management")],
}

TYPE_LABEL = {"pcs_inverter": "PCS", "battery_container": "Battery", "power_transformer": "Transformer"}


def _f(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _qty(eq) -> tuple[int, bool]:
    q = eq.get("quantity")
    try:
        q = int(q)
        return (q, True) if q > 0 else (1, False)
    except (TypeError, ValueError):
        return 1, False


def rated_current(s_mva: float, u_kv: float) -> float:
    """I = S / (sqrt(3) * U)   [A]  (three-phase, IEC 60076-1 rated current)."""
    return s_mva * 1000 / (SQRT3 * u_kv)


def parse_ct_primary(ratio: str | None) -> float | None:
    """'800-400/1-1A' -> 800 (largest primary tap)."""
    if not ratio:
        return None
    primary = str(ratio).split("/")[0]
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", primary)]
    return max(nums) if nums else None


def _near(a: float, b: float, tol: float = 0.06) -> bool:
    return abs(a - b) <= tol * max(a, b)


def run_checks(sld: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    derived: list[dict[str, str]] = []

    def add(sev, rule, msg, ref=""):
        findings.append({"severity": sev, "rule": rule, "message": msg, "reference": ref})

    def calc(item, quantity, value, formula, ref):
        derived.append({"item": item, "quantity": quantity, "value": value, "formula": formula, "reference": ref})

    proj = sld.get("project", {}) or {}
    buses = {b["id"]: b for b in sld.get("buses", []) if b.get("id")}
    eqs = sld.get("equipment", []) or []
    by_id: dict[str, dict] = {}

    # 1. Unique ids
    for e in eqs:
        if e.get("id") in by_id:
            add("error", "Duplicate ID", f"Equipment id '{e['id']}' appears more than once - topology will be ambiguous.")
        by_id[e.get("id")] = e
    dup_bus = set(buses) & set(by_id)
    for d in dup_bus:
        add("error", "Duplicate ID", f"'{d}' is used both as a bus id and an equipment id.")

    # 2. Topology
    refs = defaultdict(list)
    for e in eqs:
        for side in ("from_node", "to_node"):
            n = e.get(side)
            if n:
                refs[n].append(e["id"])
                if n not in buses and n not in by_id:
                    add("warning", "Dangling connection",
                        f"{e['id']}.{side} = '{n}' is not a known bus or equipment id.")
        if e.get("type") in TWO_TERMINAL_TYPES and not (e.get("from_node") and e.get("to_node")):
            add("warning", "Incomplete connection",
                f"{e['id']} ({e['type']}) is a two-terminal device but does not have both from_node and to_node.")
    for bid in buses:
        if bid not in refs:
            add("warning", "Isolated bus", f"Bus '{bid}' has no equipment connected to it.")

    # 3. Voltage levels
    for bid, b in buses.items():
        v = _f(b.get("voltage_kv"))
        if v is None:
            add("warning", "Missing bus voltage", f"Bus '{bid}' has no voltage.")
        elif not any(_near(v, s, 0.03) for s in STANDARD_KV):
            add("info", "Non-standard voltage",
                f"Bus '{bid}' at {v} kV is not an IEC 60038 / IS 12360 nominal voltage - confirm reading.",
                "IEC 60038, IS 12360")

    def node_kv(n):
        if n in buses:
            return _f(buses[n].get("voltage_kv"))
        if n in by_id:
            return _f(by_id[n].get("voltage_kv"))
        return None

    # 4. Missing key ratings + low confidence + quantity
    for e in eqs:
        for key, why in KEY_RATINGS.get(e.get("type"), []):
            if e.get(key) in (None, "", []):
                add("warning", "Missing rating", f"{e['id']}: '{key}' not read from SLD - needed for {why}.")
        if e.get("confidence") == "low":
            add("warning", "Low confidence", f"{e['id']}: AI marked this reading as low confidence - verify against drawing.")
        if e.get("type") in ("pcs_inverter", "battery_container", "inverter_duty_transformer") and not _qty(e)[1]:
            add("warning", "Quantity not stated", f"{e['id']}: number of identical units not read - capacity totals use 1.")

    # 5. Transformers: voltages vs connected buses, rated currents
    for e in eqs:
        if e.get("type") not in ("power_transformer", "inverter_duty_transformer", "auxiliary_transformer"):
            continue
        hv, lv, s = _f(e.get("voltage_kv")), _f(e.get("secondary_voltage_kv")), _f(e.get("rated_power_mva"))
        for side in ("from_node", "to_node"):
            nv = node_kv(e.get(side)) if e.get(side) in buses else None
            if nv and hv and lv and not (_near(nv, hv) or _near(nv, lv)):
                add("error", "Voltage mismatch",
                    f"{e['id']} is {hv}/{lv} kV but {side} '{e[side]}' is {nv} kV.")
        if s and hv:
            calc(e["id"], "HV rated current", f"{rated_current(s, hv):.1f} A",
                 f"{s} MVA x 1000 / (√3 x {hv} kV)", "IEC 60076-1")
        if s and lv:
            calc(e["id"], "LV rated current", f"{rated_current(s, lv):.1f} A",
                 f"{s} MVA x 1000 / (√3 x {lv} kV)", "IEC 60076-1")
        if s and hv and s >= 5 and "87T" not in [a.upper() for a in e.get("ansi_functions", [])] \
                and "87" not in e.get("ansi_functions", []):
            add("info", "Protection", f"{e['id']} ({s} MVA): no transformer differential (87T) shown on SLD. "
                "Differential protection is normal practice at this rating - confirm utility/CEA requirement.",
                "CEA Technical Standards for Connectivity; CBIP Manual on Protection (engineering practice)")

    # 6. Breakers and CTs vs the transformer they connect to
    def adjacent_transformer(e):
        for side in ("from_node", "to_node"):
            n = e.get(side)
            if n in by_id and "transformer" in by_id[n].get("type", ""):
                return by_id[n]
        for t in eqs:  # transformer referencing this device
            if "transformer" in t.get("type", "") and e["id"] in (t.get("from_node"), t.get("to_node")):
                return t
        return None

    def side_current(dev, tr):
        s, hv, lv = _f(tr.get("rated_power_mva")), _f(tr.get("voltage_kv")), _f(tr.get("secondary_voltage_kv"))
        if not s:
            return None, None
        v = _f(dev.get("voltage_kv")) or next((node_kv(dev.get(k)) for k in ("from_node", "to_node")
                                                if node_kv(dev.get(k))), None)
        for cand in (hv, lv):
            if v and cand and _near(v, cand):
                return rated_current(s, cand), cand
        return None, None

    for e in eqs:
        t = e.get("type")
        if t not in ("circuit_breaker", "current_transformer"):
            continue
        tr = adjacent_transformer(e)
        if not tr:
            continue
        i_load, kv = side_current(e, tr)
        if i_load is None:
            continue
        if t == "circuit_breaker" and _f(e.get("rated_current_a")):
            ir = _f(e["rated_current_a"])
            if ir < i_load:
                add("error", "Breaker undersized",
                    f"{e['id']} rated {ir:.0f} A < {tr['id']} rated current {i_load:.0f} A at {kv} kV.",
                    "IEC 62271-1 rated normal current")
            elif ir not in STD_BREAKER_A:
                add("info", "Non-preferred rating", f"{e['id']} {ir:.0f} A is not an IEC 62271-1 R10 value - confirm reading.",
                    "IEC 62271-1")
        if t == "current_transformer":
            ip = parse_ct_primary(e.get("ct_ratio"))
            if ip is None:
                continue
            if ip < i_load:
                add("warning", "CT primary below load",
                    f"{e['id']} primary {ip:.0f} A < {tr['id']} rated current {i_load:.0f} A - check extended "
                    f"thermal rating (ext. %) or ratio reading.", "IEC 61869-2 rated continuous thermal current")
            elif ip > 4 * i_load:
                add("info", "CT oversized",
                    f"{e['id']} primary {ip:.0f} A is >4x load current {i_load:.0f} A - metering/relay "
                    f"sensitivity may suffer.", "Engineering practice")

    # 7. Capacity consistency
    def total(types, key):
        tot, found = 0.0, False
        for e in eqs:
            if e.get("type") in types and _f(e.get(key)):
                tot += _f(e[key]) * _qty(e)[0]
                found = True
        return tot if found else None

    pcs_mva = total({"pcs_inverter"}, "rated_power_mva")
    pcs_mw = total({"pcs_inverter"}, "rated_power_mw")
    bat_mwh = total({"battery_container", "battery_rack"}, "energy_mwh")
    idt_mva = total({"inverter_duty_transformer"}, "rated_power_mva")
    p_mw, p_mwh = _f(proj.get("capacity_mw")), _f(proj.get("capacity_mwh"))

    if pcs_mva:
        calc("All PCS", "Total PCS apparent power", f"{pcs_mva:.2f} MVA", "Σ (rating x quantity)", "SLD values")
    if bat_mwh:
        calc("All batteries", "Total installed energy", f"{bat_mwh:.2f} MWh", "Σ (energy x quantity)", "SLD values")
    if p_mw and p_mwh:
        calc("Plant", "Duration", f"{p_mwh / p_mw:.2f} h", f"{p_mwh} MWh / {p_mw} MW", "SLD title block")

    if p_mw and pcs_mw and pcs_mw < 0.95 * p_mw:
        add("warning", "Capacity", f"Σ PCS active power {pcs_mw:.1f} MW < plant rating {p_mw} MW.")
    if p_mw and pcs_mva and pcs_mva < p_mw:
        add("warning", "Capacity", f"Σ PCS apparent power {pcs_mva:.1f} MVA < plant rating {p_mw} MW - "
            "no margin for reactive power at full output.", "CEA Technical Standards for Connectivity (reactive capability)")
    if p_mwh and bat_mwh and bat_mwh < p_mwh:
        add("warning", "Capacity", f"Σ battery energy {bat_mwh:.1f} MWh < plant rating {p_mwh} MWh "
            "(may be BOL vs usable - confirm basis).")
    if idt_mva and pcs_mva and idt_mva < 0.98 * pcs_mva:
        add("warning", "Capacity", f"Σ inverter-duty transformer {idt_mva:.1f} MVA < Σ PCS {pcs_mva:.1f} MVA.")

    # 8. Metering / protection presence
    types = {e.get("type") for e in eqs}
    if "energy_meter" not in types:
        add("warning", "Metering", "No energy meter identified - main & check meters are required at the interconnection point.",
            "CEA (Installation and Operation of Meters) Regulations")
    if "protection_relay" not in types and not any(e.get("ansi_functions") for e in eqs):
        add("info", "Protection", "No protection relays / ANSI functions identified on the SLD.")
    if not proj.get("poi_voltage_kv"):
        add("warning", "Project data", "POI voltage not identified.")

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return {
        "findings": findings,
        "derived": derived,
        "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in order},
        "totals": {"pcs_mva": pcs_mva, "pcs_mw": pcs_mw, "battery_mwh": bat_mwh, "idt_mva": idt_mva},
    }
