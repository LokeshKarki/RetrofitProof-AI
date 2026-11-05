"""Hold-out evaluation of the cross-reference matcher on REAL request data.

Split is by (hashed) request id, not by row, so the same request never sits on both sides.
Build the index from 80% of requests, then ask it about the codes in the other 20% and compare
with what the Festo engineer actually answered in that request.

Caveats worth saying out loud:
  * the "truth" is one engineer's answer in one request. Festo itself gave different answers to the
    same code in different requests (see the conflict rate), so top-1 has a ceiling below 100%.
  * series rules were written after looking at this data, so their numbers are optimistic.
  * curated rows (refined project sheet) and feedback are excluded from both sides.
"""
from collections import Counter, defaultdict

from .xref import XrefIndex, load_evidence, norm_ftc

TEST_MOD = 5          # 1 request in 5 goes to the test side


def _is_test(req: str) -> bool:
    try:
        return int(req, 16) % TEST_MOD == 0
    except ValueError:
        return False


def evaluate_holdout(rows=None) -> dict:
    rows = [r for r in (rows if rows is not None else load_evidence()) if not int(r.get("curated") or 0)]
    train = [r for r in rows if not _is_test(r["req"])]
    test = [r for r in rows if _is_test(r["req"])]
    idx = XrefIndex(train)
    seen = set(idx.by_code)
    idx_exact_only = idx

    pairs, seen_pairs = [], set()
    for r in test:
        key = (r["req"], r["competitor"], r["code_norm"], norm_ftc(r["festo_type_code"]))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        pairs.append(r)

    m = Counter()
    by_method, by_comp = defaultdict(Counter), defaultdict(Counter)
    for r in pairs:
        truth = norm_ftc(r["festo_type_code"])
        res = idx.lookup(r["code_norm"], r["competitor"])
        live = [c for c in res["candidates"] if not c["rejected"]]
        top = [norm_ftc(c["festo_type_code"]) for c in live]
        was_seen = r["code_norm"] in seen
        comp = r["competitor"] or "(unknown)"
        if truth:
            m["match_rows"] += 1
            m["match_rows_seen_code"] += was_seen
            answered = bool(live)
            m["answered"] += answered
            ok1, ok3 = bool(top) and top[0] == truth, truth in top[:3]
            m["top1"] += ok1
            m["top3"] += ok3
            if was_seen:
                m["top1_seen"] += ok1
                m["top3_seen"] += ok3
                m["answered_seen"] += answered
            else:
                m["answered_unseen"] += answered
                m["top1_unseen"] += ok1
            for c in (by_method[res["method"]], by_comp[comp]):
                c["n"] += 1
                c["answered"] += answered
                c["top1"] += ok1
        else:
            m["nomatch_rows"] += 1
            m["abstained"] += not live
    n, nm = m["match_rows"], m["nomatch_rows"]
    pct = lambda a, b: round(a / b, 3) if b else None
    unseen = n - m["match_rows_seen_code"]
    # how often does the same code get different Festo answers in different requests (ceiling on top-1)
    answers = defaultdict(set)
    for r in train:
        if r["festo_type_code"]:
            answers[r["code_norm"]].add(norm_ftc(r["festo_type_code"]))
    multi_req = Counter()
    reqs_by_code = defaultdict(set)
    for r in train:
        reqs_by_code[r["code_norm"]].add(r["req"])
    repeated = [c for c, s in reqs_by_code.items() if len(s) > 1]
    conflicting = [c for c in repeated if len(answers[c]) > 1]
    return {
        "split": f"by request id, 1 in {TEST_MOD} requests held out",
        "train_rows": len(train), "test_rows": len(test), "test_pairs": len(pairs),
        "match_rows": n, "no_match_rows": nm,
        "coverage": pct(m["answered"], n), "top1_of_all": pct(m["top1"], n), "top3_of_all": pct(m["top3"], n),
        "top1_when_answered": pct(m["top1"], m["answered"]),
        "seen_code_share": pct(m["match_rows_seen_code"], n),
        "coverage_seen_codes": pct(m["answered_seen"], m["match_rows_seen_code"]),
        "top1_seen_codes": pct(m["top1_seen"], m["match_rows_seen_code"]),
        "top3_seen_codes": pct(m["top3_seen"], m["match_rows_seen_code"]),
        "coverage_unseen_codes": pct(m["answered_unseen"], unseen),
        "top1_unseen_codes": pct(m["top1_unseen"], unseen),
        "correct_abstain_rate": pct(m["abstained"], nm),
        "repeated_codes_in_train": len(repeated),
        "repeated_codes_with_conflicting_answers": len(conflicting),
        "by_method": {k: {"rows": v["n"], "top1": pct(v["top1"], v["n"])} for k, v in by_method.items()},
        "by_competitor": {k: {"rows": v["n"], "coverage": pct(v["answered"], v["n"]), "top1": pct(v["top1"], v["n"])}
                          for k, v in sorted(by_comp.items(), key=lambda kv: -kv[1]["n"])[:6]},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(evaluate_holdout(), indent=2))
