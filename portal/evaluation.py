"""Evaluate the guard and the auditor on the Meridian lab (needs ANSWER_KEY = ../lab/seed/answer_key).

  1. Guard quality per access: AUC of the guard score against the answer key (does this person need it?).
  2. Auditor quality: where do the planted cases C01-C12 end up in the ranked permission list?

Run:  python evaluation.py   (after connector.py)
"""
import csv

import pandas as pd
import yaml

import config as K


def _key_dir():
    if not K.ANSWER_KEY or not K.ANSWER_KEY.exists():
        raise RuntimeError("ANSWER_KEY is not set (lab: ../lab/seed/answer_key)")
    return K.ANSWER_KEY


def need_map(idx):
    """item id -> set of people who need to know (from the lab answer key)."""
    kd = _key_dir()
    files = list(csv.DictReader(open(kd / "files.csv", encoding="utf-8")))
    mails = {r["message_id"]: r for r in csv.DictReader(open(kd / "mail.csv", encoding="utf-8"))}
    team = {"/" + r["path"]: r for r in files if r["location"] == "team"}
    home = {(r["owner"], "/" + r["path"]): r for r in files if r["location"] == "home"}
    out = {}
    for iid, it in idx["items"].items():
        if it["kind"] == "email":
            r = mails.get(it.get("message_id"))
        else:
            r = team.get(it["path"]) or home.get((it.get("home_owner"), it["path"]))
        if r:
            out[iid] = set(filter(None, r["need_to_know"].split(";")))
    return out


def guard_auc(idx, guard):
    """AUC of guard scores over every (employee, item) access that the guard actually scores."""
    from sklearn.metrics import roc_auc_score
    need = need_map(idx)
    y, s = [], []
    for u, acc in idx["access"].items():
        ids = [i for i in acc if i in need and u not in idx["items"][i].get("authors", [])]
        for d in guard.decide(u, ids):
            if d["score"] is not None:
                y.append(int(u in need[d["id"]]))
                s.append(d["score"])
    if len(set(y)) < 2:
        return None, len(y)
    return roc_auc_score(y, s), len(y)


def case_keys(idx):
    """Planted case -> permission keys in the index (derived from company.yaml and the answer key)."""
    kd = _key_dir()
    company = yaml.safe_load(open(kd.parent.parent / "company.yaml", encoding="utf-8"))
    out = {}
    for s in company["shares"]:
        if not s.get("case"):
            continue
        t = {"user": 0, "group": 1, "link": 3}[s["type"]]
        for k, g in idx["grants"].items():
            d = g["detail"]
            if g["kind"] in ("share", "link") and (d.get("path") or "").strip("/") == s["path"] and d.get("owner") == s["owner"] \
                    and (t == 3 and g["kind"] == "link" or g["kind"] == "share" and d.get("share_type") == t and d.get("to") == s.get("to")):
                out.setdefault(s["case"], []).append(k)
    for r in csv.DictReader(open(kd / "files.csv", encoding="utf-8")):
        if r["case"] == "C03":
            for k, g in idx["grants"].items():
                if g["kind"] == "location" and idx["items"].get(g["detail"]["item"], {}).get("title") == r["path"].rsplit("/", 1)[-1]:
                    out.setdefault("C03", []).append(k)
    for p in company["people"]:
        if p.get("status") == "transferred":
            for g in set(p.get("groups", [])) - set(p.get("should_be", [])):
                for k in (f"group:{g}:{p['uid']}", f"list:{g}:{p['uid']}"):
                    if k in idx["grants"]:
                        out.setdefault("C04", []).append(k)
    for r in csv.DictReader(open(kd / "mail.csv", encoding="utf-8")):
        if r["case"] and f"send:{r['message_id']}" in idx["grants"]:
            out.setdefault(r["case"], []).append(f"send:{r['message_id']}")
    for f in company.get("mail_forwards") or []:
        k = f"forward:{f['from']}:{f['to']}"
        if k in idx["grants"]:
            out.setdefault(f["case"], []).append(k)
    return {c: sorted(set(v)) for c, v in sorted(out.items())}


def evaluate(idx, table, guard=None, rules=None):
    cases = case_keys(idx)
    key2case = {k: c for c, ks in cases.items() for k in ks}
    t = table.copy()
    t["case"] = t["key"].map(key2case).fillna("")
    rows = []
    for c, ks in cases.items():
        best = t[t["key"].isin(ks)].sort_values("rank").head(1)
        get = lambda col: best[col].iloc[0] if len(best) else None  # noqa: E731
        rows.append({"case": c, "permission": get("permission") or ks[0], "scope": get("scope"), "source": get("source"),
                     "rank": get("rank"), "rank_in_scope": get("rank_in_scope"), "suspicion": get("suspicion")})
    df = pd.DataFrame(rows)
    parts = []
    for sc in ("narrow", "broad"):
        k = int((df["scope"] == sc).sum())
        if not k:
            continue
        top = t[t["scope"] == sc].head(k)
        hit = int((top["case"] != "").sum())
        df.loc[df["scope"] == sc, "in_top_k_of_scope"] = df.loc[df["scope"] == sc, "rank_in_scope"] <= k
        parts.append(f"{sc} permissions: **P@{k} = {hit / k:.2f}** ({hit} of the top {k} are planted cases)")
    if rules is not None:
        for c, who, kind in (("C06", "abello", "account without history"), ("C07", "ebrooks", "inactive account")):
            hit = rules[(rules["type"] == kind) & rules["object"].str.contains(f"({who})", regex=False)]
            df.loc[len(df)] = {"case": c, "permission": f"{who}: {kind}", "scope": "rules", "source": "Rules",
                               "rank": None, "rank_in_scope": None, "suspicion": None, "in_top_k_of_scope": bool(len(hit))}
    text = " · ".join(parts)
    if guard is not None:
        auc, n = guard_auc(idx, guard)
        if auc is not None:
            text += f" · guard AUC on {n} scored accesses: **{auc:.3f}**"
    return text, df, t


if __name__ == "__main__":
    import audit
    from connector import load_index
    from guard import Guard
    idx = load_index()
    g = Guard(idx, audit.approvals(), audit.legit_keys())
    tab = audit.proactive_audit(idx, g)
    text, df, t = evaluate(idx, tab, g, audit.rule_based_risks(idx, g))
    print(text.replace("**", ""))
    print(df.to_string(index=False))
    for sc in ("narrow", "broad"):
        print(f"\n--- top {sc}")
        print(t[t["scope"] == sc][["rank_in_scope", "source", "permission", "accesses", "held_share", "suspicion", "case"]].head(12).to_string(index=False))
