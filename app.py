# --------------------------------------------------------------
# DealScout LA — FINAL WORKING VERSION
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
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
price_idx = 132   # EC
lat_idx = 311     # KZ
lon_idx = 254     # IU
num_idx = 522     # TC
name_idx = 520    # TA
suffix_idx = 523  # TD
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

# --- Geometry ---
mls["geometry"] = mls.apply(lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None, axis=1)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# --- Load Zoning (WITH ERROR HANDLING) ---
@st.cache_resource
def load_zoning():
    path = "Zoning.geojson"
    if not os.path.exists(path):
        st.error(f"File `{path}` not found! Make sure it's named exactly `Zoning.geojson` (not .json)")
        st.stop()
    try:
        return gpd.read_file(path)
    except Exception as e:
        st.error(f"Error reading GeoJSON: {e}")
        st.stop()

zoning = load_zoning()
st.write("**Zoning columns:**", zoning.columns.tolist())

# Auto-select ZONE_CLASS
zone_cols = [c for c in zoning.columns if "zone" in c.lower() or "class" in c.lower()]
default = next((c for c in zone_cols if "class" in c.lower()), zone_cols[0] if zone_cols else zoning.columns[0])
zoning_field = st.selectbox("Select Zoning Field", zoning.columns, index=zoning.columns.get_loc(default))
st.success(f"Using **{zoning_field}**")

# --- Join ---
gdf = gdf.to_crs(zoning.crs)
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA")

la_city = joined[joined["Zoning"] != "Outside LA"].copy()
if la_city.empty:
    st.error("No LA City listings found.")
    st.stop()

st.write(f"**{len(la_city):,}** LA City deals")

# --- Max Units ---
sqft_map = {
    'RD1.5':1500, 'RD2':2000, 'R3':800, 'R4':400, 'R5':200,
    'R1':5000, 'R2':2500, 'C2':400, 'C1':800, 'CM':800
}
la_city["base"] = la_city["Zoning"].str.split("-").str[0].str.upper()
la_city["sqft_per"] = la_city["base"].map(sqft_map).fillna(5000)
la_city["max_units"] = (la_city["lot_sqft"] / la_city["sqft_per"]).clip(1, 20)
r1 = la_city["base"].str.startswith("R1")
la_city.loc[r1, "max_units"] = la_city.loc[r1, "lot_sqft"].apply(lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2)
la_city["price_per_unit"] = (la_city["price"] / la_city["max_units"]).round(0)

# --- Filters ---
max_ppu = st.sidebar.slider("Max $/unit", 0, 1000000, 300000, 25000)
filtered = la_city[la_city["price_per_unit"] <= max_ppu].copy()

# --- Map ---
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 200000 else "orange" if r.price_per_unit < 400000 else "red"
        folium.CircleMarker(
            [r.geometry.y, r.geometry.x], radius=6, color=color, fill=True,
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

st.success("**LIVE!** Full address + real zoning (e.g., RD1.5-1)")
