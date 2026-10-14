"""
Calculation + rules engine (no Flask code here, so it is easy to test and to explain in a viva).

Formulas used (also shown in the Help page):
  value            = quantity x latest price
  cost basis       = quantity x purchase price
  unrealized P/L   = value - cost basis            (before fees and taxes)
  return %         = P/L / cost basis x 100        (cost basis must be > 0)
  weight %         = holding value / total value x 100
  period return %  = (end - start) / start x 100
  SIP (annuity)    = target / [ ((1+i)^n - 1) / i x (1+i) ]   i = annual rate / 12, n = months
"""
import numpy as np
import pandas as pd

import data_loader as dl

CATEGORIES = ["Equity", "Mutual Fund", "Debt", "Gold", "Cash", "Other"]
# Illustrative "volatility rating" per category (assumption, shown to the user).
RISK_WEIGHT = {"Equity": 1.0, "Mutual Fund": 0.7, "Gold": 0.5, "Debt": 0.25, "Cash": 0.0, "Other": 0.8}
RANGES = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365, "2Y": 730, "ALL": None}
FREQS = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Y": "Yearly"}

# ------------------------------------------------------------------ holdings maths


def enrich_holdings(rows, latest):
    out = []
    for r in rows:
        h = dict(r)
        qty, buy = float(h["quantity"]), float(h["buy_price"])
        manual = h.get("latest_price")
        sym = h.get("symbol")
        if manual is not None:
            price, src = float(manual), "manual"
        elif sym and sym in latest:
            price, src = latest[sym], "sample dataset"
        else:
            price, src = None, "missing"
        cost = qty * buy
        value = qty * price if price is not None else None
        pl = value - cost if value is not None else None
        ret = (pl / cost * 100) if (pl is not None and cost > 0) else None
        h.update(price=price, price_source=src, cost=cost, value=value, pl=pl, ret=ret)
        out.append(h)
    return out


def summarize(enriched):
    priced = [h for h in enriched if h["value"] is not None]
    value = sum(h["value"] for h in priced)
    cost = sum(h["cost"] for h in priced)
    pl = value - cost
    ret = (pl / cost * 100) if cost > 0 else None
    for h in enriched:
        h["weight"] = (h["value"] / value * 100) if (h["value"] is not None and value > 0) else None
    return dict(value=value, cost=cost, pl=pl, ret=ret, count=len(enriched),
                priced=len(priced), missing=len(enriched) - len(priced))


def allocation(enriched, summary):
    total = summary["value"]
    cats = {}
    for h in enriched:
        if h["value"] is not None:
            cats[h["category"]] = cats.get(h["category"], 0) + h["value"]
    by_cat = [dict(category=c, value=v, weight=(v / total * 100 if total > 0 else 0))
              for c, v in sorted(cats.items(), key=lambda x: -x[1])]
    by_hold = sorted([dict(id=h["id"], name=h["name"], category=h["category"], value=h["value"], weight=h["weight"])
                      for h in enriched if h["value"] is not None], key=lambda x: -x["value"])
    return dict(by_category=by_cat, by_holding=by_hold, risk_exposure=risk_exposure(by_cat))


def risk_exposure(by_cat):
    """0-100 score: weight-averaged category volatility rating (illustrative assumption)."""
    return round(sum(c["weight"] * RISK_WEIGHT.get(c["category"], 0.8) for c in by_cat), 1)


def compare_with_target(by_cat, target):
    """Current category weights vs a user-chosen hypothetical allocation (percentages)."""
    cur = {c["category"]: c["weight"] for c in by_cat}
    rows = []
    for cat in CATEGORIES:
        c, t = cur.get(cat, 0.0), float(target.get(cat, 0) or 0)
        rows.append(dict(category=cat, current=round(c, 2), target=round(t, 2), diff=round(t - c, 2)))
    exp_t = round(sum(r["target"] * RISK_WEIGHT[r["category"]] for r in rows), 1)
    return dict(rows=rows, target_total=round(sum(r["target"] for r in rows), 2), target_risk_exposure=exp_t)

# ------------------------------------------------------------------ risk profiling

EXPERIENCE = ["None", "Beginner", "Intermediate", "Experienced", "Professional"]
GOALS = ["Protect savings for a near-term goal", "Regular income", "Build wealth for a major purchase",
         "Long-term wealth creation", "Retirement planning", "Learning / exploring"]
