import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import numpy as np
import folium
from streamlit_folium import st_folium
import warnings
warnings.filterwarnings("ignore")

st.set_page_config(page_title="DealScout LA", layout="wide", page_icon="🏠")

st.title("🏠 DealScout LA")
st.markdown("Upload MLS CSV → Get zoning, $/unit, SB-9, map, and download.")

# Sidebar
st.sidebar.header("Filters")
max_price = st.sidebar.slider("Max $/unit", 0, 10000, 5000)
zone_filter = st.sidebar.multiselect("Zoning", ["RE40", "R1", "RD", "R3", "All"], default="All")

# Upload
uploaded = st.file_uploader("Upload MLS CSV", type="csv")
if uploaded:
    mls = pd.read_csv(uploaded)
    st.write(f"Loaded {len(mls)} listings")

    # Clean
    mls['price'] = pd.to_numeric(mls['CurrentPrice'], errors='coerce')
    mls['lot_sqft'] = pd.to_numeric(mls['LotSizeSquareFeet'], errors='coerce')
    mls['latitude'] = pd.to_numeric(mls['Latitude'], errors='coerce')
    mls['longitude'] = pd.to_numeric(mls['Longitude'], errors='coerce')
    mls['streetnumber'] = mls['StreetNumber'].astype(str)
    mls['streetdirection'] = mls['StreetDirPrefix'].fillna('').astype(str)
    mls['streetdirsuffix'] = mls['StreetDirSuffix'].fillna('').astype(str)
    mls['streetname'] = mls['StreetName'].fillna('').astype(str)
    mls['streetsuffix'] = mls['StreetSuffix'].fillna('').astype(str)

    mls['address'] = (
        mls['streetnumber'] + " " +
        mls['streetdirection'] + " " +
        mls['streetname'] + " " +
        mls['streetdirsuffix'] + " " +
        mls['streetsuffix']
    ).str.strip().str.replace(r'\s+', ' ', regex=True)

    mls['geometry'] = mls.apply(
        lambda r: Point(r.longitude, r.latitude) if pd.notnull(r.longitude) and pd.notnull(r.latitude) else None,
        axis=1
    )
    mls = mls.dropna(subset=['geometry', 'price', 'lot_sqft'])
    gdf = gpd.GeoDataFrame(mls, geometry='geometry', crs="EPSG:4326")

    # Join with zoning
    zoning = gpd.read_file("Zoning.geojson").to_crs("EPSG:4326")
    joined = gpd.sjoin(gdf, zoning, how="left", predicate="within")
    zoning_cols = [c for c in joined.columns if 'Zoning' in c and c != 'Zoning']
    zoning_field = zoning_cols[0] if zoning_cols else 'Zoning'
    joined['Zoning'] = joined[zoning_field].fillna("Outside LA")
    joined['zone_code'] = joined['Zoning'].str.split('-').str[0].str.upper()

    # Zoning map
    sqft_map = {
        'A1':108900,'A2':43560,'RE40':40000,'RE20':20000,'RE15':15000,'RE11':11000,'RE9':9000,
        'RS':7500,'R1':5000,'RU':3500,'RZ2.5':2500,'RZ3':3000,'RZ4':4000,'RW1':2300,'R2':2500,'RW2':2300,
        'RD1.5':1500,'RD2':2000,'RD3':3000,'RD4':4000,'RD5':5000,'RD6':6000,
        'RMP':20000,'R3':800,'RAS3':800,'R4':400,'RAS4':400,'R5':200,
    }
    joined['sqft_per_unit'] = joined['zone_code'].map(sqft_map).fillna(0)
    joined['max_units'] = (joined['lot_sqft'] / joined['sqft_per_unit'].replace(0,1)).replace([np.inf],0)
    joined['price_per_unit'] = (joined['price'] / joined['max_units'].replace(0,1)).replace([np.inf],np.nan)

    def sb9(lot): return 3 if lot < 2400 else 4
    joined['SB9_units'] = joined['lot_sqft'].apply(sb9)

    non_la = joined['sqft_per_unit'] == 0
    joined.loc[non_la, ['max_units','price_per_unit','SB9_units']] = None
    joined.loc[non_la, 'Zoning'] = "Outside LA"

    final = joined[['address','price_per_unit','max_units','Zoning','SB9_units','lot_sqft','latitude','longitude']].copy()
    final['price_per_unit'] = final['price_per_unit'].round(0).astype('Int64')
    final.rename(columns={
        'address': 'Address',
        'price_per_unit': '$/unit',
        'max_units': '# units',
        'SB9_units': 'SB-9 Units'
    }, inplace=True)

    final = final[~final['Zoning'].str.contains("Outside LA")]
    final = final.sort_values('$ / unit').reset_index(drop=True)

    # Filters
    filtered = final[final['$/unit'] <= max_price]
    if zone_filter != ["All"]:
        filtered = filtered[filtered['Zoning'].str.contains('|'.join(zone_filter), case=False)]

    # Display
    col1, col2 = st.columns([1, 2])
    with col1:
        st.metric("Deals", len(filtered))
        st.metric("Avg $/unit", f"${filtered['$/unit'].mean():,.0f}")
    with col2:
        m = folium.Map([34.05, -118.24], zoom_start=11)
        for _, r in filtered.iterrows():
            folium.CircleMarker(
                [r.latitude, r.longitude], radius=6,
                color="red" if r['$/unit'] < 3000 else "orange",
                popup=f"{r.Address}<br>${r['$/unit']:,}/unit"
            ).add_to(m)
        st_folium(m, width=700)

    st.dataframe(filtered[['Address', '$/unit', '# units', 'Zoning']], use_container_width=True)
    csv = filtered.to_csv(index=False).encode()
    st.download_button("Download CSV", csv, "dealscout.csv", "text/csv")
else:
    st.info("Upload CSV to start")
