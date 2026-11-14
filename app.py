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

# ==============================================================================
# 0. PAGE CONFIGURATION & SESSION STATE INITIALIZATION
# ==============================================================================
st.set_page_config(page_title="Nifty Institutional Options Desk", layout="wide", initial_sidebar_state="expanded")
st.title("🎯 Nifty Institutional Desk: IV Skew, Live GEX & OI Radar (FYERS)")

# --- Session States for OI Dashboard (100% Intact from app (2).py) ---
if 'morning_anchor' not in st.session_state:
    st.session_state.morning_anchor = None
    
    if os.path.exists("anchor_data.csv") and os.path.exists("anchor_date.txt"):
        with open("anchor_date.txt", "r") as f:
            saved_date = f.read().strip()
            
        if saved_date == datetime.now().strftime("%Y-%m-%d"):
            st.session_state.morning_anchor = pd.read_csv("anchor_data.csv")

if 'live_df' not in st.session_state:
    st.session_state.live_df = None
if 'fyers_access_token' not in st.session_state:
    st.session_state.fyers_access_token = None
if 'fetch_now' not in st.session_state:
    st.session_state.fetch_now = False
if 'set_anchor_flag' not in st.session_state:
    st.session_state.set_anchor_flag = False
if 'data_generated' not in st.session_state:
    st.session_state.data_generated = False


# ==============================================================================
# 1. PERMANENT MEMORY STORAGE FOR IV SKEW (100% INTACT FROM app (1).py)
# ==============================================================================
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


# ==============================================================================
# 2. DATA FETCHERS (IV SURFACE + GEX FETCHER & OI RADAR FETCHER)
# ==============================================================================
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
                
                # --- A. COLLECT DATA FOR LIVE GEX ---
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
                if dte > 15 and strike % 100 != 0:
                    continue
                
                if 0 < iv <= 150 and volume > 0 and abs(strike - spot_price) <= 1500:
                    if (strike < spot_price and opt_type == 'PE') or (strike >= spot_price and opt_type == 'CE'):
                        surface_data.append({'Strike': strike, 'DTE': dte, 'IV': iv})
                        
            gex_records.extend(strike_map.values())
        time.sleep(0.3) 
        
    return pd.DataFrame(surface_data), pd.DataFrame(gex_records), spot_price


# --- YOUR EXACT OI DASHBOARD FETCHER (100% UNCHANGED FROM app (2).py) ---
@st.cache_data(ttl=60)
def fetch_fyers_oc(client_id, access_token):
    fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False, log_path="")
    
    try:
        req_data = {
            "symbol": "NSE:NIFTY50-INDEX",
            "strikecount": 15,
            "greeks": 1  
        }
        
        resp = fyers.optionchain(req_data)
        
        if resp.get('s') != 'ok' or 'data' not in resp:
            st.error(f"API Error: {resp.get('message', 'Unknown Error')}")
            return None
            
        chain_data = resp['data'].get('optionsChain', [])
        
        records = []
        for opt in chain_data:
            opt_type = opt.get('option_type')
            if opt_type not in ['CE', 'PE']:
                continue
                
            current_oi = opt.get('oi', 0)
            prev_oi = opt.get('prev_oi', current_oi)
            # Support both top-level 'iv' and nested 'greeks.iv'
            iv_val = opt.get('iv', 0) or opt.get('greeks', {}).get('iv', 0)
            
            records.append({
                'STRIKE': int(opt.get('strike_price', 0)),
                'TYPE': opt_type,
                'OI': current_oi / 100000.0,
                'CHG_OI': (current_oi - prev_oi) / 100000.0,
                'LTP': opt.get('ltp', 0),
                'IV': iv_val
            })
            
        if not records:
            st.warning("No active premium data returned.")
            return None
            
        df_raw = pd.DataFrame(records)
        ce_df = df_raw[df_raw['TYPE'] == 'CE'].rename(columns={'OI': 'CE_OI', 'CHG_OI': 'CE_CHG_OI', 'LTP': 'CE_LTP', 'IV': 'CE_IV'}).drop('TYPE', axis=1)
        pe_df = df_raw[df_raw['TYPE'] == 'PE'].rename(columns={'OI': 'PE_OI', 'CHG_OI': 'PE_CHG_OI', 'LTP': 'PE_LTP', 'IV': 'PE_IV'}).drop('TYPE', axis=1)
        
        df = pd.merge(ce_df, pe_df, on='STRIKE', how='outer').fillna(0).sort_values('STRIKE').reset_index(drop=True)
        return df
        
    except Exception as e:
        st.error(f"Fyers Option Chain Error: {e}")
        return None


