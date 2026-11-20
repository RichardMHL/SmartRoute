# Imports
import streamlit as st
import base64, pathlib
import hashlib
import time

st.set_page_config(page_title="SmartRoute – Professional Routing", layout="wide")


def format_duration_minutes(minutes):
    """Format an estimated duration using a readable scale from minutes to years."""
    minutes = float(minutes)
    if minutes < 60:
        rounded_minutes = round(minutes)
        unit = "minute" if rounded_minutes == 1 else "minutes"
        return f"~{rounded_minutes} {unit}"

    hours = minutes / 60
    if hours < 24:
        value, unit = hours, "hour"
    else:
        days = hours / 24
        if days < 7:
            value, unit = days, "day"
        elif days < 30.436875:
            value, unit = days / 7, "week"
        elif days < 365.2425:
            value, unit = days / 30.436875, "month"
        else:
            value, unit = days / 365.2425, "year"

    rounded_value = round(value, 1)
    if rounded_value != 1:
        unit += "s"
    return f"~{rounded_value:.1f} {unit}"


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
            return get_google_distance_matrix(locations, api_key), "Google Maps driving distances"
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

    graph, graph_error = get_road_graph(locations)
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


@st.cache_data(show_spinner=False)
def get_optimized_routes(
    distance_matrix,
    num_vehicles,
    demands,
    vehicle_capacities,
    allow_alternative,
    max_alternatives,
):
    from utils import solve_vrp
    return solve_vrp(
        distance_matrix,
        num_vehicles=num_vehicles,
        depot=0,
        demands=demands,
        vehicle_capacities=vehicle_capacities,
        fuel_price=1.0,
        allow_alternative=allow_alternative,
        max_alternatives=max_alternatives,
    )


@st.cache_resource(show_spinner=False)
def get_road_graph(locations):
    """Build and cache the road graph used for distance and map routing."""
    from utils import build_road_graph
    try:
        return build_road_graph(locations), None
    except Exception as exc:
        return None, type(exc).__name__


def get_route_shapes(df, routes, routing_provider, api_key=None):
    """Get road-following geometry and a user-visible warning, if needed."""
    locations = df[["latitude", "longitude"]]
    try:
        if routing_provider == "google":
            geometries, missing = get_google_route_shapes(locations, routes, api_key)
            if missing:
                return geometries, f"Google Maps road paths unavailable for {len(missing)} vehicle route(s)."
            return geometries, None

        if routing_provider in ("osrm", "fossgis"):
            server = "fossgis" if routing_provider == "fossgis" else "public"
            provider_name = "FOSSGIS OSRM" if routing_provider == "fossgis" else "OSRM"
            geometries, missing = get_osrm_route_shapes(locations, routes, server)
            if missing:
                return geometries, f"{provider_name} road paths unavailable for {len(missing)} vehicle route(s)."
            return geometries, None

        graph, graph_error = get_road_graph(locations)
        if graph is None:
            return None, f"OpenStreetMap road paths unavailable ({graph_error or 'road network error'})."
        from utils import calculate_route_geometries
        geometries, missing = calculate_route_geometries(locations, routes, graph)
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


@st.cache_data(show_spinner=False)
def load_app_assets(asset_signatures):
    """Load and base64-encode UI assets once across all user reruns."""
    base_dir = pathlib.Path(__file__).parent
    css_text = ""
    css_path = base_dir / "design_system.css"
    if css_path.exists():
        css_text = css_path.read_text(encoding="utf-8")

    bg_b64 = ""
    bg_path = base_dir / "background.jpg"
    if bg_path.exists():
        bg_b64 = base64.b64encode(bg_path.read_bytes()).decode("ascii")

    logo_b64 = ""
    logo_path = base_dir / "logo.jpg"
    if logo_path.exists():
        logo_b64 = base64.b64encode(logo_path.read_bytes()).decode("ascii")

    return css_text, bg_b64, logo_b64


# Load cached design system assets (CSS, background canvas, logo)
asset_root = pathlib.Path(__file__).parent
asset_signatures = tuple(
    (path.name, path.stat().st_mtime_ns, path.stat().st_size)
    if path.exists()
    else (path.name, None, None)
    for path in (
        asset_root / "design_system.css",
        asset_root / "background.jpg",
        asset_root / "logo.jpg",
    )
)
css_text, bg_base64, logo_b64 = load_app_assets(asset_signatures)
if css_text:
    st.markdown(f"<style>{css_text}</style>", unsafe_allow_html=True)

if bg_base64:
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

