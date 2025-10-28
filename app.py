import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import numpy as np
import folium
from streamlit_folium import st_folium
import warnings
warnings.filterwarnings("ignore")

st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("🏠 DealScout LA")
st.markdown("Upload MLS CSV → Get zoning, $/unit, SB-9, map, and download.")

# Sidebar
st.sidebar.header("Filters")
max_price = st.sidebar.slider("Max $/unit", 0, 10000, 5000)
zone_filter = st.sidebar.multiselect("Zoning", ["RE40", "R1", "RD", "R3", "All"], default="All")

# Upload
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if uploaded:
    mls = pd.read_csv(uploaded)
    st.write(f"Loaded {len(mls)} listings")

    # Clean
    mls['price'] = pd.to_numeric(mls.get('CurrentPrice', mls.get('price', None)), errors='coerce')
    mls['lot_sqft'] = pd.to_numeric(mls.get('LotSizeSquareFeet', mls.get('lot_sqft', None)), errors='coerce')
    mls['latitude'] = pd.to_numeric(mls['Latitude'], errors='coerce')
    mls['longitude'] = pd.to_numeric(mls['Longitude'], errors='coerce')
    mls['streetnumber'] = mls.get('StreetNumber', '').astype(str)
    mls['streetdirection'] = mls.get('StreetDirPrefix', '').astype(str)
    mls['streetdirsuffix'] = mls.get('StreetDirSuffix', '').astype(str)
    mls['streetname'] = mls.get('StreetName', '').astype(str)
    mls['streetsuffix'] = mls.get('StreetSuffix', '').astype(str)

    mls['address'] = (
        mls['streetnumber'] + " " +
        mls['streetdirection'] + " " +
        mls['streetname'] + " " +
        mls['streetdirsuffix'] + " " +
        mls['streetsuffix']
    ).str.strip().str.replace(r'\s+', ' ', regex=True)

    mls['geometry'] = mls.apply(
        lambda r: Point(r.longitude, r.latitude) if pd.notnull(r.longitude) and pd.notnull(r.latitude) else None,
        axis=1
    )
    mls = mls.dropna(subset=['geometry', 'price', 'lot_sqft'])
    gdf = gpd.GeoDataFrame(mls, geometry='geometry', crs="EPSG:4326")

    # Join with zoning
try:
    zoning = gpd.read_file("Zoning.geojson").to_crs("EPSG:4326")
    joined = gpd.sjoin(gdf_mls, zoning, how="left", predicate="within")
    
    # Find the actual zoning code column (common names)
    possible_cols = ['ZONE_CLASS', 'ZONING', 'ZONE', 'LAND_USE', 'ZONECODE', 'ZONE_CODE']
    zoning_field = None
    for col in possible_cols:
        if col in zoning.columns:
            zoning_field = col
            break
    
    if zoning_field is None:
        st.error("No zoning code column found in Zoning.geojson. Expected one of: " + ", ".join(possible_cols))
        st.stop()
    
    st.write(f"Using zoning field: **{zoning_field}**")
    
    # Use the correct column
    joined['Zoning'] = joined[zoning_field].fillna("Outside LA")
    joined['zone_code'] = joined['Zoning'].str.split('-').str[0].str.upper()
except Exception as e:
    st.error(f"Zoning error: {e}")
    st.stop()

