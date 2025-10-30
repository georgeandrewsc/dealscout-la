# --------------------------------------------------------------
# DealScout LA – City of Los Angeles ONLY
# --------------------------------------------------------------
import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium
import os

# --------------------------------------------------------------
# Page config
# --------------------------------------------------------------
st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("DealScout LA")
st.markdown("**Upload MLS CSV → Search for LA City Deals**")

# --------------------------------------------------------------
# Helper – find column (case-insensitive + letter fallback)
# --------------------------------------------------------------
def find_col(df, candidates):
    cols_lower = [c.lower() for c in df.columns]
    for cand in candidates:
        if cand.lower() in cols_lower:
            return df.columns[cols_lower.index(cand.lower())]
    return None

# --------------------------------------------------------------
# Upload CSV
# --------------------------------------------------------------
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# --------------------------------------------------------------
# Required columns (friendly name OR Excel letter)
# --------------------------------------------------------------
price_col = find_col(mls, ["CurrentPrice", "price", "ListPrice", "EC"])          # EC = price
lot_col   = find_col(mls, ["LotSizeSquareFeet", "lot_sqft", "LotSizeAcres"])
lat_col   = find_col(mls, ["Latitude", "lat", "KZ"])                            # KZ = lat
lon_col   = find_col(mls, ["Longitude", "lon", "IU"])                           # IU = lon

# ----- ADDRESS PIECES (TC, TA, TD) -----
num_col   = find_col(mls, ["StreetNumber", "TC"])          # TC = number
name_col  = find_col(mls, ["StreetName", "TA"])            # TA = name
suffix_col= find_col(mls, ["StreetSuffix", "TD"])          # TD = suffix

if not all([price_col, lot_col, lat_col, lon_col, num_col, name_col, suffix_col]):
    st.error(
        "CSV must contain: price (EC), lot size, Latitude (KZ), Longitude (IU), "
        "StreetNumber (TC), StreetName (TA), StreetSuffix (TD)."
    )
    st.stop()

# --------------------------------------------------------------
# Clean numeric fields
# --------------------------------------------------------------
mls["price"] = pd.to_numeric(mls[price_col], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls[lot_col], errors="coerce")
if "acres" in lot_col.lower():
    mls["lot_sqft"] = mls["lot_sqft"] * 43560

mls["lat"] = pd.to_numeric(mls[lat_col], errors="coerce")
mls["lon"] = pd.to_numeric(mls[lon_col], errors="coerce")

# --------------------------------------------------------------
# Build **full** address – e.g. "2622 S Cochran Ave"
# --------------------------------------------------------------
def clean(series):
    return series.astype(str).str.strip().replace({"nan":"", "None":"", "<NA>":""}, regex=False)

num   = clean(mls[num_col])
name  = clean(mls[name_col])
suf   = clean(mls[suffix_col])

address = [
    " ".join(filter(None, [n, nm, s]))
    for n, nm, s in zip(num, name, suf)
]
mls["address"] = [a if a else "Unknown Address" for a in address]

# --------------------------------------------------------------
# Geometry
# --------------------------------------------------------------
mls["geometry"] = mls.apply(
    lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None,
    axis=1
)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# --------------------------------------------------------------
# Load Zoning (cached)
# --------------------------------------------------------------
if not st.session_state.get("zoning_processed", False):
    @st.cache_resource
    def load_zoning():
        path = "Zoning.geojson"
        if not os.path.exists(path):
            st.error(f"`{path}` not found – place it next to `app.py`.")
            st.stop()
        return gpd.read_file(path)

    zoning = load_zoning()
    st.write("**Zoning columns:**", zoning.columns.tolist())

    # Auto-pick a column that contains "zone"
    zone_cols = [c for c in zoning.columns if "zone" in c.lower()]
    default = zone_cols[0] if zone_cols else zoning.columns[0]

    zoning_field = st.selectbox(
        "Select zoning column (e.g. ZONE_CLASS, ZONECODE, ZONE)",
        options=zoning.columns,
        index=zoning.columns.get_loc(default),
        key="zoning_sel"
    )
    st.session_state.update(zoning_field=zoning_field, zoning=zoning, zoning_processed=True)
    st.success(f"Using **{zoning_field}**")
else:
    zoning = st.session_state.zoning
    zoning_field = st.session_state.zoning_field
    st.success(f"Using cached **{zoning_field}**")

