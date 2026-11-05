"""Editable payback worksheet. All defaults are PLACEHOLDERS, not benchmarks."""
DEFAULTS = {
    "downtime_cost_inr_per_hr": 20000.0,
    "downtime_hours_saved_per_year": 24.0,
    "install_commissioning_cost_inr": 40000.0,
    "eng_hours_per_quote_manual": 16.0,
    "eng_hours_per_quote_with_tool": 4.0,
    "eng_rate_inr_per_hr": 1500.0,
    "quotes_per_year": 20.0,
}


def hardware_cost(bom: list[dict]) -> float:
    return sum((l["unit_price_inr"] or 0) * l["qty"] for l in bom if l["status"].split(":")[0] != "ESCALATED")


def compute_roi(bom: list[dict], a: dict | None = None) -> dict:
    a = {**DEFAULTS, **(a or {})}
    hw = hardware_cost(bom)
    invest = hw + a["install_commissioning_cost_inr"]
    saving_year = a["downtime_cost_inr_per_hr"] * a["downtime_hours_saved_per_year"]
    payback = invest / (saving_year / 12) if saving_year > 0 else None
    eng_saved_hrs = (a["eng_hours_per_quote_manual"] - a["eng_hours_per_quote_with_tool"]) * a["quotes_per_year"]
    return {"assumptions": a, "hardware_cost_inr": hw, "investment_inr": invest,
            "annual_saving_inr": saving_year, "payback_months": payback,
            "eng_hours_saved_per_year": eng_saved_hrs,
            "eng_cost_saved_per_year_inr": eng_saved_hrs * a["eng_rate_inr_per_hr"],
            "note": "Illustrative prices and placeholder assumptions: replace with customer data."}
