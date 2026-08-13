"""
Run once each morning (via GitHub Actions on a schedule, or manually) to
pull ATM +/- N strikes from Angel One, compute the reversal zones, and
write:
    data/pine_script_latest.pine
    data/reversal_zones_latest.csv
    data/last_run.json

Credentials come from environment variables so nothing sensitive is
ever written into the repo:
    ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.generator import run_pipeline  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--expiry", required=True)
    ap.add_argument("--step", type=float, default=50)
    ap.add_argument("--n_each_side", type=int, default=10)
    args = ap.parse_args()

    api_key = os.environ.get("ANGEL_API_KEY", "")
    client_id = os.environ.get("ANGEL_CLIENT_ID", "")
    password = os.environ.get("ANGEL_PASSWORD", "")
    totp_secret = os.environ.get("ANGEL_TOTP_SECRET", "")

    result = run_pipeline(
        api_key, client_id, password, totp_secret,
        args.symbol, args.expiry, args.step, args.n_each_side,
    )

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "pine_script_latest.pine").write_text(result["pine_text"])
    (DATA_DIR / "reversal_zones_latest.csv").write_text(result["csv_text"])
    (DATA_DIR / "last_run.json").write_text(json.dumps({
        "generated_at": result["generated_at"],
        "symbol": result["symbol"],
        "expiry": result["expiry"],
        "spot": result["spot"],
        "atm": result["atm"],
        "strike_count": len(result["data"]),
    }, indent=2))

    print(f"Done. ATM={result['atm']} spot={result['spot']} "
          f"strikes={len(result['data'])}. Wrote files to {DATA_DIR}")


if __name__ == "__main__":
    main()
