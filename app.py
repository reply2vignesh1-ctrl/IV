import streamlit as st
import pandas as pd
import numpy as np
from scipy.stats import norm
import plotly.graph_objects as go
import time
from datetime import datetime
import json
import os
from fyers_apiv3 import fyersModel

# --- 1. PERMANENT MEMORY STORAGE (100% INTACT) ---
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


# --- 2. DATA FETCHER (FETCHES BOTH IV SURFACE & GEX IN 1 PASS) ---
@st.cache_data(ttl=60)
def fetch_fyers_iv_surface(client_id, access_token, symbol="NSE:NIFTY50-INDEX", strikecount=50):
    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
    response = fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "greeks": 1})
    
    if response.get('s') != 'ok':
        st.error(f"Fyers API Error: {response.get('message', 'Unknown Error')}")
        return pd.DataFrame(), pd.DataFrame(), 0
        
    spot_price = response['data']['optionsChain'][0]['ltp']
    expiries = response['data']['expiryData']
    surface_data, gex_records, today = [], [], datetime.now()
    
    for exp in expiries[:5]:
        exp_timestamp = int(exp['expiry'])
        exp_date_str = exp.get('date', str(exp_timestamp))
        dte = max((datetime.fromtimestamp(exp_timestamp) - today).days, 0.01)
        # Exact time in years for Black-Scholes Gamma
        T_years = max((datetime.fromtimestamp(exp_timestamp) - today).total_seconds() / (365.0 * 24.0 * 3600.0), 1 / (365.0 * 24.0))
        
        chain_res = fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "greeks": 1, "timestamp": str(exp_timestamp)})
        
        if chain_res.get('s') == 'ok':
            strike_map = {}
            for item in chain_res['data']['optionsChain']:
                if item.get('strike_price') == -1: continue 
                strike = item['strike_price']
                opt_type = item['option_type']
                iv = item.get('greeks', {}).get('iv', 0)
                volume = item.get('volume', 0)
                oi = float(item.get('oi', 0))
                chgoi = float(item.get('chgoi', 0))
                
                # --- A. COLLECT DATA FOR LIVE GEX (Within 1500 pts of Spot) ---
                if abs(strike - spot_price) <= 1500 and opt_type in ['CE', 'PE']:
                    if strike not in strike_map:
                        strike_map[strike] = {
                            'Strike': float(strike), 'Expiry': exp_date_str, 
                            'DTE': dte, 'T': T_years,
                            'ce_iv': 0.0, 'pe_iv': 0.0, 
                            'ce_oi': 0.0, 'pe_oi': 0.0, 
                            'ce_chgoi': 0.0, 'pe_chgoi': 0.0
                        }
                    prefix = 'ce_' if opt_type == 'CE' else 'pe_'
                    strike_map[strike][f'{prefix}iv'] = float(iv)
                    strike_map[strike][f'{prefix}oi'] = oi
                    strike_map[strike][f'{prefix}chgoi'] = chgoi

                # --- B. YOUR EXACT IV SURFACE LOGIC (100% UNCHANGED) ---
                # Filter out illiquid 50-point strikes on far expiries that cause jagged spikes
                if dte > 15 and strike % 100 != 0:
                    continue
                
                # Filter with raw IV (no multiplier) and volume > 0 to ensure liquid quotes
                if 0 < iv <= 150 and volume > 0 and abs(strike - spot_price) <= 1500:
                    if (strike < spot_price and opt_type == 'PE') or (strike >= spot_price and opt_type == 'CE'):
                        surface_data.append({'Strike': strike, 'DTE': dte, 'IV': iv})
                        
            gex_records.extend(strike_map.values())
        time.sleep(0.3) 
        
    return pd.DataFrame(surface_data), pd.DataFrame(gex_records), spot_price


# --- 3. PLOT IV SKEW CHART (100% INTACT) ---
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


# --- 4. HUD METRICS & STATE TRACKING (100% INTACT) ---
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


# --- 5. NEW MODULE: LIVE DEALER GEX ENGINE & CHART ---
def bs_gamma(S, K, T, r, sigma_pct):
    """Calculates Black-Scholes Gamma from percentage IV (e.g. 14.5 -> 0.145)."""
    T_safe = np.maximum(T, 1 / (365.0 * 24.0))
    # Convert FYERS percentage IV to decimal; fallback to 12% if Fyers reports 0 on deep strikes
    sigma = np.where((sigma_pct > 0) & (sigma_pct <= 150), sigma_pct / 100.0, 0.12)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T_safe) / (sigma * np.sqrt(T_safe))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T_safe))