PREFERRED = ["Conservative", "Moderate", "Aggressive"]

QUESTIONS = [
    dict(id="income_stability", group="ability", text="How stable is your income?",
         options=["Very unstable / none", "Somewhat unstable", "Fairly stable", "Stable", "Very stable"]),
    dict(id="emergency_fund", group="ability", text="How many months of expenses do you keep as emergency savings?",
         options=["Less than 1", "1 to 3", "3 to 6", "6 to 12", "More than 12"]),
    dict(id="obligations", group="ability", text="How heavy are your fixed obligations (loans, dependants) compared with income?",
         options=["Very heavy", "Heavy", "Moderate", "Light", "None"]),
    dict(id="loss_reaction", group="willingness", text="If your portfolio fell 20% in one month, you would most likely...",
         options=["Sell everything", "Sell some", "Do nothing and wait", "Invest a little more", "Invest a lot more"]),
    dict(id="max_loss", group="willingness", text="What is the largest 1-year loss you could accept without losing sleep?",
         options=["About 5%", "About 10%", "About 20%", "About 30%", "40% or more"]),
    dict(id="preference", group="willingness", text="Which statement describes you best?",
         options=["Protect my capital above all", "Steady income, small ups and downs", "A balance of growth and safety",
                  "Growth, accepting big ups and downs", "Maximum growth, whatever the swings"]),
]


def horizon_points(years):
    return 1 if years < 1 else 2 if years < 3 else 3 if years < 5 else 4 if years < 10 else 5


def risk_features(profile):
    """Turn profile + answers into 8 factor points (1-5). Returns (dict, missing_list)."""
    ans = profile.get("answers") or {}
    pts, missing = {}, []
    try:
        pts["horizon"] = horizon_points(float(profile.get("horizon_years")))
    except (TypeError, ValueError):
        missing.append("horizon")
    exp = profile.get("experience")
    if exp in EXPERIENCE:
        pts["experience"] = EXPERIENCE.index(exp) + 1
    else:
        missing.append("experience")
    for q in QUESTIONS:
        v = ans.get(q["id"])
        if isinstance(v, int) and 0 <= v < len(q["options"]):
            pts[q["id"]] = v + 1
        else:
            missing.append(q["id"])
    return pts, missing


FEATURE_ORDER = ["horizon", "income_stability", "emergency_fund", "obligations",
                 "experience", "loss_reaction", "max_loss", "preference"]
ABILITY = ["horizon", "income_stability", "emergency_fund", "obligations"]
WILLINGNESS = ["experience", "loss_reaction", "max_loss", "preference"]
FACTOR_LABEL = dict(horizon="Time horizon", income_stability="Income stability", emergency_fund="Emergency savings",
                    obligations="Fixed obligations", experience="Investing experience",
                    loss_reaction="Reaction to a 20% fall", max_loss="Acceptable 1-year loss", preference="Growth vs safety preference")


def category_for(score):
    return "Conservative" if score < 35 else "Moderate" if score < 65 else "Aggressive"


def score_from_points(pts):
    norm = lambda keys: sum((pts[k] - 1) / 4 * 100 for k in keys) / len(keys)
    ability, willing = norm(ABILITY), norm(WILLINGNESS)
    return round((ability + willing) / 2, 1), round(ability, 1), round(willing, 1)


def compute_risk(profile):
    pts, missing = risk_features(profile)
    if missing:
        return None, missing
    score, ability, willing = score_from_points(pts)
    cat = category_for(score)
    factors = [dict(key=k, label=FACTOR_LABEL[k], points=pts[k], contribution=round((pts[k] - 1) / 4 * 100 / 8, 1))
               for k in FEATURE_ORDER]
    notes = ["This is an educational estimate from a short questionnaire. It is not a suitability assessment."]
    if abs(ability - willing) > 25:
        lower = "ability" if ability < willing else "willingness"
        notes.append(f"Your ability ({ability}) and willingness ({willing}) to take losses differ a lot. "
                     f"Many planners lean toward the lower one ({lower}).")
    pref = profile.get("preferred_risk")
    if pref in PREFERRED and pref != cat:
        notes.append(f"You said you prefer a {pref} approach, but the questionnaire points to {cat}.")
    return dict(score=score, ability=ability, willingness=willing, category=cat, factors=factors,
                notes=notes, points=pts,
                bands="Conservative < 35 <= Moderate < 65 <= Aggressive"), []

