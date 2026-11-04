import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import time
from fyers_apiv3 import fyersModel

st.set_page_config(page_title="Nifty 2D Volatility Skew", layout="wide")
st.title("Nifty Volatility Skew (FYERS API)")

# --- 1. PLOTTING FUNCTION (2D SKEW WITH SPOT LINE) ---
def plot_iv_skew(df, spot_price):
    if df.empty or not {'Strike', 'DTE', 'IV'}.issubset(df.columns):
        st.error("Data is empty or missing required columns.")
        return

    df = df.sort_values(by=['DTE', 'Strike'])
    df['Expiry'] = df['DTE'].apply(lambda x: f"{int(x)} Days" if x >= 1 else "0 Days (Today)")

    fig = px.line(
        df, x='Strike', y='IV', color='Expiry', markers=True,
        title=f"Nifty Volatility Skew",
        labels={'IV': 'Implied Volatility (%)', 'Strike': 'Strike Price'}
    )
    
    # NEW: Add a vertical dashed line for the Spot Price
    fig.add_vline(x=spot_price, line_dash="dash", line_color="red", 
                  annotation_text=f"Live Spot: {spot_price}", annotation_position="top left")
    
    fig.update_layout(
        height=600, 
        hovermode="x unified",
        legend_title_text="Time to Expiration",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

# --- 2. FYERS DATA FETCHER ---
@st.cache_data(ttl=300)
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
                
                iv_pct = iv * 100
                if 0 < iv_pct <= 150 and abs(strike - spot_price) <= 1500:
                    if (strike < spot_price and opt_type == 'PE') or (strike >= spot_price and opt_type == 'CE'):
                        surface_data.append({'Strike': strike, 'DTE': dte, 'IV': iv_pct})
        time.sleep(0.3) 
    # Return both the dataframe and the spot price so the chart can draw the line
    return pd.DataFrame(surface_data), spot_price

# --- 3. STREAMLIT UI & OAUTH LOGIN ---
st.sidebar.header("1. FYERS Authentication")

client_id = st.secrets.get("FYERS_CLIENT_ID", "")
secret_key = st.secrets.get("FYERS_SECRET_KEY", "")
redirect_uri = "https://127.0.0.1"

if "access_token" not in st.session_state:
    st.session_state.access_token = st.secrets.get("FYERS_ACCESS_TOKEN", "")

if client_id and secret_key:
    session_model = fyersModel.SessionModel(client_id=client_id, secret_key=secret_key, redirect_uri=redirect_uri, response_type="code", grant_type="authorization_code")
    auth_link = session_model.generate_authcode()
    
    st.sidebar.markdown(f"**[👉 Click Here to Login to FYERS]({auth_link})**")
    st.sidebar.caption("After logging in, copy the 'auth_code' from the blank page's URL bar.")
    
    auth_code = st.sidebar.text_input("Paste auth_code here:")
    if st.sidebar.button("Set Daily Token"):
        session_model.set_token(auth_code)
        res = session_model.generate_token()
        if res.get('s') == 'ok':
            st.session_state.access_token = res['access_token']
            st.sidebar.success("Token generated successfully for today!")
        else:
            st.sidebar.error(f"Error: {res}")
else:
    st.sidebar.warning("Please add FYERS_CLIENT_ID and FYERS_SECRET_KEY to your Streamlit Secrets.")

st.sidebar.divider()
st.sidebar.header("2. Generate Surface")
symbol = st.sidebar.text_input("Index Symbol", value="NSE:NIFTY50-INDEX")

if st.sidebar.button("Generate IV Surface"):
    if not st.session_state.access_token:
        st.warning("Please log in and generate your token first.")
    else:
        with st.spinner("Fetching live option chain data from FYERS..."):
            df, spot = fetch_fyers_iv_surface(client_id, st.session_state.access_token, symbol)
            
            # Now it will tell you if the data is empty instead of going blank
            if not df.empty:
                st.success(f"Surface generated for {len(df)} data points.")
                plot_iv_skew(df, spot)
            else:
                st.warning("⚠️ No valid data to plot! The market is closed and FYERS is currently reporting '0' for all IVs.")

