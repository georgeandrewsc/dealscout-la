# --------------------------------------------------------------
# FINAL app.py – DealScout LA (100% working)
# --------------------------------------------------------------
import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import os
import warnings

warnings.filterwarnings("ignore")
st.set_page_config(page_title="DealScout LA", layout="wide")

# ------------------------------------------------------------------
# Helper: find column
# ------------------------------------------------------------------
def find_col(df, candidates):
    cols = [c.lower() for c in df.columns]
    for cand in candidates:
        if cand.lower() in cols:
            return df.columns[cols.index(cand.lower())]
    return None

# ------------------------------------------------------------------
# Sidebar filters
# ------------------------------------------------------------------
st.sidebar.header("Filters")
max_dollar_per_unit = st.sidebar.slider("Max $/unit", 0, 20_000, 5_000, 500)
zone_options = ["All", "RE40", "RE20", "RE15", "R1", "RD", "R3", "R4", "R5"]
zone_filter = st.sidebar.multiselect("Zoning", zone_options, ["All"])

# ------------------------------------------------------------------
# Upload CSV
# ------------------------------------------------------------------
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# ------------------------------------------------------------------
# Identify columns
# ------------------------------------------------------------------
price_col = find_col(mls, ["CurrentPrice", "price"])
lot_col   = find_col(mls, ["LotSizeSquareFeet", "lot_sqft"])
lat_col   = find_col(mls, ["Latitude", "lat"])
lon_col   = find_col(mls, ["Longitude", "lon"])

if not all([price_col, lot_col, lat_col, lon_col]):
    st.error("Need: CurrentPrice, LotSizeSquareFeet, Latitude, Longitude")
    st.stop()

# ------------------------------------------------------------------
# Clean data
# ------------------------------------------------------------------
mls["price"] = pd.to_numeric(mls[price_col], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls[lot_col], errors="coerce")
mls["lat"] = pd.to_numeric(mls[lat_col], errors="coerce")
mls["lon"] = pd.to_numeric(mls[lon_col], errors="coerce")

addr_parts = [mls.get(c, pd.Series("")) for c in ["StreetNumber", "StreetDirPrefix", "StreetName", "StreetDirSuffix", "StreetSuffix"]]
mls["address"] = pd.concat(addr_parts, axis=1).apply(lambda x: " ".join(str(p) for p in x if p and p != "nan"), axis=1).str.replace(r"\s+", " ", regex=True)

mls["geometry"] = mls.apply(lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None, axis=1)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# ------------------------------------------------------------------
# Load Zoning (cached)
# ------------------------------------------------------------------
@st.cache_data
def load_zoning():
    zoning_path = "Zoning.geojson"
    if not os.path.exists(zoning_path):
        st.error(f"`{zoning_path}` not found.")
        st.stop()
    return gpd.read_file(zoning_path)

zoning = load_zoning()

if "zoning_columns_shown" not in st.session_state:
    st.caption("**Zoning.geojson columns** (first 20):")
    st.write(zoning.columns[:20].tolist())
    st.session_state.zoning_columns_shown = True

# --- Select zoning field (once) ---
if "zoning_field" not in st.session_state:
    zone_candidates = [c for c in zoning.columns if "zone" in c.lower()]
    default = zone_candidates[0] if zone_candidates else ("name" if "name" in zoning.columns else zoning.columns[0])
    st.session_state.zoning_field = default

zoning_field = st.selectbox(
    "Zoning column",
    options=zoning.columns.tolist(),
    index=zoning.columns.get_loc(st.session_state.zoning_field),
    key="zoning_column_select",
    help="Column with zoning codes (e.g. R1, RE40)"
)
st.session_state.zoning_field = zoning_field
st.success(f"Using zoning field **{zoning_field}**")

# --- Reproject & join ---
gdf = gdf.to_crs(zoning.crs)
st.caption(f"Reprojected to CRS: **{gdf.crs}**")

joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

matched = joined[joined["Zoning"] != "Outside LA (No Zoning)"]
st.write(f"**{len(matched):,}** listings matched to zoning polygons")

# --- Calculations ---
sqft_per_unit_map = { "RE40":40000, "RE20":20000, "RE15":15000, "RE11":11000, "RE9":9000, "R1":5000, "RD":2000, "R3":800, "R4":400, "R5":200 }
joined["sqft_per_unit"] = joined["zone_code"].map(sqft_per_unit_map).fillna(0)
joined["max_units"] = (joined["lot_sqft"] / joined["sqft_per_unit"].replace(0,1)).replace([float("inf")],0)
joined["price_per_unit"] = (joined["price"] / joined["max_units"].replace(0,1)).replace([float("inf")], pd.NA)
joined["SB9_units"] = joined["lot_sqft"].apply(lambda x: 4 if x >= 2400 else 3 if x >= 1000 else pd.NA)

outside = joined["sqft_per_unit"] == 0
joined.loc[outside, ["max_units", "price_per_unit", "SB9_units"]] = pd.NA

# --- Filters ---
filtered = joined.copy()
if "All" not in zone_filter:
    filtered = filtered[filtered["zone_code"].isin([z.upper() for z in zone_filter])]
filtered = filtered[filtered["price_per_unit"] <= max_dollar_per_unit]

st.write(f"**{len(filtered):,}** after filters")

# --- Map ---
if not filtered.empty:
    m = folium.Map(location=[34.05, -118.24], zoom_start=11)
    for _, r in filtered.iterrows():
        popup = f"<b>{r['address']}</b><br>$/unit: ${r['price_per_unit']:,.0f}<br>Max: {r['max_units']:.1f}<br>SB-9: {r['SB9_units']}"
        folium.CircleMarker(
            [r.geometry.y, r.geometry.x], radius=6,
            color="crimson" if r["SB9_units"] >= 3 else "steelblue",
            popup=folium.Popup(popup, max_width=300)
        ).add_to(m)
    st_folium(m, width=1200, height=600)
else:
    st.warning("No matches.")

# --- Download ---
download_df = filtered[["address", "price", "lot_sqft", "price_per_unit", "max_units", "Zoning", "SB9_units"]].copy()
download_df.rename(columns={"address":"Address", "price":"Price", "lot_sqft":"Lot SF", "price_per_unit":"$/unit", "max_units":"Max Units"}, inplace=True)
st.download_button("Download CSV", download_df.to_csv(index=False), "DealScout_LA.csv", "text/csv")
