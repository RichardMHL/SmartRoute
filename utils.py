
import numpy as np
import osmnx as ox
import networkx as nx
# OR‑Tools is optional – it may not be available on all Windows setups.
try:
    from ortools.constraint_solver import routing_enums_pb2
    from ortools.constraint_solver import pywrapcp
    _ORTOOLS_AVAILABLE = True
except Exception:  # pragma: no cover
    _ORTOOLS_AVAILABLE = False


ROAD_NETWORK_TIMEOUT_SECONDS = 30




def _greedy_vrp(distance_matrix, num_vehicles=1, depot=0, demands=None, vehicle_capacities=None, fuel_price=1.0):
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
    return {"routes": routes, "total_distance": total_dist, "total_cost": total_dist * fuel_price, "alternative": None}


def calculate_distance_matrix(locations):
    """Calculate a deterministic road‑network distance matrix.

    Args:
        locations (pd.DataFrame): must contain columns ['latitude', 'longitude'].
    Returns:
        np.ndarray: square matrix of distances in kilometers.
    """
    # Build a graph that covers all points (add a small buffer)
    lat_min, lat_max = locations['latitude'].min(), locations['latitude'].max()
    lon_min, lon_max = locations['longitude'].min(), locations['longitude'].max()
    # 5 km buffer
    bbox = (lon_min - 0.05, lat_min - 0.05, lon_max + 0.05, lat_max + 0.05)
    ox.settings.requests_timeout = ROAD_NETWORK_TIMEOUT_SECONDS
    G = ox.graph_from_bbox(bbox, network_type='drive')
    # Project to UTM for accurate distance computation
    G = ox.project_graph(G)
    # Pre‑compute nearest nodes for each location
    nodes = []
    for _, row in locations.iterrows():
        point = (row['latitude'], row['longitude'])
        node = ox.distance.nearest_nodes(G, X=point[1], Y=point[0])
        nodes.append(node)
    n = len(nodes)
    dist_mat = np.zeros((n, n))
    # Use shortest_path_length (weight='length') for each pair
    for i in range(n):
        lengths = nx.shortest_path_length(G, source=nodes[i], weight='length')
        for j in range(n):
            if i == j:
                dist = 0.0
            else:
                dist = lengths.get(nodes[j], np.inf) / 1000.0  # convert meters to km
            dist_mat[i, j] = dist
    return dist_mat


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
