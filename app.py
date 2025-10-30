# --------------------------------------------------------------
# DealScout LA — FIXED: Buffer + Intersects for Edge Cases
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

# --- Column Indices (Your CSV) ---
price_idx = 132
lat_idx = 311
lon_idx = 254
num_idx = 522
name_idx = 520
suffix_idx = 523
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

# --- Geometry + Buffer (10m to catch edges) ---
mls["geometry"] = mls.apply(lambda r: Point(r.lon, r.lat) if pd.notnull(r.lon) and pd.notnull(r.lat) else None, axis=1)
mls = mls.dropna(subset=["geometry", "price", "lot_sqft"])
gdf = gpd.GeoDataFrame(mls, geometry="geometry", crs="EPSG:4326")
gdf["geometry"] = gdf["geometry"].buffer(0.0001)  # ~10m buffer

# --- Load Zoning (24.9 MB version) ---
@st.cache_resource
def load_zoning():
    try:
        z = gpd.read_file("Zoning.geojson")
        if z.crs is None:
            z.set_crs("EPSG:2229", inplace=True)
        return z.to_crs("EPSG:4326")
    except Exception as e:
        st.error(f"Cannot load Zoning.geojson: {e}")
        st.stop()

zoning = load_zoning()
st.write("**Zoning loaded:**", len(zoning), "polygons")

# --- Select Zoning Field ---
zoning_field = "Zoning"  # Your file's column with CM-1, RD1.5-1
if zoning_field not in zoning.columns:
    st.error("Zoning column not found!")
    st.stop()

# --- Spatial Join (INTERSECTS + BUFFER) ---
gdf = gdf.to_crs(zoning.crs)
zoning_subset = zoning[[zoning_field, "geometry"]].rename(columns={zoning_field: "ZONING_CODE"})
joined = gpd.sjoin(gdf, zoning_subset, how="left", predicate="intersects")
joined["Zoning"] = joined["ZONING_CODE"].fillna("Outside LA")

# --- LA City Only ---
la_city = joined[joined["Zoning"] != "Outside LA"].copy()

# --- Debug Toggle ---
show_all = st.checkbox("Show All Listings (Debug)", value=True)
if show_all:
    display = joined
    st.write(f"**{len(display):,}** total listings")
else:
    if la_city.empty:
        st.warning("No LA City listings. Try 'Show All'.")
        display = joined
    else:
        display = la_city
        st.write(f"**{len(display):,}** LA City deals")

# --- Max Units ---
sqft_map = {
    'CM':800, 'C1':800, 'C2':400, 'C4':400,
    'RD1.5':1500, 'RD2':2000, 'R3':800, 'R4':400, 'R5':200,
    'R1':5000, 'R2':2500
}
display["base"] = display["Zoning"].str.split("-").str[0].str.upper()
display["sqft_per"] = display["base"].map(sqft_map).fillna(5000)
display["max_units"] = (display["lot_sqft"] / display["sqft_per"]).clip(1, 20)
r1 = display["base"].str.startswith("R1")
display.loc[r1, "max_units"] = display.loc[r1, "lot_sqft"].apply(lambda x: 4 if x >= 2400 else 3 if x >= 1000 else 2)
display["price_per_unit"] = (display["price"] / display["max_units"]).round(0)

# --- Filter ---
max_ppu = st.sidebar.slider("Max $/unit", 0, 1000000, 300000, 25000)
filtered = display[display["price_per_unit"] <= max_ppu].copy()

# --- Map ---
if not filtered.empty:
    m = folium.Map([34.05, -118.24], zoom_start=11, tiles="CartoDB positron")
    for _, r in filtered.iterrows():
        color = "lime" if r.price_per_unit < 200000 else "orange" if r.price_per_unit < 400000 else "red"
        folium.CircleMarker(
            [r.geometry.centroid.y, r.geometry.centroid.x], radius=6, color=color, fill=True,
            popup=folium.Popup(
                f"<b>{r.address}</b><br>"
                f"Price: ${r.price:,.0f}<br>"
                f"$/Unit: ${r.price_per_unit:,.0f}<br>"
                f"Max: {r.max_units:.0f}<br>"
                f"Zoning: {r.Zoning}",
                max_width=300
            )
        ).add_to(m)
    st_folium(m, width=1200, height=600)

# --- Download ---
dl = filtered[["address", "price", "price_per_unit", "max_units", "Zoning"]].copy()
dl.columns = ["Address", "Price", "$/Unit", "Max Units", "Zoning"]
dl["Price"] = dl["Price"].apply(lambda x: f"${x:,.0f}")
dl["$/Unit"] = dl["$/Unit"].apply(lambda x: f"${x:,.0f}")
st.download_button("Download", dl.to_csv(index=False), "LA_Deals.csv", "text/csv")

st.success("**LIVE!** 24.9 MB file, buffer + intersects, real LA City deals")
