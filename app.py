import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import time
from datetime import datetime
import json
import os
from fyers_apiv3 import fyersModel

# --- 1. PERMANENT MEMORY STORAGE ---
ANCHOR_FILE = "daily_anchor.json"

def get_persisted_state():
    """Reads the JSON file. If it's a new day, it wipes the memory clean."""
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(ANCHOR_FILE):
        with open(ANCHOR_FILE, "r") as f:
            try:
                data = json.load(f)
                if data.get("date") == today:
                    return data.get("morning_skew"), data.get("prev_skew")
            except:
                pass
    return None, None

def save_persisted_state(morning_skew, prev_skew):
    """Saves the tick data to the server's hard drive."""
    today = datetime.now().strftime("%Y-%m-%d")
    with open(ANCHOR_FILE, "w") as f:
        json.dump({"date": today, "morning_skew": morning_skew, "prev_skew": prev_skew}, f)


# --- 2. DATA FETCHER ---
@st.cache_data(ttl=60)
def fetch_fyers_iv_surface(client_id, access_token, symbol="NSE:NIFTY50-INDEX", strikecount=50):
    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
    response = fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "greeks": 1})
    
    if response.get('s') != 'ok':
        st.error(f"Fyers API Error: {response.get('message', 'Unknown Error')}")
        return pd.DataFrame(), 0
        
    spot_price = response['data']['optionsChain'][0]['ltp']
    expiries = response['data']['expiryData']
    surface_data, today = [], datetime.now()
    
    for exp in expiries[:5]:
        exp_timestamp = int(exp['expiry'])
        dte = max((datetime.fromtimestamp(exp_timestamp) - today).days, 0.01)
        chain_res = fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "greeks": 1, "timestamp": str(exp_timestamp)})
        
        if chain_res.get('s') == 'ok':
            for item in chain_res['data']['optionsChain']:
                if item.get('strike_price') == -1: continue 
                strike, opt_type, iv = item['strike_price'], item['option_type'], item.get('greeks', {}).get('iv', 0)
                volume = item.get('volume', 0)
                
                # Filter out illiquid 50-point strikes on far expiries that cause jagged spikes
                if dte > 15 and strike % 100 != 0:
                    continue
                
                # Filter with raw IV (no multiplier) and volume > 0 to ensure liquid quotes
                if 0 < iv <= 150 and volume > 0 and abs(strike - spot_price) <= 1500:
                    if (strike < spot_price and opt_type == 'PE') or (strike >= spot_price and opt_type == 'CE'):
                        surface_data.append({'Strike': strike, 'DTE': dte, 'IV': iv})
        time.sleep(0.3) 
    return pd.DataFrame(surface_data), spot_price


# --- 3. PLOT CHART ---
def plot_iv_skew(df, spot):
    fig = go.Figure()
    dtes = sorted(df['DTE'].unique())
    
    for dte in dtes:
        subset = df[df['DTE'] == dte].sort_values(by='Strike')
        if not subset.empty:
            fig.add_trace(go.Scatter(
                x=subset['Strike'], 
                y=subset['IV'], 
                mode='lines+markers', 
                name=f'{int(dte)} Days',
                marker=dict(size=4)
            ))

    # Vertical line for live spot price
    fig.add_vline(x=spot, line_dash="dash", line_color="red", annotation_text=f"Live Spot: {spot:.1f}", annotation_position="top left")
    
    fig.update_layout(
        title="Nifty Volatility Skew",
        xaxis_title="Strike Price",
        yaxis_title="Implied Volatility (%)",
        template="plotly_dark",
        hovermode="x unified",
        legend_title="Time to Expiration"
    )
    st.plotly_chart(fig, use_container_width=True)


