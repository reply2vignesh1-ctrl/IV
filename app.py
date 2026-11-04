import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.interpolate import griddata
from datetime import datetime
import time
from fyers_apiv3 import fyersModel

st.set_page_config(page_title="Nifty 3D IV Surface", layout="wide")
st.title("Nifty Implied Volatility Surface (FYERS API)")

# --- 1. PLOTTING FUNCTION ---
def plot_iv_surface(df):
    if df.empty or not {'Strike', 'DTE', 'IV'}.issubset(df.columns):
        st.error("Data is empty or missing required columns.")
        return

    x, y, z = df['Strike'].values, df['DTE'].values, df['IV'].values
    xi, yi = np.linspace(x.min(), x.max(), 50), np.linspace(y.min(), y.max(), 50)
    X, Y = np.meshgrid(xi, yi)

    try:
        Z = griddata((x, y), z, (X, Y), method='cubic')
    except Exception as e:
        st.error(f"Interpolation error: {e}")
        return

    fig = go.Figure(data=[go.Surface(z=Z, x=X, y=Y, colorscale='Viridis', colorbar_title='Implied Volatility (%)')])
    fig.update_layout(
        title="3D Implied Volatility Surface",
        scene=dict(xaxis_title='Strike Price', yaxis_title='Days to Expiry (DTE)', zaxis_title='Implied Volatility (%)', camera=dict(eye=dict(x=1.5, y=-1.5, z=1.2))),
        height=750, margin=dict(l=0, r=0, b=0, t=40)
    )
    st.plotly_chart(fig, use_container_width=True)


# --- 2. FYERS DATA FETCHER ---
@st.cache_data(ttl=300)
def fetch_fyers_iv_surface(client_id, access_token, symbol="NSE:NIFTY50-INDEX", strikecount=50):
    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
    response = fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "greeks": 1})
    
    if response.get('s') != 'ok':
        st.error(f"Fyers API Error: {response.get('message', 'Unknown Error')}")
        return pd.DataFrame()
        
    spot_price = response['data']['optionsChain'][0]['ltp']
    expiries = response['data']['expiryData']
    surface_data, today = [], datetime.now()
    
    for exp in expiries[:5]:
        # FIX: Force the timestamp text into a math-friendly integer
        exp_timestamp = int(exp['expiry'])
        
        dte = max((datetime.fromtimestamp(exp_timestamp) - today).days, 0.01)
        chain_res = fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "greeks": 1, "timestamp": str(exp_timestamp)})
        
        if chain_res.get('s') == 'ok':
            for item in chain_res['data']['optionsChain']:
                if item.get('strike_price') == -1: continue 
                strike, opt_type, iv = item['strike_price'], item['option_type'], item.get('greeks', {}).get('iv', 0)
                if iv > 0 and ((strike < spot_price and opt_type == 'PE') or (strike >= spot_price and opt_type == 'CE')):
                    surface_data.append({'Strike': strike, 'DTE': dte, 'IV': iv * 100})
        time.sleep(0.3) 
    return pd.DataFrame(surface_data)


# --- 3. STREAMLIT UI & OAUTH LOGIN ---
st.sidebar.header("1. FYERS Authentication")

client_id = st.secrets.get("FYERS_CLIENT_ID", "")
secret_key = st.secrets.get("FYERS_SECRET_KEY", "")
redirect_uri = "https://127.0.0.1"

# Initialize session state for the token
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
            df = fetch_fyers_iv_surface(client_id, st.session_state.access_token, symbol)
            if not df.empty:
                st.success(f"Surface generated for {len(df)} data points.")
                plot_iv_surface(df)
