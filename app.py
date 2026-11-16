# Imports
import streamlit as st
import pandas as pd
import folium
from requests.exceptions import ConnectionError, Timeout
from streamlit_folium import st_folium
import base64, pathlib

st.set_page_config(page_title="SmartRoute – Professional Routing", layout="wide")

# Helper: cached distance matrix (Streamlit cache)
@st.cache_data(show_spinner=False)
def get_distance_matrix(df, use_google=False, api_key=None):
    """Return distance matrix.
    If `use_google` is True and a valid `api_key` is provided, uses Google Directions API.
    Otherwise falls back to OSM‑Nx (or straight‑line if OSM fails).
    """
    locations = df[["latitude", "longitude"]]
    try:
        if use_google and api_key:
            n = len(locations)
            dist = [[0.0] * n for _ in range(n)]
            for i in range(n):
                for j in range(i + 1, n):
                    coords_i = (float(locations.iloc[i]["latitude"]), float(locations.iloc[i]["longitude"]))
                    coords_j = (float(locations.iloc[j]["latitude"]), float(locations.iloc[j]["longitude"]))
                    route = get_google_route_coords(coords_i, coords_j, api_key)
                    # Approximate haversine distance along polyline
                    d = 0.0
                    from math import radians, sin, cos, sqrt, atan2
                    R = 6371.0
                    for k in range(1, len(route)):
                        lat1, lon1 = route[k-1]
                        lat2, lon2 = route[k]
                        dlat = radians(lat2 - lat1)
                        dlon = radians(lon2 - lon1)
                        a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
                        c = 2 * atan2(sqrt(a), sqrt(1-a))
                        d += R * c
                    dist[i][j] = dist[j][i] = d
            return pd.DataFrame(dist).values
        else:
            from utils import calculate_distance_matrix
            return calculate_distance_matrix(locations)
    except Exception:
        from utils import calculate_straight_line_distance_matrix
        return calculate_straight_line_distance_matrix(locations)

