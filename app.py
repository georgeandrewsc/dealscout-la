# --------------------------------------------------------------
# DealScout LA — City of Los Angeles ONLY
# FINAL: 1234 S Cochran Ave + REAL ZONING CODES + FIXED .str[0]
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import os

# --- Page Config ---
st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("DealScout LA")
st.markdown("**Upload MLS CSV → Get LA City deals with 1234 S Cochran Ave, R3/RD1.5 zoning, $/unit & map.**")

# --- Helper: Find Column ---
def find_col(df, candidates):
    cols = [c.lower() for c in df.columns]
    for cand in candidates:
        if cand.lower() in cols:
            return df.columns[cols.index(cand.lower())]
    return None

# --- Upload CSV ---
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# --- Find Required Columns ---
price_col = find_col(mls, ["CurrentPrice", "price", "ListPrice"])
lot_col   = find_col(mls, ["LotSizeSquareFeet", "lot_sqft", "LotSizeAcres"])
lat_col   = find_col(mls, ["Latitude", "lat"])
lon_col   = find_col(mls, ["Longitude", "lon"])

if not all([price_col, lot_col, lat_col, lon_col]):
    st.error("CSV must
