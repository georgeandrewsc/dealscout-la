# --------------------------------------------------------------
# app.py  –  DealScout LA
# --------------------------------------------------------------
import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import warnings
import os

warnings.filterwarnings("ignore")
st.set_page_config(page_title="DealScout LA", layout="wide")

st.title("DealScout LA")
st.markdown(
    "Upload **MLS CSV** → Get zoning, **$/unit**, **SB-9** eligibility, interactive map & download."
)

# -------------------------- Sidebar Filters --------------------------
st.sidebar.header("Filters")
max_price_per_unit = st.sidebar.slider(
    "Max $/unit", min_value=0, max_value=20_000, value=5_000, step=500
)

zone_options = ["RE40", "R1", "RD", "R3", "All"]
zone_filter = st.sidebar.multiselect(
    "Zoning", options=zone_options, default=["All"]
)

# -------------------------- Upload CSV --------------------------
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

# -------------------------- Load & Clean MLS --------------------------
mls = pd.read_csv(uploaded)
st.write(f"**{len(mls)}** listings loaded")

# ---- Find price / lot columns (flexible) ----
price_col = next((c for c in ["CurrentPrice", "price"] if c in mls.columns), None)
lot_col   = next((c for c in ["LotSizeSquareFeet", "lot_sqft"] if c in mls.columns), None)

if not price_col or not lot_col:
    st.error("CSV needs a price column (`CurrentPrice` or `price`) and a lot-size column (`LotSizeSquareFeet` or `lot_sqft`).")
    st.stop()

mls["price"]    = pd.to_numeric(mls[price_col], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls[lot_col],   errors="coerce")
mls["latitude"] = pd.to_numeric(mls.get("Latitude"),  errors="coerce")
mls["longitude"]= pd.to_numeric(mls.get("Longitude"), errors="coerce")

# ---- Build clean address -------------------------------------------------
addr_parts = [
    mls.get("StreetNumber", "").astype(str),
    mls.get("StreetDirPrefix", "").astype(str),
    mls.get("StreetName", "").astype(str),
    mls.get("StreetDirSuffix", "").astype(str),
    mls.get("StreetSuffix", "").astype(str),
]
mls["address"] = (
    pd.concat(addr_parts, axis=1)
    .apply(lambda row: " ".join(p for p in row if p and p != "nan"), axis=1)
    .str.replace(r"\s+", " ", regex=True)
)

# ---- Geometry ------------------------------------------------------------
mls["geometry"] = mls.apply(
    lambda r: Point(r.longitude, r.latitude)
    if pd.notnull(r.longitude) and pd.notnull(r.latitude)
    else None,
    axis=1,
)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# -------------------------- Load Zoning --------------------------
zoning_path = "Zoning.geojson"
if not os.path.exists(zoning_path):
    st.error(f"**{zoning_path}** not found. Place it next to `app.py`.")
    st.stop()

try:
    zoning = gpd.read_file(zoning_path).to_crs("EPSG:4326")
except Exception as e:
    st.error(f"Failed to read Zoning.geojson: {e}")
    st.stop()

# ---- Detect zoning code column -------------------------------------------
possible_cols = ["ZONE_CLASS", "ZONING", "ZONE", "LAND_USE", "ZONECODE", "ZONE_CODE"]
zoning_field = next((c for c in possible_cols if c in zoning.columns), None)

if zoning_field is None:
    st.error("No zoning column found. Expected one of: " + ", ".join(possible_cols))
    st.stop()

st.write(f"Using zoning field: **{zoning_field}**")

# ---- Spatial join --------------------------------------------------------
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA")
joined["zone_code"] = joined["Zoning"].str.split("-").str[0].str.upper()

# -------------------------- Calculations --------------------------
# Units (fallback to 1)
joined["units"] = pd.to_numeric(joined.get("Units", 1), errors="coerce").fillna(1)
joined["price_per_unit"] = joined["price"] / joined["units"]

# SB-9 eligibility (simple rule)
sb9_zones = {"RE40", "R1", "RD", "R3"}
joined["SB9_eligible"] = (
    joined["zone_code"].isin(sb9_zones) &
    (joined["lot_sqft"] >= 2_400)
)

# -------------------------- Apply Filters --------------------------
filtered = joined.copy()

if "All" not in zone_filter:
    filtered = filtered[filtered["zone_code"].isin([z.upper() for z in zone_filter])]

filtered = filtered[filtered["price_per_unit"] <= max_price_per_unit]

st.write(f"**{len(filtered)}** listings after filters")

# -------------------------- Interactive Map --------------------------
if not filtered.empty:
    center_lat = filtered.geometry.y.mean()
    center_lon = filtered.geometry.x.mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles="CartoDB positron")

    for _, row in filtered.iterrows():
        popup_html = f"""
        <b>{row.get('address', 'N/A')}</b><br>
        Price: ${row['price']:,.0f}<br>
        $/unit: ${row['price_per_unit']:,.0f}<br>
        Lot: {row['lot_sqft']:,.0f} sq ft<br>
        Zoning: {row['Zoning']}<br>
        SB-9: {'Yes' if row['SB9_eligible'] else 'No'}
        """
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=7,
            popup=folium.Popup(popup_html, max_width=300),
            color="crimson" if row["SB9_eligible"] else "steelblue",
            fill=True,
            fillOpacity=0.8,
        ).add_to(m)

    st_folium(m, width=1200, height=600)
else:
    st.warning("No listings match the current filters.")

# -------------------------- Download CSV --------------------------
csv_data = filtered.drop(columns=["geometry"]).to_csv(index=False).encode()
st.download_button(
    label="Download enriched CSV",
    data=csv_data,
    file_name="DealScout_LA_enriched.csv",
    mime="text/csv",
)

