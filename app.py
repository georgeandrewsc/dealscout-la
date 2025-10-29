# --------------------------------------------------------------
# DealScout LA — City of Los Angeles ONLY
# Final version with FULL sqft_per_unit_map
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
st.markdown("**Upload MLS CSV → Get LA City deals with $/unit, SB-9, zoning & map.**")

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
    mls["lot_sqft"] = mls["lot_sqft"] * 43560  # Convert acres to sq ft
mls["lat"] = pd.to_numeric(mls[lat_col], errors="coerce")
mls["lon"] = pd.to_numeric(mls[lon_col], errors="coerce")

# --- Build Clean Address ---
addr_parts = [
    mls.get("StreetNumber", pd.Series("")).astype(str),
    mls.get("StreetDirPrefix", pd.Series("")).astype(str),
    mls.get("StreetName", pd.Series("")).astype(str),
    mls.get("StreetDirSuffix", pd.Series("")).astype(str),
    mls.get("StreetSuffix", pd.Series("")).astype(str),
]
mls["address"] = (
    pd.concat(addr_parts, axis=1)
    .apply(lambda row: " ".join(p for p in row if p and p not in ["nan", "NaN"]), axis=1)
    .str.replace(r"\s+", " ", regex=True)
    .str.strip()
)

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
    st.caption("**Zoning.geojson columns** (first 20):")
    st.write(zoning.columns[:20].tolist())
    
    # Auto-detect zoning field
    zone_candidates = [c for c in zoning.columns if "zone" in c.lower()]
    default = zone_candidates[0] if zone_candidates else ("name" if "name" in zoning.columns else zoning.columns[0])
    
    zoning_field = st.selectbox(
        "Zoning column",
        options=zoning.columns.tolist(),
        index=zoning.columns.get_loc(default),
        key="zoning_column_select",
        help="Pick the column with zoning codes (e.g. R1, RE40)"
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
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

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

la_city_only["sqft_per_unit"] = la_city_only["zone_code"].map(sqft_per_unit_map).fillna(5000)  # Safe fallback
la_city_only["max_units"] = (la_city_only["lot_sqft"] / la_city_only["sqft_per_unit"]).clip(1, 20)

# --- SB-9 Boost for R1 Zones ---
r1_mask = la_city_only["zone_code"].str.startswith("R1")
la_city_only.loc[r1_mask, "max_units"] = la_city_only.loc[r1_mask, "lot_sqft"].apply(
    lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2
)

# --- Final $/Unit ---
la_city_only["price_per_unit"] = (la_city_only["price"] / la_city_only["max_units"]).round(0)

# --- Clean Address ---
la_city_only["address"] = la_city_only["address"].str.replace(r'\bnan\b', '', regex=True).str.replace(r'\s+', ' ', regex=True).str.strip()

# --- SIDEBAR FILTERS ---
max_price_per_unit = st.sidebar.slider(
    "Max $/unit", 0, 2000000, 500000, 50000, key="price_slider"
)
zone_filter = st.sidebar.multiselect(
    "Zoning", ["All"] + sorted(la_city_only["zone_code"].unique()), ["All"], key="zone_filter"
)

filtered = la_city_only[la_city_only["price_per_unit"] <= max_price_per_unit].copy()
if "All" not in zone_filter:
    filtered = filtered[filtered["zone_code"].isin(zone_filter)]

st.write(f"**{len(filtered):,}** deals after filters (City of LA only)")

# --- INTERACTIVE MAP ---
if not filtered.empty:
    m = folium.Map(location=[34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r["price_per_unit"] < 300000 else "orange" if r["price_per_unit"] < 600000 else "red"
        folium.CircleMarker(
            location=[r.geometry.y, r.geometry.x],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.8,
            popup=folium.Popup(
                f"<b>{r['address']}</b><br>"
                f"Price: ${r['price']:,.0f}<br>"
                f"$/Unit: ${r['price_per_unit']:,.0f}<br>"
                f"Max Units: {r['max_units']:.0f}<br>"
                f"Zoning: {r['Zoning']}",
                max_width=300
            )
        ).add_to(m)
    st_folium(m, width=1200, height=600)
else:
    st.warning("No deals match filters. Try increasing Max $/unit.")

# --- DOWNLOAD CSV ---
download_df = filtered[["address", "price", "price_per_unit", "max_units", "Zoning"]].copy()
download_df.columns = ["Address", "Price", "$/Unit", "Max Units", "Zoning"]
st.download_button(
    "Download LA City Deals",
    data=download_df.to_csv(index=False),
    file_name="DealScout_LA_City_Only.csv",
    mime="text/csv"
)

st.success("**Done!** All data is **City of Los Angeles only** with full zoning map.")
