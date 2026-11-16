# Imports
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import base64, pathlib
import hashlib

st.set_page_config(page_title="SmartRoute – Professional Routing", layout="wide")

# Helper: cached distance matrix (Streamlit cache)
@st.cache_data(show_spinner=False)
def get_distance_matrix(df, routing_provider="openstreetmap", api_key=None):
    """Return driving distances and the source used for the calculation."""
    locations = df[["latitude", "longitude"]]

    if routing_provider == "google":
        if not api_key:
            raise ValueError("A Google Maps API key is required.")
        try:
            from utils import get_google_distance_matrix
            return get_google_distance_matrix(df, api_key), "Google Maps driving distances"
        except Exception as exc:
            raise RuntimeError(
                f"Google Maps driving-distance request failed ({type(exc).__name__})."
            ) from exc

    if routing_provider in ("osrm", "fossgis"):
        from utils import calculate_osrm_distance_matrix
        server = "fossgis" if routing_provider == "fossgis" else "public"
        provider_name = "FOSSGIS OSRM" if routing_provider == "fossgis" else "Public OSRM"
        return (
            calculate_osrm_distance_matrix(locations, server=server),
            f"{provider_name} driving distances",
        )

    graph, graph_error = get_road_graph(df)
    if graph is not None:
        try:
            from utils import calculate_distance_matrix
            return calculate_distance_matrix(locations, graph=graph), "OpenStreetMap road-network distances"
        except Exception as exc:
            graph_error = type(exc).__name__

    from utils import calculate_straight_line_distance_matrix
    error_source = graph_error or "road network unavailable"
    return (
        calculate_straight_line_distance_matrix(locations),
        f"Approximate straight-line distances; road routing unavailable ({error_source})",
    )


@st.cache_data(show_spinner=False)
def get_google_route_shapes(df, routes, api_key):
    from utils import calculate_google_route_geometries
    return calculate_google_route_geometries(df, routes, api_key)


@st.cache_data(show_spinner=False)
def get_osrm_route_shapes(df, routes, server):
    from utils import calculate_osrm_route_geometries
    return calculate_osrm_route_geometries(df, routes, server=server)


@st.cache_resource(show_spinner=False)
def get_road_graph(df):
    """Build and cache the road graph used for distance and map routing."""
    from utils import build_road_graph
    try:
        return build_road_graph(df[["latitude", "longitude"]]), None
    except Exception as exc:
        return None, type(exc).__name__


def get_route_shapes(df, routes, routing_provider, api_key=None):
    """Get road-following geometry and a user-visible warning, if needed."""
    try:
        if routing_provider == "google":
            geometries, missing = get_google_route_shapes(df, routes, api_key)
            if missing:
                return geometries, f"Google Maps road paths unavailable for {len(missing)} vehicle route(s)."
            return geometries, None

        if routing_provider in ("osrm", "fossgis"):
            server = "fossgis" if routing_provider == "fossgis" else "public"
            provider_name = "FOSSGIS OSRM" if routing_provider == "fossgis" else "OSRM"
            geometries, missing = get_osrm_route_shapes(df, routes, server)
            if missing:
                return geometries, f"{provider_name} road paths unavailable for {len(missing)} vehicle route(s)."
            return geometries, None

        graph, graph_error = get_road_graph(df)
        if graph is None:
            return None, f"OpenStreetMap road paths unavailable ({graph_error or 'road network error'})."
        from utils import calculate_route_geometries
        geometries, missing = calculate_route_geometries(df, routes, graph)
        if missing:
            return geometries, f"Road paths unavailable for {len(missing)} route segment(s)."
        return geometries, None
    except Exception as exc:
        provider_name = {
            "google": "Google Maps",
            "osrm": "OSRM",
            "fossgis": "FOSSGIS OSRM",
            "openstreetmap": "OpenStreetMap",
        }.get(routing_provider, "Road")
        return None, f"{provider_name} road geometry unavailable ({type(exc).__name__})."


from utils import load_css

# Load centralized Claymorphism design system
load_css()

# Load background image (base64) and inject into CSS as an animated live canvas
bg_path = pathlib.Path(__file__).parent / "background.jpg"
if bg_path.exists():
    with open(bg_path, "rb") as f:
        bg_base64 = base64.b64encode(f.read()).decode()
    bg_css = f"""
    .stApp::before {{
        content: "";
        position: fixed;
        top: -6%;
        left: -6%;
        width: 112%;
        height: 112%;
        background-image: linear-gradient(rgba(15, 3, 7, 0.20), rgba(30, 6, 14, 0.42)), url('data:image/jpeg;base64,{bg_base64}') !important;
        background-size: cover !important;
        background-position: center 40% !important;
        background-repeat: no-repeat !important;
        z-index: -1;
        pointer-events: none;
        animation: liveBackgroundMotion 22s ease-in-out infinite alternate !important;
        will-change: transform, filter;
    }}
    [data-testid="stAppViewContainer"], .stApp {{
        background: transparent !important;
    }}
    """
    st.markdown(f"<style>{bg_css}</style>", unsafe_allow_html=True)

