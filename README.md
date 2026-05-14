# Options Wheel Coach

A position-management app for the Options Wheel Strategy. Tracks open cash-secured puts and covered calls, evaluates rules-based alerts daily after market close, and pushes critical decisions to Slack so they don't get missed.

The app is built around two principles:

- **Mechanical rules where possible.** Every alert is a deterministic function of position state. No "the model thinks you should..." outputs.
- **Human judgment where it matters.** Close-vs-roll-vs-wait decisions go through a chat-based assistant that walks the user through the three-check framework from the Wheel Strategy docs. The app surfaces what's true; the human decides what to do.

This is a personal project. The Wheel Strategy reference content is paid subscription material from [wheelstrategy.substack.com](https://wheelstrategy.substack.com) and is not redistributed.

## Status

In development. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) for scope and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the technical plan.

## Repo layout

```
options-wheel-coach/
├── README.md                       This file
├── CLAUDE.md                       Project context for AI-assisted sessions
├── .gitignore
├── requirements.txt                Python dependencies
├── docs/                           Specification
│   ├── REQUIREMENTS.md
│   ├── ARCHITECTURE.md
│   ├── ALERTS.md
│   ├── DATA_MODEL.md
│   ├── RULE_CLARIFICATIONS.md
│   └── DASHBOARD.md
├── data/                           Position and portfolio data (gitignored)
│   └── portfolio.example.json      Schema template (committed)
├── scripts/                        Python — daily data refresh, alerts engine
└── dashboard/                      Single-file HTML dashboard
```

## Setup

1. Clone the repository.
2. Install Python dependencies: `pip install -r requirements.txt`
3. Copy the portfolio template and fill in your account details:
   ```bash
   cp data/portfolio.example.json data/portfolio.json
   ```
4. Open `data/portfolio.json` and set `total_account_value`, `cash_available`, and any rule overrides.
5. Build the dashboard data file (this reads the JSON files and writes `dashboard/data.js`):
   ```bash
   python scripts/build_dashboard_data.py
   ```
6. Double-click `dashboard/index.html` to open the dashboard in your browser.

Re-run step 5 anytime the JSON files change. In Phase 2, the daily updater script runs it automatically.

## Disclaimer

This is educational software for personal use. Nothing in this repository is investment advice. Options trading involves risk and is not suitable for all investors. Past performance is not indicative of future results. The author is not a financial advisor.
