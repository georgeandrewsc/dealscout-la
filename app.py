# --------------------------------------------------------------
# app.py — DealScout LA (100% Reliable Zoning + Scalable)
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
import requests
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point
from folium.plugins import MarkerCluster
import os

st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("DealScout LA")
st.markdown("**Upload MLS CSV → Real LA City Zoning Deals**")

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
# 2. Column indices (adjust if your CSV changes)
# ------------------------------------------------------------------
price_idx = 132
lat_idx  = 311
lon_idx  = 254
num_idx  = 522
name_idx = 520
suffix_idx = 523
lot_idx  = mls.columns.get_loc("LotSizeSquareFeet")

# ------------------------------------------------------------------
# 3. Clean data (keep only needed columns early)
# ------------------------------------------------------------------
cols_to_keep = [
    mls.columns[price_idx], mls.columns[lat_idx], mls.columns[lon_idx],
    mls.columns[num_idx], mls.columns[name_idx], mls.columns[suffix_idx],
    mls.columns[lot_idx]
]
mls = mls[cols_to_keep].copy()

mls["price"] = pd.to_numeric(mls.iloc[:, 0], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls.iloc[:, 6], errors="coerce")
if "acres" in mls.columns[6].lower():
    mls["lot_sqft"] *= 43560

mls["lat"] = pd.to_numeric(mls.iloc[:, 1], errors="coerce")
mls["lon"] = pd.to_numeric(mls.iloc[:, 2], errors="coerce")

def clean(s):
    return s.astype(str).str.strip().replace({"nan":"","NaN":"","None":""}, regex=False)

mls["address"] = (
    clean(mls.iloc[:, 3]) + " " +
    clean(mls.iloc[:, 4]) + " " +
    clean(mls.iloc[:, 5])
).str.replace(r"\s+", " ", regex=True).str.strip()
mls["address"] = mls["address"].replace("", "Unknown Address")

# Drop rows missing core data
mls = mls.dropna(subset=["price", "lot_sqft", "lat", "lon", "address"])

# ------------------------------------------------------------------
# 4. Points → GeoDataFrame
# ------------------------------------------------------------------
mls["geometry"] = mls.apply(
    lambda r: Point(r.lon, r.lat), axis=1
)
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# ------------------------------------------------------------------
# 5. LA CITY BOUNDARY
# ------------------------------------------------------------------
@st.cache_data
def load_la_city_boundary():
    url = "https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/los-angeles.geojson"
    gdf = gpd.read_file(url)
    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)
    return gdf.to_crs("EPSG:4326")

la_boundary = load_la_city_boundary()
st.write(f"**LA City boundary loaded:** {len(la_boundary)} polygon(s)")

gdf_city = gpd.sjoin(gdf, la_boundary, how="inner", predicate="within")
if gdf_city.empty:
    st.error("**No MLS points inside LA City.** Your CSV is likely LA County.")
    st.stop()
st.success(f"**{len(gdf_city):,}** points inside **LA City**")

# ------------------------------------------------------------------
# 6. REAL LA CITY ZONING (cached)
# ------------------------------------------------------------------
@st.cache_data(show_spinner="Downloading LA City zoning (440 MB)…", ttl=24*3600)
def load_zoning():
    url = "https://github.com/georgeandrewsc/dealscout-la/releases/download/v1.0-zoning/Zoning.geojson"
    cache_file = "zoning_cache.geojson"
    if os.path.exists(cache_file):
        try:
            gdf = gpd.read_file(cache_file)
            st.write("**Using cached zoning file**")
            return _fix_zoning_gdf(gdf)
        except Exception as e:
            st.warning(f"Cache corrupt ({e}), redownloading...")
    try:
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            with st.spinner(f"Downloading {total/1e6:.1f} MB…"):
                with open(cache_file, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
        gdf = gpd.read_file(cache_file)
        return _fix_zoning_gdf(gdf)
    except Exception as e:
        st.error(f"Failed to load zoning: {e}")
        st.stop()

def _fix_zoning_gdf(gdf):
    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)
    gdf = gdf.to_crs("EPSG:4326")
    cols = [c for c in gdf.columns if c.lower() in ['zone_class','zoning','zone','class','zonecode']]
    if not cols:
        st.error(f"No zoning column! Columns: {list(gdf.columns)}")
        st.stop()
    zone_col = cols[0]
    st.write(f"**Using zoning column:** `{zone_col}`")
    gdf["ZONE_CLASS"] = gdf[zone_col].astype(str)
    return gdf[["ZONE_CLASS", "geometry"]].copy()

zoning = load_zoning()
st.success(f"**REAL Zoning loaded:** {len(zoning):,} polygons")

# ------------------------------------------------------------------
# 7. JOIN: Buffer + Nearest Fallback (100% Success)
# ------------------------------------------------------------------
BUFFER_FEET = 3
gdf_city_buf = gdf_city.copy()
gdf_city_buf["geometry"] = gdf_city_buf["geometry"].buffer(BUFFER_FEET * 0.3048 / 111320)

