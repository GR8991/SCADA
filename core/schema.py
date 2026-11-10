"""Structured schema the AI must fill when reading an SLD.

The model is forced to answer through the `record_sld` tool, so its output is
always machine-readable JSON in this shape. Any value not visible on the
drawing must be null - never estimated.
"""

EQUIPMENT_TYPES = [
    "grid_source", "transmission_line", "power_transformer", "inverter_duty_transformer",
    "auxiliary_transformer", "circuit_breaker", "disconnector", "earth_switch", "rmu",
    "current_transformer", "voltage_transformer", "surge_arrester", "cable",
    "pcs_inverter", "battery_container", "battery_rack", "bms", "mv_skid",
    "energy_meter", "protection_relay", "ppc_ems", "rtu_scada", "statcom_capacitor",
    "solar_inverter", "diesel_generator", "ups", "fuse", "aux_load", "other",
]

TWO_TERMINAL_TYPES = {
    "transmission_line", "power_transformer", "inverter_duty_transformer",
    "auxiliary_transformer", "circuit_breaker", "disconnector", "cable", "fuse",
}

_num = {"type": ["number", "null"]}
_str = {"type": ["string", "null"]}

SLD_TOOL = {
    "name": "record_sld",
    "description": "Record the structured contents of the single line diagram exactly as drawn.",
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {
                "type": "object",
                "properties": {
                    "name": _str, "location": _str, "drawing_number": _str, "revision": _str,
                    "capacity_mw": _num, "capacity_mwh": _num, "poi_voltage_kv": _num,
                    "grid_operator": _str, "frequency_hz": _num,
                    "evidence": {"type": "string", "description": "Where on the drawing these values were read."},
                },
            },
            "buses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Short unique node id, e.g. BUS_33KV_A"},
                        "name": _str,
                        "voltage_kv": _num,
                        "configuration": _str,
                    },
                    "required": ["id"],
                },
            },
            "equipment": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique id; use the drawing tag if shown, else TYPE_n."},
                        "tag": _str,
                        "type": {"type": "string", "enum": EQUIPMENT_TYPES},
                        "description": _str,
                        "quantity": {"type": ["integer", "null"], "description": "Number of identical units this row represents."},
                        "voltage_kv": {"type": ["number", "null"], "description": "Rated / primary (HV) voltage."},
                        "secondary_voltage_kv": {"type": ["number", "null"], "description": "Transformer LV voltage(s) - first LV winding."},
                        "rated_power_mva": _num,
                        "rated_power_mw": _num,
                        "energy_mwh": _num,
                        "rated_current_a": _num,
                        "breaking_current_ka": _num,
                        "ct_ratio": {"type": ["string", "null"], "description": "As written, e.g. '800-400/1-1A'."},
                        "ct_class": {"type": ["string", "null"], "description": "e.g. '5P20, 0.2S, PS'."},
                        "vt_ratio": _str,
                        "impedance_percent": _num,
                        "vector_group": _str,
                        "manufacturer_model": _str,
                        "from_node": {"type": ["string", "null"], "description": "Bus id or equipment id on one side."},
                        "to_node": {"type": ["string", "null"], "description": "Bus id or equipment id on the other side."},
                        "ansi_functions": {"type": "array", "items": {"type": "string"},
                                           "description": "ANSI device numbers shown, e.g. ['50','51','87T']."},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "evidence": {"type": "string", "description": "Exact text/symbol seen and its location."},
                    },
                    "required": ["id", "type", "confidence", "evidence"],
                },
            },
            "unreadable_items": {
                "type": "array",
                "items": {"type": "object",
                          "properties": {"location": {"type": "string"}, "description": {"type": "string"}},
                          "required": ["description"]},
            },
            "missing_inputs": {
                "type": "array",
                "description": "Data needed for SCADA/protection design that is NOT on the SLD.",
                "items": {"type": "object",
                          "properties": {"parameter": {"type": "string"}, "why_needed": {"type": "string"}},
                          "required": ["parameter", "why_needed"]},
            },
            "observations": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string", "description": "Engineer-level narrative of the plant topology."},
        },
        "required": ["project", "buses", "equipment", "missing_inputs", "summary"],
    },
}

EQUIPMENT_COLUMNS = [
    "id", "tag", "type", "description", "quantity", "voltage_kv", "secondary_voltage_kv",
    "rated_power_mva", "rated_power_mw", "energy_mwh", "rated_current_a",
    "breaking_current_ka", "ct_ratio", "ct_class", "vt_ratio", "impedance_percent",
    "vector_group", "manufacturer_model", "from_node", "to_node", "ansi_functions",
    "confidence", "evidence",
]
