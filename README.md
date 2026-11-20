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
   * A Folium map with a visible color key for the recommended route (colored per vehicle) and separately toggleable dashed alternatives (Alternative 1, Alternative 2, etc.); only selected stops are marked.
   * Per-vehicle stop order, total distance, estimated completion time, average driving speed, and fuel cost for recommended and alternative routes. Durations automatically scale from minutes and hours to days, weeks, months, or years. The Output tab shows the summary; Predictions shows per-stop ETAs and segment travel durations.
   * Configurable alternative routes calculation (choose 1 to 5 alternatives), ranked strictly from shortest to longer (Alternative 1 is the second-shortest valid route).
   * Arrival-time predictions and estimated speeds based on fictional historical traffic patterns across different times of day. Completion time includes the illustrative service time at intermediate stops; speed and traffic are not live estimates.
   * Routes with up to 6 deliveries and 3 vehicles are solved and ranked exactly, avoiding the optimizer wait for small inputs. Larger problems use OR-Tools with a 3-second primary search and up to three 1-second alternative searches. Total time also depends on the selected distance/geometry provider and network latency, so a fixed end-to-end guarantee is not possible for remote services.

## Large CSVs and performance
* `app.py` defers its pandas import until a CSV is uploaded; solver and GIS work stay lazy until route calculation is requested.
* CSV data is parsed and validated once per uploaded file in the current session. All valid rows are retained; large-file previews show 1,000 rows, and the full dataset remains available in the Data tab.
* For files above 1,000 rows, search the depot and delivery IDs to select rows without sending every option to the browser at once. Selections from separate searches are retained.
* Routing uses a dense distance matrix. If the selected locations would require more than 128 MiB for that matrix, route calculation is disabled with an explanation; the uploaded data is not truncated.
* The OpenStreetMap distance matrix runs shortest-path searches only from selected stops, rather than from every node in the downloaded road graph. Repeating an unchanged route calculation uses cached distance and optimization results.

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