def find_gamma_flip(gex_df, spot, r=0.07, use_chgoi=False):
    """Finds the exact Nifty price where Net Dealer Gamma flips from Negative to Positive."""
    price_grid = np.linspace(spot * 0.96, spot * 1.04, 80)
    c_oi = gex_df['ce_chgoi'].values if use_chgoi else gex_df['ce_oi'].values
    p_oi = gex_df['pe_chgoi'].values if use_chgoi else gex_df['pe_oi'].values
    K = gex_df['Strike'].values
    T = gex_df['T'].values
    ce_iv = gex_df['ce_iv'].values
    pe_iv = gex_df['pe_iv'].values
    
    net_curve = []
    for s_sim in price_grid:
        c_gam = bs_gamma(s_sim, K, T, r, ce_iv)
        p_gam = bs_gamma(s_sim, K, T, r, pe_iv)
        factor = (s_sim ** 2) * 0.01 / 1e7
        net_curve.append(np.sum((c_gam * c_oi - p_gam * p_oi) * factor))
        
    net_curve = np.array(net_curve)
    crossings = np.where(np.diff(np.sign(net_curve)))[0]
    if len(crossings) > 0:
        # Pick crossing closest to current Spot
        best_idx = crossings[np.argmin(np.abs(price_grid[crossings] - spot))]
        p1, p2 = price_grid[best_idx], price_grid[best_idx + 1]
        g1, g2 = net_curve[best_idx], net_curve[best_idx + 1]
        return round(float(p1 - g1 * (p2 - p1) / (g2 - g1)), 1)
    return None

def display_gex_dashboard(gex_df, spot):
    if gex_df.empty:
        st.warning("No GEX data available.")
        return

    c1, c2, c3 = st.columns([1.4, 1.8, 1.2])
    expiries_list = list(gex_df['Expiry'].unique())
    with c1:
        selected_exp = st.selectbox("Select Expiry", ["Front Expiry (" + expiries_list[0] + ")", "All 5 Expiries Combined"] + expiries_list[1:])
    with c2:
        gex_mode = st.radio("GEX Mode", ["Total OI (Structural Walls)", "Today's Change in OI (Intraday Flow)"], horizontal=True)
    with c3:
        view_mode = st.radio("Bar View", ["Net GEX", "Split Call / Put"], horizontal=True)

    # Filter Expiry
    if selected_exp.startswith("Front Expiry"):
        sub_df = gex_df[gex_df['Expiry'] == expiries_list[0]].copy()
    elif selected_exp == "All 5 Expiries Combined":
        sub_df = gex_df.copy()
    else:
        sub_df = gex_df[gex_df['Expiry'] == selected_exp].copy()

    use_chgoi = "Change in OI" in gex_mode
    c_oi_col = 'ce_chgoi' if use_chgoi else 'ce_oi'
    p_oi_col = 'pe_chgoi' if use_chgoi else 'pe_oi'

    # Calculate Gamma and Rupee GEX (in Crores per 1% Spot move)
    r = 0.07
    sub_df['call_gamma'] = bs_gamma(spot, sub_df['Strike'], sub_df['T'], r, sub_df['ce_iv'])
    sub_df['put_gamma'] = bs_gamma(spot, sub_df['Strike'], sub_df['T'], r, sub_df['pe_iv'])
    
    spot_factor = (spot ** 2) * 0.01 / 1e7
    sub_df['call_gex'] = sub_df['call_gamma'] * sub_df[c_oi_col] * spot_factor
    sub_df['put_gex'] = -1.0 * sub_df['put_gamma'] * sub_df[p_oi_col] * spot_factor
    sub_df['net_gex'] = sub_df['call_gex'] + sub_df['put_gex']

    strike_gex = sub_df.groupby('Strike', as_index=False)[['call_gex', 'put_gex', 'net_gex']].sum()
    # Keep strikes within +/- 800 points for clean visual ladder
    strike_gex = strike_gex[(strike_gex['Strike'] >= spot - 800) & (strike_gex['Strike'] <= spot + 800)]

    if strike_gex.empty:
        st.warning("No strikes within +/- 800 points of Spot.")
        return

    call_wall = int(strike_gex.loc[strike_gex['call_gex'].idxmax(), 'Strike'])
    put_wall = int(strike_gex.loc[strike_gex['put_gex'].idxmin(), 'Strike'])
    total_net_gex = strike_gex['net_gex'].sum()
    gamma_flip = find_gamma_flip(sub_df, spot, r, use_chgoi=use_chgoi)

    # Render GEX Metric Cards
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Dealer GEX", f"₹{total_net_gex:+.1f} Cr", "Positive (Mean Revert / Pin)" if total_net_gex >= 0 else "Negative (Trend / Squeeze)", delta_color="normal" if total_net_gex >= 0 else "inverse")
    m2.metric("Zero Gamma Flip", f"{gamma_flip:.1f}" if gamma_flip else "Out of Range", "Above = Calm | Below = Volatile", delta_color="off")
    m3.metric("Call Wall (Resistance)", f"{call_wall}", f"{call_wall - spot:+.0f} pts from Spot", delta_color="off")
    m4.metric("Put Wall (Support)", f"{put_wall}", f"{put_wall - spot:+.0f} pts from Spot", delta_color="off")

    # Plot Horizontal Bar Chart
    fig_gex = go.Figure()
    if view_mode == "Net GEX":
        colors = ['#00E676' if v >= 0 else '#FF1744' for v in strike_gex['net_gex']]
        fig_gex.add_trace(go.Bar(
            y=strike_gex['Strike'], x=strike_gex['net_gex'],
            orientation='h', marker_color=colors, name='Net GEX (₹ Cr)'
        ))
    else:
        fig_gex.add_trace(go.Bar(
            y=strike_gex['Strike'], x=strike_gex['call_gex'],
            orientation='h', marker_color='#00E676', name='Call GEX (+)'
        ))
        fig_gex.add_trace(go.Bar(
            y=strike_gex['Strike'], x=strike_gex['put_gex'],
            orientation='h', marker_color='#FF5252', name='Put GEX (-)'
        ))

    fig_gex.add_hline(y=spot, line_dash="dash", line_color="yellow", annotation_text=f"Live Spot: {spot:.1f}", annotation_position="top right")
    if gamma_flip and (spot - 800 <= gamma_flip <= spot + 800):
        fig_gex.add_hline(y=gamma_flip, line_dash="dot", line_color="cyan", annotation_text=f"Gamma Flip: {gamma_flip:.1f}", annotation_position="bottom right")

    fig_gex.update_layout(
        title=f"Nifty Dealer Gamma Exposure ({selected_exp} | {gex_mode})",
        xaxis_title="Dealer Gamma Exposure (₹ Crores per 1% Move)",
        yaxis_title="Strike Price",
        template="plotly_dark",
        barmode="relative",
        height=650
    )
    st.plotly_chart(fig_gex, use_container_width=True)


