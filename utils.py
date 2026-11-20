
import numpy as np
import requests
# Note: Heavy spatial libraries (osmnx, networkx) are lazy-loaded inside
# road-graph functions below to avoid a 30+ second freeze on startup.
# OR‑Tools is optional – it may not be available on all Windows setups.
try:
    from ortools.constraint_solver import routing_enums_pb2
    from ortools.constraint_solver import pywrapcp
    _ORTOOLS_AVAILABLE = True
except Exception:  # pragma: no cover
    _ORTOOLS_AVAILABLE = False


ROAD_NETWORK_TIMEOUT_SECONDS = 4
ALTERNATIVE_SEARCH_TIME_LIMIT_SECONDS = 1
EXACT_SMALL_ROUTE_CUSTOMER_LIMIT = 6
EXACT_SMALL_ROUTE_VEHICLE_LIMIT = 3
OSRM_ROUTING_SERVERS = {
    "public": "https://router.project-osrm.org",
    "fossgis": "https://routing.openstreetmap.de/routed-car",
}




def generate_ranked_alternatives(
    primary_routes,
    distance_matrix,
    depot=0,
    demands=None,
    vehicle_capacities=None,
    fuel_price=1.0,
    max_alternatives=2,
    extra_candidates=None,
):
    """Generate strictly valid, distinct alternative VRP routes ranked from shortest to longer.

    Every alternative route:
      - Starts and ends at the depot.
      - Visits all requested customers across vehicles exactly once.
      - Respects vehicle capacities and demands.
      - Has a stop sequence distinct from primary and previously accepted alternatives.
      - Is ranked in ascending order of total distance (Alternative 1 is the 2nd shortest).
    """
    num_vehicles = len(primary_routes)
    max_alternatives = max(0, int(max_alternatives))
    if max_alternatives == 0:
        return []

    def is_valid_solution(routes):
        if len(routes) != num_vehicles:
            return False
        all_customers = []
        for v_idx, r in enumerate(routes):
            if len(r) < 2 or r[0] != depot or r[-1] != depot:
                return False
            customers = r[1:-1]
            all_customers.extend(customers)
            if demands is not None and vehicle_capacities is not None:
                if sum(demands[c] for c in customers) > vehicle_capacities[v_idx]:
                    return False
        expected_customers = sorted([c for r in primary_routes for c in r[1:-1]])
        if sorted(all_customers) != expected_customers:
            return False
        return True

    def calculate_distance(routes):
        return sum(
            float(distance_matrix[r[i], r[i + 1]])
            for r in routes
            for i in range(len(r) - 1)
        )

    def canonical(routes):
        return tuple(tuple(int(x) for x in r) for r in routes)

    primary_canon = canonical(primary_routes)
    primary_dist = calculate_distance(primary_routes)
    capacity_constrained = demands is not None and vehicle_capacities is not None
    valid_extra_routes = []
    seen_extra_routes = {primary_canon}
    for candidate in extra_candidates or []:
        if not is_valid_solution(candidate):
            continue
        candidate_key = canonical(candidate)
        if candidate_key not in seen_extra_routes:
            seen_extra_routes.add(candidate_key)
            valid_extra_routes.append([list(route) for route in candidate])

    best_candidates = []

    def could_beat(distance):
        return (
            len(best_candidates) < max_alternatives
            or round(distance, 6) <= round(best_candidates[-1][0], 6)
        )

    def offer_candidate(routes, distance):
        candidate_key = canonical(routes)
        if candidate_key == primary_canon or any(
            candidate_key == existing_key
            for _, existing_key, _ in best_candidates
        ):
            return
        best_candidates.append((distance, candidate_key, routes))
        best_candidates.sort(key=lambda item: (round(item[0], 6), item[2]))
        del best_candidates[max_alternatives:]

    def swapped_route_delta(route, i, j):
        def node_at(index):
            if index == i:
                return route[j]
            if index == j:
                return route[i]
            return route[index]

        edge_starts = {i - 1, i, j - 1, j}
        return sum(
            float(distance_matrix[node_at(index), node_at(index + 1)])
            - float(distance_matrix[route[index], route[index + 1]])
            for index in edge_starts
        )

    # Keep only the best requested candidates. Move costs are calculated from
    # changed edges, avoiding a full route copy and rescan for every permutation.
    seed_pool = [primary_routes] + valid_extra_routes[:4]
    for base_routes in seed_pool:
        base_routes = [list(route) for route in base_routes]
        base_distance = calculate_distance(base_routes)
        route_loads = [
            sum(demands[node] for node in route[1:-1])
            if capacity_constrained else 0
            for route in base_routes
        ]
        offer_candidate(base_routes, base_distance)

        for vehicle_id, route in enumerate(base_routes):
            route_length = len(route)
            if route_length >= 3:
                reverse_prefix = [0.0] * route_length
                for edge_index in range(1, route_length):
                    reverse_prefix[edge_index] = (
                        reverse_prefix[edge_index - 1]
                        + float(distance_matrix[route[edge_index], route[edge_index - 1]])
                        - float(distance_matrix[route[edge_index - 1], route[edge_index]])
                    )

                for i in range(1, route_length - 2):
                    for j in range(i + 1, route_length - 1):
                        delta = (
                            float(distance_matrix[route[i - 1], route[j]])
                            + float(distance_matrix[route[i], route[j + 1]])
                            - float(distance_matrix[route[i - 1], route[i]])
                            - float(distance_matrix[route[j], route[j + 1]])
                            + reverse_prefix[j]
                            - reverse_prefix[i]
                        )
                        distance = base_distance + delta
                        if could_beat(distance):
                            candidate_routes = [list(item) for item in base_routes]
                            candidate_routes[vehicle_id] = (
                                route[:i]
                                + list(reversed(route[i : j + 1]))
                                + route[j + 1 :]
                            )
                            offer_candidate(candidate_routes, distance)

                for i in range(1, route_length - 2):
                    for j in range(i + 1, route_length - 1):
                        distance = base_distance + swapped_route_delta(route, i, j)
                        if could_beat(distance):
                            candidate_route = list(route)
                            candidate_route[i], candidate_route[j] = (
                                candidate_route[j],
                                candidate_route[i],
                            )
                            candidate_routes = [list(item) for item in base_routes]
                            candidate_routes[vehicle_id] = candidate_route
                            offer_candidate(candidate_routes, distance)

            for i in range(1, route_length - 1):
                for j in range(1, route_length - 1):
                    if i == j:
                        continue
                    node = route[i]
                    remove_delta = (
                        float(distance_matrix[route[i - 1], route[i + 1]])
                        - float(distance_matrix[route[i - 1], node])
                        - float(distance_matrix[node, route[i + 1]])
                    )
                    if j < i:
                        left, right = route[j - 1], route[j]
                    else:
                        left, right = route[j], route[j + 1]
                    insert_delta = (
                        float(distance_matrix[left, node])
                        + float(distance_matrix[node, right])
                        - float(distance_matrix[left, right])
                    )
                    distance = base_distance + remove_delta + insert_delta
                    if could_beat(distance):
                        remainder = route[:i] + route[i + 1 :]
                        candidate_route = remainder[:j] + [node] + remainder[j:]
                        candidate_routes = [list(item) for item in base_routes]
                        candidate_routes[vehicle_id] = candidate_route
                        offer_candidate(candidate_routes, distance)

        if num_vehicles < 2:
            continue

        for vehicle1 in range(num_vehicles):
            route1 = base_routes[vehicle1]
            for vehicle2 in range(num_vehicles):
                if vehicle1 == vehicle2:
                    continue
                route2 = base_routes[vehicle2]
                for i in range(1, len(route1) - 1):
                    node = route1[i]
                    remove_delta = (
                        float(distance_matrix[route1[i - 1], route1[i + 1]])
                        - float(distance_matrix[route1[i - 1], node])
                        - float(distance_matrix[node, route1[i + 1]])
                    )
                    for position in range(1, len(route2)):
                        if (
                            capacity_constrained
                            and route_loads[vehicle2] + demands[node]
                            > vehicle_capacities[vehicle2]
                        ):
                            continue
                        left, right = route2[position - 1], route2[position]
                        insert_delta = (
                            float(distance_matrix[left, node])
                            + float(distance_matrix[node, right])
                            - float(distance_matrix[left, right])
                        )
                        distance = base_distance + remove_delta + insert_delta
                        if could_beat(distance):
                            candidate_routes = [list(item) for item in base_routes]
                            candidate_routes[vehicle1] = route1[:i] + route1[i + 1 :]
                            candidate_routes[vehicle2] = (
                                route2[:position] + [node] + route2[position:]
                            )
                            offer_candidate(candidate_routes, distance)

                if vehicle2 <= vehicle1:
                    continue
                for i in range(1, len(route1) - 1):
                    node1 = route1[i]
                    for j in range(1, len(route2) - 1):
                        node2 = route2[j]
                        if capacity_constrained and (
                            route_loads[vehicle1] - demands[node1] + demands[node2]
                            > vehicle_capacities[vehicle1]
                            or route_loads[vehicle2] - demands[node2] + demands[node1]
                            > vehicle_capacities[vehicle2]
                        ):
                            continue
                        delta = (
                            float(distance_matrix[route1[i - 1], node2])
                            + float(distance_matrix[node2, route1[i + 1]])
                            - float(distance_matrix[route1[i - 1], node1])
                            - float(distance_matrix[node1, route1[i + 1]])
                            + float(distance_matrix[route2[j - 1], node1])
                            + float(distance_matrix[node1, route2[j + 1]])
                            - float(distance_matrix[route2[j - 1], node2])
                            - float(distance_matrix[node2, route2[j + 1]])
                        )
                        distance = base_distance + delta
                        if could_beat(distance):
                            candidate_routes = [list(item) for item in base_routes]
                            candidate_routes[vehicle1][i] = node2
                            candidate_routes[vehicle2][j] = node1
                            offer_candidate(candidate_routes, distance)

    evaluated = [
        (distance, routes)
        for distance, _, routes in best_candidates
    ]

    alternatives = []
    seen = {primary_canon}
    for d, sol_routes in evaluated:
        c = canonical(sol_routes)
        if c in seen:
            continue
        seen.add(c)
        alt_label = f"Alternative {len(alternatives) + 1}"
        alternatives.append({
            "label": alt_label,
            "routes": sol_routes,
            "total_distance": round(d, 2),
            "total_cost": round(d * fuel_price, 2),
            "distance_diff": round(d - primary_dist, 2),
        })
        if len(alternatives) >= max_alternatives:
            break

    return alternatives