# ==============================================================================
# 3. IV SKEW & HUD METRICS FUNCTIONS (100% INTACT FROM app (1).py)
# ==============================================================================
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

def display_iv_metrics(df, spot):
    morning_skew, prev_skew = get_persisted_state()

    dtes = sorted(df['DTE'].unique())
    front_dte = dtes[0]
    next_dte = dtes[1] if len(dtes) > 1 else front_dte

    front_df = df[df['DTE'] == front_dte]
    next_df = df[df['DTE'] == next_dte]

    try:
        atm_front_row = front_df.iloc[(front_df['Strike'] - spot).abs().argsort()[:1]]
        atm_iv = atm_front_row['IV'].values[0]
        atm_strike = int(atm_front_row['Strike'].values[0])

        atm_next_row = next_df.iloc[(next_df['Strike'] - spot).abs().argsort()[:1]]
        next_atm_iv = atm_next_row['IV'].values[0]
        term_diff = atm_iv - next_atm_iv

        put_target = spot - 500
        call_target = spot + 500
        put_iv = front_df.iloc[(front_df['Strike'] - put_target).abs().argsort()[:1]]['IV'].values[0]
        call_iv = front_df.iloc[(front_df['Strike'] - call_target).abs().argsort()[:1]]['IV'].values[0]
        current_skew = put_iv - call_iv

        if morning_skew is None:
            morning_skew = current_skew
            
        morning_change = current_skew - morning_skew
        
        prev_change = 0.0
        if prev_skew is not None:
            prev_change = current_skew - prev_skew
            
        save_persisted_state(morning_skew, current_skew)

        if term_diff > 1.0:
            regime = "🔴 SELL PREMIUM (Front IV High)"
            advice = f"Front DTE ({int(front_dte)}d) is +{term_diff:.1f}% overpriced. Sell Credit Spreads."
        elif term_diff < -0.5:
            regime = "🟢 BUY OPTIONS (Front IV Cheap)"
            advice = f"Front DTE ({int(front_dte)}d) is cheap ({term_diff:.1f}%). Favorable for buying ATM/ITM options."
        else:
            regime = "🟡 NEUTRAL IV (Use Vertical Spreads)"
            advice = "Term structure is flat. Use Debit or Credit Spreads based on Order Flow."

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Nifty Spot (ATM)", f"{spot:.1f}", f"ATM Strike: {atm_strike}", delta_color="off")
        col2.metric(f"Front ATM IV ({int(front_dte)}d)", f"{atm_iv:.2f}%", f"{term_diff:+.2f}% vs {int(next_dte)}d", delta_color="inverse")
        
        col3.metric(label="500-Pt Put Skew", value=f"{current_skew:+.2f}%", delta=f"{prev_change:+.2f}% (Tick)", delta_color="inverse")
        col3.caption(f"Since Morning Anchor: **{morning_change:+.2f}%**")
        
        col4.metric("IV Regime", "Sell Front" if term_diff > 1.0 else ("Buy Front" if term_diff < -0.5 else "Spreads"), f"Next ATM: {next_atm_iv:.2f}%", delta_color="off")

        st.info(f"**{regime}** — {advice}")
    except Exception as e:
        st.warning("Gathering sufficient live market data for HUD metrics...")


# ==============================================================================
# 4. LIVE DEALER GEX ENGINE & CHART (100% INTACT)
# ==============================================================================
def bs_gamma(S, K, T, r, sigma_pct):
    T_safe = np.maximum(T, 1 / (365.0 * 24.0))
    sigma = np.where((sigma_pct > 0) & (sigma_pct <= 150), sigma_pct / 100.0, 0.12)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T_safe) / (sigma * np.sqrt(T_safe))
    return norm.pdf(d1) / (S * sigma * np.sqrt(T_safe))