# Custom CSS – glassmorphism with dune ochre, deep umber, turquoise palette and Departure Mono font
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Departure+Mono&display=swap');
    html, body {font-family: 'Departure Mono', monospace; background: linear-gradient(135deg, #3B2F2F, #C19A6B); color: #E0E0E0; margin:0;}
    .stApp {padding:2rem;}
    .css-1d391kg {background:rgba(60,45,35,0.45)!important; backdrop-filter:blur(12px); border-right:1px solid rgba(48,213,200,0.2);}
    .card {background:rgba(70,55,45,0.55); border-radius:12px; padding:1.5rem; margin-bottom:1.5rem; box-shadow:0 8px 32px 0 rgba(0,0,0,0.37); border:1px solid rgba(48,213,200,0.2);}
    .title {font-size:2.2rem; font-weight:600; color:#30D5C8; margin-bottom:.5rem;}
    .subtitle {font-size:1rem; color:#E0E0E0; margin-bottom:1rem;}
    .stSelectbox, .stNumberInput, .stFileUploader, .stCheckbox, .stTextInput {background:rgba(80,65,55,0.6); color:#E0E0E0; border-radius:8px; padding:.5rem; border:1px solid rgba(48,213,200,0.3);}
    .stButton button {background:linear-gradient(145deg,#C19A6B,#C19A6B); color:#3B2F2F; border:none; border-radius:8px; padding:.5rem 1rem; font-weight:500; cursor:pointer; box-shadow:0 4px 12px rgba(0,0,0,0.2);}
    .stButton button:hover {background:linear-gradient(145deg,#30D5C8,#C19A6B);}
    .stDataFrame table {border-collapse:separate; border-spacing:0; background:rgba(70,55,45,0.55); border-radius:8px; overflow:hidden; color:#E0E0E0;}
    .stDataFrame th {background:rgba(90,75,65,0.6); color:#30D5C8; padding:.75rem;}
    .stDataFrame td {padding:.75rem;}
</style>
""", unsafe_allow_html=True)


# Load background image (base64) and inject into CSS
import base64, pathlib
bg_path = pathlib.Path(__file__).parent / "background.jpg"
if bg_path.exists():
    with open(bg_path, "rb") as f:
        bg_base64 = base64.b64encode(f.read()).decode()
    bg_css = f"body {{background-image: url('data:image/jpeg;base64,{bg_base64}'); background-size: cover; background-attachment: fixed;}}"
    st.markdown(f"<style>{bg_css}</style>", unsafe_allow_html=True)

# Page configuration
# Header card
st.markdown("""
<div class="card">
  <h1 class="title">SmartRoute – Professional Delivery Routing</h1>
  <p class="subtitle">Upload location data, configure your fleet, and let the optimizer find the most fuel‑efficient routes.</p>
</div>
""", unsafe_allow_html=True)

# Sidebar – Data & API key
st.sidebar.header("📂 Data & API")
uploaded_file = st.sidebar.file_uploader("Upload CSV (id, latitude, longitude, demand?)", type=["csv"])
use_google = st.sidebar.checkbox("Use Google Maps routing (requires API key)")
api_key = None
if use_google:
    api_key = st.sidebar.text_input("Google Maps API Key", type="password")
    if not api_key:
        st.sidebar.warning("API key required – Google routing disabled.")
        use_google = False

# Main workflow
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    if not {"latitude", "longitude"}.issubset(df.columns):
        st.error("CSV must contain 'latitude' and 'longitude' columns.")
        st.stop()
    # Ensure an ID column exists for display
    if "id" not in df.columns:
        df.insert(0, "id", range(len(df)))
    depot_index = st.sidebar.selectbox("Depot (start/end)", options=range(len(df)), format_func=lambda i: str(df.iloc[i]["id"]))

    # Tabs for clean separation
    tab_data, tab_params, tab_output = st.tabs(["Data", "Parameters", "Output"])

    with tab_data:
        st.subheader("Uploaded Data")
        st.dataframe(df)

    with tab_params:
        st.subheader("Routing Parameters")
        num_vehicles = st.number_input("Number of vehicles", min_value=1, value=1, step=1)
        fuel_price = st.number_input("Fuel price per km", min_value=0.0, value=1.0, step=0.1)
        capacity_enabled = st.checkbox("Enable vehicle capacity constraints")
        if capacity_enabled:
            if "demand" not in df.columns:
                st.error("'demand' column missing – required for capacity constraints.")
                st.stop()
            vehicle_capacity = st.number_input("Vehicle capacity (sum of demands)", min_value=1, value=100, step=1)
            demands = df["demand"].fillna(0).astype(int).tolist()
            capacities = [vehicle_capacity] * num_vehicles
        else:
            demands = None
            capacities = None
        allow_alternative = st.checkbox("Show alternative (second‑best) route")
        compute = st.button("🚀 Compute Optimal Routes")

    if compute:
        with st.spinner("Preparing distance matrix…"):
            distance_matrix = get_distance_matrix(df, use_google=use_google, api_key=api_key)
        with st.spinner("Solving VRP…"):
            from utils import solve_vrp
            result = solve_vrp(
                distance_matrix,
                num_vehicles=num_vehicles,
                depot=depot_index,
                demands=demands,
                vehicle_capacities=capacities,
                fuel_price=fuel_price,
                allow_alternative=allow_alternative,
            )
        st.session_state["result"] = result
        st.session_state["distance_matrix"] = distance_matrix
        st.success("✅ Routing completed!")

    if "result" in st.session_state:
        with tab_output:
            result = st.session_state["result"]
            distance_matrix = st.session_state["distance_matrix"]
            st.subheader("🗂 Summary")
            summary = pd.DataFrame({
                "Vehicle": list(range(num_vehicles)),
                "Total Distance (km)": [round(sum(
                    distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                ), 2) for r in result["routes"]],
                "Fuel Cost": [round(sum(
                    distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                ) * fuel_price, 2) for r in result["routes"]],
            })
            st.table(summary)
            # Download summary CSV
            csv = summary.to_csv(index=False).encode('utf-8')
            st.download_button(label="Download Summary CSV", data=csv, file_name="routing_summary.csv", mime='text/csv')

            # Map
            st.subheader("🗺 Route Map")
            center_lat = df["latitude"].mean()
            center_lon = df["longitude"].mean()
            m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles='OpenStreetMap')
            colors = ["#b8860b", "#ffd700", "#c0a080", "#8b5a2b", "#deb887"]
            for veh_id, route in enumerate(result["routes"]):
                col = colors[veh_id % len(colors)]
                for i in range(len(route)-1):
                    a = df.iloc[route[i]]
                    b = df.iloc[route[i+1]]
                    folium.PolyLine(locations=[(a["latitude"], a["longitude"]), (b["latitude"], b["longitude"])],
                                    color=col, weight=4, opacity=0.7).add_to(m)
                for idx in route:
                    p = df.iloc[idx]
                    folium.CircleMarker(location=(p["latitude"], p["longitude"]), radius=4,
                                        color=col, fill=True, fill_color=col,
                                        popup=f"ID: {p.get('id', idx)}").add_to(m)
            st_folium(m, height=600)

            # Alternative route section
            if allow_alternative and result.get("alternative"):
                st.subheader("🔀 Alternative Routes")
                alt = result["alternative"]
                alt_df = pd.DataFrame({
                    "Vehicle": list(range(num_vehicles)),
                    "Total Distance (km)": [round(sum(
                        distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                    ), 2) for r in alt["routes"]],
                    "Fuel Cost": [round(sum(
                        distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                    ) * fuel_price, 2) for r in alt["routes"]],
                })
                st.table(alt_df)

            # Clear cache button
            if st.button("🗑 Clear Cached Results"):
                for k in ["result", "distance_matrix"]:
                    if k in st.session_state:
                        del st.session_state[k]
                st.experimental_rerun()
else:
    st.info("Upload a CSV file to begin the routing workflow.")
