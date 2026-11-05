"""Build data/xref_evidence.csv from the request sheets.

    python tools/build_xref.py --sep Sep_2026.xlsx --refined Project_316_parts_1.xlsx

Both sheets use the same 18-column layout. What is kept: competitor, code, description, Festo answer,
tier (from the wording of the Remarks), a cleaned technical remark, region, product type, and a HASHED
request id (needed to split hold-out sets by request). What is dropped: request ids, TRS refs,
customer material numbers, dates, countries, project editor, links.
The source .xlsx files are git-ignored; keep them out of any public repo."""
import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.xref import (EVIDENCE, FIELDS, classify_tier, clean_article, clean_remark, is_empty,
                       norm_code, norm_comp)

COLS = ["date", "otrs", "trs", "cluster", "country", "sl", "cust", "comp", "code", "desc", "fpn", "ftc",
        "sign", "rem", "link", "crt", "pe", "pt"]


def h(v) -> str:
    return hashlib.sha1(str(v).encode()).hexdigest()[:10]


def read(path) -> pd.DataFrame:
    df = pd.read_excel(path, dtype=str)
    df = df.iloc[:, :18]
    df.columns = COLS
    return df


def rows_from(df, source, curated, fixed_req=None):
    out, seen = [], set()
    for i, r in enumerate(df.to_dict("records")):
        code = norm_code(r["code"])
        if not code or is_empty(r["code"]):
            continue
        ftc = "" if is_empty(r["ftc"]) else str(r["ftc"]).strip()
        remark = clean_remark(r["rem"])
        tier = classify_tier(ftc, remark)
        req = fixed_req or h(r["otrs"] if not is_empty(r["otrs"]) else f"{source}-{i}")
        comp = norm_comp(r["comp"])
        row = {"source": source, "req": req, "competitor": comp, "code_norm": code,
               "orig_code": str(r["code"]).strip()[:60], "description": ("" if is_empty(r["desc"]) else str(r["desc"]).strip()[:120]),
               "festo_type_code": ftc, "festo_article_no": clean_article(r["fpn"]),
               "tier": tier, "curated": int(curated), "remark": remark,
               "region": ("" if is_empty(r["cluster"]) else str(r["cluster"]).strip().upper()),
               "product_type": ("" if is_empty(r["pt"]) else str(r["pt"]).strip().upper())}
        key = (req, comp, code, ftc.upper(), row["festo_article_no"]) if not curated else (i,)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sep", help="monthly consolidated request sheet (.xlsx)")
    ap.add_argument("--refined", help="your refined project parts sheet (.xlsx), treated as curated")
    ap.add_argument("--out", default=str(EVIDENCE))
    a = ap.parse_args()
    rows = []
    if a.sep:
        rows += rows_from(read(a.sep), "requests_sheet", False)
    if a.refined:
        rows += rows_from(read(a.refined), "refined_project", True, fixed_req="REFINED")
    if not rows:
        sys.exit("Give at least one of --sep / --refined")
    out = pd.DataFrame(rows, columns=FIELDS)
    out.to_csv(a.out, index=False)
    t = out.tier.value_counts().to_dict()
    print(f"wrote {len(out)} rows -> {a.out}")
    print(f"distinct codes {out.code_norm.nunique()}, requests {out.req.nunique()}, tiers {t}")


if __name__ == "__main__":
    main()
