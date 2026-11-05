"""Cross-reference knowledge base: competitor part code -> Festo type code, with evidence.

Three layers, in order:
  1. exact lookup   - every distinct Festo answer seen for the code, ranked, with "seen in N requests, M agree"
  2. series rules   - deterministic patterns for families where the evidence is regular (SMC KQ2, AN)
  3. no match       - an explicit "request datasheet", never a guess

Nothing here is a model. The data is data/xref_evidence.csv, built by tools/build_xref.py from the
request sheets (customer and request identifiers are dropped; only a hashed request id is kept so
hold-out splits can be made by request, not by row).
"""
import bisect
import csv
import json
import re
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
EVIDENCE = DATA / "xref_evidence.csv"
FEEDBACK = DATA / "feedback_log.jsonl"

TIERS = {
    "T1": "Direct equivalent",
    "T2": "Equivalent, check differences",
    "T3": "Suggested, unverified",
    "T0": "No match",
}
TIER_RANK = {"T1": 0, "T2": 1, "T3": 2, "T0": 3}
FIELDS = ["source", "req", "competitor", "code_norm", "orig_code", "description", "festo_type_code",
          "festo_article_no", "tier", "curated", "remark", "region", "product_type"]

_EMPTY = {"", "0", "NA", "N/A", "NAN", "NONE", "-", "NULL", "0.0"}


# ---------------------------------------------------------------- normalisation
def is_empty(v) -> bool:
    return v is None or str(v).strip().upper() in _EMPTY


def norm_comp(raw) -> str:
    s = "" if is_empty(raw) else str(raw).strip().upper()
    return s


def norm_ftc(raw) -> str:
    return re.sub(r"\s+", "", str(raw or "")).upper()


def norm_code(raw) -> str:
    """Upper-case, drop bracketed notes, and keep only the code when a description was typed after it."""
    s = str(raw or "").upper().strip()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    toks = [t.strip(",;:") for t in s.split()]
    toks = [t for t in toks if t]
    if not toks:
        return ""
    if len(toks[0]) >= 5 and re.search(r"[A-Z]", toks[0]) and re.search(r"\d", toks[0]):
        return toks[0]
    return "".join(toks)


def clean_article(raw) -> str:
    if is_empty(raw):
        return ""
    s = str(raw).strip()
    return s[:-2] if s.endswith(".0") else s


_PROJ_PREFIX = re.compile(r"^Project\s+\d+\s*-\s*[^;]*;\s*(?:drawing item \d+;?|unnumbered label on drawing;?)\s*", re.I)


def clean_remark(text) -> str:
    """Strip customer/project prefixes, e-mail addresses and URLs; keep the technical note."""
    s = "" if text is None else str(text)
    if s.strip().upper() in _EMPTY:
        return ""
    s = _PROJ_PREFIX.sub("", s.strip())
    s = re.sub(r"\S+@\S+", "", s)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ;")
    return s[:200]


_SUGGEST = re.compile(r"typecode not found|type code not found|code not found|series pattern|to be confirmed|"
                      r"similar|suggest|based on the description|based on description|not found in", re.I)
_CHECK = re.compile(r"check|dimension|based on equivalent|differ|prüfen|pruefen|not fully|thread|"
                    r"operating pressure|without |cover with|not interchangeable|does not fit", re.I)


def classify_tier(festo_code, remark) -> str:
    """T1 direct, T2 equivalent but differences to check, T3 suggestion only, T0 no match.
    Based on the wording Festo engineers used in the Remarks column."""
    if is_empty(festo_code):
        return "T0"
    r = remark or ""
    if _SUGGEST.search(r):
        return "T3"
    if _CHECK.search(r):
        return "T2"
    return "T1"


# ---------------------------------------------------------------- series rules
_THREAD = {"01": "1/8", "02": "1/4", "03": "3/8", "04": "1/2"}
_TUBES = {4, 6, 8, 10, 12, 16}
_KQ2 = re.compile(r"^KQ2([HLTRUES])(\d{2})-?(U?0[1-4]|M[35]|0[6-9]|1[0-6]|00)(A|AS|S|N|AN)?$")
_AN = re.compile(r"^AN(\d{2})-(0[1-4]|C(?:06|08|10|12)|M5)$")