with st.spinner("Joining points to zoning (buffered)…"):
    joined = gpd.sjoin(gdf_city_buf, zoning, how="left", predicate="intersects")

# Nearest fallback
no_zone = joined["ZONE_CLASS"].isna()
if no_zone.any():
    st.warning(f"{no_zone.sum()} points missed – using nearest zone.")
    missing = joined[no_zone].copy()
    zoning_sindex = zoning.sindex
    for idx, row in missing.iterrows():
        nearest_idx = zoning.iloc[list(zoning_sindex.nearest(row.geometry.bounds))].distance(row.geometry).idxmin()
        joined.loc[idx, "ZONE_CLASS"] = zoning.loc[nearest_idx, "ZONE_CLASS"]

joined = joined.loc[:, ~joined.columns.duplicated()].reset_index(drop=True)
joined = joined.dropna(subset=["ZONE_CLASS"])

if joined.empty:
    st.error("No zoning found even with fallback. Check coordinates.")
    st.stop()

gdf_la = joined.copy()
st.success(f"**{len(gdf_la):,}** points have zoning")

la_city = gdf_la.copy()
la_city["Zoning"] = la_city["ZONE_CLASS"]

# ------------------------------------------------------------------
# 8. DEBUG MAP
# ------------------------------------------------------------------
st.subheader("DEBUG: Zoning (blue) + MLS Points (red)")
debug_map = folium.Map(location=[34.05, -118.24], zoom_start=10, tiles="CartoDB positron")
folium.GeoJson(
    zoning,
    style_function=lambda x: {"fillColor": "transparent", "color": "deepskyblue", "weight": 1.2, "opacity": 0.6},
    tooltip=folium.GeoJsonTooltip(fields=["ZONE_CLASS"], aliases=["Zone:"])
).add_to(debug_map)
for _, r in gdf_city.iterrows():
    folium.CircleMarker(
        [r.geometry.y, r.geometry.x], radius=4, color="red", fill=True,
        popup=folium.Popup(f"<b>{r.address}</b>", max_width=200)
    ).add_to(debug_map)
st_folium(debug_map, width=1000, height=550, key="debug")

# ------------------------------------------------------------------
# 9. Unit Calculations
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

la_city["base"] = la_city["Zoning"].str.replace(r'[\[\](Q)F].*', '', regex=True).str.split("-").str[0].str.upper()
la_city["sqft_per"] = la_city["base"].map(sqft_map).fillna(5000)
la_city["max_units"] = (la_city["lot_sqft"] / la_city["sqft_per"]).clip(lower=1).apply(lambda x: min(x, 20))

r1 = la_city["base"] == "R1"
la_city.loc[r1, "max_units"] = la_city.loc[r1, "lot_sqft"].apply(
    lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2
)

la_city["price_per_unit"] = (la_city["price"] / la_city["max_units"]).round(0).astype(int)

# ------------------------------------------------------------------
# 10. Filter
# ------------------------------------------------------------------
max_ppu = st.sidebar.slider("Max $/unit", 0, 1_000_000, 300_000, 25_000)
filtered = la_city[la_city["price_per_unit"] <= max_ppu].copy()

# ------------------------------------------------------------------
# 11. FINAL MAP (with clustering)
# ------------------------------------------------------------------
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    cluster = MarkerCluster().add_to(m)
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 200_000 else "orange" if r.price_per_unit < 400_000 else "red"
        folium.CircleMarker(
            [r.geometry.y, r.geometry.x], radius=6, color=color, fill=True,
            popup=folium.Popup(
                f"<b>{r.address}</b><br>"
                f"Price: ${r.price:,.0f}<br>"
                f"$/Unit: ${r.price_per_unit:,.0f}<br>"
                f"Max Units: {r.max_units:.0f}<br>"
                f"Zoning: {r.Zoning}",
                max_width=300
            )
        ).add_to(cluster)
    st_folium(m, width=1200, height=600, key="final")
else:
    st.warning("No deals under threshold.")

# ------------------------------------------------------------------
# 12. Download
# ------------------------------------------------------------------
if not filtered.empty:
    dl = filtered[["address","price","price_per_unit","max_units","Zoning"]].copy()
    dl.columns = ["Address","Price","$ per Unit","Max Units","Zoning"]
    dl["Price"] = dl["Price"].apply(lambda x: f"${x:,.0f}")
    dl["$ per Unit"] = dl["$ per Unit"].apply(lambda x: f"${x:,.0f}")
    st.download_button("Download CSV", dl.to_csv(index=False), "LA_Deals.csv", "text/csv")
else:
    st.info("No data to download.")

st.success("**LIVE!** 100% zoning coverage with buffer + nearest fallback.")