def calculate_route_travel_time(routes, distance_matrix, departure_time=None, location_names=None):
    """Estimate total driving time, completion time, and average speed across vehicle routes."""
    total_minutes = 0.0
    total_driving_minutes = 0.0
    max_vehicle_minutes = 0.0
    total_dist = 0.0
    vehicle_details = []
    for veh_id, route in enumerate(routes):
        pred = generate_route_predictions(
            route,
            distance_matrix,
            departure_time=departure_time,
            location_names=location_names,
        )
        t = pred["total_time_minutes"]
        driving_minutes = pred["driving_time_minutes"]
        d = pred["total_distance_km"]
        total_minutes += t
        total_driving_minutes += driving_minutes
        total_dist += d
        if t > max_vehicle_minutes:
            max_vehicle_minutes = t
        vehicle_details.append({
            "vehicle_id": veh_id,
            "distance_km": round(d, 2),
            "time_minutes": round(t, 1),
            "avg_speed_kmh": round(pred["avg_speed_kmh"], 1),
            "arrival_time": pred["estimated_arrival"],
            "departure_time": pred["departure_time"],
            "segments": pred["segments"],
        })
    avg_speed = (
        round(total_dist / (total_driving_minutes / 60.0), 1)
        if total_driving_minutes > 0 else 0.0
    )
    return {
        "total_time_minutes": round(total_minutes, 1),
        "completion_time_minutes": round(max_vehicle_minutes, 1),
        "avg_speed_kmh": avg_speed,
        "vehicle_details": vehicle_details,
    }


