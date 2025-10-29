import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import os

st.set_page_config(page_title="DealScout LA", layout="wide")

def find_col(df, candidates):
    cols = [c.lower() for c in df.columns]
    for c in candidates:
        if c.lower() in cols: return df.columns[cols.index(c.lower())]
    return None

# --- Upload ---
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded: st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings")

# --- Columns ---
price_col = find_col(mls, ["CurrentPrice", "price"])
lot_col   = find_col(mls, ["LotSizeSquareFeet", "lot_sqft"])
lat_col   = find_col(mls, ["Latitude", "lat"])
lon_col   = find_col(mls, ["Longitude", "lon"])
if not all([price_col, lot_col, lat_col, lon_col]):
    st.error("Need: CurrentPrice, LotSizeSquareFeet, Latitude, Longitude")
    st.stop()

mls["price"] = pd.to_numeric(mls[price_col], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls[lot_col], errors="coerce")
mls["lat"] = pd.to_numeric(mls[lat_col], errors="coerce")
mls["lon"] = pd.to_numeric(mls[lon_col], errors="coerce")

addr_parts = [mls.get(c, pd.Series("")) for c in ["StreetNumber", "StreetDirPrefix", "StreetName", "StreetDirSuffix", "StreetSuffix"]]
mls["address"] = pd.concat(addr_parts, axis=1).apply(lambda x: " ".join(str(p) for p in x if p and p != "nan"), axis=1)

mls["geometry"] = mls.apply(lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None, axis=1)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# --- ZONING: ONCE ---
if not st.session_state.get('zoning_processed', False):
    @st.cache_data
    def load_zoning():
        path = "Zoning.geojson"
        if not os.path.exists(path): st.error(f"`{path}` not found."); st.stop()
        return gpd.read_file(path)
    zoning = load_zoning()
    st.caption("**Zoning.geojson columns**:"); st.write(zoning.columns[:20].tolist())
    default = next((c for c in zoning.columns if "zone" in c.lower()), "name" if "name" in zoning.columns else zoning.columns[0])
    zoning_field = st.selectbox("Zoning column", zoning.columns.tolist(), index=zoning.columns.get_loc(default), key="zoning_once")
    st.session_state.update(zoning_field=zoning_field, zoning=zoning, zoning_processed=True)
    st.success(f"Using zoning field **{zoning_field}**")
else:
    zoning = st.session_state.zoning
    zoning_field = st.session_state.zoning_field
    st.success(f"Using zoning field **{zoning_field}** (cached)")

# --- Reproject & Join ---
gdf = gdf.to_crs(zoning.crs)
st.caption(f"Reprojected to **{gdf.crs}**")
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()
matched = joined[joined["Zoning"] != "Outside LA"]
st.write(f"**{len(matched):,}** matched")

# --- Full Zoning Map ---
sqft_map = {
    "A1": 108900, "A2": 43560,
    "RE40": 40000, "RE20": 20000, "RE15": 15000, "RE11": 11000, "RE9": 9000,
    "RS": 7500, "R1": 5000, "R1V": 5000, "R1F": 5000, "R1R": 5000, "R1H": 5000,
    "RU": 3500, "RZ2.5": 2500, "RZ3": 3000, "RZ4": 4000,
    "RW1": 2300, "R2": 2500, "RW2": 2300,
    "RD1.5": 1500, "RD2": 2000, "RD3": 3000, "RD4": 4000, "RD5": 5000, "RD6": 6000,
    "RMP": 20000, "R3": 800, "RAS3": 800, "R4": 400, "RAS4": 400, "R5": 200,
    "C1": 800, "C1.5": 800, "C2": 400, "C4": 400, "C5": 400,
    "CM": 800, "CR": 400, "MR1": 400, "M1": 400, "MR2": 200, "M2": 200,
}
joined["sqft_per_unit"] = joined["zone_code"].map(sqft_map).fillna(0)
joined["max_units"] = (joined["lot_sqft"] / joined["sqft_per_unit"].replace(0,1)).replace([float("inf")],0)

# SB-9 for R1
sb9_mask = joined["zone_code"].isin(["R1", "R1V", "R1F", "R1R", "R1H"])
joined.loc[sb9_mask, "max_units"] = joined.loc[sb9_mask, "lot_sqft"].apply(lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2)

joined["price_per_unit"] = (joined["price"] / joined["max_units"].replace(0,1)).replace([float("inf")], pd.NA)

# --- FILTERS (UNIQUE KEYS) ---
max_price_per_unit = st.sidebar.slider(
    "Max $/unit", 0, 20000, 5000, 500, key="max_price_per_unit_slider"
)
zone_filter = st.sidebar.multiselect(
    "Zoning", ["All"] + sorted(joined["zone_code"].unique().tolist()), ["All"], key="zone_filter_multiselect"
)

filtered = joined[joined["price_per_unit"] <= max_price_per_unit].copy()
if "All" not in zone_filter:
    filtered = filtered[filtered["zone_code"].isin(zone_filter)]
st.write(f"**{len(filtered):,}** after filters")

# --- Map ---
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11)
    for _, r in filtered.iterrows():
        folium.CircleMarker(
            [r.geometry.y, r.geometry.x], radius=6,
            color="crimson" if r["max_units"] >= 3 else "steelblue",
            popup=f"<b>{r['address']}</b><br>$/unit: ${r['price_per_unit']:,.0f}<br>Max: {r['max_units']:.1f}"
        ).add_to(m)
    st_folium(m, width=1200)
else:
    st.warning("No matches.")

# --- Download ---
st.download_button("Download CSV", filtered[["address", "price", "price_per_unit", "max_units", "Zoning"]].to_csv(index=False), "deals.csv", "text/csv")
