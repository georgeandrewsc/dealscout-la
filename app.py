# app.py — DealScout.LA
import streamlit as st
import pandas as pd

st.set_page_config(page_title="DealScout.LA", layout="wide")
st.title("DealScout.LA")
st.markdown("**AI sifts 756+ LA deals daily — act first, win more.**")

df = pd.read_csv("dealscout_data.csv")

# Filters
st.sidebar.header("Filters")
show_la = st.sidebar.checkbox("City of LA only", True)
threshold = st.sidebar.slider("Max $/unit", 0, 500000, 100000, 5000)

deals = df.copy()
if show_la:
    deals = deals[deals['Zoning'] != "Outside LA (No Zoning)"]
deals = deals[deals['price_per_unit'] <= threshold].sort_values('price_per_unit')

# Results
st.write(f"**{len(deals)} deals under ${threshold:,}/unit**")
for _, r in deals.iterrows():
    c1, c2, c3, c4 = st.columns([4, 2, 2, 2])
    with c1: st.write(f"**{r['address']}**")
    with c2: st.write(f"${int(r['price_per_unit']):,}")
    with c3: st.write(f"{r['max_units']:.1f} units")
    with c4: st.write(r['Zoning'])
