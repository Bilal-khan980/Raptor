# 🦅 RAPTOR: Ultra-Optimized Transit Engine
### San Francisco Bay Area Transit Router | Version 3.0

> **"Very, very, very fast."**

This project is a high-performance implementation of the **Round-Based Public Transit Routing (RAPTOR)** algorithm, engineered for extreme speed and accuracy in the San Francisco Bay Area. It features **Parallel Multiprocessing**, **A* Heuristic Pruning**, and **Lazy Initialization** to deliver lightning-fast routing results across complex multi-agency networks (BART, Muni, SamTrans).

## 🚀 Key Features

*   **⚡ CPU-Bound Multiprocessing**: Unlike standard Python servers, this engine bypasses the GIL by spawning parallel worker processes. It scans 100+ trip scenarios simultaneously across all available CPU cores.
*   **🧠 A* Heuristic Pruning**: The engine intelligently "prunes" (discards) routes that are mathematically impossible to improve upon, using real-time Haversine distance calculations to the target.
*   **�️ strict-Window Optimization**: Data loading is optimized to only keep trips relevant to the rush-hour window (05:00 - 09:00), drastically reducing memory footprint and search space.
*   **� Hardcoded Verification Time**: The system operates on a fixed test vector of **05:15 AM**, ensuring consistent, reproducible debugging and performance testing.
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

## � Project Structure
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