# ------------------------------------------------------------------ insights


def _ins(id_, sev, title, message, why, inputs, assumptions=""):
    return dict(id=id_, severity=sev, title=title, message=message, why=why, inputs=inputs, assumptions=assumptions)


def generate_insights(enriched, summary, alloc, risk, profile, threshold, status):
    out = []
    if not enriched:
        return [_ins("empty", "info", "No holdings yet",
                     "Add a few holdings (or load a sample portfolio) to see insights.",
                     "The rules engine needs holdings to analyse.", ["holdings: 0"])]
    # 1 missing data
    miss = [h["name"] for h in enriched if h["value"] is None]
    if miss:
        out.append(_ins("missing", "high", f"{len(miss)} holding(s) have no price",
                        "These holdings are left out of totals and charts: " + ", ".join(miss[:5]) + ". Enter a latest price to include them.",
                        "A holding without a latest price or sample-dataset symbol cannot be valued.",
                        [f"missing price: {len(miss)} of {len(enriched)}"], "Missing values are never guessed."))
    # 2 single-holding concentration
    for h in enriched:
        if h.get("weight") is not None and h["weight"] > threshold:
            out.append(_ins(f"conc-{h['id']}", "medium", f"{h['name']} is {h['weight']:.1f}% of the portfolio",
                            "One position is above your concentration threshold. Diversification can reduce concentration risk "
                            "but cannot remove investment risk. You could test a more spread-out mix in Compare Investments.",
                            f"Holding weight ({h['weight']:.1f}%) > threshold ({threshold:.0f}%).",
                            [f"weight: {h['weight']:.1f}%", f"threshold: {threshold:.0f}% (editable in Settings)"],
                            "Weight is based on current value."))
    # 3 category concentration
    for c in alloc["by_category"]:
        if c["weight"] > 70 and c["category"] != "Cash":
            out.append(_ins("cat-" + c["category"], "low", f"{c['weight']:.0f}% of value is in {c['category']}",
                            "A single asset class dominates the portfolio, so it may move as one block.",
                            "Category weight above 70%.", [f"{c['category']}: {c['weight']:.1f}%"]))
    # 4 few holdings / categories
    if summary["priced"] < 4 or len(alloc["by_category"]) < 3:
        out.append(_ins("diversification", "low", "Limited diversification",
                        f"The portfolio has {summary['priced']} priced holding(s) across {len(alloc['by_category'])} categories.",
                        "Fewer than 4 holdings or fewer than 3 categories.",
                        [f"holdings: {summary['priced']}", f"categories: {len(alloc['by_category'])}"]))
    exposure = alloc["risk_exposure"]
    horizon = None
    try:
        horizon = float(profile.get("horizon_years")) if profile.get("horizon_years") not in (None, "") else None
    except (TypeError, ValueError):
        pass
    # 5 horizon mismatch
    if horizon is not None and horizon <= 3 and exposure >= 60:
        out.append(_ins("horizon", "high", "Short time horizon with a volatile mix",
                        f"You plan to use this money within {horizon:g} year(s), but the mix has a volatility rating of {exposure}/100. "
                        "Short horizons leave little time to recover from a fall. One scenario to explore is a lower-volatility allocation.",
                        "Horizon <= 3 years and portfolio volatility rating >= 60.",
                        [f"horizon: {horizon:g} years", f"portfolio volatility rating: {exposure}/100"],
                        "Rating = sum of weight x category rating (Equity 1.0, Mutual Fund 0.7, Gold 0.5, Debt 0.25, Cash 0)."))
    # 6 risk mismatch
    if risk:
        gap = exposure - risk["score"]
        if abs(gap) >= 25:
            direction = "riskier" if gap > 0 else "more conservative"
            out.append(_ins("mismatch", "medium", f"Portfolio looks {direction} than your questionnaire result",
                            f"Your risk score is {risk['score']} ({risk['category']}) while the portfolio's volatility rating is {exposure}. "
                            "That gap may be intentional; it is worth checking that it matches your goals.",
                            "|portfolio volatility rating - risk score| >= 25.",
                            [f"risk score: {risk['score']}", f"portfolio rating: {exposure}"],
                            "Both numbers are rough, illustrative scales."))
    else:
        out.append(_ins("no-risk", "info", "Risk profile not completed",
                        "Complete the Risk Profile questionnaire so insights can compare your portfolio with your comfort level.",
                        "No saved questionnaire result.", ["risk profile: missing"]))
    # 7 cash
    cash = next((c["weight"] for c in alloc["by_category"] if c["category"] == "Cash"), 0)
    if cash > 30 and (horizon or 0) >= 5:
        out.append(_ins("cash-high", "low", f"{cash:.0f}% held in cash with a long horizon",
                        "Cash is stable but its purchasing power can shrink with inflation over long periods.",
                        "Cash > 30% and horizon >= 5 years.", [f"cash: {cash:.1f}%", f"horizon: {horizon:g} years"]))
    if cash < 5 and exposure >= 50:
        out.append(_ins("cash-low", "low", "Very little in cash or liquid assets",
                        "Liquidity matters if you need money at short notice. Selling volatile assets after a fall can lock in losses.",
                        "Cash < 5% and volatility rating >= 50.", [f"cash: {cash:.1f}%", f"rating: {exposure}"]))
    # 8 large unrealised loss
    for h in enriched:
        if h.get("ret") is not None and h["ret"] <= -20:
            out.append(_ins(f"loss-{h['id']}", "info", f"{h['name']} is down {abs(h['ret']):.1f}% from cost",
                            "Large paper losses are a prompt to review whether your original reasons still apply. This is not a sell instruction.",
                            "Unrealized return <= -20%.", [f"return: {h['ret']:.1f}%"], "Before fees and taxes."))
    # 9 data freshness
    if status["days_old"] > 7:
        out.append(_ins("stale", "info", f"Sample prices are {status['days_old']} days old",
                        "The simulated dataset was generated a while ago, so 'latest' prices are not today's.",
                        "Dataset last date is more than 7 days ago.", [f"last updated: {status['last_updated']}"],
                        "No live feed is connected."))
    if not [i for i in out if i["severity"] in ("high", "medium", "low")]:
        out.append(_ins("ok", "good", "No rule-based flags right now",
                        "None of the current rules fired. This does not mean the portfolio is safe or suitable; markets can fall even when rules stay quiet.",
                        "All rules evaluated without a trigger.", [f"rules checked: 9"]))
    order = dict(high=0, medium=1, low=2, info=3, good=4)
    return sorted(out, key=lambda i: order[i["severity"]])

