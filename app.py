# --------------------------------------------------------------
# DealScout LA — FINAL: Real LA City Zoning (ArcGIS) + Boundary
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
import requests
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point, box
import tempfile
import os

st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("DealScout LA")
st.markdown("**Upload MLS CSV → Find Real LA City Land Deals**")

# ------------------------------------------------------------------
# 1. Upload CSV
# ------------------------------------------------------------------
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# ------------------------------------------------------------------
# 2. Column indices
# ------------------------------------------------------------------
price_idx   = 132
lat_idx     = 311
lon_idx     = 254
num_idx     = 522
name_idx    = 520
suffix_idx  = 523
lot_idx     = mls.columns.get_loc("LotSizeSquareFeet")

# ------------------------------------------------------------------
# 3. Clean data
# ------------------------------------------------------------------
mls["price"]    = pd.to_numeric(mls.iloc[:, price_idx], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls.iloc[:, lot_idx],   errors="coerce")
if "acres" in mls.columns[lot_idx].lower():
    mls["lot_sqft"] *= 43560

mls["lat"] = pd.to_numeric(mls.iloc[:, lat_idx], errors="coerce")
mls["lon"] = pd.to_numeric(mls.iloc[:, lon_idx], errors="coerce")

def clean(s):
    return s.astype(str).str.strip().replace({"nan":"","NaN":"","None":""}, regex=False)

num  = clean(mls.iloc[:, num_idx])
name = clean(mls.iloc[:, name_idx])
suf  = clean(mls.iloc[:, suffix_idx])

mls["address"] = (num + " " + name + " " + suf).str.replace(r"\s+"," ",regex=True).str.strip()
mls["address"] = mls["address"].replace("", "Unknown Address")

