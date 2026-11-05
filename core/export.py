from io import BytesIO
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment

BANNER = ("DECISION SUPPORT ONLY. Part matches come from earlier cross-reference requests (with evidence counts) and a "
          "DEMO catalog for sizing. Requires senior engineer review and sign-off. Every part must be verified against "
          "current Festo technical data and the site engineer. This tool does not approve designs.")

REQUEST_COLUMNS = ["Date", "OTRSID", "TRSref/FaxidRef", "Cluster", "Country", "SL.NO", "Customer material number",
                   "Competitor", "Original code", "Description", "Festo part number", "Festo type code", "Sign",
                   "Remarks", "US-Competitorlink", "FoundinCRT:Yes/No/NA", "Projecteditor", "Product Type"]


BOM_COLS = ["legacy_id", "role", "legacy_part", "part_number", "festo_article_no", "manufacturer", "qty", "status",
            "verification_status", "unit_price_inr", "rationale"]


def _bom_df(bom):
    rows = []
    for l in bom:
        r = {k: l.get(k) for k in BOM_COLS}
        r["evidence"] = ""
        x = l.get("xref")
        if x and x["candidates"]:
            t = x["candidates"][0]
            r["evidence"] = f"seen in {t['n_requests']} request(s), {t['n_agree']} agree"
        r["open_checks"] = "; ".join(f"{c['check']} [{c['status']}]" for c in l["checks"] if c["status"] != "passed")
        rows.append(r)
    return pd.DataFrame(rows)


def request_rows(bom, project="") -> pd.DataFrame:
    """The like-for-like BOM in the 18-column layout of the request sheets, so it can be pasted straight in."""
    rows = []
    for i, l in enumerate([x for x in bom if x["role"] != "sensor (new)"], 1):
        demo = l.get("verification_status") == "illustrative"
        has = l["part_number"] != "-" and not demo and not l["status"].startswith("ESCALATED")
        remark = ""
        if has:
            top = ((l.get("xref") or {}).get("candidates") or [{}])[0]
            parts = [top.get("tier_label") or l["status"]]
            if top.get("remark"):
                parts.append(top["remark"])
            remark = "; ".join(parts)
        elif demo:
            remark = f"Sizing pick from the DEMO catalog only ({l['part_number']}): no real Festo type code yet."
        elif l["status"].startswith("ESCALATED"):
            remark = "Potential safety function: not substituted; engineer review required."
        else:
            remark = "No Festo equivalent identified; please provide technical details."
        rows.append({"Date": None, "OTRSID": None, "TRSref/FaxidRef": None, "Cluster": None, "Country": None,
                     "SL.NO": i, "Customer material number": "NA", "Competitor": l.get("legacy_mfr") or "NA",
                     "Original code": l.get("legacy_part") or "NA", "Description": l.get("legacy_label") or l["role"],
                     "Festo part number": (l.get("festo_article_no") or None) if has else 0,
                     "Festo type code": l["part_number"] if has else 0, "Sign": None,
                     "Remarks": (f"{project}; " if project else "") + f"item {l.get('callout_no') or l['legacy_id']}"
                                + (f"; {remark}" if remark else ""),
                     "US-Competitorlink": None, "FoundinCRT:Yes/No/NA": None, "Projecteditor": None, "Product Type": None})
    return pd.DataFrame(rows, columns=REQUEST_COLUMNS)


def _style(book):
    for ws in book.worksheets:
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3A5F")
        for col in ws.columns:
            w = max(len(str(c.value or "")) for c in col[:50])
            ws.column_dimensions[col[0].column_letter].width = min(max(12, w + 2), 60)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")


def build_xref_workbook(result: dict) -> bytes:
    lfl = next(a for a in result["alternatives"] if a["name"] == "like_for_like")
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        request_rows(lfl["bom"]).to_excel(xw, sheet_name="Sheet1", index=False)
        _style(xw.book)
    return buf.getvalue()


def build_workbook(result: dict, alt_name: str) -> bytes:
    alt = next(a for a in result["alternatives"] if a["name"] == alt_name)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"RetrofitProof AI": [BANNER, f"Alternative: {alt['title']}", *alt["notes"]]}).to_excel(
            xw, "Summary", index=False)
        pd.DataFrame(result["flags"]).to_excel(xw, "Risk flags", index=False)
        _bom_df(alt["bom_grouped"]).to_excel(xw, "BOM", index=False)
        lfl = next(a for a in result["alternatives"] if a["name"] == "like_for_like")
        request_rows(lfl["bom"]).to_excel(xw, "Xref (request format)", index=False)
        pd.DataFrame(result.get("gaps", [])).assign(
            similar=lambda d: d["similar"].apply(lambda v: "; ".join(f"{x['code']}->{x['festo_type_code']}" for x in v))
            if len(d) else None, legacy_ids=lambda d: d["legacy_ids"].apply(", ".join) if len(d) else None
        ).to_excel(xw, "Gaps", index=False)
        pd.DataFrame(alt["io"]).to_excel(xw, "IO migration", index=False)
        pd.DataFrame(alt["tests"]).to_excel(xw, "FAT-SAT tests", index=False)
        r = alt["roi"]
        roi_rows = [{"item": k, "value": v} for k, v in r["assumptions"].items()] + \
                   [{"item": k, "value": r[k]} for k in ("hardware_cost_inr", "investment_inr", "annual_saving_inr",
                                                         "payback_months", "eng_hours_saved_per_year",
                                                         "eng_cost_saved_per_year_inr", "note")]
        pd.DataFrame(roi_rows).to_excel(xw, "ROI", index=False)
        sz = [{"component": k, **v["inputs"], "required_bore_mm": v["required_bore_mm"],
               "required_flow_lpm": v["required_flow_lpm"], "status": v["status"],
               "verify": "; ".join(v["verify"]), "assumptions": "; ".join(v["assumptions"])}
              for k, v in result["sizing"].items()]
        pd.DataFrame(sz).to_excel(xw, "Sizing", index=False)
        _style(xw.book)
    return buf.getvalue()
