
import numpy as np
import osmnx as ox
import networkx as nx
import requests
# OR‑Tools is optional – it may not be available on all Windows setups.
try:
    from ortools.constraint_solver import routing_enums_pb2
    from ortools.constraint_solver import pywrapcp
    _ORTOOLS_AVAILABLE = True
except Exception:  # pragma: no cover
    _ORTOOLS_AVAILABLE = False


ROAD_NETWORK_TIMEOUT_SECONDS = 30
ALTERNATIVE_SEARCH_TIME_LIMIT_SECONDS = 5
OSRM_ROUTING_SERVERS = {
    "public": "https://router.project-osrm.org",
    "fossgis": "https://routing.openstreetmap.de/routed-car",
}




def _calculate_routes_distance(routes, distance_matrix):
    return sum(
        float(distance_matrix[route[index], route[index + 1]])
        for route in routes
        for index in range(len(route) - 1)
    )


def _greedy_vrp(
    distance_matrix,
    num_vehicles=1,
    depot=0,
    demands=None,
    vehicle_capacities=None,
    fuel_price=1.0,
    allow_alternative=False,
):
    """Fallback greedy VRP solver used when OR‑Tools is unavailable.
    It constructs routes by repeatedly choosing the nearest unvisited location.
    Capacity constraints are respected if `demands` and `vehicle_capacities` are provided.
    """
    n = len(distance_matrix)
    unserved = set(range(n))
    unserved.discard(depot)
    routes = []
    total_dist = 0.0
    for v in range(num_vehicles):
        route = [depot]
        load = 0
        while unserved:
            last = route[-1]
            # Find nearest feasible candidate
            candidates = []
            for cand in unserved:
                if demands is not None and vehicle_capacities is not None:
                    if load + demands[cand] > vehicle_capacities[v]:
                        continue
                candidates.append((distance_matrix[last, cand], cand))
            if not candidates:
                break
            _, next_node = min(candidates, key=lambda x: x[0])
            route.append(next_node)
            total_dist += distance_matrix[last, next_node]
            if demands is not None:
                load += demands[next_node]
            unserved.remove(next_node)
        route.append(depot)
        total_dist += distance_matrix[route[-2], depot]
        routes.append(route)
    if unserved:
        raise RuntimeError("Vehicle capacities are insufficient to serve all locations.")
    result = {
        "routes": routes,
        "total_distance": total_dist,
        "total_cost": total_dist * fuel_price,
        "alternative": None,
    }

    if allow_alternative:
        best_alternative = None
        best_alternative_distance = None
        for vehicle_id, route in enumerate(routes):
            for first_position in range(1, len(route) - 2):
                for second_position in range(first_position + 1, len(route) - 1):
                    alternative_routes = [vehicle_route.copy() for vehicle_route in routes]
                    alternative_route = alternative_routes[vehicle_id]
                    alternative_route[first_position], alternative_route[second_position] = (
                        alternative_route[second_position],
                        alternative_route[first_position],
                    )
                    alternative_distance = _calculate_routes_distance(
                        alternative_routes, distance_matrix
                    )
                    if (
                        best_alternative_distance is None
                        or alternative_distance < best_alternative_distance
                    ):
                        best_alternative = alternative_routes
                        best_alternative_distance = alternative_distance

        if best_alternative is not None:
            result["alternative"] = {
                "routes": best_alternative,
                "total_distance": best_alternative_distance,
                "total_cost": best_alternative_distance * fuel_price,
            }

    return result


def build_road_graph(locations):
    """Download a driving-road graph covering the supplied coordinates."""
    lat_min, lat_max = locations['latitude'].min(), locations['latitude'].max()
    lon_min, lon_max = locations['longitude'].min(), locations['longitude'].max()
    bbox = (lon_min - 0.05, lat_min - 0.05, lon_max + 0.05, lat_max + 0.05)
    ox.settings.requests_timeout = ROAD_NETWORK_TIMEOUT_SECONDS
    return ox.graph_from_bbox(bbox, network_type='drive')


def _nearest_road_nodes(locations, graph):
    return [
        ox.distance.nearest_nodes(
            graph,
            X=float(row['longitude']),
            Y=float(row['latitude']),
        )
        for _, row in locations.iterrows()
    ]