# Load logo image (base64)
logo_path = pathlib.Path(__file__).parent / "logo.jpg"
logo_b64 = ""
if logo_path.exists():
    with open(logo_path, "rb") as f:
        logo_b64 = base64.b64encode(f.read()).decode()

logo_html = (
    f'<img src="data:image/jpeg;base64,{logo_b64}" class="clay-brand-logo" alt="SmartRoute SR Logo" />'
    if logo_b64
    else '<span class="clay-brand-mark" aria-hidden="true">SR</span>'
)

# Page configuration
# Header card
st.markdown(f"""
<div class="clay-header">
  <div class="clay-brand-lockup">
    {logo_html}
    <div>
      <span class="clay-brand-label">Delivery route planner</span>
      <h1 class="clay-title">SmartRoute</h1>
    </div>
  </div>
  <p class="clay-subtitle">Upload location data, configure your fleet, and let the optimizer find the most fuel‑efficient routes.</p>
</div>
""", unsafe_allow_html=True)

# Sidebar – Data & API key
if logo_b64:
    st.sidebar.markdown(
        f'<div style="text-align: center; padding-top: 0.5rem; margin-bottom: 1.2rem;">'
        f'<img src="data:image/jpeg;base64,{logo_b64}" style="width: 76px; height: 76px; border-radius: 18px; box-shadow: var(--clay-shadow-ivory); object-fit: cover; border: 2px solid rgba(255,255,255,0.95);" alt="SR Logo" />'
        f'</div>',
        unsafe_allow_html=True
    )