def find_gamma_flip(gex_df, spot, r=0.07, use_chgoi=False):
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

    if selected_exp.startswith("Front Expiry"):
        sub_df = gex_df[gex_df['Expiry'] == expiries_list[0]].copy()
    elif selected_exp == "All 5 Expiries Combined":
        sub_df = gex_df.copy()
    else:
        sub_df = gex_df[gex_df['Expiry'] == selected_exp].copy()

    use_chgoi = "Change in OI" in gex_mode
    c_oi_col = 'ce_chgoi' if use_chgoi else 'ce_oi'
    p_oi_col = 'pe_chgoi' if use_chgoi else 'pe_oi'

    r = 0.07
    sub_df['call_gamma'] = bs_gamma(spot, sub_df['Strike'], sub_df['T'], r, sub_df['ce_iv'])
    sub_df['put_gamma'] = bs_gamma(spot, sub_df['Strike'], sub_df['T'], r, sub_df['pe_iv'])
    
    spot_factor = (spot ** 2) * 0.01 / 1e7
    sub_df['call_gex'] = sub_df['call_gamma'] * sub_df[c_oi_col] * spot_factor
    sub_df['put_gex'] = -1.0 * sub_df['put_gamma'] * sub_df[p_oi_col] * spot_factor
    sub_df['net_gex'] = sub_df['call_gex'] + sub_df['put_gex']

    strike_gex = sub_df.groupby('Strike', as_index=False)[['call_gex', 'put_gex', 'net_gex']].sum()
    strike_gex = strike_gex[(strike_gex['Strike'] >= spot - 800) & (strike_gex['Strike'] <= spot + 800)]

    if strike_gex.empty:
        st.warning("No strikes within +/- 800 points of Spot.")
        return

    call_wall = int(strike_gex.loc[strike_gex['call_gex'].idxmax(), 'Strike'])
    put_wall = int(strike_gex.loc[strike_gex['put_gex'].idxmin(), 'Strike'])
    total_net_gex = strike_gex['net_gex'].sum()
    gamma_flip = find_gamma_flip(sub_df, spot, r, use_chgoi=use_chgoi)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Dealer GEX", f"₹{total_net_gex:+.1f} Cr", "Positive (Mean Revert / Pin)" if total_net_gex >= 0 else "Negative (Trend / Squeeze)", delta_color="normal" if total_net_gex >= 0 else "inverse")
    m2.metric("Zero Gamma Flip", f"{gamma_flip:.1f}" if gamma_flip else "Out of Range", "Above = Calm | Below = Volatile", delta_color="off")
    m3.metric("Call Wall (Resistance)", f"{call_wall}", f"{call_wall - spot:+.0f} pts from Spot", delta_color="off")
    m4.metric("Put Wall (Support)", f"{put_wall}", f"{put_wall - spot:+.0f} pts from Spot", delta_color="off")

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


# ==============================================================================
# 5. SIDEBAR: UNIFIED AUTHENTICATION & CONTROLS
# ==============================================================================
st.sidebar.header("1. FYERS Authentication")

client_id = st.secrets.get("FYERS_CLIENT_ID", "")
secret_key = st.secrets.get("FYERS_SECRET_KEY", "")
redirect_uri = "https://nifty-iv-surface-vdfuj8nv2kpgxfkwvk7unh.streamlit.app"

if "access_token" not in st.session_state:
    st.session_state.access_token = st.secrets.get("FYERS_ACCESS_TOKEN", "")

if client_id and secret_key:
    session_model = fyersModel.SessionModel(
        client_id=client_id, secret_key=secret_key, 
        redirect_uri=redirect_uri, response_type="code", grant_type="authorization_code"
    )
    
    if "auth_code" in st.query_params and not st.session_state.access_token:
        auth_code = st.query_params["auth_code"]
        session_model.set_token(auth_code)
        res = session_model.generate_token()
        if res.get('s') == 'ok':
            st.session_state.access_token = res['access_token']
            st.session_state.fyers_access_token = res['access_token']
            st.query_params.clear() 
            st.rerun()
        else:
            st.sidebar.error("Auto-login failed. Please try again.")

    if not st.session_state.access_token:
        auth_link = session_model.generate_authcode()
        st.sidebar.markdown(f"**[👉 1-Tap Login to FYERS]({auth_link})**")
    else:
        st.sidebar.success("✅ Connected to FYERS Live Data")
        # Automatically share token with OI Radar!
        if not st.session_state.fyers_access_token:
            st.session_state.fyers_access_token = st.session_state.access_token
        if st.sidebar.button("Logout"):
            st.session_state.access_token = ""
            st.session_state.fyers_access_token = None
            st.session_state.data_generated = False
            st.rerun()
else:
    st.sidebar.warning("Please add FYERS_CLIENT_ID and FYERS_SECRET_KEY to your Streamlit Secrets.")

