# SmartRoute – Cost‑Effective Delivery Routing Tool

## Overview
**SmartRoute** is a lightweight, interactive decision‑analysis application for a logistics company that wants to find the most cost‑effective routing schedule for its fleet under predictable distances and a fixed fuel cost.

* **Deterministic model** – uses a classic Vehicle Routing Problem (VRP) formulation with exact distances (no stochasticity).
* **GIS integration** – fetches road‑network distances via **OSM‑Nx** and visualises routes on an interactive map with **Folium**; no Google Maps API key is required.
* **Beautiful UI** – built with **Streamlit**, allowing users to upload location data, tweak parameters (fuel price, vehicle capacity, number of vehicles) and instantly see the optimal routes and alternative suggestions.
* **Extensible** – the core optimisation logic is isolated in `utils.py` and can be swapped for other solvers if needed.

---

## Quick start (Windows)
1. **Install Python 3.10+** – e.g., from the official installer.
2. Open a PowerShell terminal in the project folder:
   ```powershell
   cd C:\Users\bonga\Downloads\SmartRoute
   ```
3. Install required packages (internet needed for the first time):
   ```powershell
   pip install -r requirements.txt
   ```
4. Run the Streamlit app:
   ```powershell
   streamlit run app.py
   ```
5. Open the URL shown by Streamlit (normally `http://localhost:8501`).

---

## How it works
1. **Data input** – upload a CSV with columns `id, latitude, longitude, demand` (demand is optional for capacity‑constrained routing).
2. **Distance matrix** – `utils.calculate_distance_matrix` builds a road‑network distance matrix using OSM‑Nx (uses the OpenStreetMap road graph). The matrix is deterministic because the same graph and routing algorithm always produce the same distances.
   * Route calculation uses OpenStreetMap's Overpass API when reachable. If a network timeout occurs after 30 seconds, the app warns and falls back to approximate great-circle distances.
3. **Mathematical model** – `utils.solve_vrp` formulates the VRP with Google OR‑Tools:
   * Objective: minimise total travelled distance (hence fuel cost = distance × fuel_price).
   * Constraints: each location visited exactly once, each vehicle starts/ends at the depot, optional vehicle capacity.
4. **Result visualisation** – Streamlit displays:
   * A Folium map with the optimal route (coloured per vehicle) and markers for each stop.
   * A table of routes and total distance per vehicle.
   * An “alternative routes” section that searches for a route assignment distinct from the primary solution.

---

## Files
| File | Purpose |
|------|---------|
| `app.py` | Streamlit UI – data upload, parameter controls, map rendering |
| `utils.py` | Distance‑matrix calculation (`osmnx`) and VRP solver (`ortools`) |
| `requirements.txt` | Python dependencies |
| `README.md` | This documentation |

---

## Extending the tool
* **Different cost structures** – modify the objective in `solve_vrp` to include time‑dependent fuel prices or tolls.
* **Multiple depots** – extend the model with additional start/end nodes.
* **Real‑time updates** – replace the static OSM‑Nx graph with a live traffic API (requires network calls).

---

## License
MIT – feel free to adapt the code for your own fleet management needs.