# ------------------------------------------------------------------ time series


def slice_range(obj, rng):
    days = RANGES.get(rng, 365)
    if days is None or len(obj) == 0:
        return obj
    return obj[obj.index >= obj.index[-1] - pd.Timedelta(days=days)]


def resample(obj, freq):
    """Keep the last observation of each week/month/year (works on pandas 2.x and 3.x)."""
    if freq == "D" or len(obj) == 0:
        return obj
    keys = obj.index.to_period({"W": "W", "M": "M", "Y": "Y"}[freq])
    return obj[~keys.duplicated(keep="last")]


def _period_return(s):
    s = s.dropna()
    if len(s) < 2 or s.iloc[0] == 0:
        return None
    return float((s.iloc[-1] - s.iloc[0]) / s.iloc[0] * 100)


def series_metrics(s):
    s = s.dropna()
    r = s.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252) * 100) if len(r) > 1 else None
    dd = float(((s / s.cummax()) - 1).min() * 100) if len(s) else None
    return dict(period_return=_period_return(s), volatility=vol, max_drawdown=dd)


def portfolio_series(enriched, prices):
    parts, used, excluded = [], 0, 0
    for h in enriched:
        sym = h.get("symbol")
        if sym in prices.columns and float(h["quantity"]) > 0:
            parts.append(prices[sym] * float(h["quantity"]))
            used += 1
        else:
            excluded += 1
    if not parts:
        return None, used, excluded
    return pd.concat(parts, axis=1).sum(axis=1, min_count=1).dropna(), used, excluded


def _rebase(s):
    return (s / s.iloc[0] * 100).round(2).tolist() if len(s) and s.iloc[0] else []