def _calculate_routes_distance(routes, distance_matrix):
    return sum(
        float(distance_matrix[route[index], route[index + 1]])
        for route in routes
        for index in range(len(route) - 1)
    )


def _solve_small_vrp_exact(
    distance_matrix,
    num_vehicles,
    depot,
    demands,
    vehicle_capacities,
    fuel_price,
    allow_alternative,
    max_alternatives,
):
    """Exactly rank small VRPs without waiting on the time-bounded metaheuristic."""
    customers = [node for node in range(len(distance_matrix)) if node != depot]
    if (
        not customers
        or len(customers) > EXACT_SMALL_ROUTE_CUSTOMER_LIMIT
        or num_vehicles < 1
        or num_vehicles > EXACT_SMALL_ROUTE_VEHICLE_LIMIT
    ):
        return None

    from itertools import combinations_with_replacement, permutations

    cut_patterns = list(combinations_with_replacement(
        range(len(customers) + 1), num_vehicles - 1
    ))
    feasible = {}
    for ordering in permutations(customers):
        for cuts in cut_patterns:
            boundaries = (0, *cuts, len(customers))
            routes = [
                [depot, *ordering[boundaries[v]:boundaries[v + 1]], depot]
                for v in range(num_vehicles)
            ]
            if demands is not None and vehicle_capacities is not None:
                if any(
                    sum(demands[node] for node in route[1:-1])
                    > vehicle_capacities[vehicle_id]
                    for vehicle_id, route in enumerate(routes)
                ):
                    continue
            route_key = tuple(tuple(route) for route in routes)
            if route_key not in feasible:
                feasible[route_key] = _calculate_routes_distance(
                    routes, distance_matrix
                )

    if not feasible:
        raise RuntimeError("No feasible solution found.")

    ranked_solutions = sorted(
        feasible.items(),
        key=lambda item: (round(item[1], 6), item[0]),
    )
    best_key, best_distance = ranked_solutions[0]
    best_routes = [list(route) for route in best_key]
    time_info = calculate_route_travel_time(best_routes, distance_matrix)
    alternatives = []
    if allow_alternative and max_alternatives > 0:
        for index, (route_key, distance) in enumerate(
            ranked_solutions[1:max_alternatives + 1], start=1
        ):
            routes = [list(route) for route in route_key]
            alt_time = calculate_route_travel_time(routes, distance_matrix)
            alternatives.append({
                "label": f"Alternative {index}",
                "routes": routes,
                "total_distance": round(distance, 2),
                "total_cost": round(distance * fuel_price, 2),
                "distance_diff": round(distance - best_distance, 2),
                "completion_time_minutes": alt_time["completion_time_minutes"],
                "total_time_minutes": alt_time["total_time_minutes"],
                "avg_speed_kmh": alt_time["avg_speed_kmh"],
                "vehicle_details": alt_time["vehicle_details"],
            })

    return {
        "routes": best_routes,
        "total_distance": round(best_distance, 2),
        "total_cost": round(best_distance * fuel_price, 2),
        "completion_time_minutes": time_info["completion_time_minutes"],
        "total_time_minutes": time_info["total_time_minutes"],
        "avg_speed_kmh": time_info["avg_speed_kmh"],
        "vehicle_details": time_info["vehicle_details"],
        "alternatives": alternatives,
        "alternative": alternatives[0] if alternatives else None,
    }


