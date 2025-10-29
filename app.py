# --------------------------------------------------------------
# app.py  –  DealScout LA (fixed & production-ready)
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
# Helper: column finder (case-insensitive, partial match)
# ------------------------------------------------------------------
def find_col(df, candidates):
    """Return first column name that matches any candidate (case-insensitive)."""
    cols = [c.lower() for c in df.columns]
    for cand in candidates:
        if cand.lower() in cols:
            return df.columns[cols.index(cand.lower())]
    return None


# ------------------------------------------------------------------
# Sidebar filters
# ------------------------------------------------------------------
st.sidebar.header("Filters")
max_dollar_per_unit = st.sidebar.slider(
    "Max $/unit", min_value=0, max_value=20_000, value=5_000, step=500
)

zone_options = ["All", "RE40", "RE20", "RE15", "RE11", "RE9", "R1", "RD", "R3", "R4", "R5", "RAS"]
zone_filter = st.sidebar.multiselect("Zoning", options=zone_options, default=["All"])

# ------------------------------------------------------------------
# Upload MLS CSV
# ------------------------------------------------------------------
uploaded = st.file_uploader("Upload MLS CSV (max 200 MB)", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# ------------------------------------------------------------------
# 1. Identify core columns (flexible)
# ------------------------------------------------------------------
price_col   = find_col(mls, ["CurrentPrice", "price", "listprice"])
lot_col     = find_col(mls, ["LotSizeSquareFeet", "lotsize", "lot_sqft"])
lat_col     = find_col(mls, ["Latitude", "lat"])
lon_col     = find_col(mls, ["Longitude", "lon"])

required = [price_col, lot_col, lat_col, lon_col]
if any(c is None for c in required):
    st.error(
        "CSV must contain:\n"
        "- Price column (`CurrentPrice` or `price`)\n"
        "- Lot-size column (`LotSizeSquareFeet` or `lot_sqft`)\n"
        "- Latitude & Longitude"
    )
    st.stop()

# ------------------------------------------------------------------
# 2. Clean core fields
# ------------------------------------------------------------------
mls["price"]    = pd.to_numeric(mls[price_col], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls[lot_col],   errors="coerce")
mls["lat"]      = pd.to_numeric(mls[lat_col],  errors="coerce")
mls["lon"]      = pd.to_numeric(mls[lon_col],  errors="coerce")

# ------------------------------------------------------------------
# 3. Build clean address (all possible street parts)
# ------------------------------------------------------------------
addr_parts = [
    mls.get("StreetNumber", pd.Series("")).astype(str),
    mls.get("StreetDirPrefix", pd.Series("")).astype(str),
    mls.get("StreetName", pd.Series("")).astype(str),
    mls.get("StreetDirSuffix", pd.Series("")).astype(str),
    mls.get("StreetSuffix", pd.Series("")).astype(str),
]
mls["address"] = (
    pd.concat(addr_parts, axis=1)
    .apply(lambda row: " ".join(p for p in row if p and p != "nan"), axis=1)
    .str.replace(r"\s+", " ", regex=True)
)

# ---- Geometry ------------------------------------------------------------
mls["geometry"] = mls.apply(
    lambda r: Point(r.lon, r.lat)
    if pd.notnull(r.lon) and pd.notnull(r.lat)
    else None,
    axis=1,
)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# ------------------------------------------------------------------
# FINAL: Load Zoning + Select Field (ONE TIME ONLY)
# ------------------------------------------------------------------
@st.cache_data
def load_zoning():
    zoning_path = "Zoning.geojson"
    if not os.path.exists(zoning_path):
        st.error(f"`{zoning_path}` not found – place it next to `app.py`.")
        st.stop()
    return gpd.read_file(zoning_path)

# Load once
zoning = load_zoning()

# Show columns once
if "zoning_columns_shown" not in st.session_state:
    st.caption("**Zoning.geojson columns** (first 20):")
    st.write(zoning.columns[:20].tolist())
    st.session_state.zoning_columns_shown = True

# --- ONLY SHOW SELECTBOX ONCE ---
if "zoning_field" not in st.session_state:
    # First run: auto-detect or default
    zone_candidates = [c for c in zoning.columns if "zone" in c.lower()]
    default = zone_candidates[0] if zone_candidates else ("name" if "name" in zoning.columns else zoning.columns[0])
    st.session_state.zoning_field = default

# SINGLE selectbox with unique key
zoning_field = st.selectbox(
    "Zoning column",
    options=zoning.columns.tolist(),
    index=zoning.columns.get_loc(st.session_state.zoning_field),
    key="zoning_column_select",  # UNIQUE KEY
    help="Pick the column with zoning codes (e.g. R1, RE40)"
)

# Sync session state
st.session_state.zoning_field = zoning_field
st.success(f"Using zoning field **{zoning_field}**")

# --- Reproject MLS to zoning CRS ---
try:
    gdf = gdf.to_crs(zoning.crs)
    st.caption(f"Reprojected MLS points to CRS: **{gdf.crs}**")
except Exception as e:
    st.error(f"CRS reprojection failed: {e}")
    st.stop()

# --- Spatial join ---
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

# Show matches
matched = joined[joined["Zoning"] != "Outside LA (No Zoning)"]
st.write(f"**{len(matched):,}** listings matched to zoning polygons")

# ---- Find zoning column with session state --------------------------------
if "zoning_field" not in st.session_state:
    # First run: auto-detect or default to 'name'
    zone_candidates = [c for c in zoning.columns if "zone" in c.lower()]
    if zone_candidates:
        st.session_state.zoning_field = zone_candidates[0]
    elif "name" in zoning.columns:
        st.session_state.zoning_field = "name"
    else:
        st.session_state.zoning_field = zoning.columns[0]

# Create selectbox with key to prevent duplicates
zoning_field = st.selectbox(
    "Zoning column",
    options=zoning.columns.tolist(),
    index=zoning.columns.get_loc(st.session_state.zoning_field),
    key="zoning_selectbox",  # UNIQUE KEY = NO DUPLICATES
    help="Select the column containing zoning codes (e.g. R1, RE40, C2)"
)

# Update session state when user changes selection
if st.session_state.zoning_field != zoning_field:
    st.session_state.zoning_field = zoning_field

st.success(f"Using zoning field **{zoning_field}**")

# ---- Reproject MLS to match zoning CRS ------------------------------------
try:
    gdf = gdf.to_crs(zoning.crs)
    st.caption(f"Reprojected MLS points to CRS: **{gdf.crs}**")
except Exception as e:
    st.error(f"CRS reprojection failed: {e}")
    st.stop()

# ---- Spatial join ---------------------------------------------------------
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

# DEBUG: Show matches
matched = joined[joined["Zoning"] != "Outside LA (No Zoning)"]
st.write(f"**{len(matched):,}** listings matched to zoning polygons")

# ---- NOW REPROJECT MLS TO MATCH ZONING CRS -------------------------------
try:
    gdf = gdf.to_crs(zoning.crs)
    st.caption(f"Reprojected MLS points to CRS: **{gdf.crs}**")
except Exception as e:
    st.error(f"CRS reprojection failed: {e}")
    st.stop()

# ---- DEBUG: Show zoning columns and pick field ---------------------------
st.caption("**Zoning.geojson columns** (first 20):")
st.write(zoning.columns[:20].tolist())

zone_candidates = [c for c in zoning.columns if "zone" in c.lower()]
if not zone_candidates:
    st.warning("No column containing **zone** found. Select the zoning code column.")
    zoning_field = st.selectbox(
        "Select zoning column",
        options=zoning.columns.tolist(),
        index=zoning.columns.get_loc("name") if "name" in zoning.columns else 0
    )
else:
    zoning_field = st.selectbox(
        "Zoning column (auto-detected)",
        options=zone_candidates,
        index=0
    )

st.success(f"Using zoning field **{zoning_field}**")

# ---- Spatial join --------------------------------------------------------
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

# DEBUG: Show matches
matched = joined[joined["Zoning"] != "Outside LA (No Zoning)"]
st.write(f"**{len(matched):,}** listings matched to zoning polygons")

# ------------------------------------------------------------------
# 5. Load Zoning.geojson  (FINAL – works with ANY column name)
# ------------------------------------------------------------------
zoning_path = "Zoning.geojson"
if not os.path.exists(zoning_path):
    st.error(f"`{zoning_path}` not found – place it next to `app.py`.")
    st.stop()

zoning = gpd.read_file(zoning_path).to_crs("EPSG:4326")

# ---- DEBUG: show the actual columns ----
st.caption("**Zoning.geojson columns** (first 20):")
st.write(zoning.columns[:20].tolist())

# ---- 1. Try to auto-detect a column that contains "zone" ----
zone_candidates = [c for c in zoning.columns if "zone" in c.lower()]

# ---- 2. If nothing found, let the user pick ANY column ----
if not zone_candidates:
    st.warning(
        "No column containing **zone** was found. "
        "Please **select the column that holds the zoning code** (usually `name` or similar)."
    )
    zoning_field = st.selectbox(
        "Select zoning column",
        options=zoning.columns.tolist(),
        index=zoning.columns.get_loc("name") if "name" in zoning.columns else 0
    )
else:
    # Auto-pick the first match, but still let the user override
    zoning_field = st.selectbox(
        "Zoning column (auto-detected)",
        options=zone_candidates,
        index=0
    )

st.success(f"Using zoning field **{zoning_field}**")
# ------------------------------------------------------------------
# 6. Spatial join
# ------------------------------------------------------------------
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

# ------------------------------------------------------------------
# 7. Unit calculations (same logic as Colab)
# ------------------------------------------------------------------
# ---- sqft per unit lookup (LA zoning) ----
sqft_per_unit_map = {
    "A1": 108900, "A2": 43560,
    "RE40": 40000, "RE20": 20000, "RE15": 15000, "RE11": 11000, "RE9": 9000,
    "RS": 7500, "R1": 5000, "R1V": 5000, "R1F": 5000, "R1R": 5000, "R1H": 5000,
    "RU": 3500, "RZ2.5": 2500, "RZ3": 3000, "RZ4": 4000,
    "RW1": 2300, "R2": 2500, "RW2": 2300,
    "RD1.5": 1500, "RD2": 2000, "RD3": 3000, "RD4": 4000,
    "RD5": 5000, "RD6": 6000,
    "RMP": 20000, "R3": 800, "RAS3": 800, "R4": 400, "RAS4": 400, "R5": 200,
    "C1": 800, "C1.5": 800, "C2": 400, "C4": 400, "C5": 400,
    "CM": 800, "CR": 400, "MR1": 400, "M1": 400, "MR2": 200, "M2": 200,
}
joined["sqft_per_unit"] = joined["zone_code"].map(sqft_per_unit_map).fillna(0)

# ---- max units (avoid div-by-zero) ----
joined["max_units"] = (joined["lot_sqft"] / joined["sqft_per_unit"].replace(0, 1)).replace([float("inf")], 0)
joined["price_per_unit"] = (joined["price"] / joined["max_units"].replace(0, 1)).replace([float("inf")], pd.NA)

# ---- SB-9 potential (simple rule) ----
def sb9_potential(lot):
    if pd.isna(lot):
        return pd.NA
    return 3 if lot < 2_400 else 4

joined["SB9_units"] = joined["lot_sqft"].apply(sb9_potential)

# ---- Clean up non-LA rows ----
outside = joined["sqft_per_unit"] == 0
joined.loc[outside, ["max_units", "price_per_unit", "SB9_units"]] = pd.NA
joined.loc[outside, "Zoning"] = "Outside LA (No Zoning)"

# ------------------------------------------------------------------
# 8. Apply filters
# ------------------------------------------------------------------
filtered = joined.copy()

# Zoning filter
if "All" not in zone_filter:
    allowed = [z.upper() for z in zone_filter]
    filtered = filtered[filtered["zone_code"].isin(allowed)]

# $/unit filter
filtered = filtered[filtered["price_per_unit"] <= max_dollar_per_unit]

st.write(f"**{len(filtered):,}** listings after filters")

# ------------------------------------------------------------------
# 9. Interactive map
# ------------------------------------------------------------------
if not filtered.empty:
    centre_lat = filtered.geometry.y.mean()
    centre_lon = filtered.geometry.x.mean()
    m = folium.Map(location=[centre_lat, centre_lon], zoom_start=12, tiles="CartoDB positron")

    for _, row in filtered.iterrows():
        sb9 = row["SB9_units"] and row["SB9_units"] > 0
        popup = f"""
        <b>{row.get('address', '—')}</b><br>
        Price: ${row['price']:,.0f}<br>
        $/unit: ${row['price_per_unit']:,.0f}<br>
        Max units: {row['max_units']:.1f}<br>
        Zoning: {row['Zoning']}<br>
        SB-9: {row['SB9_units']} units
        """
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=7,
            color="crimson" if sb9 else "steelblue",
            fill=True,
            fillOpacity=0.8,
            popup=folium.Popup(popup, max_width=300),
        ).add_to(m)

    st_folium(m, width=1200, height=600)
else:
    st.warning("No listings match the current filters.")

# ------------------------------------------------------------------
# 10. Download CSV
# ------------------------------------------------------------------
download_cols = [
    "address", "price", "lot_sqft", "price_per_unit",
    "max_units", "Zoning", "SB9_units", "lat", "lon"
]
download_df = filtered[download_cols].copy()
download_df.rename(columns={
    "address": "Address",
    "price": "Price",
    "lot_sqft": "Lot (sq ft)",
    "price_per_unit": "$/unit",
    "max_units": "Max Units",
    "SB9_units": "SB-9 Units",
    "lat": "Latitude",
    "lon": "Longitude"
}, inplace=True)

download_df["$/unit"] = download_df["$/unit"].round(0).astype("Int64")
csv_bytes = download_df.to_csv(index=False).encode()

st.download_button(
    label="Download enriched CSV",
    data=csv_bytes,
    file_name="DealScout_LA_enriched.csv",
    mime="text/csv",
)

st.success("Done! Use the map, table, and download button above.")
