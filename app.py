# --------------------------------------------------------------
# DealScout LA — FIXED: Use Official LA City Zoning GeoJSON
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import tempfile
import os

st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("DealScout LA")
st.markdown("**Upload MLS CSV → Search for LA City Deals**")

# --- Upload CSV ---
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# --- Column Indices ---
price_idx = 132
lat_idx = 311
lon_idx = 254
num_idx = 522
name_idx = 520
suffix_idx = 523
lot_idx = mls.columns.get_loc("LotSizeSquareFeet")

# --- Clean Data ---
mls["price"] = pd.to_numeric(mls.iloc[:, price_idx], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls.iloc[:, lot_idx], errors="coerce")
if "acres" in mls.columns[lot_idx].lower():
    mls["lot_sqft"] *= 43560
mls["lat"] = pd.to_numeric(mls.iloc[:, lat_idx], errors="coerce")
mls["lon"] = pd.to_numeric(mls.iloc[:, lon_idx], errors="coerce")

# --- Full Address ---
def clean(series):
    return series.astype(str).str.strip().replace({"nan": "", "NaN": "", "None": ""}, regex=False)

num = clean(mls.iloc[:, num_idx])
name = clean(mls.iloc[:, name_idx])
suf = clean(mls.iloc[:, suffix_idx])

mls["address"] = (num + " " + name + " " + suf).str.replace(r"\s+", " ", regex=True).str.strip()
mls["address"] = mls["address"].replace("", "Unknown Address")

# --- Geometry + Buffer ---
mls["geometry"] = mls.apply(lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None, axis=1)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")
gdf["geometry"] = gdf["geometry"].buffer(0.0005)  # Increased buffer slightly to 50m for better intersection if points are off

# --- Load Zoning from Official Source ---
@st.cache_resource(show_spinner="Loading official LA City zoning GeoJSON...")
def load_zoning():
    url = "https://data.lacity.org/resource/jjxn-vhan.geojson"
    try:
        gdf = gpd.read_file(url)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        return gdf
    except Exception as e:
        st.error(f"Download failed: {e}")
        st.stop()

zoning = load_zoning()
st.write("**Zoning loaded:**", len(zoning), "polygons")

# --- AUTO-DETECT ZONING COLUMN (should find 'zone_cmplt') ---
zone_cols = [col for col in zoning.columns if any(k in col.lower() for k in ["zone", "zoning", "class"])]
if not zone_cols:
    st.error("No zoning column found! Columns: " + ", ".join(zoning.columns))
    st.stop()

zoning_field = zone_cols[0]  # Should be 'zone_cmplt'
st.success(f"Auto-detected zoning field: **{zoning_field}** → e.g., R1-1")

# --- Join ---
gdf = gdf.to_crs(zoning.crs)
joined = gpd.sjoin(gdf, zoning[[zoning_field, "geometry"]], how="left", predicate="intersects")

# --- FIX: sjoin adds "_left" suffix ---
zoning_col_in_joined = zoning_field + "_left" if zoning_field + "_left" in joined.columns else zoning_field
joined["Zoning"] = joined[zoning_col_in_joined].fillna("Outside LA")

# --- LA City Only ---
la_city = joined[joined["Zoning"] != "Outside LA"].copy()
if la_city.empty:
    st.error("No LA City listings found.")
    st.stop()

st.write(f"**{len(la_city):,}** LA City deals")

# --- YOUR ORIGINAL FULL sqft_map ---
sqft_map = {
    'CM':800, 'C1':800, 'C2':400, 'C4':400, 'C5':400,
    'RD1.5':1500, 'RD2':2000, 'R3':800, 'RAS3':800, 'R4':400, 'RAS4':400, 'R5':200,
    'RE40':40000, 'RE20':20000, 'RE15':15000, 'RE11':11000, 'RE9':9000,
    'RS':7500, 'R1':5000, 'R1V':5000, 'R1F':5000, 'R1R':5000, 'R1H':5000,
    'RU':3500, 'RZ2.5':2500, 'RZ3':3000, 'RZ4':4000, 'RW1':2300, 'R2':2500, 'RW2':2300,
    'RMP':20000, 'MR1':400, 'M1':400, 'MR2':200, 'M2':200,
    'A1':108900, 'A2':43560
}
# Extract base zoning, ignoring qualifiers like (Q), [Q], etc.
la_city["base"] = la_city["Zoning"].str.replace(r'[\[\](Q)F]', '', regex=True).str.split("-").str[0].str.upper()
la_city["sqft_per"] = la_city["base"].map(sqft_map).fillna(5000)
la_city["max_units"] = (la_city["lot_sqft"] / la_city["sqft_per"]).clip(1, 20)
r1 = la_city["base"].str.startswith("R1")
la_city.loc[r1, "max_units"] = la_city.loc[r1, "lot_sqft"].apply(lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2)
la_city["price_per_unit"] = (la_city["price"] / la_city["max_units"]).round(0)

# --- Filter ---
max_ppu = st.sidebar.slider("Max $/unit", 0, 1000000, 300000, 25000)
filtered = la_city[la_city["price_per_unit"] <= max_ppu].copy()

# --- Map ---
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 200000 else "orange" if r.price_per_unit < 400000 else "red"
        folium.CircleMarker(
            [r.geometry.centroid.y, r.geometry.centroid.x], radius=6, color=color, fill=True,
            popup=folium.Popup(
                f"<b>{r.address}</b><br>"
                f"Price: ${r.price:,.0f}<br>"
                f"$/Unit: ${r.price_per_unit:,.0f}<br>"
                f"Max: {r.max_units:.0f}<br>"
                f"Zoning: {r.Zoning}",
                max_width=300
            )
        ).add_to(m)
    st_folium(m, width=1200, height=600)

# --- Download ---
dl = filtered[["address", "price", "price_per_unit", "max_units", "Zoning"]].copy()
dl.columns = ["Address", "Price", "$/Unit", "Max Units", "Zoning"]
dl["Price"] = dl["Price"].apply(lambda x: f"${x:,.0f}")
dl["$/Unit"] = dl["$/Unit"].apply(lambda x: f"${x:,.0f}")
st.download_button("Download", dl.to_csv(index=False), "LA_Deals.csv", "text/csv")

st.success("**LIVE!** Using official LA City Zoning GeoJSON from data.lacity.org. Zoning lookup via spatial join on lat/lon points. Increased buffer for better matches. Cleaned base zoning extraction to handle qualifiers.")