def calculate_distance_matrix(locations, graph=None):
    """Calculate a deterministic road‑network distance matrix.

    Args:
        locations (pd.DataFrame): must contain columns ['latitude', 'longitude'].
        graph: Optional OpenStreetMap driving graph covering all locations.
    Returns:
        np.ndarray: square matrix of distances in kilometers.
    """
    graph = graph or build_road_graph(locations)
    nodes = _nearest_road_nodes(locations, graph)
    n = len(nodes)
    dist_mat = np.zeros((n, n))
    # OSM edge lengths are in meters, so convert road distances to kilometers.
    for i in range(n):
        lengths = nx.single_source_dijkstra_path_length(graph, nodes[i], weight='length')
        for j in range(n):
            if i == j:
                dist = 0.0
            else:
                dist = lengths.get(nodes[j], np.inf) / 1000.0  # convert meters to km
            dist_mat[i, j] = dist
    return dist_mat


def calculate_route_geometries(locations, routes, graph):
    """Return road-following (latitude, longitude) coordinates for each vehicle route."""
    road_nodes = _nearest_road_nodes(locations, graph)
    route_geometries = []
    missing_legs = []

    for vehicle_id, route in enumerate(routes):
        coordinates = []
        for start, end in zip(route, route[1:]):
            try:
                node_path = nx.shortest_path(
                    graph,
                    source=road_nodes[start],
                    target=road_nodes[end],
                    weight='length',
                )
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                missing_legs.append((vehicle_id, start, end))
                coordinates = []
                break

            start_location = locations.iloc[start]
            end_location = locations.iloc[end]
            leg_coordinates = [
                (float(start_location['latitude']), float(start_location['longitude']))
            ]
            road_coordinates = []
            for from_node, to_node in zip(node_path, node_path[1:]):
                edge_data = graph.get_edge_data(from_node, to_node)
                if "length" in edge_data:
                    parallel_edges = [edge_data]
                else:
                    parallel_edges = list(edge_data.values())
                edge = min(parallel_edges, key=lambda item: item.get('length', 0))
                geometry = edge.get('geometry')
                if geometry is None:
                    segment = [
                        (float(graph.nodes[from_node]['y']), float(graph.nodes[from_node]['x'])),
                        (float(graph.nodes[to_node]['y']), float(graph.nodes[to_node]['x'])),
                    ]
                else:
                    segment = [
                        (float(latitude), float(longitude))
                        for longitude, latitude in geometry.coords
                    ]
                road_coordinates.extend(segment[1:] if road_coordinates else segment)
            leg_coordinates.extend(road_coordinates)
            leg_coordinates.append(
                (float(end_location['latitude']), float(end_location['longitude']))
            )
            coordinates.extend(leg_coordinates[1:] if coordinates else leg_coordinates)
        route_geometries.append(coordinates)

    return route_geometries, missing_legs


def _osrm_coordinates(locations):
    return ";".join(
        f"{float(row['longitude'])},{float(row['latitude'])}"
        for _, row in locations.iterrows()
    )


def _osrm_server_url(server):
    try:
        return OSRM_ROUTING_SERVERS[server]
    except KeyError as exc:
        raise ValueError(f"Unknown OSRM routing server: {server}") from exc