def series_rule(code: str):
    """Return (festo_type_code, rule_name) or None. Metric tube sizes only; anything else abstains."""
    c = norm_code(code)
    m = _KQ2.match(c)
    if m:
        kind, d, second, _ = m.groups()
        d = int(d)
        if d not in _TUBES:
            return None
        if second.startswith("U"):          # U-thread: Rc vs G is ambiguous in the data, so abstain
            return None
        thread = _THREAD.get(second) if second in _THREAD else None
        metric = second if second in ("M3", "M5") else None
        tube2 = int(second) if second.isdigit() and int(second) in _TUBES and second not in _THREAD else None
        equal = second == "00"
        big, small = (max(d, tube2), min(d, tube2)) if tube2 else (None, None)
        out = None
        if kind == "H":
            out = (f"QS-{thread}-{d}" if thread else f"QSM-{metric}-{d}" if metric
                   else f"QS-{big}-{small}" if tube2 and big != small else None)
        elif kind == "L":
            out = (f"QSL-{thread}-{d}" if thread else f"QSML-{metric}-{d}" if metric
                   else f"QSL-{d}" if equal else f"QSL-{big}H-{small}" if tube2 else None)
        elif kind == "T":
            out = f"QST-{d}" if equal else f"QST-{big}-{small}" if tube2 and big != small else None
        elif kind == "R":
            out = f"QS-{big}H-{small}" if tube2 and big != small else None
        elif kind == "U":
            out = f"QSY-{d}" if equal else f"QSY-{big}-{small}" if tube2 and big != small else None
        elif kind == "E":
            out = f"QSS-{d}" if equal else None
        elif kind == "S":
            out = (f"QS-{thread}-{d}-I" if thread else f"QSM-{metric}-{d}-I-R" if metric else None)
        return (out, f"SMC KQ2{kind} push-in fitting pattern") if out else None
    m = _AN.match(c)
    if m:
        t = m.group(2)
        out = (f"UC-{_THREAD[t]}" if t in ("01", "02", "03") else "U-1/2" if t == "04"
               else f"UC-QS-{int(t[1:])}H" if t.startswith("C") else "UC-M5")
        return out, "SMC AN silencer pattern"
    return None


# ---------------------------------------------------------------- index
def _best(counter: Counter, default=""):
    return counter.most_common(1)[0][0] if counter else default


