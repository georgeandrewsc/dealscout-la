# --------------------------------------------------------------
# DealScout LA — REAL LA CITY ZONING (GitHub Release) + BOUNDARY
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
import requests
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point
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
# 5. DEBUG MAP
# ------------------------------------------------------------------
st.subheader("Debug: All MLS Points")
m_debug = folium.Map([34.05, -118.24], zoom_start=10, tiles="CartoDB positron")
for _, r in gdf.iterrows():
    folium.CircleMarker([r.geometry.y, r.geometry.x], radius=3, color="blue", fill=True).add_to(m_debug)
st_folium(m_debug, width=800, height=400, key="debug_raw")

# ------------------------------------------------------------------
# 6. LA CITY ZONING — FROM GITHUB RELEASE (440 MB) — FIXED & FINAL
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
            st.write(f"Downloading {total/1e6:.1f} MB...")
            with open(cache_file, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
        gdf = gpd.read_file(cache_file)
        return _fix_zoning_gdf(gdf)
    except Exception as e:
        st.error(f"Failed to load zoning file: {e}")
        st.stop()

def _fix_zoning_gdf(gdf):
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs("EPSG:4326")
    
    # Auto-detect zoning column
    cols = [c for c in gdf.columns if c.lower() in ['zone_class', 'zoning', 'zone', 'class', 'zonecode']]
    if not cols:
        st.error(f"No zoning column found! Columns: {list(gdf.columns)}")
        st.stop()
    
    zone_col = cols[0]
    st.write(f"**Using zoning column:** `{zone_col}`")
    gdf["ZONE_CLASS"] = gdf[zone_col].astype(str)
    return gdf[["ZONE_CLASS", "geometry"]].copy()

zoning = load_zoning()
st.success(f"**REAL Zoning loaded:** {len(zoning):,} polygons")

# ------------------------------------------------------------------
# 7. Filter to LA City + Join Zoning
# ------------------------------------------------------------------
gdf_la = gpd.sjoin(gdf.to_crs("EPSG:4326"), zoning.to_crs("EPSG:4326"), how="inner", predicate="intersects")

if gdf_la.empty:
    st.error("**No points inside LA City zoning.**\n→ Check MLS CSV lat/lon or zoning file.")
    st.stop()

# Drop duplicate index from sjoin
gdf_la = gdf_la.reset_index(drop=True)
gdf_la = gdf_la.loc[:, ~gdf_la.columns.duplicated()]  # remove duplicate geometry cols

st.success(f"**{len(gdf_la):,}** points inside LA City with zoning")

# ------------------------------------------------------------------
# 8. Create la_city with Zoning
# ------------------------------------------------------------------
la_city = gdf_la.copy()
la_city["Zoning"] = la_city["ZONE_CLASS"]  # Now this exists!
st.write("**Sample zoning values:**", la_city["Zoning"].unique()[:20])

# ------------------------------------------------------------------
# 9. sqft_map + Calculations
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

# Clean base zone
la_city["base"] = la_city["Zoning"].str.replace(r'[\[\](Q)F].*', '', regex=True).str.split("-").str[0].str.upper()
la_city["sqft_per"] = la_city["base"].map(sqft_map).fillna(5000)
la_city["max_units"] = (la_city["lot_sqft"] / la_city["sqft_per"]).clip(lower=1).apply(lambda x: min(x, 20))

# Special R1 rule
r1 = la_city["base"] == b"R1"
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
# 11. Map
# ------------------------------------------------------------------
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 200_000 else "orange" if r.price_per_unit < 400_000 else "red"
        folium.CircleMarker(
            [r.geometry.y, r.geometry.x],
            radius=6, color=color, fill=True,
            popup=folium.Popup(
                f"<b>{r.address}</b><br>"
                f"Price: ${r.price:,.0f}<br>"
                f"$/Unit: ${r.price_per_unit:,.0f}<br>"
                f"Max Units: {r.max_units:.0f}<br>"
                f"Zoning: {r.Zoning}",
                max_width=300
            )
        ).add_to(m)
    st_folium(m, width=1200, height=600, key="final_map")
else:
    st.warning("No deals under selected $/unit threshold.")

# ------------------------------------------------------------------
# 12. Download
# ------------------------------------------------------------------
if not filtered.empty:
    dl = filtered[["address","price","price_per_unit","max_units","Zoning"]].copy()
    dl.columns = ["Address","Price","$/Unit","Max Units","Zoning"]
    dl["Price"] = dl["Price"].apply(lambda x: f"${x:,.0f}")
    dl["$/Unit"] = dl["$/Unit"].apply(lambda x: f"${x:,.0f}")
    st.download_button("Download CSV", dl.to_csv(index=False), "LA_Deals.csv", "text/csv")
else:
    st.info("No data to download at current filter.")

st.success("**LIVE!** Using **REAL LA City zoning** from GitHub (440 MB).")