# --- 4. HUD METRICS & STATE TRACKING ---
def display_iv_metrics(df, spot):
    # Load Persistent Memory (Survives tab close!)
    morning_skew, prev_skew = get_persisted_state()

    dtes = sorted(df['DTE'].unique())
    front_dte = dtes[0]
    next_dte = dtes[1] if len(dtes) > 1 else front_dte

    front_df = df[df['DTE'] == front_dte]
    next_df = df[df['DTE'] == next_dte]

    try:
        # Calculate Front ATM IV & Next-Week ATM IV
        atm_front_row = front_df.iloc[(front_df['Strike'] - spot).abs().argsort()[:1]]
        atm_iv = atm_front_row['IV'].values[0]
        atm_strike = int(atm_front_row['Strike'].values[0])

        atm_next_row = next_df.iloc[(next_df['Strike'] - spot).abs().argsort()[:1]]
        next_atm_iv = atm_next_row['IV'].values[0]
        term_diff = atm_iv - next_atm_iv

        # Calculate 500-Point Skew
        put_target = spot - 500
        call_target = spot + 500
        put_iv = front_df.iloc[(front_df['Strike'] - put_target).abs().argsort()[:1]]['IV'].values[0]
        call_iv = front_df.iloc[(front_df['Strike'] - call_target).abs().argsort()[:1]]['IV'].values[0]
        current_skew = put_iv - call_iv

        # Update Memory & Calculate Changes
        if morning_skew is None:
            morning_skew = current_skew  # Anchors the very first fetch of the day
            
        morning_change = current_skew - morning_skew
        
        prev_change = 0.0
        if prev_skew is not None:
            prev_change = current_skew - prev_skew
            
        # Instantly saves the new state back to the file
        save_persisted_state(morning_skew, current_skew)

        # Determine Actionable Strategy
        if term_diff > 1.0:
            regime = "🔴 SELL PREMIUM (Front IV High)"
            advice = f"Front DTE ({int(front_dte)}d) is +{term_diff:.1f}% overpriced. Sell Credit Spreads."
        elif term_diff < -0.5:
            regime = "🟢 BUY OPTIONS (Front IV Cheap)"
            advice = f"Front DTE ({int(front_dte)}d) is cheap ({term_diff:.1f}%). Favorable for buying ATM/ITM options."
        else:
            regime = "🟡 NEUTRAL IV (Use Vertical Spreads)"
            advice = "Term structure is flat. Use Debit or Credit Spreads based on Order Flow."

        # Render Metric Cards
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Nifty Spot (ATM)", f"{spot:.1f}", f"ATM Strike: {atm_strike}", delta_color="off")
        col2.metric(f"Front ATM IV ({int(front_dte)}d)", f"{atm_iv:.2f}%", f"{term_diff:+.2f}% vs {int(next_dte)}d", delta_color="inverse")
        
        col3.metric(label="500-Pt Put Skew", value=f"{current_skew:+.2f}%", delta=f"{prev_change:+.2f}% (Tick)", delta_color="inverse")
        col3.caption(f"Since Morning Anchor: **{morning_change:+.2f}%**")
        
        col4.metric("IV Regime", "Sell Front" if term_diff > 1.0 else ("Buy Front" if term_diff < -0.5 else "Spreads"), f"Next ATM: {next_atm_iv:.2f}%", delta_color="off")

        st.info(f"**{regime}** — {advice}")
    except Exception as e:
        st.warning("Gathering sufficient live market data for HUD metrics...")


# --- 5. STREAMLIT UI & OAUTH LOGIN ---
st.set_page_config(page_title="Nifty Volatility Skew", layout="wide")
st.title("Nifty Volatility Skew (FYERS API)")

st.sidebar.header("1. FYERS Authentication")

client_id = st.secrets.get("FYERS_CLIENT_ID", "")
secret_key = st.secrets.get("FYERS_SECRET_KEY", "")

# Matches your FYERS dashboard!
redirect_uri = "https://nifty-iv-surface-vdfuj8nv2kpgxfkwvk7unh.streamlit.app"

if "access_token" not in st.session_state:
    st.session_state.access_token = st.secrets.get("FYERS_ACCESS_TOKEN", "")

if client_id and secret_key:
    session_model = fyersModel.SessionModel(client_id=client_id, secret_key=secret_key, redirect_uri=redirect_uri, response_type="code", grant_type="authorization_code")
    
    # 1-TAP LOGIN MAGIC
    if "auth_code" in st.query_params:
        auth_code = st.query_params["auth_code"]
        session_model.set_token(auth_code)
        res = session_model.generate_token()
        if res.get('s') == 'ok':
            st.session_state.access_token = res['access_token']
            st.query_params.clear() 
            st.rerun()
        else:
            st.sidebar.error("Auto-login failed. Please try again.")

    if not st.session_state.access_token:
        auth_link = session_model.generate_authcode()
        st.sidebar.markdown(f"**[👉 1-Tap Login to FYERS]({auth_link})**")
    else:
        st.sidebar.success("✅ Connected to FYERS Live Data")
        if st.sidebar.button("Logout"):
            st.session_state.access_token = ""
            st.rerun()
else:
    st.sidebar.warning("Please add FYERS_CLIENT_ID and FYERS_SECRET_KEY to your Streamlit Secrets.")


# --- 6. GENERATE BUTTON & AUTO-REFRESH ---
st.sidebar.divider()
st.sidebar.header("2. Generate Surface")
symbol = st.sidebar.text_input("Index Symbol", value="NSE:NIFTY50-INDEX")

# The auto-refresh toggle
auto_refresh = st.sidebar.checkbox("Auto-Refresh (3 mins)")

if st.sidebar.button("Generate IV Surface") or auto_refresh:
    if not st.session_state.access_token:
        st.warning("Please log in first.")
    else:
        with st.spinner("Fetching live option chain data from FYERS..."):
            # Clears the cache if auto-refreshing so data is genuinely live
            if auto_refresh:
                fetch_fyers_iv_surface.clear()
                
            df, spot = fetch_fyers_iv_surface(client_id, st.session_state.access_token, symbol)
            
            if not df.empty:
                display_iv_metrics(df, spot)
                st.success(f"Surface generated for {len(df)} data points.")
                plot_iv_skew(df, spot)
                
                # Sleeps for 3 minutes, then triggers a complete page refresh
                if auto_refresh:
                    time.sleep(180)
                    st.rerun()
            else:
                st.warning("⚠️ No valid data to plot! The market is closed or FYERS is reporting '0' for all IVs.")
                