# --- Optional Override for Separate OI App ID (BJ5M8T24YW-200) ---
with st.sidebar.expander("🔌 Optional: Separate OI App Credentials"):
    st.caption("Leave blank to use your main 1-Tap Login automatically, or enter your 2nd App ID if desired.")
    app_id_override = st.text_input("OI App ID", value="BJ5M8T24YW-200")
    secret_id_override = st.text_input("OI Secret ID", type="password")
    if app_id_override and secret_id_override:
        oi_session = fyersModel.SessionModel(
            client_id=app_id_override, secret_key=secret_id_override,
            redirect_uri=redirect_uri, response_type='code', grant_type='authorization_code'
        )
        st.markdown(f"**[👉 Login with 2nd App ID]({oi_session.generate_authcode()})**")

# Active Client ID & Token for OI Radar
active_oi_client_id = client_id if st.session_state.access_token else app_id_override
active_oi_token = st.session_state.access_token or st.session_state.fyers_access_token

st.sidebar.divider()
st.sidebar.header("2. Generate Surface, GEX & OI")
symbol = st.sidebar.text_input("Index Symbol", value="NSE:NIFTY50-INDEX")

if st.sidebar.button("🚀 Generate All Live Data (IV + GEX + OI)", use_container_width=True):
    st.session_state.data_generated = True
    st.session_state.fetch_now = True

st.sidebar.markdown("---")
st.sidebar.header("⏱️ OI Session Comparison")
if st.sidebar.button("📸 Set Current Data as Morning Anchor"):
    st.session_state.set_anchor_flag = True
else:
    st.session_state.set_anchor_flag = False
    
if st.session_state.morning_anchor is not None:
    st.sidebar.success("Morning anchor saved! Comparison active.")
    if st.sidebar.button("Clear Anchor"):
        st.session_state.morning_anchor = None
        if os.path.exists("anchor_data.csv"): os.remove("anchor_data.csv")
        if os.path.exists("anchor_date.txt"): os.remove("anchor_date.txt")
        st.rerun()


