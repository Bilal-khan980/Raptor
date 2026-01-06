# 🦅 RAPTOR: San Francisco Bay Area Transit Router

Experience the power of the **Round-Based Public Transit Routing (RAPTOR)** algorithm, optimized for the San Francisco Bay Area. This project provides a full-stack solution for multi-modal transit planning, featuring iterative range-based searching and a premium, modern user interface.

![RAPTOR Banner](https://img.shields.io/badge/Algorithm-RAPTOR-blue.svg?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/Backend-FastAPI-green.svg?style=for-the-badge)
![React](https://img.shields.io/badge/Frontend-React-blue.svg?style=for-the-badge)
![Mapbox](https://img.shields.io/badge/Map-Mapbox-black.svg?style=for-the-badge)

---

## 🚀 Key Features

*   **⚡ High-Performance Routing**: Implementation of the RAPTOR algorithm, which avoids slow graph-traversal overhead by processing transit data in rounds.
*   **⏱️ Iterative Range Search**: Unlike traditional routers that find one result, our engine looks ahead locally. It scans a **60-minute window** from the current California time to find all possible ride combinations.
*   **🌉 Multi-Agency Support**: Seamlessly combines schedules from **BART, SamTrans, and SFMTA (Muni)**.
*   **📍 Accurate Map Visualizations**: Uses GTFS `shapes.txt` data to render exact bus/train paths on a high-fidelity Mapbox layer.
*   **🌓 Premium UX/UI**: A sleek, dark-mode interface featuring glassmorphism, real-time California time synchronization, and a node-based professional itinerary.

---

## 🛠️ How it Works: The Architecture

### 1. The Core Engine (`raptor_engine.py`)
The engine uses the **RAPTOR algorithm**, which is fundamentally different from Dijkstra or A*. Instead of edges and nodes, it operates on **Routes and Trips**:
- **Round-based Scanning**: Each "round" represents a transfer. Round 1 is direct travel, Round 2 is travel with 1 transfer, etc.
- **Pruning**: It uses local and target pruning to ensure it only calculates journeys that arrive earlier than anything previously found.
- **Range Query**: We implemented a `query_range` wrapper that identifies every unique starting opportunity in a 1-hour window and runs the engine iteratively to provide user choice.

### 2. The API Layer (`server.py`)
A **FastAPI** backend that:
- **Time Sync**: Handles the conversion of local browser time to **America/Los_Angeles** time to match transit schedules.
- **Shape Slicing**: Slices complex transit polylines so the map only shows the portion of the route between *your* specific boarding and alighting stops.
- **Sanitization**: Formats internal "transit seconds" (which can exceed 24:00 in GTFS) into a clean, 24-hour HH:MM:SS format.

### 3. The Frontend (`App.jsx`)
A **React + Mapbox GL JS** application:
- **Interactive Map**: Users select source/target stops by clicking markers.
- **Live Dashboard**: A real-time header showing current California time and the active search window.
- **Itinerary Timeline**: A sophisticated, node-based visual component that connects your journey steps with transit-specific icons (Bus/Train/Walk).

---

## 🚦 Getting Started

### Prerequisites
- Python 3.9+
- Node.js & npm
- A [Mapbox Access Token](https://mapbox.com/)

### Backend Setup
1.  **Download GTFS Data**:
    ```powershell
    cd backend
    python data_downloader.py
    ```
2.  **Run the Server**:
    ```powershell
    python server.py
    ```
    *The server will initialize the RAPTOR engine and load pre-processed GTFS data.*

### Frontend Setup
1.  **Install Dependencies**:
    ```bash
    cd Frontend
    npm install
    ```
2.  **Launch the App**:
    ```bash
    npm run dev
    ```

---

## 📖 Deep Dive: The Range Search Logic

When you click **"Find Route"**, RAPTOR doesn't just look for "now". 
1. It calculates the current time in California.
2. It identifies every trip departing from your source stop (or stops reachable within a 15-minute walk) for the next **60 minutes**.
3. It runs a full routing query for each unique departure time.
4. It filters the results to ensure **Strict Windowing**: If a suggested journey starts even one minute after your 1-hour window, it is censored to keep your options relevant.
5. It de-duplicates trips that result in the same arrival time, presenting only the most efficient path for each starting slot.

---

## 🎨 Design Aesthetics
This project follows a **Premium Aesthetic** philosophy:
- **Typography**: Uses `JetBrains Mono` for time and `Inter` for UI elements.
- **Components**: Framer Motion for smooth drawer transitions and node-link animations.
- **Colors**: A professional palette of `#4facfe` (Transit Blue) and dark slate textures.

---

## 🛠️ Tech Stack
- **Backend**: Python, FastAPI, Pytz (Timezone handling)
- **Algorithm**: RAPTOR (Round-Based Public Transit Routing)
- **Frontend**: Vite, React, Mapbox GL, Framer Motion, Axios, CSS Modules (Vanilla)

Developed with ❤️ for the SF Bay Area.
