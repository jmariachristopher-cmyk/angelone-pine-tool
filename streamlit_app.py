import json
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

from core.generator import run_pipeline

st.set_page_config(page_title="Options Reversal Zones", layout="wide")
st.title("Options Reversal Zones — daily Pine Script generator")

# ── Config: point this at your own GitHub repo ──────────────────────
GITHUB_RAW_BASE = st.secrets.get(
    "GITHUB_RAW_BASE",
    "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data",
)


def fetch_raw(path):
    r = requests.get(f"{GITHUB_RAW_BASE}/{path}", timeout=15)
    r.raise_for_status()
    return r.text


# ── Section 1: latest auto-generated result from GitHub Actions ────
st.header("This morning's auto-generated script")
st.caption(
    "Pulled straight from GitHub — reflects the most recent scheduled run. "
    "There can be a short delay (a minute or two) after the Action commits."
)

try:
    meta = json.loads(fetch_raw("last_run.json"))
    pine_text = fetch_raw("pine_script_latest.pine")
    csv_text = fetch_raw("reversal_zones_latest.csv")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Symbol", meta["symbol"])
    c2.metric("Expiry", meta["expiry"])
    c3.metric("ATM", meta["atm"])
    c4.metric("Strikes pulled", meta["strike_count"])
    st.caption(f"Generated at {meta['generated_at']} · spot was {meta['spot']}")

    st.subheader("Computed table")
    df = pd.read_csv(pd.io.common.StringIO(csv_text))
    st.dataframe(df, use_container_width=True)

    st.subheader("Pine Script")
    st.code(pine_text, language="text")
    st.download_button("Download .pine file", pine_text, file_name="pine_script.pine")

except Exception as e:
    st.warning(
        f"Couldn't load the latest auto-generated files yet ({e}). "
        "Either the scheduled Action hasn't run yet today, or GITHUB_RAW_BASE "
        "in Streamlit secrets doesn't point at your repo."
    )

st.divider()

# ── Section 2: manual on-demand regenerate (e.g. ATM drifted midday) ─
st.header("Regenerate now (optional)")
st.caption(
    "Use this if price has moved far enough that today's pulled strikes no "
    "longer cover ATM. Requires your Angel One credentials to be set in this "
    "app's Streamlit secrets (separate from the GitHub Actions secrets)."
)

with st.expander("Run a fresh pull right now"):
    colA, colB, colC, colD = st.columns(4)
    symbol = colA.text_input("Symbol", value="NIFTY")
    expiry = colB.text_input("Expiry (Angel One format)", value="")
    step = colC.number_input("Strike step", value=50.0, step=50.0)
    n_each_side = colD.number_input("Strikes each side", value=10, step=1)

    if st.button("Fetch & Generate"):
        try:
            with st.spinner("Logging in and pulling data..."):
                result = run_pipeline(
                    st.secrets["ANGEL_API_KEY"],
                    st.secrets["ANGEL_CLIENT_ID"],
                    st.secrets["ANGEL_PASSWORD"],
                    st.secrets["ANGEL_TOTP_SECRET"],
                    symbol, expiry, step, int(n_each_side),
                )
            st.success(
                f"ATM {result['atm']} · spot {result['spot']} · "
                f"{len(result['data'])} strikes · {result['generated_at']}"
            )
            df2 = pd.DataFrame(result["data"])
            st.dataframe(df2, use_container_width=True)
            st.code(result["pine_text"], language="text")
            st.download_button(
                "Download this .pine file", result["pine_text"],
                file_name=f"pine_script_{datetime.now().strftime('%H%M')}.pine",
            )
        except Exception as e:
            st.error(f"Failed: {e}")