st.sidebar.markdown("""
<div class="clay-sidebar-brand">
  <span class="clay-sidebar-mark" aria-hidden="true">DA</span>
  <div>
    <span class="clay-sidebar-label">Inputs &amp; integrations</span>
    <h2 class="clay-sidebar-title">Data &amp; API</h2>
  </div>
</div>
""", unsafe_allow_html=True)
uploaded_file = st.sidebar.file_uploader("Upload CSV (id, latitude, longitude, demand?)", type=["csv"])
provider_choice = st.sidebar.radio(
    "Road routing provider",
    options=[
        "OpenStreetMap road graph",
        "FOSSGIS OSRM (shares selected coordinates)",
        "Public OSRM (shares selected coordinates)",
        "Google Maps (requires API key)",
    ],
    help=(
        "FOSSGIS and public OSRM send the depot and selected delivery coordinates "
        "to their public routing servers. Choose one only if this data sharing is acceptable."
    ),
)
routing_provider = {
    "OpenStreetMap road graph": "openstreetmap",
    "FOSSGIS OSRM (shares selected coordinates)": "fossgis",
    "Public OSRM (shares selected coordinates)": "osrm",
    "Google Maps (requires API key)": "google",
}[provider_choice]
api_key = None
if routing_provider == "google":
    api_key = st.sidebar.text_input("Google Maps API Key", type="password")
    if not api_key:
        st.sidebar.warning("API key required – Google routing disabled.")
        routing_provider = "openstreetmap"

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
    delivery_options = [i for i in range(len(df)) if i != depot_index]
    selected_delivery_indices = st.sidebar.multiselect(
        "Delivery locations",
        options=delivery_options,
        format_func=lambda i: str(df.iloc[i]["id"]),
        help="Only the depot and selected delivery locations are used for routing and the map.",
    )
    if not selected_delivery_indices:
        st.sidebar.caption("Select at least one delivery location to calculate a route.")
    route_row_indices = [depot_index, *selected_delivery_indices]
    route_locations = df.iloc[route_row_indices].reset_index(drop=True)

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
            demands = route_locations["demand"].fillna(0).astype(int).tolist()
            capacities = [vehicle_capacity] * num_vehicles
        else:
            demands = None
            capacities = None
        allow_alternative = st.checkbox("Calculate an alternative route", value=True)
        compute = st.button("🚀 Compute Optimal Routes", disabled=not selected_delivery_indices)
        progress_text_placeholder = st.empty()
        progress_placeholder = st.empty()
        compute_status = st.empty()

    route_settings = (
        int(num_vehicles),
        float(fuel_price),
        bool(capacity_enabled),
        int(vehicle_capacity) if capacity_enabled else None,
        bool(allow_alternative),
        routing_provider,
        hashlib.sha256((api_key or "").encode("utf-8")).hexdigest() if routing_provider == "google" else None,
    )

    def update_progress(message, value):
        progress_text_placeholder.markdown(
            f'<div class="sr-progress-label" role="status">{message} — {value}%</div>',
            unsafe_allow_html=True,
        )
        progress_placeholder.progress(value)

    if compute:
        update_progress("Preparing selected locations", 5)
        update_progress("Calculating driving distances", 20)
        try:
            distance_matrix, distance_source = get_distance_matrix(
                route_locations,
                routing_provider=routing_provider,
                api_key=api_key,
            )
        except Exception as exc:
            progress_text_placeholder.empty()
            progress_placeholder.empty()
            compute_status.error(
                f"Driving-distance request failed ({type(exc).__name__}): {exc}"
            )
            st.stop()

        update_progress("Optimizing the selected route", 50)
        from utils import solve_vrp
        result = solve_vrp(
            distance_matrix,
            num_vehicles=num_vehicles,
            depot=0,
            demands=demands,
            vehicle_capacities=capacities,
            fuel_price=fuel_price,
            allow_alternative=allow_alternative,
        )
        update_progress("Preparing road paths for the map", 80)
        route_geometries, route_geometry_warning = get_route_shapes(
            route_locations,
            result["routes"],
            routing_provider,
            api_key,
        )
        alternative_route_geometries = None
        alternative_route_geometry_warning = None
        if result.get("alternative"):
            alternative_route_geometries, alternative_route_geometry_warning = get_route_shapes(
                route_locations,
                result["alternative"]["routes"],
                routing_provider,
                api_key,
            )
            if alternative_route_geometry_warning:
                alternative_route_geometry_warning = (
                    f"Alternative route: {alternative_route_geometry_warning}"
                )
        if route_geometry_warning:
            route_geometry_warning = f"Recommended route: {route_geometry_warning}"

        update_progress("Saving route results", 95)
        st.session_state["result"] = result
        st.session_state["distance_matrix"] = distance_matrix
        st.session_state["fuel_price"] = fuel_price
        st.session_state["distance_source"] = distance_source
        st.session_state["result_locations"] = route_locations.copy()
        st.session_state["result_settings"] = route_settings
        st.session_state["route_geometries"] = route_geometries
        st.session_state["route_geometry_warning"] = route_geometry_warning
        st.session_state["alternative_route_geometries"] = alternative_route_geometries
        st.session_state["alternative_route_geometry_warning"] = alternative_route_geometry_warning
        update_progress("Route calculation complete", 100)
        if distance_source.startswith("Approximate"):
            compute_status.warning(
                "Route estimate ready, but road data was unavailable. Distances are approximate."
            )
        elif route_geometry_warning or alternative_route_geometry_warning:
            compute_status.warning("Routes calculated; one or more road paths could not be displayed.")
        else:
            compute_status.success("✅ Road routes calculated and displayed!")

    saved_locations = st.session_state.get("result_locations")
    result_matches_inputs = (
        "result" in st.session_state
        and isinstance(saved_locations, pd.DataFrame)
        and saved_locations.equals(route_locations)
        and st.session_state.get("result_settings") == route_settings
    )
    if "result" in st.session_state and not result_matches_inputs:
        with tab_output:
            st.info("Routing inputs changed. Compute Optimal Routes to update the results.")

    if "result" in st.session_state and result_matches_inputs:
        with tab_output:
            result = st.session_state["result"]
            distance_matrix = st.session_state["distance_matrix"]
            result_fuel_price = st.session_state.get("fuel_price", fuel_price)
            st.subheader("🗂 Summary")
            distance_source = st.session_state.get("distance_source", "Road distance source unavailable")
            if distance_source.startswith("Approximate"):
                st.warning(
                    f"{distance_source}. Enable Google Maps routing with an API key "
                    "or choose FOSSGIS/public OSRM (which shares selected coordinates) "
                    "to calculate actual driving distances."
                )
            else:
                st.caption(f"Distance calculation: {distance_source}.")
            summary = pd.DataFrame({
                "Vehicle": list(range(num_vehicles)),
                "Route (stop order)": [
                    " -> ".join(str(route_locations.iloc[index]["id"]) for index in route)
                    for route in result["routes"]
                ],
                "Total Distance (km)": [round(sum(
                    distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                ), 2) for r in result["routes"]],
                "Fuel Cost": [round(sum(
                    distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                ) * result_fuel_price, 2) for r in result["routes"]],
            })
            st.table(summary)
            # Download summary CSV
            csv = summary.to_csv(index=False).encode('utf-8')
            st.download_button(label="Download Summary CSV", data=csv, file_name="routing_summary.csv", mime='text/csv')

            # Map
            st.subheader("🗺 Route Map")
            map_indices = sorted({index for route in result["routes"] for index in route})
            map_locations = route_locations.iloc[map_indices]
            center_lat = map_locations["latitude"].mean()
            center_lon = map_locations["longitude"].mean()
            route_geometries = st.session_state.get("route_geometries")
            route_geometry_warning = st.session_state.get("route_geometry_warning")
            alternative_route_geometries = st.session_state.get("alternative_route_geometries")
            alternative_route_geometry_warning = st.session_state.get(
                "alternative_route_geometry_warning"
            )
            for warning in (route_geometry_warning, alternative_route_geometry_warning):
                if warning:
                    st.warning(warning)
            m = folium.Map(
                location=[center_lat, center_lon],
                zoom_start=7,
                tiles='OpenStreetMap',
                scrollWheelZoom=False,
            )
            colors = ["#6B1728", "#8E2338", "#D98890", "#B23A48", "#4A0E1B"]
            recommended_layer = folium.FeatureGroup(name="Recommended route", show=True)
            alternative_layer = folium.FeatureGroup(name="Alternative route", show=True)
            has_recommended_geometry = False
            has_alternative_geometry = False
            for veh_id, route in enumerate(result["routes"]):
                col = colors[veh_id % len(colors)]
                if route_geometries and route_geometries[veh_id]:
                    folium.PolyLine(
                        locations=route_geometries[veh_id],
                        color=col,
                        weight=6,
                        opacity=0.95,
                        smooth_factor=1,
                        tooltip=f"Recommended route - Vehicle {veh_id + 1}",
                    ).add_to(recommended_layer)
                    has_recommended_geometry = True
                for idx in route:
                    p = route_locations.iloc[idx]
                    folium.CircleMarker(location=(p["latitude"], p["longitude"]), radius=4,
                                        color=col, fill=True, fill_color=col,
                                        popup=f"ID: {p.get('id', idx)}").add_to(m)
            if has_recommended_geometry:
                recommended_layer.add_to(m)
            if alternative_route_geometries:
                for veh_id, geometry in enumerate(alternative_route_geometries):
                    if geometry:
                        folium.PolyLine(
                            locations=geometry,
                            color="#1675A9",
                            weight=5,
                            opacity=0.9,
                            dash_array="10, 8",
                            smooth_factor=1,
                            tooltip=f"Alternative route - Vehicle {veh_id + 1}",
                        ).add_to(alternative_layer)
                        has_alternative_geometry = True
            if has_alternative_geometry:
                alternative_layer.add_to(m)
            if has_recommended_geometry or has_alternative_geometry:
                folium.LayerControl(position="topright", collapsed=False).add_to(m)
                st.caption("Use the map layer control to compare the recommended and alternative road routes.")
            m.fit_bounds(
                [
                    [map_locations["latitude"].min(), map_locations["longitude"].min()],
                    [map_locations["latitude"].max(), map_locations["longitude"].max()],
                ],
                padding=(24, 24),
                max_zoom=12,
            )
            st_folium(m, height=600, use_container_width=True)

            # Alternative route section
            if result.get("alternative"):
                alt = result["alternative"]
                st.subheader("🔀 Alternative Route Calculation")
                alt_distance = alt["total_distance"]
                alt_cost = alt["total_cost"]
                distance_metric, cost_metric = st.columns(2)
                distance_metric.metric("Alternative distance", f"{alt_distance:.2f} km")
                cost_metric.metric("Estimated fuel cost", f"{alt_cost:.2f}")
                st.caption(
                    f"Fuel cost = {alt_distance:.2f} km × "
                    f"{result_fuel_price:.2f} per km = {alt_cost:.2f}"
                )
                alt_df = pd.DataFrame({
                    "Vehicle": list(range(num_vehicles)),
                    "Route (stop order)": [
                        " -> ".join(str(route_locations.iloc[index]["id"]) for index in route)
                        for route in alt["routes"]
                    ],
                    "Total Distance (km)": [round(sum(
                        distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                    ), 2) for r in alt["routes"]],
                    "Fuel Cost": [round(sum(
                        distance_matrix[r[i], r[i+1]] for i in range(len(r)-1)
                    ) * result_fuel_price, 2) for r in alt["routes"]],
                })
                st.table(alt_df)
            elif allow_alternative:
                st.info(
                    "No alternative route is in the saved result. "
                    "Click Compute Optimal Routes to calculate one."
                )

            # Clear cache button
            if st.button("🗑 Clear Cached Results"):
                for k in [
                    "result",
                    "distance_matrix",
                    "result_locations",
                    "result_settings",
                    "fuel_price",
                    "distance_source",
                    "route_geometries",
                    "route_geometry_warning",
                    "alternative_route_geometries",
                    "alternative_route_geometry_warning",
                ]:
                    if k in st.session_state:
                        del st.session_state[k]
                st.experimental_rerun()
else:
    st.info("Upload a CSV file to begin the routing workflow.")
