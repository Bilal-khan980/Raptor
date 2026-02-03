# 🦅 RAPTOR: Ultra-Optimized Transit Engine
### San Francisco Bay Area Transit Router | Version 3.0

> **"Very, very, very fast."**

This project is a high-performance implementation of the **Round-Based Public Transit Routing (RAPTOR)** algorithm, engineered for extreme speed and accuracy in the San Francisco Bay Area. It features **Parallel Multiprocessing**, **A* Heuristic Pruning**, and **Lazy Initialization** to deliver lightning-fast routing results across complex multi-agency networks (BART, Muni, SamTrans).

## 🚀 Key Features

*   **⚡ CPU-Bound Multiprocessing**: Unlike standard Python servers, this engine bypasses the GIL by spawning parallel worker processes. It scans 100+ trip scenarios simultaneously across all available CPU cores.
*   **🧠 A* Heuristic Pruning**: The engine intelligently "prunes" (discards) routes that are mathematically impossible to improve upon, using real-time Haversine distance calculations to the target.
*   **⏱️ Strict-Window Optimization**: Data loading is optimized to only keep trips relevant to the rush-hour window (05:00 - 09:00), drastically reducing memory footprint and search space.
*   **🧪 Hardcoded Verification Time**: The system operates on a fixed test vector of **05:15 AM**, ensuring consistent, reproducible debugging and performance testing.
*   **📂 Lazy Initialization**: Uses O(1) memory structures (`defaultdict`) to eliminate the initialization overhead of millions of array cells per query.

---

## 🛠️ The Architecture (Logic & Flow)

### 1. Data Ingestion & Optimization (`raptor_engine.py`)
When the server starts:
1.  **GTFS Loading**: It reads raw GTFS data (stops, routes, stop_times).
2.  **Window Filtering**: It **discards** any trip that does not start between **05:00 and 09:00**. This reduces the dataset size by ~70%, making lookups faster.
3.  **Global State Sharing**: Instead of passing massive data objects to workers (slow), it initializes global read-only memory structures (`G_STOPS`, `G_ROUTES`) once per worker process.

### 2. The Core Engine Logic
The search process is split into two phases:

#### Phase A: Discovery
-   The user selects a Source and Target.
-   The engine identifies **100 valid departure times** (sampling) within a 12-hour window starting from 05:15.

#### Phase B: Parallel Execution
-   These 100 queries are distributed to a **ProcessPoolExecutor**.
-   **Worker Logic**: Each CPU core runs an independent RAPTOR instance:
    -   **Round 1**: Scan direct routes.
    -   **Round 2+**: Scan transfers.
    -   **Pruning**: At every stop, calculate: `Current Time + Min Physical Travel Time to Target`. If this exceeds the best known arrival time, **STOP** searching this branch.
-   **Result Aggregation**: The main process collects valid journeys from all workers and sorts them:
    1.  By **Earliest Departure**.
    2.  By **Shortest Duration** (tie-breaker).

### 3. The Frontend (`App.jsx`)
-   **Vite + React**: Modern, fast build tooling.
-   **Mapbox GL**: Hardware-accelerated map rendering.
-   **Environment Security**: Uses `.env` files to protect API keys.

---

## 🚦 Getting Started (Step-by-Step)

