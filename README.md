# Options Reversal Zones — automated Angel One → Pine Script pipeline

Every weekday morning, GitHub Actions logs into Angel One, finds ATM, pulls
CE/PE for the 10 strikes above and below it, computes all the reversal-zone
math, and commits a ready-to-paste Pine Script into this repo. A Streamlit
app displays the result so you never touch a terminal.

## What runs where

- **GitHub Actions** — the actual daily automation. Runs on a schedule,
  needs no one to click anything.
- **Streamlit app** — a dashboard that reads the latest committed files
  and shows them nicely, plus an optional manual "regenerate now" button
  for mid-day reruns if the market moves ATM outside your pulled window.
- **Pine Script** — still has to be pasted into TradingView's Pine Editor
  once. TradingView has no public API to push code onto a chart, so this
  one step can't be automated away.

## One-time setup

### 1. Get Angel One SmartAPI credentials
- Create an app at smartapi.angelone.in → "Create an App" → Market Feeds
  APIs → note the **API key**.
- Enable TOTP at smartapi.angelone.in/enable-totp → note the **TOTP
  secret** shown alongside the QR code (not just the QR code itself).

### 2. Create the GitHub repo
- Push this folder to a new GitHub repo (public or private both work;
  public is simpler for the Streamlit raw-file fetch, private also works
  but needs a token — see note at the bottom).

### 3. Add GitHub Actions secrets
Repo → Settings → Secrets and variables → Actions → **Secrets** tab → add:
- `ANGEL_API_KEY`
- `ANGEL_CLIENT_ID`
- `ANGEL_PASSWORD`
- `ANGEL_TOTP_SECRET`

### 4. Add GitHub Actions variables
Same page → **Variables** tab → add (update `DEFAULT_EXPIRY` weekly):
- `DEFAULT_SYMBOL` = `NIFTY`
- `DEFAULT_EXPIRY` = e.g. `26JUN2025` (must match Angel One's exact format)
- `DEFAULT_STEP` = `50`
- `DEFAULT_N_EACH_SIDE` = `10`

### 5. Confirm the schedule
`.github/workflows/daily_generate.yml` runs at 3:46 UTC (9:16 AM IST),
Mon–Fri. Change the `cron:` line if you want a different time. You can
also trigger it manually anytime from the repo's Actions tab (the
"Run workflow" button) — handy right after you update `DEFAULT_EXPIRY`
each week, or intraday if you don't want to use the Streamlit button.

### 6. First manual run
Trigger the workflow once by hand (Actions tab → Daily Pine Script
Generator → Run workflow) to confirm your secrets/variables are correct
and to populate `data/` for the first time.

### 7. Deploy the Streamlit app
- Go to share.streamlit.io → New app → point it at this repo,
  `streamlit_app.py` as the entry file.
- In the app's Settings → Secrets, paste the same four Angel One
  credentials (yes, separately from GitHub's — they're different
  execution environments), plus:
  ```
  GITHUB_RAW_BASE = "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data"
  ```
- Deploy. Open the app each morning to see the day's script and copy it
  into TradingView.

## Weekly maintenance
Expiry changes every week (or month, for monthly contracts) — update the
`DEFAULT_EXPIRY` repo variable each time, in Angel One's exact date
format. Everything else runs unattended.

## If your repo is private
`raw.githubusercontent.com` requires a token for private repos. Either
keep the repo public (the option premium data itself isn't sensitive —
only your API credentials are, and those never touch the repo), or have
the Streamlit app authenticate to GitHub's API with a personal access
token instead of using the raw-file URL directly.

## Local testing (optional, before wiring up GitHub Actions)
```
pip install -r requirements.txt
export ANGEL_API_KEY=... ANGEL_CLIENT_ID=... ANGEL_PASSWORD=... ANGEL_TOTP_SECRET=...
python scripts/run_daily.py --symbol NIFTY --expiry 26JUN2025 --step 50 --n_each_side 10
```