def _greedy_vrp(
    distance_matrix,
    num_vehicles=1,
    depot=0,
    demands=None,
    vehicle_capacities=None,
    fuel_price=1.0,
    allow_alternative=False,
    max_alternatives=2,
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

    time_info = calculate_route_travel_time(routes, distance_matrix)

    result = {
        "routes": routes,
        "total_distance": total_dist,
        "total_cost": total_dist * fuel_price,
        "completion_time_minutes": time_info["completion_time_minutes"],
        "total_time_minutes": time_info["total_time_minutes"],
        "avg_speed_kmh": time_info["avg_speed_kmh"],
        "vehicle_details": time_info["vehicle_details"],
        "alternatives": [],
        "alternative": None,
    }

    if allow_alternative and max_alternatives > 0:
        alternatives = generate_ranked_alternatives(
            routes,
            distance_matrix,
            depot=depot,
            demands=demands,
            vehicle_capacities=vehicle_capacities,
            fuel_price=fuel_price,
            max_alternatives=max_alternatives,
        )
        for alt in alternatives:
            alt_time = calculate_route_travel_time(alt["routes"], distance_matrix)
            alt["completion_time_minutes"] = alt_time["completion_time_minutes"]
            alt["total_time_minutes"] = alt_time["total_time_minutes"]
            alt["avg_speed_kmh"] = alt_time["avg_speed_kmh"]
            alt["vehicle_details"] = alt_time["vehicle_details"]

        result["alternatives"] = alternatives
        result["alternative"] = alternatives[0] if alternatives else None

    return result

    return result


def build_road_graph(locations):
    """Download a driving-road graph covering the supplied coordinates."""
    import osmnx as ox
    lat_min, lat_max = locations['latitude'].min(), locations['latitude'].max()
    lon_min, lon_max = locations['longitude'].min(), locations['longitude'].max()
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min

    # If coordinates span across distant cities (> 0.35 deg, ~40+ km),
    # downloading every street via Overpass API would take minutes and timeout.
    # Fail fast so the app can fall back to driving distance / straight-line immediately!
    if lat_span > 0.35 or lon_span > 0.35:
        raise RuntimeError("Bounding box is too large for real-time OpenStreetMap street download; using fast distance calculation.")

    bbox = (lon_min - 0.03, lat_min - 0.03, lon_max + 0.03, lat_max + 0.03)
    ox.settings.requests_timeout = ROAD_NETWORK_TIMEOUT_SECONDS
    return ox.graph_from_bbox(bbox, network_type='drive')


def _nearest_road_nodes(locations, graph):
    import osmnx as ox
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
    import networkx as nx
    graph = graph or build_road_graph(locations)
    nodes = _nearest_road_nodes(locations, graph)
    n = len(nodes)
    # Only route between selected stops. A full single-source Dijkstra explores
    # the entire street graph for every stop, which becomes very slow as soon
    # as more than one destination is selected. Stop each search once all
    # selected targets have been settled.
    import heapq

    unique_nodes = set(nodes)
    lengths_by_source = {}
    for source in unique_nodes:
        distances = {source: 0.0}
        pending = [(0.0, source)]
        targets = set(unique_nodes)
        while pending and targets:
            current_distance, current_node = heapq.heappop(pending)
            if current_distance != distances.get(current_node):
                continue
            if current_node in targets:
                targets.remove(current_node)
            for neighbor, edge_data in graph[current_node].items():
                if graph.is_multigraph():
                    weight = min(
                        float(attributes.get("length", 1.0))
                        for attributes in edge_data.values()
                    )
                else:
                    weight = float(edge_data.get("length", 1.0))
                candidate_distance = current_distance + weight
                if candidate_distance < distances.get(neighbor, np.inf):
                    distances[neighbor] = candidate_distance
                    heapq.heappush(pending, (candidate_distance, neighbor))
        lengths_by_source[source] = distances

    dist_mat = np.zeros((n, n))
    for i, source in enumerate(nodes):
        lengths = lengths_by_source.get(source, {})
        for j, target in enumerate(nodes):
            # lengths are in meters; convert to km
            dist_mat[i, j] = lengths.get(target, np.inf) / 1000.0
    return dist_mat


def calculate_route_geometries(locations, routes, graph):
    """Return road-following (latitude, longitude) coordinates for each vehicle route."""
    import networkx as nx
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
    max_alternatives=2,
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
        If True, searches for valid alternative routes distinct from the best solution.
    max_alternatives : int
        Maximum number of alternative routes to compute and return (ranked from shortest to longer).

    Returns
    -------
    dict
        {
            "routes": list of lists of location indices (including depot at start/end),
            "total_distance": float,
            "total_cost": float,
            "completion_time_minutes": float,
            "total_time_minutes": float,
            "alternatives": list of dicts with each alternative's details,
            "alternative": the first (second-shortest) alternative route, or None
        }
    """
    exact_result = _solve_small_vrp_exact(
        distance_matrix,
        num_vehicles=num_vehicles,
        depot=depot,
        demands=demands,
        vehicle_capacities=vehicle_capacities,
        fuel_price=fuel_price,
        allow_alternative=allow_alternative,
        max_alternatives=max_alternatives,
    )
    if exact_result is not None:
        return exact_result

    if not _ORTOOLS_AVAILABLE:
        # Fallback to greedy implementation when OR‑Tools cannot be imported
        return _greedy_vrp(
            distance_matrix,
            num_vehicles=num_vehicles,
            depot=depot,
            demands=demands,
            vehicle_capacities=vehicle_capacities,
            fuel_price=fuel_price,
            allow_alternative=allow_alternative,
            max_alternatives=max_alternatives,
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
    # Bound optimizer search; distance and geometry providers add separate latency.
    search_parameters.time_limit.seconds = 3

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
    time_info = calculate_route_travel_time(routes, distance_matrix)

    result = {
        "routes": routes,
        "total_distance": round(total_dist, 2),
        "total_cost": round(total_dist * fuel_price, 2),
        "completion_time_minutes": time_info["completion_time_minutes"],
        "total_time_minutes": time_info["total_time_minutes"],
        "avg_speed_kmh": time_info["avg_speed_kmh"],
        "vehicle_details": time_info["vehicle_details"],
        "alternatives": [],
        "alternative": None,
    }

    if allow_alternative and max_alternatives > 0:
        ortools_candidates = []
        solver = routing.solver()
        cur_sol = solution
        # Iteratively constrain the solver to explore non-duplicate routes
        for _ in range(min(max_alternatives, 3)):
            different_arcs = []
            for vehicle_id in range(num_vehicles):
                index = routing.Start(vehicle_id)
                while not routing.IsEnd(index):
                    next_index = cur_sol.Value(routing.NextVar(index))
                    different_arcs.append(
                        solver.IsDifferentCstVar(routing.NextVar(index), next_index)
                    )
                    index = next_index
            solver.Add(solver.Sum(different_arcs) >= 1)
            search_parameters.time_limit.seconds = ALTERNATIVE_SEARCH_TIME_LIMIT_SECONDS
            alt_solution = routing.SolveWithParameters(search_parameters)
            if not alt_solution:
                break
            alt_routes, _ = extract_routes(alt_solution)
            ortools_candidates.append(alt_routes)
            cur_sol = alt_solution

        # Combine OR-Tools alternatives with systematic neighborhood search
        alternatives = generate_ranked_alternatives(
            routes,
            distance_matrix,
            depot=depot,
            demands=demands,
            vehicle_capacities=vehicle_capacities,
            fuel_price=fuel_price,
            max_alternatives=max_alternatives,
            extra_candidates=ortools_candidates,
        )

        # Attach travel times to each ranked alternative
        for alt in alternatives:
            alt_time = calculate_route_travel_time(alt["routes"], distance_matrix)
            alt["completion_time_minutes"] = alt_time["completion_time_minutes"]
            alt["total_time_minutes"] = alt_time["total_time_minutes"]
            alt["avg_speed_kmh"] = alt_time["avg_speed_kmh"]
            alt["vehicle_details"] = alt_time["vehicle_details"]

        result["alternatives"] = alternatives
        result["alternative"] = alternatives[0] if alternatives else None

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


##############################################################################
# ETA Predictions – Fictional Historical Data Engine
##############################################################################

import random
import datetime
from typing import Optional

# Seed categories that make the fictional data look realistic
_TRAFFIC_CONDITIONS = ["Free-flow", "Light traffic", "Moderate traffic", "Heavy traffic", "Congested"]
_TRAFFIC_WEIGHTS     = [0.15, 0.30, 0.30, 0.18, 0.07]
_TRAFFIC_SPEED_MULT  = [1.0, 0.85, 0.65, 0.45, 0.28]

# Fictional base speed ranges per road type (km/h)
_ROAD_TYPES = ["Highway", "Arterial", "Collector", "Local"]
_ROAD_BASE_SPEEDS = {"Highway": 100, "Arterial": 60, "Collector": 40, "Local": 25}

# Time-of-day multipliers (hour → speed factor relative to free-flow)
_TOD_FACTORS = {
    0: 1.05, 1: 1.05, 2: 1.05, 3: 1.05, 4: 1.02, 5: 0.95,
    6: 0.80, 7: 0.60, 8: 0.52, 9: 0.65, 10: 0.78, 11: 0.82,
    12: 0.75, 13: 0.80, 14: 0.82, 15: 0.72, 16: 0.58, 17: 0.50,
    18: 0.60, 19: 0.75, 20: 0.88, 21: 0.95, 22: 1.00, 23: 1.03,
}


def _deterministic_seed(from_idx: int, to_idx: int, salt: int = 42) -> int:
    """Create a repeatable seed from a pair of location indices."""
    return hash((from_idx, to_idx, salt)) & 0xFFFF_FFFF


def generate_segment_prediction(
    from_idx: int,
    to_idx: int,
    distance_km: float,
    departure_time: Optional[datetime.datetime] = None,
) -> dict:
    """Generate a *fictional* but deterministic ETA prediction for one segment.

    Returns a dict with keys:
        road_type, base_speed_kmh, traffic_condition, traffic_factor,
        tod_factor, estimated_speed_kmh, estimated_time_minutes,
        historical_avg_speed_kmh, confidence_pct, segment_distance_km
    """
    if departure_time is None:
        departure_time = datetime.datetime.now()

    rng = random.Random(_deterministic_seed(from_idx, to_idx))

    road_type = rng.choice(_ROAD_TYPES)
    base_speed = _ROAD_BASE_SPEEDS[road_type]

    # Weighted traffic condition
    traffic_idx = rng.choices(range(len(_TRAFFIC_CONDITIONS)), weights=_TRAFFIC_WEIGHTS, k=1)[0]
    traffic_condition = _TRAFFIC_CONDITIONS[traffic_idx]
    traffic_factor = _TRAFFIC_SPEED_MULT[traffic_idx]

    # Time-of-day factor
    hour = departure_time.hour
    tod_factor = _TOD_FACTORS.get(hour, 0.80)

    # Historical average (slight random variation around base × 0.72)
    hist_avg = round(base_speed * (0.68 + rng.random() * 0.14), 1)

    # Estimated speed for right now
    est_speed = round(base_speed * traffic_factor * tod_factor, 1)
    est_speed = max(est_speed, 5.0)  # floor at 5 km/h

    # Time to traverse
    if distance_km <= 0 or est_speed <= 0:
        est_minutes = 0.0
    else:
        est_minutes = round((distance_km / est_speed) * 60, 1)

    confidence = rng.randint(72, 97)

    return {
        "road_type": road_type,
        "base_speed_kmh": base_speed,
        "traffic_condition": traffic_condition,
        "traffic_factor": round(traffic_factor, 2),
        "tod_factor": round(tod_factor, 2),
        "estimated_speed_kmh": est_speed,
        "estimated_time_minutes": est_minutes,
        "historical_avg_speed_kmh": hist_avg,
        "confidence_pct": confidence,
        "segment_distance_km": round(distance_km, 2),
    }


def generate_route_predictions(
    route: list,
    distance_matrix,
    departure_time: Optional[datetime.datetime] = None,
    location_names: Optional[list] = None,
) -> dict:
    """Build a full ETA prediction for a vehicle route (list of location indices).

    Returns a dict:
        segments: list of per-leg prediction dicts (augmented with from/to labels)
        total_distance_km, total_time_minutes, avg_speed_kmh,
        estimated_arrival: datetime string,
        historical_30d: dict with avg / best / worst times
    """
    if departure_time is None:
        departure_time = datetime.datetime.now()

    segments = []
    running_time = 0.0
    running_dist = 0.0
    total_elapsed_minutes = 0.0
    cur_departure = departure_time

    for leg_idx in range(len(route) - 1):
        from_idx = route[leg_idx]
        to_idx   = route[leg_idx + 1]
        dist = float(distance_matrix[from_idx, to_idx])

        pred = generate_segment_prediction(from_idx, to_idx, dist, cur_departure)

        # Labels
        from_label = str(location_names[from_idx]) if location_names else f"Stop {from_idx}"
        to_label   = str(location_names[to_idx])   if location_names else f"Stop {to_idx}"
        pred["from"] = from_label
        pred["to"]   = to_label
        pred["leg"]  = leg_idx + 1

        # Running totals
        running_time += pred["estimated_time_minutes"]
        running_dist += pred["segment_distance_km"]

        # ETA at destination of this leg
        arrival_dt = cur_departure + datetime.timedelta(minutes=pred["estimated_time_minutes"])
        pred["eta"] = arrival_dt.strftime("%H:%M")
        pred["cumulative_time_minutes"] = round(
            (arrival_dt - departure_time).total_seconds() / 60.0, 1
        )

        # Include service stops in completion time, but not after the final arrival.
        if leg_idx < len(route) - 2:
            rng = random.Random(_deterministic_seed(from_idx, to_idx, salt=99))
            service_min = rng.randint(2, 8)
        else:
            service_min = 0
        pred["service_time_minutes"] = service_min
        cur_departure = arrival_dt + datetime.timedelta(minutes=service_min)
        total_elapsed_minutes = (
            cur_departure - departure_time
        ).total_seconds() / 60.0

        segments.append(pred)

    avg_speed = round(running_dist / (running_time / 60), 1) if running_time > 0 else 0.0
    final_arrival = cur_departure

    # Fictional 30-day historical summary (deterministic)
    rng_hist = random.Random(hash(tuple(route)) & 0xFFFF_FFFF)
    hist_avg_min  = round(total_elapsed_minutes * (0.90 + rng_hist.random() * 0.20), 1)
    hist_best_min = round(total_elapsed_minutes * (0.70 + rng_hist.random() * 0.10), 1)
    hist_worst_min = round(total_elapsed_minutes * (1.20 + rng_hist.random() * 0.40), 1)

    return {
        "segments": segments,
        "total_distance_km": round(running_dist, 2),
        "total_time_minutes": round(total_elapsed_minutes, 1),
        "driving_time_minutes": round(running_time, 1),
        "avg_speed_kmh": avg_speed,
        "estimated_arrival": final_arrival.strftime("%H:%M"),
        "departure_time": departure_time.strftime("%H:%M"),
        "historical_30d": {
            "avg_minutes": hist_avg_min,
            "best_minutes": hist_best_min,
            "worst_minutes": hist_worst_min,
        },
    }


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