### Prerequisites
-   **Python 3.9+**
-   **Node.js 18+**
-   [Mapbox Access Token](https://mapbox.com/) (Required for map rendering)

### 1. Clone the Repository
```bash
git clone <repository-url>
cd Raptor
```

### 2. Backend Setup
The backend runs the high-performance RAPTOR engine.

1.  **Navigate to backend**:
    ```powershell
    cd backend
    ```
2.  **Install Python Dependencies**:
    ```powershell
    pip install fastapi uvicorn pytz
    ```
3.  **Download/Verify Data**:
    Ensure the `gtfs_data` folder exists. If not, run the downloader:
    ```powershell
    python data_downloader.py
    ```
4.  **Start the Server**:
    ```powershell
    python server.py
    ```
    *You will see logs indicating "Initializing Process Pool" and "Trips filtered". Wait for "Uvicorn running on..."*

### 3. Frontend Setup
The frontend displays the map and results.

1.  **Navigate to Frontend**:
    ```bash
    cd ../Frontend
    ```
2.  **Install Node Modules**:
    ```bash
    npm install
    ```
3.  **Configure Environment Variables**:
    *   Create a file named `.env` in the `Frontend` directory.
    *   Add your Mapbox token (or copy the example):
    ```env
    VITE_MAPBOX_TOKEN=pk.your_mapbox_token_here
    ```
    *   *Note: Using a real token is required for the map to load.*

4.  **Run the Development Server**:
    ```bash
    npm run dev
    ```
5.  **Open in Browser**:
    Go to `http://localhost:5173` (or the URL shown in terminal).

---

## 🧪 How to Verify
1.  **Launch**: Ensure both Backend and Frontend terminals are running.
2.  **Check Time**: The sidebar should display **05:15:00** (Hardcoded Test Time) and **Trip Window: 05:00 - 09:00**.
3.  **Search**: Click two points on the map (Source & Target).
4.  **Observe**:
    -   The route should appear almost instantly (thanks to Multiprocessing).
    -   The results list is sorted: Earliest departures are top, ties broken by shortest duration.
    -   Console logs in the backend will show worker activity.

---

## 📂 Project Structure
```
Raptor/
├── backend/
│   ├── raptor_engine.py    # The optimized Logic Core (Multiprocessing & A*)
│   ├── server.py           # FastAPI Gateway
│   └── data_downloader.py  # GTFS Fetcher
├── Frontend/
│   ├── src/App.jsx         # Main UI Logic
│   ├── .env                # API Keys (You create this)
│   └── package.json        # Dependencies
└── README.md               # You are here
```

---
*Built for extreme performance.*

## How it works?

# Raptor System: Zero to Hero detailed Guide

This document is the **definitive guide** to the Raptor Public Transit Routing system. It begins with a high-level architectural overview ("The Bird's Eye View") and descends into the deepest level of code implementation ("The Engine Room").

---

# PART I: The Big Picture (Architecture)

## 1. System Components
The system consists of three main parts working in harmony:

1.  **Frontend (React/Vite)**: 
    -   **Role**: The User Interface.
    -   **Tech**: React, Mapbox GL JS, Socket.IO Client.
    -   **Responsibility**: Displays the map, sends routing requests, and visualizes the results (polylines on the map).
2.  **Backend (FastAPI/Python)**: 
    -   **Role**: The Brain.
    -   **Tech**: FastAPI, Uvicorn, Python-SocketIO, Multiprocessing.
    -   **Responsibility**: Loads GTFS data, manages synchronization, and runs the CPU-intensive routing algorithm.
3.  **Transit Data (GTFS)**: 
    -   **Role**: The Knowledge.
    -   **Format**: Static Text Files (`stops.txt`, `routes.txt`, `trips.txt`) that define the bus/train network.

## 2. How Data Flows (High-Level)
1.  **Server Start**: Backend loads GTFS data => Builds optimized memory structures.
2.  **User Request**: Frontend sends `source`, `target`, and `time` to `/api/route`.
3.  **Search**: Backend runs the RAPTOR algorithm (simulating walking and riding buses).
4.  **Response**: Best journeys are returned to Frontend.
5.  **Sync**: A background task reloads data every hour to ensure validity.

---

# PART II: Backend & Data Loading (The Setup)

Before we can route, we must load the map. This happens in `server.py` and `raptor_engine.py`.

## 1. Startup Sequence
When `python server.py` runs:
1.  **Lifespan Event**: The `lifespan` function triggers.
2.  **Background Task**: `background_sync_task()` starts running in a separate loop.
3.  **Server Listen**: The API starts listening on port 5001.

## 2. The Sync Logic (`background_sync_task`)
This task ensures the server always has the correct data loaded for the *current* time of day.
-   **Why?** Loading ALL trips for a massive network consumes too much RAM.
-   **How?** It calculates a "Window": `Current Hour - 1` to `Current Hour + 4`.
-   **Trigger**: Every hour (e.g., 12:00, 13:00), it calls `load_all_data`.
-   **Outcome**: It replaces the global `router_instance` with a fresh one containing only relevant trips.
-   **Notification**: It emits a `sync_complete` socket event so the Frontend knows to refresh.

---

# PART III: Data Structures (The "Brains")

This is **Crucial**. The algorithm is fast because the data is organized specifically for it. We do NOT use a standard graph (Nodes/Edges). We use Arrays and Hash Maps.

### 1. `G_STOPS` (Stops Lookup)
-   **What**: Dictionary of all stops.
-   **Structure**: `{ "StopID": TransitStopObject }`
-   **Key Attribute**: `stop.footpaths`.
    -   Instead of calculating "Distance to nearby stops" during the search, we pre-calculate it.
    -   If Stop A is close to Stop B, `StopA.footpaths` includes `(StopB, 300_seconds)`.
    -   This allows the algorithm to "instantly" walk between stops without math.

### 2. `G_ROUTES` (Route Patterns)
-   **Concept**: In RAPTOR, a "Route" is a specific sequence of stops. 
    -   *Real World*: "Bus 42" might have two versions. Version A stops at [1, 2, 3]. Version B stops at [1, 3] (Express).
    -   *Raptor World*: These are **Two Different Routes**. Route 42_A and Route 42_B.
-   **Structure**: `{ "RouteID": TransitRouteObject }`
    -   `route.stops`: List of Stop IDs `[A, B, C, D]`.
    -   `route.trips`: List of Trip IDs that follow this *exact* pattern.

### 3. `G_TRIPS` (The Schedule)
-   **What**: Specific instances of a route (e.g., the 9:00 AM bus vs the 9:15 AM bus).
-   **Structure**: `{ "TripID": TransitTripObject }`
    -   `trip.departure_times`: Array of integers (seconds since midnight).
    -   `trip.arrival_times`: Array of integers.
-   **Usage**: Once we board a trip, we just look at these arrays to see when we arrive.

### 4. Acceleration Indices (The Speed Boosters)
These lists allow the algorithm to make O(1) decision lookups.

-   **`G_STOP_TO_ROUTES`**: "I am at Stop A. What buses stop here?"
    -   `{ "StopA": ["Route1", "Route2"] }`
-   **`G_ROUTE_STOP_INDEX`**: "I am on Route 1. What index number is Stop A?"
    -   `{ "Route1": { "StopA": 0, "StopB": 1 } }`.
    -   This tells us if Stop B comes *after* Stop A (1 > 0).
-   **`G_ROUTE_STOP_TIMES_CACHE`**: A sorted matrix of all departure times.
    -   Allows us to use **Binary Search** to instantly find the next bus leaving after 9:00 AM.

---

# PART IV: The Main Algorithm (Code Deep Dive)

The function `run_raptor_worker` is the core engine. It finds the fastests path from Source to Target.

## The Logic: "Rounds"
RAPTOR works in rounds.
-   **Round 0**: Start at Source.
-   **Round 1**: Where can I get with **1 Bus Ride**?
-   **Round 2**: Where can I get with **1 Bus Ride + 1 Transfer + 1 Bus Ride**?
-   ... up to K rounds.

## Step-by-Step Code Explanation

### 1. Initialization
We prepare "Infinity Arrays". At the start, we haven't reached anywhere except the Source.

```python
def run_raptor_worker(source_stop_id, target_stop_id, departure_time_seconds):
    # arrival_times[k][stop] = Best time to reach 'stop' using exactly 'k' trips.
    arrival_times = [defaultdict(lambda: float('inf')) for _ in range(max_rounds + 1)]
    
    # best_arrival[stop] = Absolute best time ever seen for this stop (used for pruning).
    best_arrival = defaultdict(lambda: float('inf'))
    
    # We start at Source at departure_time
    arrival_times[0][source_stop_id] = departure_time_seconds
    best_arrival[source_stop_id] = departure_time_seconds
    
    # 'marked_stops' are the stops we just arrived at. 
    # Only these stops can be starting points for the next round.
    marked_stops = {source_stop_id}
```

### 2. The Loop (Iterating Rounds)
We loop from Round 1 to `max_rounds` (e.g., 5).

```python
    for k in range(1, max_rounds + 1):
        routes_to_scan = {}
        
        # --- PHASE 1: Identify Routes ---
        # Look at every stop we reached in the previous round.
        for stop_id in marked_stops:
            
            # OPTIMIZATION (A* Pruning):
            # If (Current Time + Min Walk Time to Target) > Best Time Already Found at Target
            # Then it is IMPOSSIBLE for this path to be better. Stop exploring it.
            if arrival_times[k-1][stop_id] + min_time_to_target(stop_id) > best_arrival[target_stop_id]:
                continue 
            
            # Find all routes passing through this stop
            for route_id in G_STOP_TO_ROUTES[stop_id]:
                # We want to scan this route starting from this stop's index
                # If we already marked this route, keep the EARLIEST index (scanning more is safer)
                idx = G_ROUTE_STOP_INDEX[route_id][stop_id]
                routes_to_scan[route_id] = min(routes_to_scan.get(route_id, inf), idx)
        
        marked_stops = set() # Reset for this round
```

### 3. Scanning Routes (The Core "Train" Logic)
This is where we simulate riding the vehicle.

```python
        # --- PHASE 2: Ride the Routes ---
        for route_id, start_index in routes_to_scan.items():
            route = G_ROUTES[route_id]
            current_trip = None  # Are we on a bus?
            
            # Iterate down the stops of the route
            for i in range(start_index, len(route.stops)):
                stop_id = route.stops[i]
                
                # ACTION: GET OFF THE BUS
                # If we are currently on a trip, update the arrival time at this stop
                if current_trip:
                    arr_time = current_trip.arrival_times[i]
                    
                    # Did we beat the record?
                    if arr_time < min(best_arrival[stop_id], best_arrival[target_stop_id]):
                        arrival_times[k][stop_id] = arr_time
                        best_arrival[stop_id] = arr_time
                        marked_stops.add(stop_id) # We reached a new place! explore from here next round.
                        record_parent_pointer(stop_id, current_trip) # Remember how we got here
                
                # ACTION: GET ON THE BUS
                # Can we catch a trip at this stop?
                # We can board if we arrived here in the PREVIOUS round (k-1)
                prev_arrival = arrival_times[k-1][stop_id]
                if prev_arrival < infinity:
                    # Find the earliest trip departing after (prev_arrival + buffer)
                    # We utilize the Sorted Cache + Binary Search for O(log N) speed
                    trip = find_earliest_trip(route_id, index=i, min_time=prev_arrival + buffer)
                    
                    # If found, and it's better than our current trip, BOARD IT.
                    if trip and (current_trip is None or trip.dep < current_trip.dep):
                        current_trip = trip
```

### 4. Walking (Transfers)
After riding buses, we check if we can walk to nearby stops.

```python
        # --- PHASE 3: Walk Transfers ---
        for stop_id in list(marked_stops):
            arrival = arrival_times[k][stop_id]
            
            # Check pre-calculated footpaths
            for neighbor_id, walk_sec in G_STOPS[stop_id].footpaths:
                new_time = arrival + walk_sec
                
                # If walking gets us there faster than previous attempts...
                if new_time < best_arrival[neighbor_id]:
                     arrival_times[k][neighbor_id] = new_time
                     best_arrival[neighbor_id] = new_time
                     marked_stops.add(neighbor_id) # Explore from here next round (e.g. catch a bus)
                     record_walk_pointer(neighbor_id, from=stop_id)
```

### 5. Termination
If `marked_stops` is empty (we didn't identify any new reachable stops), we stop.

---

# PART V: Life of a Request (Summary)

1.  **User** clicks "Search" on Frontend.
2.  **Request**: `GET /api/route?source=StationA&target=StationB&time=09:00`.
3.  **Backend**:
    -   Calls `query_range` in `RaptorRouter`.
    -   Determines potential departure times (e.g., 09:00, 09:15, 09:30).
    -   **Parallelism**: Spawns multiple `run_raptor_worker` processes.
        -   Worker 1 solves for 09:00 start.
        -   Worker 2 solves for 09:15 start.
    -   Collects all results.
    -   Sorts by Arrival Time and Duration.
4.  **Result**: Returns a JSON list of legs (Bus A -> Walk -> Bus B).
5.  **Frontend**: Parses JSON and renders the blue lines on the Mapbox map.

This completes the detailed explanation of the Raptor system. You now know the Architecture, the Data structure design, and the line-by-line Algorithm logic.