# --------------------------------------------------------------
# Spatial join
# --------------------------------------------------------------
gdf = gdf.to_crs(zoning.crs)
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA (No Zoning)")

# --------------------------------------------------------------
# Keep ONLY City of Los Angeles
# --------------------------------------------------------------
la_city = joined[joined["Zoning"] != "Outside LA (No Zoning)"].copy()
if la_city.empty:
    st.error("No listings inside the City of Los Angeles.")
    st.stop()
st.write(f"**{len(la_city):,}** listings inside LA City (out of {len(joined):,})")

# --------------------------------------------------------------
# Max-units look-up (base zone only)
# --------------------------------------------------------------
sqft_per_unit_map = {
    'A1':108900,'A2':43560,'RE40':40000,'RE20':20000,'RE15':15000,'RE11':11000,'RE9':9000,
    'RS':7500,'R1':5000,'R1V':5000,'R1F':5000,'R1R':5000,'R1H':5000,
    'RU':3500,'RZ2.5':2500,'RZ3':3000,'RZ4':4000,'RW1':2300,'R2':2500,'RW2':2300,
    'RD1.5':1500,'RD2':2000,'RD3':3000,'RD4':4000,'RD5':5000,'RD6':6000,
    'RMP':20000,'R3':800,'RAS3':800,'R4':400,'RAS4':400,'R5':200,
    'C1':800,'C1.5':800,'C2':400,'C4':400,'C5':400,'CM':800,'CR':400,
    'MR1':400,'M1':400,'MR2':200,'M2':200,
}

la_city["base_zone"] = la_city["Zoning"].astype(str).str.split('-').str[0].str.upper()
la_city["sqft_per_unit"] = la_city["base_zone"].map(sqft_per_unit_map).fillna(5000)
la_city["max_units"] = (la_city["lot_sqft"] / la_city["sqft_per_unit"]).clip(lower=1, upper=20)

# SB-9 boost for R1 zones
r1 = la_city["base_zone"].str.startswith("R1")
la_city.loc[r1, "max_units"] = la_city.loc[r1, "lot_sqft"].apply(
    lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2
)

la_city["price_per_unit"] = (la_city["price"] / la_city["max_units"]).round(0)

# --------------------------------------------------------------
# Sidebar filters
# --------------------------------------------------------------
max_ppu = st.sidebar.slider("Max $/unit", 0, 2_000_000, 500_000, 50_000, key="ppu")
zone_opts = ["All"] + sorted(la_city["Zoning"].dropna().unique().tolist())
zone_sel = st.sidebar.multiselect("Zoning", zone_opts, ["All"], key="zone")

filtered = la_city[la_city["price_per_unit"] <= max_ppu].copy()
if "All" not in zone_sel:
    filtered = filtered[filtered["Zoning"].isin(zone_sel)]

st.write(f"**{len(filtered):,}** deals after filters")

# --------------------------------------------------------------
# Interactive map
# --------------------------------------------------------------
if not filtered.empty:
    m = folium.Map(location=[34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r["price_per_unit"] < 300_000 else "orange" if r["price_per_unit"] < 600_000 else "red"
        popup = folium.Popup(
            f"<b>{r['address']}</b><br>"
            f"Price: ${r['price']:,.0f}<br>"
            f"$/Unit: ${r['price_per_unit']:,.0f}<br>"
            f"Max Units: {r['max_units']:.0f}<br>"
            f"Zoning: {r['Zoning']}",
            max_width=300,
        )
        folium.CircleMarker(
            location=[r.geometry.y, r.geometry.x],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.8,
            popup=popup,
        ).add_to(m)
    st_folium(m, width=1200, height=600)
else:
    st.warning("No deals match the filters – try a higher $/unit limit.")

# --------------------------------------------------------------
# Download CSV
# --------------------------------------------------------------
dl = filtered[["address", "price", "price_per_unit", "max_units", "Zoning"]].copy()
dl.columns = ["Address", "Price", "$/Unit", "Max Units", "Zoning"]
dl["Price"] = dl["Price"].apply(lambda x: f"${x:,.0f}")
dl["$/Unit"] = dl["$/Unit"].apply(lambda x: f"${x:,.0f}")

st.download_button(
    "Download LA City Deals",
    data=dl.to_csv(index=False),
    file_name="DealScout_LA_City_Only.csv",
    mime="text/csv",
)

st.success("**Done!** Full address (TC+TA+TD), exact zoning (e.g. RD1.5-1), City of LA only.")