# ==============================================================================
# 6. MAIN DASHBOARD EXECUTION (3 TABS: IV SKEW | LIVE GEX | OI RADAR)
# ==============================================================================
if st.session_state.data_generated:
    if not active_oi_token:
        st.warning("Please log in first in the sidebar.")
    else:
        with st.spinner("Fetching live option chain data from FYERS (60s Cache Active)..."):
            # 1. Fetch IV Surface & GEX Data
            df_iv, gex_df, spot = fetch_fyers_iv_surface(client_id, st.session_state.access_token, symbol)
            
            # 2. Fetch OI Radar Data
            if st.session_state.fetch_now or st.session_state.live_df is None:
                st.session_state.live_df = fetch_fyers_oc(active_oi_client_id, active_oi_token)
                st.session_state.fetch_now = False

        if not df_iv.empty:
            # Always display Top IV HUD Metrics
            display_iv_metrics(df_iv, spot)
            
            # 3 Clean Tabs for Instant Switching
            tab1, tab2, tab3 = st.tabs([
                "📈 Volatility Skew (Original)", 
                "⚡ Live Dealer GEX Levels", 
                "🎯 Live NIFTY OI Radar"
            ])
            
            # --- TAB 1: VOLATILITY SKEW ---
            with tab1:
                st.success(f"Surface generated for {len(df_iv)} data points.")
                plot_iv_skew(df_iv, spot)
                
            # --- TAB 2: LIVE DEALER GEX ---
            with tab2:
                display_gex_dashboard(gex_df, spot)
                
            # --- TAB 3: YOUR EXACT OI DASHBOARD (100% UNCHANGED FROM app (2).py) ---
            with tab3:
                df = st.session_state.live_df

                if df is not None and not df.empty:
                    st.success("Dashboard updated. Displaying latest fetched data.")

                    # PERSISTENT ANCHOR SAVE LOGIC
                    if st.session_state.set_anchor_flag:
                        st.session_state.morning_anchor = df.copy()
                        df.to_csv("anchor_data.csv", index=False)
                        with open("anchor_date.txt", "w") as f:
                            f.write(datetime.now().strftime("%Y-%m-%d"))
                        st.session_state.set_anchor_flag = False
                        st.rerun()

                    # TRUE IMPLIED ATM & STRADDLE CALCULATIONS
                    v_ltp = df[(df['CE_LTP'] > 0) & (df['PE_LTP'] > 0)].copy()
                    
                    if v_ltp.empty:
                        st.warning("No active premium data found.")
                    else:
                        v_ltp['DIFF'] = (v_ltp['CE_LTP'] - v_ltp['PE_LTP']).abs()
                        atm_r = v_ltp.loc[v_ltp['DIFF'].idxmin()]
                        atm_strike = int(atm_r['STRIKE'])
                        
                        implied_fwd = round(atm_strike + (atm_r['CE_LTP'] - atm_r['PE_LTP']), 2)
                        straddle_prem = round(atm_r['CE_LTP'] + atm_r['PE_LTP'], 2)

                        # PCR METRICS
                        pcr_overall = df['PE_OI'].sum() / df['CE_OI'].sum() if df['CE_OI'].sum() > 0 else 0
                        df_300 = df[(df['STRIKE'] >= atm_strike - 300) & (df['STRIKE'] <= atm_strike + 300)].copy()
                        pcr_300 = df_300['PE_OI'].sum() / df_300['CE_OI'].sum() if df_300['CE_OI'].sum() > 0 else 0
                        
                        pe_rupee = (df['PE_OI'] * df['PE_LTP']).sum()
                        ce_rupee = (df['CE_OI'] * df['CE_LTP']).sum()
                        pcr_rupee = pe_rupee / ce_rupee if ce_rupee > 0 else 0

                        st.subheader("📊 Key Market Levels & PCR")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("ATM Strike (Implied)", atm_strike)
                        c2.metric("Implied Forward", implied_fwd)
                        c3.metric("Straddle Premium", f"₹ {straddle_prem}")
                        c4.metric("PCR (Overall)", round(pcr_overall, 2))
                        
                        c5, c6, c7, c8 = st.columns(4)
                        c5.metric("PCR (± 300 Pts)", round(pcr_300, 2))
                        c6.metric("PCR (Rupee Wtd)", round(pcr_rupee, 2))
                        c7.metric("Macro Call Ceiling", int(df.loc[df['CE_OI'].idxmax(), 'STRIKE']))
                        c8.metric("Macro Put Floor", int(df.loc[df['PE_OI'].idxmax(), 'STRIKE']))

                        # CHART 1: TUG OF WAR (TOTAL OI)
                        st.subheader("⚔️ Tug-of-War OI Radar (±300 Pts)")
                        fig1 = go.Figure()
                        fig1.add_trace(go.Bar(
                            y=df_300['STRIKE'], x=-df_300['CE_OI'],
                            orientation='h', name='🔴 Call OI', marker_color='#ef4444',
                            text=[f"<b>{oi:.2f}L</b>" for oi in df_300['CE_OI']], textposition='outside'
                        ))
                        fig1.add_trace(go.Bar(
                            y=df_300['STRIKE'], x=df_300['PE_OI'],
                            orientation='h', name='🟢 Put OI', marker_color='#22c55e',
                            text=[f"<b>{oi:.2f}L</b>" for oi in df_300['PE_OI']], textposition='outside'
                        ))
                        fig1.add_hline(y=atm_strike, line_dash="dash", line_color="#facc15")
                        fig1.add_vline(x=0, line_width=2, line_color="#ffffff")
                        max_side1 = max(df_300['CE_OI'].max(), df_300['PE_OI'].max()) * 1.45 if not df_300.empty else 1
                        fig1.update_layout(barmode='overlay', height=600, template="plotly_dark", xaxis=dict(range=[-max_side1, max_side1], title="Calls ◄ | ► Puts"))
                        st.plotly_chart(fig1, use_container_width=True)

                        # CHART 2: ACTION BOARD (INTRADAY CHG IN OI)
                        st.subheader("📋 Today's Change in OI (Action Board)")
                        board = df_300.copy()
                        board['NET_FLOW'] = board['PE_CHG_OI'] - board['CE_CHG_OI']
                        
                        word_labels = []
                        for _, r in board.iterrows():
                            net = r['NET_FLOW']
                            if net >= 0.35: action = "🟢 STRONG PUT FLOOR"
                            elif net >= 0.12: action = "🟢 Mild Put Support"
                            elif net <= -0.35: action = "🔴 HEAVY CALL CEILING"
                            elif net <= -0.12: action = "🔴 Mild Call Resist"
                            else: action = "⚪ Balanced Flow"
                            word_labels.append(f"<b>{net:+.2f}L</b> → {action}")

                        bar_colors = ['#22c55e' if v >= 0 else '#ef4444' for v in board['NET_FLOW']]
                        fig2 = go.Figure()
                        fig2.add_trace(go.Bar(
                            y=board['STRIKE'], x=board['NET_FLOW'], orientation='h',
                            marker_color=bar_colors, text=word_labels, textposition='outside'
                        ))
                        fig2.add_hline(y=atm_strike, line_dash="dash", line_color="#facc15")
                        max_x2 = max(board['NET_FLOW'].abs().max(), 0.2) * 2.35 if not board.empty else 1
                        fig2.update_layout(height=600, template="plotly_dark", xaxis=dict(range=[-max_x2, max_x2], title="Bearish Flow ◄ | ► Bullish Flow"))
                        st.plotly_chart(fig2, use_container_width=True)
                        
                        # CHART 3: PURE MIDDAY SESSION COMPARISON
                        if st.session_state.morning_anchor is not None:
                            st.markdown("---")
                            st.subheader("⏱️ Session Comparison (Anchor vs Live)")
                            
                            df_am = st.session_state.morning_anchor
                            merged = pd.merge(
                                df_am[['STRIKE', 'CE_OI', 'PE_OI']], df[['STRIKE', 'CE_OI', 'PE_OI']],
                                on='STRIKE', suffixes=('_AM', '_PM')
                            )
                            merged['CE_DELTA'] = merged['CE_OI_PM'] - merged['CE_OI_AM']
                            merged['PE_DELTA'] = merged['PE_OI_PM'] - merged['PE_OI_AM']
                            merged['NET_DELTA'] = merged['PE_DELTA'] - merged['CE_DELTA']
                            
                            m_view = merged[(merged['STRIKE'] >= atm_strike - 300) & (merged['STRIKE'] <= atm_strike + 300)]
                            
                            labels = []
                            for _, r in m_view.iterrows():
                                net_d = r['NET_DELTA']
                                if net_d >= 0.10: text = "Net Put Built"
                                elif net_d <= -0.10: text = "Net Call Built"
                                else: text = "Minimal Change"
                                labels.append(f"<b>{net_d:+.2f}L</b> → {text}")

                            colors3 = ['#22c55e' if v >= 0 else '#ef4444' for v in m_view['NET_DELTA']]
                            fig3 = go.Figure()
                            fig3.add_trace(go.Bar(
                                y=m_view['STRIKE'], x=m_view['NET_DELTA'], orientation='h',
                                marker_color=colors3, text=labels, textposition='outside'
                            ))
                            fig3.add_hline(y=atm_strike, line_dash="dash", line_color="#facc15")
                            max_x3 = max(m_view['NET_DELTA'].abs().max(), 0.15) * 2.10 if not m_view.empty else 1
                            fig3.update_layout(height=600, template="plotly_dark", xaxis=dict(range=[-max_x3, max_x3], title="Net Call Add ◄ | ► Net Put Add"))
                            st.plotly_chart(fig3, use_container_width=True)

                        # CHART 4: VOLATILITY SMILE (IV SKEW)
                        st.markdown("---")
                        st.subheader("📈 Implied Volatility (IV) Skew")
                        iv_data = df_300[(df_300['CE_IV'] > 0) & (df_300['PE_IV'] > 0)]
                        
                        fig4 = go.Figure()
                        fig4.add_trace(go.Scatter(
                            x=iv_data['STRIKE'], y=iv_data['CE_IV'], 
                            mode='lines+markers', name='Call IV', line=dict(color='#ef4444', width=2)
                        ))
                        fig4.add_trace(go.Scatter(
                            x=iv_data['STRIKE'], y=iv_data['PE_IV'], 
                            mode='lines+markers', name='Put IV', line=dict(color='#22c55e', width=2)
                        ))
                        fig4.add_vline(x=atm_strike, line_dash="dash", line_color="#facc15", annotation_text="ATM")
                        fig4.update_layout(height=400, template="plotly_dark", xaxis_title="Strike Price", yaxis_title="Implied Volatility (%)", hovermode="x unified")
                        st.plotly_chart(fig4, use_container_width=True)
                else:
                    st.warning("No OI Radar data available.")
        else:
            st.warning("⚠️ No valid data to plot! The market is closed or FYERS is reporting '0' for all IVs.")
else:
    st.info("👈 Connect to FYERS in the sidebar and click **'🚀 Generate All Live Data (IV + GEX + OI)'**.")