def calculate_osrm_distance_matrix(locations, server="public"):
    """Fetch driving-road distances from an OSRM server in kilometers."""
    coordinates = _osrm_coordinates(locations)
    response = requests.get(
        f"{_osrm_server_url(server)}/table/v1/driving/{coordinates}",
        params={"annotations": "distance"},
        timeout=ROAD_NETWORK_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM table request failed: {payload.get('code', 'unknown error')}")

    distances = payload.get("distances")
    if distances is None:
        raise RuntimeError("OSRM table response did not contain distances.")
    matrix = np.asarray(distances, dtype=float)
    expected_shape = (len(locations), len(locations))
    if matrix.shape != expected_shape or not np.isfinite(matrix).all():
        raise RuntimeError("OSRM returned an incomplete driving-distance matrix.")
    return matrix / 1000.0


def calculate_osrm_route_geometries(locations, routes, server="public"):
    """Fetch full road-following GeoJSON geometry for each vehicle route."""
    route_geometries = []
    missing_routes = []

    for vehicle_id, route in enumerate(routes):
        stops = locations.iloc[route]
        coordinates = _osrm_coordinates(stops)
        response = requests.get(
            f"{_osrm_server_url(server)}/route/v1/driving/{coordinates}",
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            },
            timeout=ROAD_NETWORK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        routes_data = payload.get("routes", [])
        if payload.get("code") != "Ok" or not routes_data:
            route_geometries.append([])
            missing_routes.append(vehicle_id)
            continue

        coordinates = routes_data[0].get("geometry", {}).get("coordinates", [])
        if not coordinates:
            route_geometries.append([])
            missing_routes.append(vehicle_id)
            continue
        route_geometries.append([
            (float(latitude), float(longitude))
            for longitude, latitude in coordinates
        ])

    return route_geometries, missing_routes


def calculate_google_route_geometries(locations, routes, api_key):
    """Fetch one driving route polyline per vehicle, including its selected stops."""
    if not api_key:
        raise ValueError("Google Maps API key is required for route geometry.")

    import googlemaps
    from googlemaps import convert

    client = googlemaps.Client(key=api_key)
    route_geometries = []
    missing_routes = []

    for vehicle_id, route in enumerate(routes):
        stops = [
            (float(locations.iloc[index]['latitude']), float(locations.iloc[index]['longitude']))
            for index in route
        ]
        directions = client.directions(
            origin=stops[0],
            destination=stops[-1],
            waypoints=stops[1:-1],
            optimize_waypoints=False,
            mode='driving',
        )
        if not directions:
            route_geometries.append([])
            missing_routes.append(vehicle_id)
            continue

        encoded_polyline = directions[0].get('overview_polyline', {}).get('points')
        if not encoded_polyline:
            route_geometries.append([])
            missing_routes.append(vehicle_id)
            continue
        route_geometries.append([
            (point['lat'], point['lng'])
            for point in convert.decode_polyline(encoded_polyline)
        ])

    return route_geometries, missing_routes


def calculate_straight_line_distance_matrix(locations):
    """Calculate approximate great-circle distances in kilometers."""
    coordinates = np.radians(locations[['latitude', 'longitude']].to_numpy(dtype=float))
    latitudes = coordinates[:, 0, np.newaxis]
    longitudes = coordinates[:, 1, np.newaxis]
    delta_latitudes = latitudes - latitudes.T
    delta_longitudes = longitudes - longitudes.T

    haversine = (
        np.sin(delta_latitudes / 2) ** 2
        + np.cos(latitudes) * np.cos(latitudes.T) * np.sin(delta_longitudes / 2) ** 2
    )
    haversine = np.clip(haversine, 0.0, 1.0)
    return 2 * 6371.0088 * np.arctan2(np.sqrt(haversine), np.sqrt(1 - haversine))



def solve_vrp(
    distance_matrix,
    num_vehicles=1,
    depot=0,
    demands=None,
    vehicle_capacities=None,
    fuel_price=1.0,
    allow_alternative=False,
):
    """Solve a deterministic Vehicle Routing Problem.

    Parameters
    ----------
    distance_matrix : np.ndarray
        Square matrix of distances (km).
    num_vehicles : int
        Number of vehicles available.
    depot : int
        Index of the depot node.
    demands : list[int] | None
        Demand for each location (same order as matrix). If None, no capacity constraints.
    vehicle_capacities : list[int] | None
        Capacity per vehicle. Required when `demands` is provided.
    fuel_price : float
        Cost per km of fuel.
    allow_alternative : bool
        If True, searches for another route assignment distinct from the best solution.

    Returns
    -------
    dict
        {
            "routes": list of lists of location indices (including depot at start/end),
            "total_distance": float,
            "total_cost": float,
            "alternative": another distinct solution with the same structure, or None
        }
    """
    if not _ORTOOLS_AVAILABLE:
        # Fallback to greedy implementation when OR‑Tools cannot be imported
        return _greedy_vrp(
            distance_matrix,
            num_vehicles,
            depot,
            demands,
            vehicle_capacities,
            fuel_price,
            allow_alternative,
        )
    manager = pywrapcp.RoutingIndexManager(len(distance_matrix), num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(distance_matrix[from_node, to_node] * 1000)  # convert km to meters for integer arithmetic

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    if demands is not None and vehicle_capacities is not None:
        def demand_callback(from_index):
            node = manager.IndexToNode(from_index)
            return int(demands[node])
        demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(
            demand_callback_index,
            0,  # null capacity slack
            vehicle_capacities,  # vehicle maximum capacities
            True,  # start cumul to zero
            "Capacity",
        )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    # Use GUIDED_LOCAL_SEARCH for better quality
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.seconds = 30

    # Solve primary problem
    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        raise RuntimeError("No feasible solution found.")

    def extract_routes(sol):
        routes = []
        total_dist = 0.0
        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            route = []
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                route.append(node)
                previous_index = index
                index = sol.Value(routing.NextVar(index))
                total_dist += distance_matrix[manager.IndexToNode(previous_index), manager.IndexToNode(index)]
            route.append(depot)  # return to depot
            routes.append(route)
        return routes, total_dist

    routes, total_dist = extract_routes(solution)
    result = {
        "routes": routes,
        "total_distance": total_dist,
        "total_cost": total_dist * fuel_price,
        "alternative": None,
    }

    if allow_alternative:
        solver = routing.solver()
        different_arcs = []
        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            while not routing.IsEnd(index):
                next_index = solution.Value(routing.NextVar(index))
                different_arcs.append(
                    solver.IsDifferentCstVar(routing.NextVar(index), next_index)
                )
                index = next_index
        solver.Add(solver.Sum(different_arcs) >= 1)
        search_parameters.time_limit.seconds = ALTERNATIVE_SEARCH_TIME_LIMIT_SECONDS
        alt_solution = routing.SolveWithParameters(search_parameters)
        if alt_solution:
            alt_routes, alt_total_dist = extract_routes(alt_solution)
            result["alternative"] = {
                "routes": alt_routes,
                "total_distance": alt_total_dist,
                "total_cost": alt_total_dist * fuel_price,
            }
    return result

# Added Google Maps route helper

def get_google_distance_matrix(df, api_key):
    """Fetch a full distance matrix from Google Distance Matrix API.
    Returns a NumPy array (km) of shape (n, n) where n = len(df).
    """
    import googlemaps
    import numpy as np
    # Prepare origin/destination coordinates
    coords = df[['latitude', 'longitude']].astype(float).values.tolist()
    origins = [(lat, lon) for lat, lon in coords]
    destinations = origins
    gmaps = googlemaps.Client(key=api_key)
    n = len(origins)
    matrix = np.zeros((n, n))
    # Google Distance Matrix API limits: max 100 elements per request (origins * destinations).
    # We'll batch in chunks to stay within limits (e.g., 10x10 = 100).
    max_chunk = 10
    for i in range(0, n, max_chunk):
        o_chunk = origins[i:i+max_chunk]
        for j in range(0, n, max_chunk):
            d_chunk = destinations[j:j+max_chunk]
            result = gmaps.distance_matrix(o_chunk, d_chunk, mode='driving')
            rows = result.get('rows', [])
            for oi, row in enumerate(rows):
                elements = row.get('elements', [])
                for dj, elem in enumerate(elements):
                    if elem.get('status') == 'OK':
                        distance_m = elem['distance']['value']
                        matrix[i+oi, j+dj] = distance_m / 1000.0
                    else:
                        matrix[i+oi, j+dj] = np.inf
    return matrix

from typing import List, Tuple

def get_google_route_coords(origin: Tuple[float, float], destination: Tuple[float, float], api_key: str) -> List[Tuple[float, float]]:
    """Fetch route coordinates from Google Maps Directions API.

    Args:
        origin: (lat, lng) tuple for start.
        destination: (lat, lng) tuple for end.
        api_key: Google Maps API key.

    Returns:
        List of (lat, lng) tuples representing the route polyline.
    """
    if not api_key:
        raise ValueError("Google Maps API key is required for route fetching.")
    import googlemaps

    gmaps = googlemaps.Client(key=api_key)
    try:
        directions = gmaps.directions(
            origin=origin,
            destination=destination,
            mode="driving",
            departure_time="now",
        )
        if not directions:
            return []
        polyline = directions[0]["overview_polyline"]["points"]
        from googlemaps import convert
        points = convert.decode_polyline(polyline)
        return [(p["lat"], p["lng"]) for p in points]
    except Exception as e:
        print(f"Error fetching Google route: {e}")
        return []


def load_css():
    """Load and inject design_system.css into the Streamlit session."""
    import pathlib
    import streamlit as st
    css_path = pathlib.Path(__file__).parent / "design_system.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def glass_card(title: str = "", body_html: str = ""):
    """Render a reusable liquid glass card wrapper."""
    import streamlit as st
    header_html = f'<h3 style="margin-top:0; color:var(--text-primary); font-weight:600; font-size:1.15rem;">{title}</h3>' if title else ""
    st.markdown(
        f'<div class="glass-card">{header_html}{body_html}</div>',
        unsafe_allow_html=True,
    )