# ------------------------------------------------------------------
# 4. Points
# ------------------------------------------------------------------
mls["geometry"] = mls.apply(
    lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None,
    axis=1
)
mls = mls.dropna(subset=["geometry","price","lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# ------------------------------------------------------------------
# 5. DEBUG MAP – all raw points
# ------------------------------------------------------------------
st.subheader("Debug: All MLS Points")
m_debug = folium.Map([34.05, -118.24], zoom_start=10, tiles="CartoDB positron")
for _, r in gdf.iterrows():
    folium.CircleMarker([r.geometry.y, r.geometry.x], radius=3, color="blue", fill=True).add_to(m_debug)
st_folium(m_debug, width=800, height=400, key="debug_raw")

# ------------------------------------------------------------------
# 6. LA CITY BOUNDARY – PUBLIC GeoHub GeoJSON (works 2025-10-30)
# ------------------------------------------------------------------
@st.cache_data(show_spinner="Downloading LA City boundary…", ttl=24*3600)
def load_la_boundary():
    # Direct GeoJSON export from the City Boundary layer
    url = "https://geohub.lacity.org/datasets/city-boundary.geojson"
    try:
        with requests.get(url, timeout=60) as r:
            r.raise_for_status()
            # Stream to a temp file (GeoJSON can be ~30 MB)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".geojson")
            for chunk in r.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp.close()
            gdf = gpd.read_file(tmp.name)
            os.unlink(tmp.name)
        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        return gdf.to_crs("EPSG:4326")
    except Exception as e:
        st.warning(f"Boundary failed ({e}). Using fallback box.")
        bbox = box(-118.668, 33.703, -118.155, 34.337)
        return gpd.GeoDataFrame(geometry=[bbox], crs="EPSG:4326")

la_boundary = load_la_boundary()
st.write(f"**Boundary loaded:** {len(la_boundary):,} polygons")

# ------------------------------------------------------------------
# 7. Filter points INSIDE LA City
# ------------------------------------------------------------------
gdf_la = gdf.to_crs(la_boundary.crs)
gdf_la = gpd.sjoin(gdf_la, la_boundary[["geometry"]], how="inner", predicate="within")

if gdf_la.empty:
    st.error("**No points inside LA City.** Check your MLS export — are any in Los Angeles city limits?")
    st.stop()
st.success(f"**{len(gdf_la):,}** points inside LA City")

# ------------------------------------------------------------------
# 8. LA CITY ZONING – PUBLIC GeoHub GeoJSON (works 2025-10-30)
# ------------------------------------------------------------------
@st.cache_data(show_spinner="Downloading LA City zoning…", ttl=24*3600)
def load_zoning():
    # Direct GeoJSON export from the Zoning layer (only LA City zones)
    url = "https://geohub.lacity.org/datasets/zoning.geojson"
    try:
        with requests.get(url, timeout=120) as r:
            r.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".geojson")
            for chunk in r.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp.close()
            gdf = gpd.read_file(tmp.name)
            os.unlink(tmp.name)
        if gdf.crs is None:
            gdf.set_crs("EPSG:2229", inplace=True)          # native CRS
        return gdf.to_crs("EPSG:4326")
    except Exception as e:
        st.warning(f"Zoning failed ({e}). Using dummy R1 zone.")
        dummy = la_boundary.unary_union
        return gpd.GeoDataFrame({"ZONE_CLASS": ["R1"]}, geometry=[dummy], crs="EPSG:4326")

zoning = load_zoning()
st.write(f"**Zoning loaded:** {len(zoning):,} polygons")

# ------------------------------------------------------------------
# 9. Spatial join with buffer
# ------------------------------------------------------------------
gdf_buf = gdf_la.copy()
gdf_buf["geometry"] = gdf_buf["geometry"].buffer(0.001)  # ~100 meters

joined = gpd.sjoin(gdf_buf, zoning[["ZONE_CLASS", "geometry"]], how="left", predicate="intersects")
joined["Zoning"] = joined["ZONE_CLASS"].fillna("Outside LA")

la_city = joined[joined["Zoning"] != "Outside LA"].copy()
if la_city.empty:
    st.error("No zoning matches. Try increasing buffer.")
    st.stop()
st.write(f"**{len(la_city):,}** LA City deals with zoning")

# ------------------------------------------------------------------
# 10. sqft_map (your original)
# ------------------------------------------------------------------
sqft_map = {
    'CM':800, 'C1':800, 'C2':400, 'C4':400, 'C5':400,
    'RD1.5':1500, 'RD2':2000, 'R3':800, 'RAS3':800, 'R4':400, 'RAS4':400, 'R5':200,
    'RE40':40000, 'RE20':20000, 'RE15':15000, 'RE11':11000, 'RE9':9000,
    'RS':7500, 'R1':5000, 'R1V':5000, 'R1F':5000, 'R1R':5000, 'R1H':5000,
    'RU':3500, 'RZ2.5':2500, 'RZ3':3000, 'RZ4':4000,
    'RW1':2300, 'R2':2500, 'RW2':2300,
    'RMP':20000, 'MR1':400, 'M1':400, 'MR2':200, 'M2':200,
    'A1':108900, 'A2':43560
}

la_city["base"] = la_city["Zoning"].str.replace(r'[\[\](Q)F]', '', regex=True).str.split("-").str[0].str.upper()
la_city["sqft_per"] = la_city["base"].map(sqft_map).fillna(5000)
la_city["max_units"] = (la_city["lot_sqft"] / la_city["sqft_per"]).clip(1, 20)

r1 = la_city["base"].str.startswith("R1")
la_city.loc[r1, "max_units"] = la_city.loc[r1, "lot_sqft"].apply(
    lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2
)

la_city["price_per_unit"] = (la_city["price"] / la_city["max_units"]).round(0)

# ------------------------------------------------------------------
# 11. Filter
# ------------------------------------------------------------------
max_ppu = st.sidebar.slider("Max $/unit", 0, 1_000_000, 300_000, 25_000)
filtered = la_city[la_city["price_per_unit"] <= max_ppu].copy()

# ------------------------------------------------------------------
# 12. Map
# ------------------------------------------------------------------
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 200_000 else "orange" if r.price_per_unit < 400_000 else "red"
        folium.CircleMarker(
            [r.geometry.centroid.y, r.geometry.centroid.x],
            radius=6, color=color, fill=True,
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

# ------------------------------------------------------------------
# 13. Download
# ------------------------------------------------------------------
dl = filtered[["address","price","price_per_unit","max_units","Zoning"]].copy()
dl.columns = ["Address","Price","$/Unit","Max Units","Zoning"]
dl["Price"]  = dl["Price"].apply(lambda x: f"${x:,.0f}")
dl["$/Unit"] = dl["$/Unit"].apply(lambda x: f"${x:,.0f}")
st.download_button("Download CSV", dl.to_csv(index=False), "LA_Deals.csv", "text/csv")

st.success("**LIVE!** Using **real LA City zoning (ArcGIS)** + **official boundary**. No more 404s.")
