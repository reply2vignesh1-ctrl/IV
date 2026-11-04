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

    # Extract X (Strike), Y (DTE), and Z (IV)
    x = df['Strike'].values
    y = df['DTE'].values
    z = df['IV'].values

    # Create a dense grid for smooth 3D plotting
    xi = np.linspace(x.min(), x.max(), 50)
    yi = np.linspace(y.min(), y.max(), 50)
    X, Y = np.meshgrid(xi, yi)

    # Interpolate Z values over the grid (Cubic smoothing)
    try:
        Z = griddata((x, y), z, (X, Y), method='cubic')
    except Exception as e:
        st.error(f"Interpolation error (not enough data points): {e}")
        return

    # Create the 3D Surface Plot
    fig = go.Figure(data=[go.Surface(
        z=Z, x=X, y=Y, 
        colorscale='Viridis', 
        colorbar_title='Implied Volatility (%)'
    )])

    fig.update_layout(
        title="3D Implied Volatility Surface",
        scene=dict(
            xaxis_title='Strike Price',
            yaxis_title='Days to Expiry (DTE)',
            zaxis_title='Implied Volatility (%)',
            camera=dict(eye=dict(x=1.5, y=-1.5, z=1.2))
        ),
        height=750,
        margin=dict(l=0, r=0, b=0, t=40)
    )

    st.plotly_chart(fig, use_container_width=True)

# --- 2. FYERS DATA FETCHER ---
@st.cache_data(ttl=300) # Caches data for 5 minutes so you don't spam the API on every UI click
def fetch_fyers_iv_surface(client_id, access_token, symbol="NSE:NIFTY50-INDEX", strikecount=50):
    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
    
    initial_data = {"symbol": symbol, "strikecount": strikecount, "greeks": 1}
    response = fyers.optionchain(data=initial_data)
    
    if response['s'] != 'ok':
        st.error(f"Fyers API Error: {response.get('message', 'Unknown Error')}")
        return pd.DataFrame()
        
    spot_price = response['optionsChain'][0]['ltp']
    expiries = response['expiryData']
    
    surface_data = []
    today = datetime.now()
    
    # Loop through nearest 5 expirations to build the Time (DTE) dimension
    for exp in expiries[:5]:
        exp_timestamp = exp['expiry']
        dte = max((datetime.fromtimestamp(exp_timestamp) - today).days, 0.01)
        
        chain_req = {"symbol": symbol, "strikecount": strikecount, "greeks": 1, "timestamp": str(exp_timestamp)}
        chain_res = fyers.optionchain(data=chain_req)
        
        if chain_res['s'] == 'ok':
            for item in chain_res['optionsChain']:
                if item.get('strike_price') == -1:
                    continue 
                
                strike = item['strike_price']
                opt_type = item['option_type']
                iv = item.get('greeks', {}).get('iv', 0)
                
                # Filter for OTM Puts and OTM Calls to build the true institutional Skew
                if iv > 0:
                    if (strike < spot_price and opt_type == 'PE') or (strike >= spot_price and opt_type == 'CE'):
                        surface_data.append({
                            'Strike': strike,
                            'DTE': dte,
                            'IV': iv * 100
                        })
        time.sleep(0.3) 
        
    return pd.DataFrame(surface_data)

# --- 3. STREAMLIT UI & EXECUTION ---
st.sidebar.header("API Credentials")
st.sidebar.info("By default, this app attempts to pull credentials from Streamlit Secrets.")

# Safely check for Streamlit Secrets, fallback to manual input if missing
default_client = st.secrets["FYERS_CLIENT_ID"] if "FYERS_CLIENT_ID" in st.secrets else ""
default_token = st.secrets["FYERS_ACCESS_TOKEN"] if "FYERS_ACCESS_TOKEN" in st.secrets else ""

client_id = st.sidebar.text_input("FYERS Client ID", value=default_client, type="password")
access_token = st.sidebar.text_input("FYERS Access Token", value=default_token, type="password")
symbol = st.sidebar.text_input("Index Symbol", value="NSE:NIFTY50-INDEX")

if st.sidebar.button("Generate IV Surface"):
    if not client_id or not access_token:
        st.warning("Please provide both your FYERS Client ID and Access Token.")
    else:
        with st.spinner("Fetching live option chain data from FYERS..."):
            df = fetch_fyers_iv_surface(client_id, access_token, symbol)
            if not df.empty:
                st.success(f"Data fetched successfully! Generating surface for {len(df)} data points.")
                plot_iv_surface(df)
            else:
                st.error("Failed to retrieve valid data for the surface.")
