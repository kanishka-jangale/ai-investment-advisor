"""
Sample data layer.

IMPORTANT: every price in this project is SIMULATED (a seeded random walk).
It exists so the demo is reproducible. It is NOT live or real market data,
and the UI labels it as such everywhere.
"""
import os
from datetime import date

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PRICE_CSV = os.path.join(DATA_DIR, "price_history.csv")

DATA_LABEL = "SAMPLE DATA (simulated) - not live market data"
BENCHMARK = "NIFTY-SAMPLE"

# symbol -> metadata used both to simulate prices and to describe the asset.
# mu = assumed yearly drift, sigma = yearly volatility, corr = correlation to the "market"
# expense = yearly fee in % (illustrative), min_invest = illustrative minimum amount.
CATALOG = {
    "NIFTY-SAMPLE": dict(name="Sample Broad Market Index (benchmark)", category="Benchmark",
                         start=1000, mu=0.12, sigma=0.15, corr=1.00, expense=None, min_invest=None),
    "EQ-LARGE": dict(name="Sample Large-Cap Stock", category="Equity",
                     start=2500, mu=0.13, sigma=0.17, corr=0.90, expense=0.0, min_invest=2500),
    "EQ-MID": dict(name="Sample Mid-Cap Stock", category="Equity",
                   start=900, mu=0.17, sigma=0.26, corr=0.80, expense=0.0, min_invest=900),
    "EQ-IT": dict(name="Sample IT Sector Stock", category="Equity",
                  start=1600, mu=0.15, sigma=0.24, corr=0.60, expense=0.0, min_invest=1600),
    "EQ-BANK": dict(name="Sample Banking Stock", category="Equity",
                    start=1200, mu=0.12, sigma=0.22, corr=0.80, expense=0.0, min_invest=1200),
    "MF-INDEX": dict(name="Sample Index Mutual Fund", category="Mutual Fund",
                     start=150, mu=0.12, sigma=0.15, corr=0.95, expense=0.20, min_invest=500),
    "MF-FLEXI": dict(name="Sample Flexi-Cap Mutual Fund", category="Mutual Fund",
                     start=80, mu=0.14, sigma=0.17, corr=0.88, expense=1.10, min_invest=500),
    "DEBT-GILT": dict(name="Sample Government Bond Fund", category="Debt",
                      start=40, mu=0.07, sigma=0.04, corr=0.10, expense=0.50, min_invest=1000),
    "DEBT-CORP": dict(name="Sample Corporate Bond Fund", category="Debt",
                      start=30, mu=0.075, sigma=0.03, corr=0.10, expense=0.60, min_invest=1000),
    "GOLD-ETF": dict(name="Sample Gold ETF", category="Gold",
                     start=60, mu=0.08, sigma=0.13, corr=-0.05, expense=0.55, min_invest=60),
    "LIQUID-FUND": dict(name="Sample Liquid Fund", category="Cash",
                        start=1000, mu=0.06, sigma=0.004, corr=0.00, expense=0.20, min_invest=500),
}