def load_and_validate_csv(file_bytes: bytes):
    """Parse and validate a CSV once when its upload changes."""
    import io
    import pandas as pd
    if not file_bytes:
        return None, None, "The uploaded file is empty."
    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
    except Exception as exc:
        return None, None, f"Failed to parse CSV file ({type(exc).__name__}): {exc}"

    if df.empty:
        return None, None, "The uploaded CSV file contains no data rows."

    # Validate coordinate columns (case-insensitive check)
    clean_cols = {str(col).strip().lower(): col for col in df.columns}
    if "latitude" not in clean_cols or "longitude" not in clean_cols:
        return None, None, "CSV must contain 'latitude' and 'longitude' columns."

    lat_col = clean_cols["latitude"]
    lon_col = clean_cols["longitude"]
    if lat_col != "latitude" or lon_col != "longitude":
        df = df.rename(columns={lat_col: "latitude", lon_col: "longitude"})

    # Coerce coordinates to numeric
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    null_coords = df["latitude"].isna() | df["longitude"].isna()
    if null_coords.any():
        bad_count = int(null_coords.sum())
        return None, None, f"CSV contains {bad_count:,} row(s) with invalid or missing coordinates."

    # Geographic range check
    invalid_range = (
        (df["latitude"] < -90.0) | (df["latitude"] > 90.0) |
        (df["longitude"] < -180.0) | (df["longitude"] > 180.0)
    )
    if invalid_range.any():
        bad_count = int(invalid_range.sum())
        return None, None, f"CSV contains {bad_count:,} row(s) with coordinates outside valid geographic range (-90 to 90 lat, -180 to 180 lon)."

    # Demand column validation
    if "demand" in clean_cols:
        dem_col = clean_cols["demand"]
        if dem_col != "demand":
            df = df.rename(columns={dem_col: "demand"})
        df["demand"] = pd.to_numeric(df["demand"], errors="coerce").fillna(0)

    # Ensure ID column exists
    id_col = clean_cols.get("id")
    if id_col and id_col != "id":
        df = df.rename(columns={id_col: "id"})
    elif "id" not in df.columns:
        df.insert(0, "id", [f"Loc_{i + 1}" for i in range(len(df))])
    else:
        df["id"] = df["id"].astype(str)

    id_labels = df["id"].tolist()
    return df, id_labels, None


def matching_location_indices(id_labels, query, limit=100):
    """Return a bounded, searchable set of row indices for large-upload widgets."""
    needle = query.strip().casefold()
    matches = []
    for index, label in enumerate(id_labels):
        if not needle or needle in str(label).casefold():
            matches.append(index)
            if len(matches) >= limit:
                break
    return matches


