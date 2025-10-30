# --------------------------------------------------------------
# DealScout LA — FINAL 100% WORKING VERSION
# --------------------------------------------------------------

import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="DealScout LA", layout="wide")
st.title("DealScout LA")
st.markdown("**Upload MLS CSV → Search for LA City Deals**")

# --- Upload CSV ---
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if not uploaded:
    st.info("Upload a CSV to start.")
    st.stop()

mls = pd.read_csv(uploaded)
st.write(f"**{len(mls):,}** raw listings loaded")

# --- Debug: Count "Los Angeles" ---
city_col = next((c for c in mls.columns if 'city' in c.lower()), None)
if city_col:
    la_count = mls[city_col].astype(str).str.contains('los angeles', case=False, na=False).sum()
    st.write(f"**{la_count} listings with 'Los Angeles' in `{city_col}`**")

# --- Column Indices ---
price_idx = 132   # EC
lat_idx = 311     # KZ
lon_idx = 254     # IU
num_idx = 522     # TC
name_idx = 520    # TA
suffix_idx = 523  # TD
lot_idx = mls.columns.get_loc("LotSizeSquareFeet")

# --- Clean Data ---
mls["price"] = pd.to_numeric(mls.iloc[:, price_idx], errors="coerce")
mls["lot_sqft"] = pd.to_numeric(mls.iloc[:, lot_idx], errors="coerce")
if "acres" in mls.columns[lot_idx].lower():
    mls["lot_sqft"] *= 43560
mls["lat"] = pd.to_numeric(mls.iloc[:, lat_idx], errors="coerce")
mls["lon"] = pd.to_numeric(mls.iloc[:, lon_idx], errors="coerce")

# --- Full Address ---
def clean(series):
    return series.astype(str).str.strip().replace({"nan": "", "NaN": "", "None": ""}, regex=False)

num = clean(mls.iloc[:, num_idx])
name = clean(mls.iloc[:, name_idx])
suf = clean(mls.iloc[:, suffix_idx])

mls["address"] = (num + " " + name + " " + suf).str.replace(r"\s+", " ", regex=True).str.strip()
mls["address"] = mls["address"].replace("", "Unknown Address")

# --- Geometry ---
mls["geometry"] = mls.apply(lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None, axis=1)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")

# --- Load Zoning (FORCE CRS TO 4326) ---
@st.cache_resource
def load_zoning():
    try:
        z = gpd.read_file("Zoning.geojson")
        if z.crs is None:
            z.set_crs("EPSG:2229", inplace=True)  # LA City default
        return z.to_crs("EPSG:4326")  # Convert to lat/lon for join
    except Exception as e:
        st.error(f"Cannot load Zoning.geojson: {e}")
        st.stop()

zoning = load_zoning()
st.write("**Zoning CRS:**", zoning.crs)
st.write("**Zoning columns:**", zoning.columns.tolist())

# Auto-select "Zoning" (with CM-1, RD1.5-1, etc.)
default = "Zoning" if "Zoning" in zoning.columns else zoning.columns[0]
zoning_field = st.selectbox("Select Zoning Field", zoning.columns, index=zoning.columns.get_loc(default))
st.success(f"Using **{zoning_field}** → e.g., CM-1, RD1.5-1")

# --- Spatial Join ---
gdf = gdf.to_crs(zoning.crs)
joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
joined["Zoning"] = joined[zoning_field].fillna("Outside LA")

# --- LA City Filter ---
la_city = joined[joined["Zoning"] != "Outside LA"].copy()

# --- Debug Toggle ---
show_all = st.checkbox("Show All Listings (Debug Mode)", value=False)
if show_all:
    display = joined
    st.write(f"**{len(display):,}** total listings (all areas)")
else:
    if la_city.empty:
        st.warning("No listings inside LA City polygons. Try 'Show All' to see why.")
        display = joined
    else:
        display = la_city
        st.write(f"**{len(display):,}** LA City deals")

# --- Max Units Calculation ---
sqft_map = {
    'CM':800, 'C1':800, 'C1.5':800, 'C2':400, 'C4':400, 'C5':400,
    'RD1.5':1500, 'RD2':2000, 'R3':800, 'R4':400, 'R5':200,
    'R1':5000, 'R2':2500
}
display["base"] = display["Zoning"].str.split("-").str[0].str.upper()
display["sqft_per"] = display["base"].map(sqft_map).fillna(5000)
display["max_units"] = (display["lot_sqft"] / display["sqft_per"]).clip(1, 20)

# SB-9 for R1
r1_mask = display["base"].str.startswith("R1")
display.loc[r1_mask, "max_units"] = display.loc[r1_mask, "lot_sqft"].apply(
    lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2
)

display["price_per_unit"] = (display["price"] / display["max_units"]).round(0)

# --- Sidebar Filters ---
max_ppu = st.sidebar.slider("Max $/unit", 0, 2000000, 500000, 50000)
zone_filter = st.sidebar.multiselect("Zoning Code", ["All"] + sorted(display["Zoning"].dropna().unique().tolist()), ["All"])

filtered = display[display["price_per_unit"] <= max_ppu].copy()
if "All" not in zone_filter:
    filtered = filtered[filtered["Zoning"].isin(zone_filter)]

st.write(f"**{len(filtered):,}** deals after filters")

# --- Interactive Map ---
if not filtered.empty:
    m = folium.Map(location=[34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 300000 else "orange" if r.price_per_unit < 600000 else "red"
        popup = folium.Popup(
            f"<b>{r.address}</b><br>"
            f"Price: ${r.price:,.0f}<br>"
            f"$/Unit: ${r.price_per_unit:,.0f}<br>"
            f"Max Units: {r.max_units:.0f}<br>"
            f"Zoning: {r.Zoning}",
            max_width=300
        )
        folium.CircleMarker(
            location=[r.geometry.y, r.geometry.x],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.8,
            popup=popup
        ).add_to(m)
    st_folium(m, width=1200, height=600)
else:
    st.warning("No deals match filters. Try increasing Max $/unit.")

# --- Download CSV ---
dl = filtered[["address", "price", "price_per_unit", "max_units", "Zoning"]].copy()
dl.columns = ["Address", "Price", "$/Unit", "Max Units", "Zoning"]
dl["Price"] = dl["Price"].apply(lambda x: f"${x:,.0f}")
dl["$/Unit"] = dl["$/Unit"].apply(lambda x: f"${x:,.0f}")
st.download_button(
    "Download LA City Deals",
    data=dl.to_csv(index=False),
    file_name="DealScout_LA_City_Only.csv",
    mime="text/csv"
)

st.success("**DONE!** Full address format, real zoning (CM-1, RD1.5-1, etc.), City of LA only.")