# --- 6. STREAMLIT UI & OAUTH LOGIN (100% INTACT) ---
st.set_page_config(page_title="Nifty Volatility Skew & Live GEX", layout="wide")
st.title("Nifty Volatility Skew & Dealer GEX (FYERS API)")

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
            st.session_state.data_generated = False
            st.rerun()
else:
    st.sidebar.warning("Please add FYERS_CLIENT_ID and FYERS_SECRET_KEY to your Streamlit Secrets.")


# --- 7. GENERATE BUTTON & TABS ---
st.sidebar.divider()
st.sidebar.header("2. Generate Surface & GEX")
symbol = st.sidebar.text_input("Index Symbol", value="NSE:NIFTY50-INDEX")

if "data_generated" not in st.session_state:
    st.session_state.data_generated = False

if st.sidebar.button("Generate IV Surface"):
    st.session_state.data_generated = True

if st.session_state.data_generated:
    if not st.session_state.access_token:
        st.warning("Please log in first.")
    else:
        with st.spinner("Fetching live option chain data from FYERS..."):
            df, gex_df, spot = fetch_fyers_iv_surface(client_id, st.session_state.access_token, symbol)
            
            if not df.empty:
                # 1. Always show your exact IV HUD Metrics at the very top
                display_iv_metrics(df, spot)
                
                # 2. Split into Two Clean Tabs (Your IV Skew + New Live GEX)
                tab1, tab2 = st.tabs(["📈 Volatility Skew (Original)", "⚡ Live Dealer GEX Levels"])
                
                with tab1:
                    st.success(f"Surface generated for {len(df)} data points.")
                    plot_iv_skew(df, spot)
                    
                with tab2:
                    display_gex_dashboard(gex_df, spot)
            else:
                st.warning("⚠️ No valid data to plot! The market is closed or FYERS is reporting '0' for all IVs.")
