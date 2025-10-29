# --------------------------------------------------------------
# DealScout LA — City of Los Angeles ONLY
# FINAL: 1234 S Cochran Ave + ALL ZONING CODES + COMMAS
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
    st.error("CSV must include: `CurrentPrice`, `LotSizeSquareFeet`, `Latitude`, `Longitude`")
    st.stop()

# --- Clean Core Data ---
mls["price"] = pd.to_numeric(mls[price_col], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls[lot_col], errors="coerce")
if "Acres" in lot_col:
    mls["lot_sqft"] = mls["lot_sqft"] * 43560
mls["lat"] = pd.to_numeric(mls[lat_col], errors="coerce")
mls["lon"] = pd.to_numeric(mls[lon_col], errors="coerce")

# --- Build Address: 1234 S Cochran Ave ---
street_number = mls.get("StreetNumber", pd.Series("")).astype(str).str.strip()
street_dir_prefix = mls.get("StreetDirPrefix", pd.Series("")).astype(str).str.strip()
street_name = mls.get("StreetName", pd.Series("")).astype(str).str.strip()
street_dir_suffix = mls.get("StreetDirSuffix", pd.Series("")).astype(str).str.strip()
street_suffix = mls.get("StreetSuffix", pd.Series("")).astype(str).str.strip()

# Replace "nan"
for col in [street_number, street_dir_prefix, street_name, street_dir_suffix, street_suffix]:
    col.replace({"nan": "", "NaN": ""}, inplace=True)

# Build: 1234 S Cochran Ave
address_parts = []
for num, dir_p, name, dir_s, suffix in zip(street_number, street_dir_prefix, street_name, street_dir_suffix, street_suffix):
    parts = []
    if num and num != "nan":
        parts.append(num)
    if dir_p and dir_p != "nan":
        parts.append(dir_p)
    elif dir_s and dir_s != "nan":
        parts.append(dir_s)
    if name and name != "nan":
        parts.append(name)
    if suffix and suffix != "nan":
        parts.append(suffix)
    address_parts.append(" ".join(parts) if parts else "Unknown Address")

mls["address"] = address_parts

# --- Geometry ---
mls["geometry"] = mls.apply(
    lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None,
    axis=1
)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# --- Load Zoning (Cached, Once) ---
if not st.session_state.get("zoning_processed", False):
    @st.cache_data
    def load_zoning():
        path = "Zoning.geojson"
        if not os.path.exists(path):
            st.error(f"`{path}` not found — place it next to `app.py`.")
            st.stop()
        return gpd.read_file(path)
    
    zoning = load_zoning()
    st.write("**Zoning.geojson columns:**", zoning.columns.tolist())
    
    # Let user pick the correct zoning code column
    zoning_field = st.selectbox(
        "Select Zoning Code Column (e.g., ZONE_CLASS, ZONING, ZONECODE)",
        options=zoning.columns.tolist(),
        index=0,
        key="zoning_column_select",
        help="Pick the column with R3, RD1.5, RE40, etc."
    )
    
    st.session_state.update(zoning_field=zoning_field, zoning=zoning, zoning_processed=True)
    st.success(f"Using zoning field **{zoning_field}**")
else:
    zoning = st.session_state.zoning
    zoning_field = st.session_state.zoning_field
    st.success(f"Using zoning field **{zoning_field}** (cached)")

# --- Reproject & Join ---
gdf = gdf.to_crs(zoning.crs)
st.caption(f"Reprojected MLS points to CRS: **{gdf.crs}**")

joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")

# --- FILTER: ONLY CITY OF LOS ANGELES ---
la_city_only = joined[joined["Zoning"] != "Outside LA (No Zoning)"].copy()

if la_city_only.empty:
    st.error("**No listings found inside City of Los Angeles.** Try a different MLS export.")
    st.stop()

st.write(f"**{len(la_city_only):,}** listings **inside City of LA** (from {len(joined):,} total)")

# --- FULL SQFT PER UNIT MAP (YOUR VERSION) ---
sqft_per_unit_map = {
    'A1':108900,'A2':43560,'RE40':40000,'RE20':20000,'RE15':15000,'RE11':11000,'RE9':9000,
    'RS':7500,'R1':5000,'R1V':5000,'R1F':5000,'R1R':5000,'R1H':5000,
    'RU':3500,'RZ2.5':2500,'RZ3':3000,'RZ4':4000,'RW1':2300,'R2':2500,'RW2':2300,
    'RD1.5':1500,'RD2':2000,'RD3':3000,'RD4':4000,'RD5':5000,'RD6':6000,
    'RMP':20000,'R3':800,'RAS3':800,'R4':400,'RAS4':400,'R5':200,
    'C1':800,'C1.5':800,'C2':400,'C4':400,'C5':400,'CM':800,'CR':400,
    'MR1':400,'M1':400,'MR2':200,'M2':200,
}

# Extract base code (e.g., RD1.5-1 → RD1.5)
la_city_only["zone_code"] = la_city_only["Zoning"].str