def performance(enriched, prices, rng="1Y", freq="D"):
    s, used, excluded = portfolio_series(enriched, prices)
    if s is None or len(s) < 2:
        return dict(available=False, reason="No holding has a symbol that exists in the sample price dataset, so no history can be drawn.",
                    excluded=excluded)
    s = slice_range(s, rng)
    start, end = float(s.iloc[0]), float(s.iloc[-1])
    rs = resample(s, freq)
    bench = slice_range(prices[dl.BENCHMARK].dropna(), rng).reindex(s.index).ffill()
    rb = resample(bench, freq)
    pct = rs.pct_change() * 100
    table = [dict(date=d.strftime("%Y-%m-%d"), value=round(float(v), 2),
                  change=(None if pd.isna(c) else round(float(c), 2))) for d, v, c in zip(rs.index, rs.values, pct.values)]
    return dict(available=True, labels=[d.strftime("%Y-%m-%d") for d in rs.index], values=[round(float(v), 2) for v in rs.values],
                portfolio_index=_rebase(rs), benchmark_index=_rebase(rb), benchmark_name=dl.CATALOG[dl.BENCHMARK]["name"],
                start_value=round(start, 2), end_value=round(end, 2), abs_change=round(end - start, 2),
                pct_change=(round((end - start) / start * 100, 2) if start else None),
                table=table[::-1][:250], used=used, excluded=excluded, range=rng, freq=freq,
                assumption="Uses today's quantities for the whole period (past purchase dates are not modelled). Prices are simulated sample data.")


def asset_history(symbol, prices, rng, freq):
    if symbol not in prices.columns:
        return None
    s = resample(slice_range(prices[symbol].dropna(), rng), freq)
    pct = s.pct_change() * 100
    return dict(symbol=symbol, labels=[d.strftime("%Y-%m-%d") for d in s.index], values=[round(float(v), 2) for v in s.values],
                table=[dict(date=d.strftime("%Y-%m-%d"), value=round(float(v), 2), change=(None if pd.isna(c) else round(float(c), 2)))
                       for d, v, c in zip(s.index[::-1][:250], s.values[::-1][:250], pct.values[::-1][:250])])


def compare_assets(symbols, prices, rng="1Y", freq="W"):
    df = slice_range(prices[symbols + ([dl.BENCHMARK] if dl.BENCHMARK not in symbols else [])].dropna(), rng)
    if len(df) < 2:
        return None
    rows, series = [], {}
    rs = resample(df, freq)
    for sym in df.columns:
        m = series_metrics(df[sym])
        meta = dl.CATALOG[sym]
        rows.append(dict(symbol=sym, name=meta["name"], category=meta["category"], expense=meta["expense"],
                         min_invest=meta["min_invest"], **m))
        series[sym] = _rebase(rs[sym])
    return dict(labels=[d.strftime("%Y-%m-%d") for d in rs.index], series=series, rows=rows, data_label=dl.DATA_LABEL)


def compare_allocations(allocs, prices, rng="1Y", freq="W"):
    """allocs: [{name, weights:{symbol: pct}}]. Buy-and-hold from the start of the range, no rebalancing, fees ignored in returns."""
    syms = sorted({s for a in allocs for s in a["weights"]})
    df = slice_range(prices[syms].dropna(), rng)
    if len(df) < 2:
        return None
    norm = df / df.iloc[0]
    out, labels = [], None
    for a in allocs:
        w = {s: float(v) / 100 for s, v in a["weights"].items() if float(v) > 0}
        s = sum(norm[sym] * wt for sym, wt in w.items()) * 100
        m = series_metrics(s)
        fee = sum(wt * (dl.CATALOG[sym]["expense"] or 0) for sym, wt in w.items())
        expo = sum(wt * 100 * RISK_WEIGHT.get(dl.CATALOG[sym]["category"], 0.8) for sym, wt in w.items())
        rs = resample(s, freq)
        labels = [d.strftime("%Y-%m-%d") for d in rs.index]
        out.append(dict(name=a["name"], series=[round(float(v), 2) for v in rs.values], weighted_expense=round(fee, 3),
                        volatility_rating=round(expo, 1), **m))
    return dict(labels=labels, results=out, data_label=dl.DATA_LABEL,
                assumption="Buy-and-hold from the first date, no rebalancing, fees and taxes not deducted from returns. Simulated data.")