# ---------------------------------------------------------------- sample investors
# amount = money invested, days_ago = approx. purchase date (buy price is read from the sample prices)
PERSONAS = {
    "conservative": dict(
        label="Priya - Conservative saver", display_name="Priya",
        experience="Beginner", goal="Protect savings for a near-term goal", horizon_years=2,
        preferred_risk="Conservative",
        answers=dict(income_stability=3, emergency_fund=2, obligations=2, loss_reaction=0, max_loss=0, preference=0),
        holdings=[
            dict(symbol="DEBT-GILT", amount=150000, days_ago=420),
            dict(symbol="DEBT-CORP", amount=120000, days_ago=380),
            dict(symbol="LIQUID-FUND", amount=80000, days_ago=300),
            dict(symbol="GOLD-ETF", amount=40000, days_ago=400),
            dict(symbol="MF-INDEX", amount=60000, days_ago=250),
            dict(name="Bank Fixed Deposit (manual)", category="Cash", manual_cost=100000, manual_value=106500),
        ]),
    "moderate": dict(
        label="Rahul - Balanced investor", display_name="Rahul",
        experience="Intermediate", goal="Build wealth for a home down-payment", horizon_years=7,
        preferred_risk="Moderate",
        answers=dict(income_stability=3, emergency_fund=3, obligations=2, loss_reaction=2, max_loss=2, preference=2),
        holdings=[
            dict(symbol="EQ-LARGE", amount=120000, days_ago=500),
            dict(symbol="MF-INDEX", amount=150000, days_ago=450),
            dict(symbol="MF-FLEXI", amount=120000, days_ago=330),
            dict(symbol="DEBT-CORP", amount=120000, days_ago=400),
            dict(symbol="GOLD-ETF", amount=60000, days_ago=350),
            dict(symbol="EQ-IT", amount=50000, days_ago=200),
        ]),
    "aggressive": dict(
        label="Aman - Growth seeker", display_name="Aman",
        experience="Experienced", goal="Long-term wealth creation", horizon_years=15,
        preferred_risk="Aggressive",
        answers=dict(income_stability=4, emergency_fund=4, obligations=4, loss_reaction=4, max_loss=3, preference=4),
        holdings=[
            dict(symbol="EQ-IT", amount=350000, days_ago=480),
            dict(symbol="EQ-MID", amount=150000, days_ago=430),
            dict(symbol="EQ-BANK", amount=100000, days_ago=300),
            dict(symbol="EQ-LARGE", amount=100000, days_ago=350),
            dict(symbol="GOLD-ETF", amount=50000, days_ago=260),
        ]),
}


def generate_sample_prices(days=760, seed=42):
    """Seeded geometric random walk with a shared 'market' factor so assets move together a bit."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(date.today()), periods=days)
    n = len(idx)
    market = rng.standard_normal(n)
    cols = {}
    for sym, m in CATALOG.items():
        own = rng.standard_normal(n)
        c = m["corr"]
        z = c * market + np.sqrt(max(0.0, 1 - c * c)) * own
        daily = m["mu"] / 252 - 0.5 * m["sigma"] ** 2 / 252 + m["sigma"] / np.sqrt(252) * z
        cols[sym] = m["start"] * np.exp(np.cumsum(daily))
    df = pd.DataFrame(cols, index=idx).round(2)
    df.index.name = "date"
    return df


def ensure_sample_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(PRICE_CSV):
        generate_sample_prices().to_csv(PRICE_CSV)


_cache = {"mtime": None, "df": None}


def load_prices():
    """Read the price CSV (cached until the file changes). You can replace it with your own CSV:
    first column 'date', one column per symbol."""
    ensure_sample_data()
    mtime = os.path.getmtime(PRICE_CSV)
    if _cache["mtime"] != mtime:
        df = pd.read_csv(PRICE_CSV, parse_dates=["date"], index_col="date").sort_index()
        _cache.update(mtime=mtime, df=df.dropna(how="all"))
    return _cache["df"]


def latest_prices():
    df = load_prices()
    return {c: float(df[c].dropna().iloc[-1]) for c in df.columns}


def price_on_or_before(symbol, days_ago):
    df = load_prices()
    s = df[symbol].dropna()
    target = s.index[-1] - pd.Timedelta(days=days_ago)
    s = s[s.index <= target]
    return float(s.iloc[-1]) if len(s) else float(df[symbol].dropna().iloc[0])


def data_status():
    df = load_prices()
    last = df.index[-1].date()
    return dict(
        label=DATA_LABEL,
        source="Simulated sample CSV (data/price_history.csv)",
        is_live=False,
        last_updated=last.isoformat(),
        days_old=(date.today() - last).days,
        first_date=df.index[0].date().isoformat(),
        rows=int(len(df)),
        symbols=len(df.columns),
    )
