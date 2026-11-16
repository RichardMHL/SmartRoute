# SmartRoute – Cost‑Effective Delivery Routing Tool

## Overview
**SmartRoute** is a lightweight, interactive decision‑analysis application for a logistics company that wants to find the most cost‑effective routing schedule for its fleet under predictable distances and a fixed fuel cost.

* **Deterministic model** – uses a classic Vehicle Routing Problem (VRP) formulation with exact distances (no stochasticity).
* **GIS integration** – fetches road‑network distances via **OSM‑Nx** and visualises routes on an interactive map with **Folium**. Optional Google Maps, public OSRM, and FOSSGIS OSRM providers are available.
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
   * Choose a depot and the delivery locations for the run; unselected CSV rows are excluded from routing and the map.
2. **Distance matrix and route geometry** – `utils.calculate_distance_matrix` uses shortest driving paths on an OpenStreetMap road graph; the map draws those road-following paths rather than straight lines between stops.
   * Select FOSSGIS OSRM or public OSRM to use driving distances and full road geometry without an API key. These public services send the depot and selected stop coordinates to their routing servers; the public OSRM demo is not intended for production use.
   * Google Maps driving distances and road geometry are available when configured with an API key. If road routing is unavailable, the app labels straight-line distances as approximate and warns that road paths cannot be displayed.
3. **Mathematical model** – `utils.solve_vrp` formulates the VRP with Google OR‑Tools:
   * Objective: minimise total travelled distance (hence fuel cost = distance × fuel_price).
   * Constraints: each selected delivery location is visited exactly once, each vehicle starts/ends at the depot, optional vehicle capacity.
4. **Result visualisation** – Streamlit displays:
   * A Folium map with the recommended route (coloured per vehicle) and, when available, a separately toggleable dashed alternative route; only selected stops are marked.
   * Per-vehicle stop order, total distance, and fuel cost for the primary and alternative routes.
   * An alternative route calculation, enabled by default, with total distance and estimated fuel cost. The alternative search is capped at 5 seconds; when OR-Tools is unavailable, the greedy fallback varies stop order when possible.

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

## Deploy to Streamlit Community Cloud
1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) with GitHub.
2. Select **Create app**, then choose the `RichardMHL/SmartRoute` repository, the `ui-liquid-glass` branch, and `app.py` as the main file.
3. Select **Deploy** and wait for the app to finish building.

The app can use FOSSGIS OSRM without an API key. If you choose Google Maps, enter its API key in the app sidebar; do not commit keys or `.streamlit/secrets.toml` to the repository.

---

## License
MIT – feel free to adapt the code for your own fleet management needs.
