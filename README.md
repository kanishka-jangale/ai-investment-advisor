# AI Investment Advisor Dashboard (College Mini Project)

A web-based, **educational** decision-support dashboard: portfolio tracking, charts, a rule-based risk profile,
explainable insights, investment comparison, a SIP/goal calculator, watchlist + reminders, and a printable report.

> Not a broker, not financial advice, no guaranteed returns. **All prices are simulated sample data** and are labelled that way in the app.

**Stack (as in your spec):** Python + Flask (backend) | HTML, CSS, JavaScript + Chart.js (frontend) | Pandas + NumPy (analysis) | scikit-learn (optional ML) | SQLite (storage)

## 1. Run it in VS Code (Windows / macOS / Linux)

1. Install **Python 3.10+** and **VS Code** (with the *Python* extension).
2. In VS Code: **File > Open Folder...** and choose this `ai-investment-advisor` folder.
3. Open the terminal (**Terminal > New Terminal**) and run:

```bash
# create a virtual environment (do this once)
python -m venv venv

# activate it
venv\Scripts\activate          # Windows (PowerShell / CMD)
source venv/bin/activate       # macOS / Linux

# install libraries (once)
pip install -r requirements.txt

# start the app
python app.py
```

4. Open **http://127.0.0.1:5000** in your browser. Click a sample investor under "Try the demo".
5. Stop the server with `Ctrl + C`.

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass` once, then activate again.
If VS Code asks for an interpreter: `Ctrl+Shift+P` > "Python: Select Interpreter" > choose the one inside `venv`.

**Internet note:** Chart.js is loaded from a CDN, so the browser needs internet the first time.
For offline use, download `chart.umd.min.js` into `static/js/` and change the `<script src=...>` line in `templates/base.html`.

## 2. Folder structure

```
ai-investment-advisor/
|-- app.py              Flask backend: routes, login, validation, JSON API
|-- engine.py           All calculations + rules engine + SIP + optional ML (no Flask code)
|-- data_loader.py      Simulated sample prices, asset catalog, sample investors
|-- database.py         SQLite schema + helpers (creates instance/advisor.db automatically)
|-- requirements.txt
|-- data/               price_history.csv is generated on first run
|-- templates/          base, login, dashboard, portfolio, risk, insights, compare, goal, watchlist, settings, report
|-- static/css/style.css
|-- static/js/app.js    shared JS helpers (API calls, formatting, charts, tooltips)
`-- instance/           database + secret key (created automatically)
```

## 3. Spec -> where it lives

| Spec section | Page / code |
|---|---|
| 4.1 Home overview (4 cards, allocation, performance, insights, data labels) | `/dashboard` |
| 4.2 Profile + 4.3 Risk module (explainable score, factors, concepts) | `/risk`, `engine.compute_risk` |
| 4.4 Holdings add/edit/delete, validation, CSV import/export | `/portfolio`, `validate_holding`, `/api/holdings/import` |
| 4.5 Allocation, concentration threshold, current vs hypothetical | `/portfolio`, Settings (threshold) |
| 4.6 Historical performance, period change, benchmark, table | `/dashboard`, `engine.performance` |
| 4.7 Rule-based insights with "why", inputs, assumptions | `/insights`, `engine.generate_insights` |
| 4.8 Compare assets / hypothetical portfolios, save scenarios | `/compare`, `engine.compare_*` |
| 4.9 SIP / goal calculator with several rates | `/goal`, `engine.goal_plan` |
| 4.10 Watchlist with asset details | `/watchlist` |
| 4.11 In-app reminders (bell icon) | `/watchlist`, `evaluate_alerts` |
| 4.12 Printable report, CSV export | `/report`, `/export/holdings.csv` |
| 4.13 Glossary, tooltips, walkthrough | `/settings` (Help tab), `?` tooltips |
| 4.14 Settings, reset demo, data export, delete account | `/settings` |
| Optional ML experiment with evaluation | Risk page, `engine.ml_model` |

## 4. How to demo (matches section 10 of your spec)

1. Open the demo, pick **Rahul / Priya / Aman**.  2. Risk Profile: answer, press Calculate, change an answer and watch the result change.
3. Home / My Portfolio: values, allocation, performance.  4. Add or edit a holding: totals recalculate (try leaving a price blank or a negative quantity to show validation).
5. AI Insights: open **Why did this appear?** (Aman's portfolio triggers concentration flags).  6. Compare two hypothetical mixes, or use the Goal Planner.
7. Reports: **Print / Save as PDF**, point out the data-source line, assumptions and disclaimer.

## 5. Key formulas (also in the Help page)

* Value = quantity x latest price | Cost basis = quantity x purchase price
* P/L = value - cost basis (before fees and taxes) | Return % = P/L / cost basis x 100
* Weight % = holding value / total value x 100 | Period return % = (end - start) / start x 100
* SIP = target / [ ((1+i)^n - 1) / i x (1+i) ], i = annual rate / 12, n = months (payment at start of month)
* Risk score = average of 8 factors (1-5 points each, scaled to 0-100): under 35 Conservative, 35 to 65 Moderate, 65+ Aggressive

## 6. Using your own data

Replace `data/price_history.csv` (first column `date`, then one column per symbol) and add each symbol to `CATALOG` in `data_loader.py`.
If you use a real market-data API, check its licence, rate limits and terms, and never show delayed or simulated data as live.

## 7. Limitations (say these in your viva)

* Prices are simulated, so returns are not real. History assumes today's quantities were held for the whole period.
* The risk score is a short, rule-based educational estimate. The ML model learns the same rules from synthetic data, so its accuracy says nothing about real suitability.
* Fees and taxes are not modelled in returns. Currency is display-only (no FX conversion).
* Security: passwords are hashed, CSRF tokens are checked, each user sees only their own rows. It has no login rate-limiting or email verification, so do not deploy it publicly as-is.

## 8. Troubleshooting

* `ModuleNotFoundError` -> the virtual environment is not active or `pip install -r requirements.txt` was not run.
* Charts are blank -> no internet for the Chart.js CDN (see the offline note above).
* Port busy -> change `port=5000` at the bottom of `app.py`.
* Start fresh -> stop the server, delete `instance/advisor.db` and `data/price_history.csv`, run again.