# Main workflow
if uploaded_file:
    import pandas as pd

    upload_id = uploaded_file.file_id
    if st.session_state.get("_smart_route_upload_id") != upload_id:
        previous_delivery_widget_key = st.session_state.get("_active_delivery_widget_key")
        if previous_delivery_widget_key:
            st.session_state.pop(previous_delivery_widget_key, None)
        for key in [
            "_smart_route_parsed_csv",
            "selected_deliveries",
            "delivery_selection",
            "depot_selector",
            "depot_search",
            "delivery_search",
            "_active_delivery_widget_key",
            "result",
            "distance_matrix",
            "result_locations",
            "result_settings",
            "fuel_price",
            "distance_source",
            "route_geometries",
            "route_geometry_warning",
            "alternatives_geometries",
            "alternative_route_geometry_warning",
        ]:
            st.session_state.pop(key, None)

        st.session_state["_smart_route_upload_id"] = upload_id
        csv_parse_started = time.perf_counter()
        with st.spinner(f"Reading and validating {uploaded_file.name}..."):
            df, id_labels, error_msg = load_and_validate_csv(uploaded_file.getvalue())
        csv_parse_seconds = time.perf_counter() - csv_parse_started
        st.session_state["_smart_route_parsed_csv"] = (
            df, id_labels, error_msg, csv_parse_seconds
        )
    else:
        df, id_labels, error_msg, csv_parse_seconds = st.session_state[
            "_smart_route_parsed_csv"
        ]

    if error_msg:
        st.error(error_msg)
        st.stop()
    st.sidebar.caption(
        f"Loaded all {len(df):,} rows ({uploaded_file.size / (1024 * 1024):.1f} MB); "
        f"validated in {csv_parse_seconds:.1f} seconds."
    )

    if len(df) > 1000:
        depot_query = st.sidebar.text_input(
            "Find depot by ID",
            key="depot_search",
            help="Search all uploaded rows; the first 100 matches are listed.",
        )
        depot_options = matching_location_indices(id_labels, depot_query)
        current_depot = st.session_state.get("depot_selector", 0)
        if current_depot not in depot_options:
            depot_options.insert(0, current_depot)
        depot_index = st.sidebar.selectbox(
            "Depot (start/end)",
            options=depot_options,
            index=depot_options.index(current_depot),
            format_func=lambda i: f"{id_labels[i]} · row {i + 1}",
            key="depot_selector",
        )
    else:
        depot_index = st.sidebar.selectbox(
            "Depot (start/end)",
            options=range(len(df)),
            format_func=lambda i: id_labels[i],
            key="depot_selector",
        )
    large_upload = len(df) > 1000
    if large_upload:
        if "selected_deliveries" not in st.session_state:
            st.session_state["selected_deliveries"] = [
                i for i in range(min(len(df), 11)) if i != depot_index
            ][:10]
        delivery_options = []
    else:
        delivery_options = [i for i in range(len(df)) if i != depot_index]
        if "selected_deliveries" not in st.session_state:
            st.session_state["selected_deliveries"] = (
                delivery_options if len(df) <= 15 else delivery_options[:10]
            )

    # Quick selection controls for convenience and performance
    col_sel_all, col_clear = st.sidebar.columns(2)
    select_all = col_sel_all.button("Select All", help="Select all delivery locations")
    clear_all = col_clear.button("Clear", help="Clear selected delivery locations")

    if select_all:
        st.session_state["selected_deliveries"] = [
            i for i in range(len(df)) if i != depot_index
        ]
        if not large_upload:
            st.session_state["delivery_selection"] = list(st.session_state["selected_deliveries"])
    elif clear_all:
        st.session_state["selected_deliveries"] = []
        if not large_upload:
            st.session_state["delivery_selection"] = []

    if large_upload:
        valid_selected = [
            i for i in st.session_state["selected_deliveries"]
            if isinstance(i, int) and 0 <= i < len(df) and i != depot_index
        ]
    else:
        valid_selected = [
            i for i in st.session_state["selected_deliveries"]
            if i in delivery_options
        ]

    if large_upload:
        delivery_query = st.sidebar.text_input(
            "Search delivery locations by ID",
            key="delivery_search",
            help="Search all uploaded rows; up to 100 matches are shown at a time.",
        )
        visible_delivery_options = [
            i for i in matching_location_indices(id_labels, delivery_query)
            if i != depot_index
        ]
        visible_delivery_set = set(visible_delivery_options)
        widget_key = "delivery_selection_" + hashlib.sha1(
            f"{delivery_query.casefold()}:{depot_index}".encode("utf-8")
        ).hexdigest()[:12]
        previous_widget_key = st.session_state.get("_active_delivery_widget_key")
        if previous_widget_key != widget_key:
            if previous_widget_key:
                st.session_state.pop(previous_widget_key, None)
            st.session_state[widget_key] = [
                i for i in valid_selected if i in visible_delivery_set
            ]
            st.session_state["_active_delivery_widget_key"] = widget_key
        if select_all:
            st.session_state[widget_key] = visible_delivery_options
        elif clear_all:
            st.session_state[widget_key] = []

        selected_visible = st.sidebar.multiselect(
            "Matching delivery locations",
            options=visible_delivery_options,
            format_func=lambda i: f"{id_labels[i]} · row {i + 1}",
            key=widget_key,
            help="Selections from other searches are kept. Refine the search to find other rows.",
        )
        selected_delivery_indices = sorted(
            (set(valid_selected) - visible_delivery_set) | set(selected_visible)
        )
        st.sidebar.caption(
            f"{len(selected_delivery_indices):,} delivery location(s) selected; "
            f"showing up to {len(visible_delivery_options)} matches."
        )
    else:
        # Keep one stable widget key and let Streamlit own the selection state.
        # Rebuilding the multiselect from a changing `default` on every rerun
        # causes selected locations to visibly jump/reset while choosing them.
        current_selection = [
            i for i in st.session_state.get("delivery_selection", valid_selected)
            if i in delivery_options
        ]
        st.session_state["delivery_selection"] = current_selection
        st.sidebar.multiselect(
            "Delivery locations",
            options=delivery_options,
            format_func=lambda i: id_labels[i],
            key="delivery_selection",
            help="Only the depot and selected delivery locations are used for routing and the map.",
        )
        selected_delivery_indices = list(st.session_state["delivery_selection"])
    st.session_state["selected_deliveries"] = selected_delivery_indices

    if not selected_delivery_indices:
        st.sidebar.caption("Select at least one delivery location to calculate a route.")
    route_row_indices = [depot_index, *selected_delivery_indices]
    estimated_matrix_bytes = len(route_row_indices) ** 2 * 8
    within_matrix_memory_limit = estimated_matrix_bytes <= 128 * 1024 * 1024
    if not within_matrix_memory_limit:
        st.sidebar.error(
            f"{len(route_row_indices):,} selected locations need about "
            f"{estimated_matrix_bytes / (1024 * 1024):,.0f} MiB for the dense distance matrix "
            "(limit: 128 MiB). Select fewer locations; no uploaded rows were discarded."
        )
        route_locations = df.iloc[0:0].copy()
    else:
        route_locations = df.iloc[route_row_indices].reset_index(drop=True)

    # Tabs for clean separation
    tab_data, tab_params, tab_output, tab_predictions = st.tabs(
        ["Data", "Parameters", "Output (routes & speed)", "Predictions (stop ETAs)"]
    )

    with tab_data:
        st.subheader("Uploaded Data")
        total_rows = len(df)
        has_demand = "demand" in df.columns
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Locations", f"{total_rows:,}")
        m2.metric("Valid Coordinates", "100%")
        m3.metric("Total Fleet Demand", f"{int(df['demand'].sum()):,}" if has_demand else "N/A")

        if total_rows > 1000:
            st.caption(f"Showing preview of first 1,000 of {total_rows:,} total locations for optimal browser performance.")
            st.dataframe(df.head(1000), use_container_width=True)
            with st.expander(f"Inspect all {total_rows:,} rows"):
                st.dataframe(df, use_container_width=True)
        else:
            st.dataframe(df, use_container_width=True)

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
        allow_alternative = st.checkbox("Calculate alternative routes", value=True)
        num_alternatives = 2
        if allow_alternative:
            num_alternatives = st.number_input(
                "Number of alternative routes to calculate",
                min_value=1,
                max_value=5,
                value=2,
                step=1,
                help="Ranked from shortest to longer. Alternative 1 will be the second-shortest valid route.",
            )
        compute = st.button(
            "Compute Optimal Routes",
            disabled=not selected_delivery_indices or not within_matrix_memory_limit,
        )
        progress_text_placeholder = st.empty()
        progress_placeholder = st.empty()
        compute_status = st.empty()

    route_settings = (
        int(num_vehicles),
        float(fuel_price),
        bool(capacity_enabled),
        int(vehicle_capacity) if capacity_enabled else None,
        bool(allow_alternative),
        int(num_alternatives) if allow_alternative else 0,
        routing_provider,
        upload_id,
        tuple(route_row_indices),
        hashlib.sha256((api_key or "").encode("utf-8")).hexdigest() if routing_provider == "google" else None,
    )

    def update_progress(message, value):
        progress_text_placeholder.markdown(
            f'<div class="sr-progress-label" role="status">{message} — {value}%</div>',
            unsafe_allow_html=True,
        )
        progress_placeholder.progress(value)

    if compute:
        calculation_started = time.perf_counter()
        update_progress("Preparing selected locations", 5)
        update_progress("Calculating driving distances", 20)
        try:
            distance_matrix, distance_source = get_distance_matrix(
                route_locations[["latitude", "longitude"]],
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
        result = get_optimized_routes(
            distance_matrix,
            num_vehicles=num_vehicles,
            demands=demands,
            vehicle_capacities=capacities,
            allow_alternative=allow_alternative,
            max_alternatives=int(num_alternatives) if allow_alternative else 0,
        )
        from utils import calculate_route_travel_time

        route_time = calculate_route_travel_time(result["routes"], distance_matrix)
        result.update({
            "completion_time_minutes": route_time["completion_time_minutes"],
            "total_time_minutes": route_time["total_time_minutes"],
            "avg_speed_kmh": route_time["avg_speed_kmh"],
            "vehicle_details": route_time["vehicle_details"],
        })
        for alternative in result.get("alternatives", []):
            alternative_time = calculate_route_travel_time(
                alternative["routes"], distance_matrix
            )
            alternative.update({
                "completion_time_minutes": alternative_time["completion_time_minutes"],
                "total_time_minutes": alternative_time["total_time_minutes"],
                "avg_speed_kmh": alternative_time["avg_speed_kmh"],
                "vehicle_details": alternative_time["vehicle_details"],
            })
        result["total_cost"] = round(result["total_distance"] * fuel_price, 2)
        for alternative in result.get("alternatives", []):
            alternative["total_cost"] = round(
                alternative["total_distance"] * fuel_price, 2
            )
        update_progress("Preparing road paths for the map", 80)
        route_geometries, route_geometry_warning = get_route_shapes(
            route_locations,
            result["routes"],
            routing_provider,
            api_key,
        )
        alternatives_geometries = []
        alt_warnings = []
        for alt in result.get("alternatives", []):
            alt_geom, alt_warn = get_route_shapes(
                route_locations,
                alt["routes"],
                routing_provider,
                api_key,
            )
            alternatives_geometries.append({
                "label": alt["label"],
                "geometries": alt_geom,
                "warning": alt_warn,
            })
            if alt_warn:
                alt_warnings.append(f"{alt['label']}: {alt_warn}")

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
        st.session_state["alternatives_geometries"] = alternatives_geometries
        st.session_state["alternative_route_geometry_warning"] = "; ".join(alt_warnings) if alt_warnings else None
        update_progress("Route calculation complete", 100)
        if distance_source.startswith("Approximate"):
            compute_status.warning(
                f"Route estimate ready in {time.perf_counter() - calculation_started:.1f} seconds, "
                "but road data was unavailable. Distances are approximate."
            )
        elif route_geometry_warning or alt_warnings:
            compute_status.warning(
                f"Routes calculated in {time.perf_counter() - calculation_started:.1f} seconds; "
                "one or more road paths could not be displayed."
            )
        else:
            num_alts_found = len(result.get("alternatives", []))
            if num_alts_found > 0:
                compute_status.success(
                    f"Recommended route and {num_alts_found} ranked alternative(s) "
                    f"calculated in {time.perf_counter() - calculation_started:.1f} seconds."
                )
            else:
                compute_status.success(
                    "Road routes calculated and displayed in "
                    f"{time.perf_counter() - calculation_started:.1f} seconds."
                )

    saved_locations = st.session_state.get("result_locations")
    result_matches_inputs = (
        "result" in st.session_state
        and isinstance(saved_locations, pd.DataFrame)
        and st.session_state.get("result_settings") == route_settings
    )
    with tab_output:
        if st.button("Clear Cached Results"):
            for key in [
                "result",
                "distance_matrix",
                "result_locations",
                "result_settings",
                "fuel_price",
                "distance_source",
                "route_geometries",
                "route_geometry_warning",
                "alternatives_geometries",
                "alternative_route_geometry_warning",
            ]:
                st.session_state.pop(key, None)
            get_distance_matrix.clear()
            get_optimized_routes.clear()
            get_google_route_shapes.clear()
            get_osrm_route_shapes.clear()
            get_road_graph.clear()
            st.rerun()

    if "result" in st.session_state and not result_matches_inputs:
        with tab_output:
            st.info("Routing inputs changed. Compute Optimal Routes to update the results.")

    if "result" in st.session_state and result_matches_inputs:
        with tab_output:
            result = st.session_state["result"]
            distance_matrix = st.session_state["distance_matrix"]
            result_fuel_price = st.session_state.get("fuel_price", fuel_price)
            alternatives = result.get("alternatives", [])
            alternatives_geometries = st.session_state.get("alternatives_geometries", [])

            st.subheader("Route Overview & Comparison")
            distance_source = st.session_state.get("distance_source", "Road distance source unavailable")
            if distance_source.startswith("Approximate"):
                st.warning(
                    f"{distance_source}. Enable Google Maps routing with an API key "
                    "or choose FOSSGIS/public OSRM (which shares selected coordinates) "
                    "to calculate actual driving distances."
                )
            else:
                st.caption(f"Distance calculation: {distance_source}.")

            overview_metrics = st.columns(3)
            overview_metrics[0].metric(
                "Recommended distance",
                f"{result['total_distance']:.1f} km",
            )
            overview_metrics[1].metric(
                "Estimated completion time",
                format_duration_minutes(result.get("completion_time_minutes", 0)),
            )
            overview_metrics[2].metric(
                "Average driving speed",
                f"{result.get('avg_speed_kmh', 0):.0f} km/h",
            )
            st.caption(
                "Speed and arrival estimates use illustrative traffic assumptions, not live traffic. "
                "Open Predictions (stop ETAs) for per-stop arrival times and departure-hour controls."
            )

            # ── High-level Comparison Table: Recommended vs All Alternatives ──
            all_solutions_overview = []
            rec_dist = result["total_distance"]
            rec_time = result.get("completion_time_minutes", 0.0)
            rec_stops_summary = " | ".join(
                " -> ".join(str(route_locations.iloc[idx]["id"]) for idx in r)
                for r in result["routes"]
            )
            all_solutions_overview.append({
                "Rank / Option": "Recommended (Best)",
                "Total Distance (km)": f"{rec_dist:.2f}",
                "Est. Completion Time": format_duration_minutes(rec_time) if rec_time > 0 else "N/A",
                "Avg Speed (km/h)": f"{result.get('avg_speed_kmh', 0):.0f}",
                "Fuel Cost": f"{result['total_cost']:.2f}",
                "Diff vs. Recommended": "Baseline",
                "Stop Sequence": rec_stops_summary,
            })

            for idx, alt in enumerate(alternatives):
                alt_d = alt["total_distance"]
                alt_t = alt.get("completion_time_minutes", 0.0)
                diff_km = alt_d - rec_dist
                diff_pct = (diff_km / rec_dist * 100) if rec_dist > 0 else 0.0
                alt_stops_summary = " | ".join(
                    " -> ".join(str(route_locations.iloc[node]["id"]) for node in r)
                    for r in alt["routes"]
                )
                rank_label = f"{alt['label']} (2nd Shortest)" if idx == 0 else f"{alt['label']}"
                all_solutions_overview.append({
                    "Rank / Option": rank_label,
                    "Total Distance (km)": f"{alt_d:.2f}",
                    "Est. Completion Time": format_duration_minutes(alt_t) if alt_t > 0 else "N/A",
                    "Avg Speed (km/h)": f"{alt.get('avg_speed_kmh', 0):.0f}",
                    "Fuel Cost": f"{alt['total_cost']:.2f}",
                    "Diff vs. Recommended": f"+{diff_km:.2f} km (+{diff_pct:.1f}%)" if diff_km >= 0 else f"{diff_km:.2f} km",
                    "Stop Sequence": alt_stops_summary,
                })

            overview_df = pd.DataFrame(all_solutions_overview)
            st.dataframe(overview_df, use_container_width=True, hide_index=True)

            # ── Recommended Route Breakdown ───────────────────────────────────
            st.markdown(
                '<div class="route-breakdown-title">Recommended Route Breakdown</div>',
                unsafe_allow_html=True,
            )
            rec_summary = pd.DataFrame({
                "Vehicle": [f"Vehicle {i + 1}" for i in range(num_vehicles)],
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
            st.table(rec_summary)

            csv_rec = rec_summary.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download Recommended Route CSV",
                data=csv_rec,
                file_name="recommended_route_summary.csv",
                mime='text/csv',
            )

            # ── Interactive Route Map ─────────────────────────────────────────
            st.subheader("Multi-Route Interactive Map")
            map_indices = sorted({index for route in result["routes"] for index in route})
            map_locations = route_locations.iloc[map_indices]
            center_lat = float(map_locations["latitude"].mean())
            center_lon = float(map_locations["longitude"].mean())
            route_geometries = st.session_state.get("route_geometries")
            route_geometry_warning = st.session_state.get("route_geometry_warning")
            alt_geom_warning = st.session_state.get("alternative_route_geometry_warning")

            for warning in (route_geometry_warning, alt_geom_warning):
                if warning:
                    st.warning(warning)

            # Lazy-load folium only when map visualization is rendered
            import folium
            from streamlit_folium import st_folium

            m = folium.Map(
                location=[center_lat, center_lon],
                zoom_start=7,
                tiles='OpenStreetMap',
                scrollWheelZoom=False,
                prefer_canvas=True,
            )

            # Recommended route layer (Burgundy)
            rec_colors = ["#6B1728", "#8E2338", "#D98890", "#B23A48", "#4A0E1B"]
            recommended_layer = folium.FeatureGroup(name="Recommended route", show=True)
            has_recommended_geometry = False
            for veh_id, route in enumerate(result["routes"]):
                col = rec_colors[veh_id % len(rec_colors)]
                if route_geometries and len(route_geometries) > veh_id and route_geometries[veh_id]:
                    folium.PolyLine(
                        locations=route_geometries[veh_id],
                        color=col,
                        weight=6,
                        opacity=0.95,
                        smooth_factor=1,
                        tooltip=f"Recommended route - Vehicle {veh_id + 1} ({result['total_distance']:.1f} km)",
                    ).add_to(recommended_layer)
                    has_recommended_geometry = True
                for idx in route:
                    p = route_locations.iloc[idx]
                    folium.CircleMarker(
                        location=(float(p["latitude"]), float(p["longitude"])),
                        radius=4,
                        color=col,
                        fill=True,
                        fill_color=col,
                        popup=f"ID: {p.get('id', idx)}",
                    ).add_to(m)

            if has_recommended_geometry:
                recommended_layer.add_to(m)

            # Palette and dash patterns for ranked alternatives
            alt_styles = [
                {"color": "#1675A9", "dash": "10, 8"},
                {"color": "#2A9D8F", "dash": "8, 6"},
                {"color": "#7209B7", "dash": "6, 6"},
                {"color": "#E76F51", "dash": "12, 6"},
                {"color": "#264653", "dash": "4, 6"},
            ]

            for alt_idx, alt in enumerate(alternatives):
                style = alt_styles[alt_idx % len(alt_styles)]
                alt_layer_name = f"{alt['label']} ({alt['total_distance']:.1f} km)"
                alt_layer = folium.FeatureGroup(name=alt_layer_name, show=(alt_idx == 0))

                matching_geom_entry = next(
                    (entry for entry in alternatives_geometries if entry.get("label") == alt["label"]),
                    None,
                )
                if matching_geom_entry and matching_geom_entry.get("geometries"):
                    for veh_id, geom in enumerate(matching_geom_entry["geometries"]):
                        if geom:
                            folium.PolyLine(
                                locations=geom,
                                color=style["color"],
                                weight=5,
                                opacity=0.9,
                                dash_array=style["dash"],
                                smooth_factor=1,
                                tooltip=f"{alt['label']} - Vehicle {veh_id + 1} ({alt['total_distance']:.1f} km)",
                            ).add_to(alt_layer)

                alt_layer.add_to(m)

            folium.LayerControl(position="topright", collapsed=False).add_to(m)
            from branca.element import Element
            from html import escape

            legend_items = [
                (rec_colors[vehicle_id % len(rec_colors)], f"Recommended · Vehicle {vehicle_id + 1}", False)
                for vehicle_id in range(len(result["routes"]))
            ]
            legend_items.extend(
                (
                    alt_styles[alt_idx % len(alt_styles)]["color"],
                    f"{alt['label']} alternative",
                    True,
                )
                for alt_idx, alt in enumerate(alternatives)
            )
            legend_rows = "".join(
                '<div style="display:flex;align-items:center;gap:8px;margin-top:5px;">'
                f'<span style="display:inline-block;width:22px;border-top:3px '
                f'{"dashed" if dashed else "solid"} {color};"></span>'
                f'<span>{escape(label)}</span></div>'
                for color, label, dashed in legend_items
            )
            m.get_root().html.add_child(Element(
                '<div style="position:fixed;bottom:22px;left:12px;z-index:9999;'
                'max-height:180px;overflow-y:auto;padding:10px 12px;'
                'background:rgba(255,255,255,.96);border:1px solid #d5d5d5;'
                'border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.25);'
                'font:12px/1.35 sans-serif;color:#222;">'
                '<strong>Route color key</strong>'
                f'{legend_rows}</div>'
            ))
            st.caption("Use the map layer control in the top-right corner to toggle between Recommended and Alternative routes.")

            m.fit_bounds(
                [
                    [float(map_locations["latitude"].min()), float(map_locations["longitude"].min())],
                    [float(map_locations["latitude"].max()), float(map_locations["longitude"].max())],
                ],
                padding=(24, 24),
                max_zoom=12,
            )
            st_folium(
                m,
                height=600,
                use_container_width=True,
                key="route_map",
                returned_objects=[],
            )

            # ── Detailed Alternative Routes Section ───────────────────────────
            st.subheader("Alternative Route Details (Ranked by Distance)")
            if alternatives:
                st.caption(
                    "All alternative routes are distinct, start/end at the depot, visit all requested locations, "
                    "respect vehicle capacity constraints, and are ranked from shortest to longer."
                )
                for alt_idx, alt in enumerate(alternatives):
                    style = alt_styles[alt_idx % len(alt_styles)]
                    diff_km = alt["total_distance"] - rec_dist
                    diff_pct = (diff_km / rec_dist * 100) if rec_dist > 0 else 0.0

                    badge_title = alt["label"]
                    if alt_idx == 0:
                        badge_title += " (Second-Shortest Valid Route)"

                    with st.expander(f"{badge_title} — {alt['total_distance']:.2f} km (+{diff_km:.2f} km)", expanded=(alt_idx == 0)):
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Total Distance", f"{alt['total_distance']:.2f} km")
                        col2.metric("Diff vs. Recommended", f"+{diff_km:.2f} km", delta=f"+{diff_pct:.1f}%", delta_color="inverse")
                        col3.metric("Est. Completion Time", format_duration_minutes(alt.get("completion_time_minutes", 0)))
                        col4.metric("Fuel Cost", f"{alt['total_cost']:.2f}")

                        alt_breakdown = pd.DataFrame({
                            "Vehicle": [f"Vehicle {i + 1}" for i in range(num_vehicles)],
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
                        st.table(alt_breakdown)

                        csv_alt = alt_breakdown.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label=f"Download {alt['label']} CSV",
                            data=csv_alt,
                            file_name=f"{alt['label'].lower().replace(' ', '_')}_summary.csv",
                            mime='text/csv',
                            key=f"dl_alt_{alt_idx}",
                        )
            elif allow_alternative:
                st.info(
                    "No valid alternative routes could be found for the selected stops and constraints. "
                    "All delivery locations form a single unique path, or vehicle capacity constraints prevent any alternative valid ordering."
                )

    # ── Predictions Tab ──────────────────────────────────────────────
    with tab_predictions:
        if "result" not in st.session_state or not result_matches_inputs:
            st.info("Compute optimal routes first to view arrival-time predictions.")
        else:
            import datetime as _dt
            from utils import generate_route_predictions

            result = st.session_state["result"]
            distance_matrix = st.session_state["distance_matrix"]
            alternatives = result.get("alternatives", [])

            st.markdown("""
            <div class="clay-header" style="margin-bottom:1.4rem; padding:1.2rem 1.6rem;">
              <h2 style="margin:0; font-size:1.45rem;">Arrival-Time Predictions</h2>
              <p class="clay-subtitle" style="font-size:0.92rem;">
                Estimated speeds and ETAs based on <strong>fictional historical traffic data</strong>
                (30-day averages, time-of-day patterns, and road-type classification).
              </p>
            </div>
            """, unsafe_allow_html=True)

            # Route selector and Departure-time selector
            pred_col_route, pred_col_time = st.columns([1, 1])
            with pred_col_route:
                route_options = ["Recommended Route"] + [alt["label"] for alt in alternatives]
                selected_route_choice = st.selectbox(
                    "Route to inspect",
                    options=route_options,
                    help="Select Recommended or an Alternative route to see its specific stop-by-stop ETAs.",
                )

            with pred_col_time:
                departure_hour = st.slider(
                    "Departure hour (24 h)",
                    min_value=0, max_value=23, value=_dt.datetime.now().hour,
                    help="Adjust to see how traffic patterns affect ETAs at different times of day.",
                )

            departure_dt = _dt.datetime.now().replace(
                hour=departure_hour, minute=0, second=0, microsecond=0
            )

            location_names = route_locations["id"].tolist()

            # Determine which route list to inspect
            if selected_route_choice == "Recommended Route":
                routes_to_inspect = result["routes"]
                route_title_prefix = "Recommended Route"
            else:
                chosen_label = selected_route_choice
                matching_alt = next((a for a in alternatives if a["label"] == chosen_label), None)
                routes_to_inspect = matching_alt["routes"] if matching_alt else result["routes"]
                route_title_prefix = chosen_label

            for veh_id, route in enumerate(routes_to_inspect):
                pred = generate_route_predictions(
                    route, distance_matrix,
                    departure_time=departure_dt,
                    location_names=location_names,
                )

                st.markdown(f"""
                <div class="pred-vehicle-header">
                    <span class="pred-vehicle-badge">{route_title_prefix} - Vehicle {veh_id + 1}</span>
                    <span class="pred-vehicle-summary">
                        {pred['total_distance_km']:.1f} km &nbsp;·&nbsp;
                        {format_duration_minutes(pred['total_time_minutes'])} &nbsp;·&nbsp;
                        Avg {pred['avg_speed_kmh']:.0f} km/h &nbsp;·&nbsp;
                        Depart {pred['departure_time']} → Arrive {pred['estimated_arrival']}
                    </span>
                </div>
                """, unsafe_allow_html=True)

                # Historical 30-day overview
                h30 = pred["historical_30d"]
                hist_cols = st.columns(3)
                hist_cols[0].metric("30-day avg trip", format_duration_minutes(h30["avg_minutes"]))
                hist_cols[1].metric("Best recorded", format_duration_minutes(h30["best_minutes"]))
                hist_cols[2].metric("Worst recorded", format_duration_minutes(h30["worst_minutes"]))

                # Per-segment breakdown table
                seg_rows = []
                for seg in pred["segments"]:
                    seg_rows.append({
                        "Leg": seg["leg"],
                        "From": seg["from"],
                        "To": seg["to"],
                        "Distance (km)": seg["segment_distance_km"],
                        "Road Type": seg["road_type"],
                        "Traffic": seg["traffic_condition"],
                        "Est. Speed (km/h)": seg["estimated_speed_kmh"],
                        "Hist. Avg Speed": seg["historical_avg_speed_kmh"],
                        "Travel Time": format_duration_minutes(seg["estimated_time_minutes"]),
                        "ETA": seg["eta"],
                        "Confidence": f"{seg['confidence_pct']}%",
                    })
                seg_df = pd.DataFrame(seg_rows)
                st.dataframe(seg_df, use_container_width=True, hide_index=True)

                # Speed comparison mini-chart (bar-style with Streamlit)
                speed_chart_df = pd.DataFrame({
                    "Leg": [f"{s['from']} → {s['to']}" for s in pred["segments"]],
                    "Estimated Speed": [s["estimated_speed_kmh"] for s in pred["segments"]],
                    "Historical Avg": [s["historical_avg_speed_kmh"] for s in pred["segments"]],
                })
                if len(speed_chart_df) > 1:
                    st.caption(f"Speed comparison for {route_title_prefix}: estimated vs. historical average (km/h)")
                    st.bar_chart(
                        speed_chart_df.set_index("Leg"),
                        use_container_width=True,
                        height=260,
                    )

                st.markdown("---")

            st.caption(
                "All predictions use **fictional historical data** generated for demonstration purposes. "
                "They do not reflect real-world traffic conditions."
            )

else:
    active_delivery_widget_key = st.session_state.get("_active_delivery_widget_key")
    if active_delivery_widget_key:
        st.session_state.pop(active_delivery_widget_key, None)
    for key in [
        "_smart_route_upload_id",
        "_smart_route_parsed_csv",
        "_active_delivery_widget_key",
        "selected_deliveries",
        "depot_selector",
        "depot_search",
        "delivery_search",
        "result",
        "distance_matrix",
        "result_locations",
        "result_settings",
        "fuel_price",
        "distance_source",
        "route_geometries",
        "route_geometry_warning",
        "alternatives_geometries",
        "alternative_route_geometry_warning",
    ]:
        st.session_state.pop(key, None)
    st.info("Upload a CSV file to begin the routing workflow.")