class XrefIndex:
    def __init__(self, rows, feedback=()):
        self.by_code = defaultdict(list)
        self.rejected = set()
        for r in rows:
            self.by_code[r["code_norm"]].append(r)
        for fb in feedback:
            code, ftc = norm_code(fb.get("code")), norm_ftc(fb.get("festo_type_code"))
            if fb.get("verdict") == "reject":
                self.rejected.add((code, ftc))
            elif fb.get("verdict") == "confirm":
                self.by_code[code].append({
                    "source": "feedback", "req": f"fb-{fb.get('ts', 0)}", "competitor": norm_comp(fb.get("competitor")),
                    "code_norm": code, "orig_code": fb.get("code", ""), "description": "",
                    "festo_type_code": fb.get("festo_type_code", ""), "festo_article_no": "",
                    "tier": "T1", "curated": 1, "remark": clean_remark(fb.get("note")), "region": "", "product_type": ""})
        self.n_rows = sum(len(v) for v in self.by_code.values())
        self._keys = None

    def similar(self, code: str, limit: int = 3) -> list[dict]:
        """Codes from the same series that DO have an answer. A hint for the engineer, never a match."""
        if self._keys is None:
            self._keys = sorted(k for k, v in self.by_code.items()
                                if any(r["tier"] != "T0" and r["festo_type_code"] for r in v))
        n = norm_code(code)
        for plen in range(min(len(n) - 1, 8), 3, -1):
            pre = n[:plen]
            i = bisect.bisect_left(self._keys, pre)
            out = []
            while i < len(self._keys) and self._keys[i].startswith(pre) and len(out) < limit:
                k = self._keys[i]
                best = Counter(r["festo_type_code"] for r in self.by_code[k] if r["festo_type_code"])
                out.append({"code": k, "festo_type_code": _best(best)})
                i += 1
            if out:
                return out
        return []

    # -- stats
    def stats(self) -> dict:
        reqs = {r["req"] for v in self.by_code.values() for r in v}
        return {"rows": self.n_rows, "distinct_codes": len(self.by_code), "requests": len(reqs)}

    def lookup(self, code, competitor=None, use_rules=True) -> dict:
        n = norm_code(code)
        comp = norm_comp(competitor)
        rows = self.by_code.get(n, [])
        if comp:
            same = [r for r in rows if r["competitor"] == comp]
            rows = same or rows
        answers, nomatch, all_reqs = defaultdict(list), set(), set()
        for r in rows:
            all_reqs.add(r["req"])
            if r["tier"] == "T0" or not r["festo_type_code"]:
                nomatch.add(r["req"])
            else:
                answers[norm_ftc(r["festo_type_code"])].append(r)
        answered_reqs = {r["req"] for v in answers.values() for r in v}
        cands = []
        for ftc, rs in answers.items():
            reqs = {r["req"] for r in rs}
            tier_counts = Counter(r["tier"] for r in rs)
            curated = any(int(r.get("curated") or 0) for r in rs)
            cur_rows = [r for r in rs if int(r.get("curated") or 0)]
            tier = (_best(Counter(r["tier"] for r in cur_rows)) if cur_rows
                    else min(tier_counts, key=lambda t: (-tier_counts[t], TIER_RANK[t])))
            remarks = Counter(r["remark"] for r in (cur_rows or rs) if r.get("remark"))
            cands.append({
                "festo_type_code": _best(Counter(r["festo_type_code"].strip() for r in rs)),
                "festo_article_no": _best(Counter(r["festo_article_no"] for r in rs if r.get("festo_article_no"))),
                "tier": tier, "tier_label": TIERS[tier], "n_requests": len(reqs), "n_total": len(answered_reqs),
                "n_agree": len(reqs), "curated": curated, "rejected": (n, ftc) in self.rejected,
                "remark": _best(remarks), "method": "exact",
            })
        cands.sort(key=lambda c: (c["rejected"], not c["curated"], -c["n_requests"], TIER_RANK[c["tier"]]))
        method, note, overridden = "exact", "", []
        curated_none = any(int(r.get("curated") or 0) and (r["tier"] == "T0" or not r["festo_type_code"]) for r in rows)
        if curated_none and not any(c["curated"] for c in cands):
            # a curated "no equivalent" decision beats raw history; the old answers stay visible as hints
            overridden = [{"code": n, "festo_type_code": c["festo_type_code"]} for c in cands]
            cands = []
        if not cands:
            rule = series_rule(n) if use_rules and not nomatch else None
            if rule and (n, norm_ftc(rule[0])) not in self.rejected:
                cands.append({"festo_type_code": rule[0], "festo_article_no": "", "tier": "T3",
                              "tier_label": TIERS["T3"], "n_requests": 0, "n_total": 0, "n_agree": 0,
                              "curated": False, "rejected": False, "method": "series_rule",
                              "remark": f"Type code by series pattern ({rule[1]}); part number to be confirmed."})
                method = "series_rule"
            else:
                method = "none"
                note = ("Marked 'no equivalent' in the curated sheet; older answers are shown as hints only."
                        if overridden else
                        "Festo engineers found no equivalent for this code in earlier requests." if nomatch
                        else "Code not seen in any request and no series rule applies. Request datasheet.")
        return {"query": str(code or ""), "code_norm": n, "competitor": comp, "method": method,
                "description": _best(Counter(r["description"] for r in rows if not is_empty(r.get("description")))),
                "candidates": cands, "n_requests": len(all_reqs), "n_no_match_requests": len(nomatch),
                "conflict": len([c for c in cands if not c["rejected"]]) > 1, "note": note,
                "similar": overridden or (self.similar(n) if method == "none" and not nomatch else [])}


# ---------------------------------------------------------------- loading
def load_evidence(path: Path | None = None) -> list[dict]:
    path = path or EVIDENCE
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_feedback(path: Path | None = None) -> list[dict]:
    path = path or FEEDBACK
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


@lru_cache(maxsize=1)
def get_index() -> XrefIndex:
    return XrefIndex(load_evidence(), load_feedback())


def reload_index() -> XrefIndex:
    get_index.cache_clear()
    return get_index()


def record_feedback(competitor, code, festo_type_code, verdict, note="") -> dict:
    """Append a technician decision to the log. Confirmed answers rank first next time; rejected ones sink."""
    if verdict not in ("confirm", "reject"):
        raise ValueError("verdict must be confirm or reject")
    entry = {"ts": int(time.time()), "competitor": norm_comp(competitor), "code": norm_code(code),
             "festo_type_code": festo_type_code, "verdict": verdict, "note": clean_remark(note)}
    FEEDBACK.parent.mkdir(exist_ok=True)
    with open(FEEDBACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    reload_index()
    return entry