# ------------------------------------------------------------------ goal planner


def sip_required(target, years, rate_pct, current=0.0, timing="start"):
    n = int(round(years * 12))
    i = rate_pct / 100 / 12
    need = max(target - current * (1 + i) ** n, 0.0)
    if i == 0:
        return need / n
    factor = ((1 + i) ** n - 1) / i
    if timing == "start":
        factor *= (1 + i)
    return need / factor


def goal_plan(target, years, rate, current=0.0, timing="start"):
    rates = sorted({round(max(rate + d, 0), 2) for d in (-4, -2, 0, 2, 4)})
    n = int(round(years * 12))
    scen = []
    for r in rates:
        p = sip_required(target, years, r, current, timing)
        invested = p * n + current
        scen.append(dict(rate=r, monthly=round(p, 2), invested=round(invested, 2), gain=round(target - invested, 2), base=(r == round(rate, 2))))
    p = sip_required(target, years, rate, current, timing)
    i = rate / 100 / 12
    bal, path = current, [round(current, 2)]
    for m in range(1, n + 1):
        if timing == "start":
            bal = (bal + p) * (1 + i)
        else:
            bal = bal * (1 + i) + p
        if m % 12 == 0 or m == n:
            path.append(round(bal, 2))
    labels = [f"Year {k}" for k in range(len(path) - 1)] + [f"Year {years:g}"]
    labels = labels[:len(path)]
    return dict(scenarios=scen, path=path, labels=labels, months=n,
                formula="Monthly SIP = (Target - Current savings x (1+i)^n) / [ ((1+i)^n - 1) / i x (1+i) ],  i = annual rate / 12,  n = months",
                assumptions=[f"Monthly compounding; payments made at the {'start' if timing == 'start' else 'end'} of each month.",
                             "Constant rate of return every year (real returns vary and can be negative).",
                             "Inflation, taxes, fees and changing contributions are NOT modelled.",
                             "This is an illustration, not a promise or personalised financial advice."])

# ------------------------------------------------------------------ optional ML experiment

_ml = {}


def ml_model():
    """Small logistic-regression classifier (scikit-learn) that learns to reproduce the rule-based category from the 8 factor points.
    Training data is SYNTHETIC (random answers labelled by the rules, 5% label noise), so the accuracy only shows the
    workflow works - it says nothing about real investor suitability."""
    if _ml:
        return _ml
    from sklearn.metrics import accuracy_score, confusion_matrix
    from sklearn.model_selection import train_test_split
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(7)
    X = rng.integers(1, 6, size=(2000, 8))
    labels = ["Conservative", "Moderate", "Aggressive"]
    y = np.array([labels.index(category_for(score_from_points(dict(zip(FEATURE_ORDER, row)))[0])) for row in X])
    flip = rng.random(len(y)) < 0.05
    y_noisy = np.where(flip, np.clip(y + rng.choice([-1, 1], len(y)), 0, 2), y)
    Xtr, Xte, ytr, yte = train_test_split(X, y_noisy, test_size=0.25, random_state=42, stratify=y_noisy)
    clf = LogisticRegression(max_iter=2000, C=5.0).fit(Xtr, ytr)
    pred = clf.predict(Xte)
    _ml.update(clf=clf, labels=labels,
               accuracy=round(float(accuracy_score(yte, pred)) * 100, 1),
               confusion=confusion_matrix(yte, pred, labels=[0, 1, 2]).tolist(),
               importances={FACTOR_LABEL[k]: round(float(v), 3) for k, v in zip(FEATURE_ORDER, np.abs(clf.coef_).mean(axis=0))},
               train_size=int(len(Xtr)), test_size=int(len(Xte)))
    return _ml


def ml_predict(points):
    m = ml_model()
    row = [[points[k] for k in FEATURE_ORDER]]
    probs = m["clf"].predict_proba(row)[0]
    return dict(prediction=m["labels"][int(np.argmax(probs))],
                probabilities={l: round(float(p) * 100, 1) for l, p in zip(m["labels"], probs)},
                accuracy=m["accuracy"], confusion=m["confusion"], labels=m["labels"], importances=m["importances"],
                train_size=m["train_size"], test_size=m["test_size"],
                caveat="Trained on synthetic data labelled by the same rules (5% noise). It shows the ML workflow, not real-world suitability.")
