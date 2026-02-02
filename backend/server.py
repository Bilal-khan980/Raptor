import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime
from pytz import timezone
import os

from raptor_engine import load_all_data, RaptorRouter, CALIF_TZ

import asyncio
from contextlib import asynccontextmanager

# Global state for sync status
last_synced_hour = -1

async def background_sync_task():
    global router_instance, stops_cache, shapes_cache, last_synced_hour
    
    while True:
        now_calif = datetime.now(CALIF_TZ)
        current_hour = now_calif.hour
        
        # Check if we need to sync (new hour or first run)
        if current_hour != last_synced_hour:
            print(f"--- HOUR CHANGE DETECTED: {current_hour}:00 ---")
            print("Syncing trip data for new window...")
            
            # Window: Current-1h to Current+4h
            # Wraps around 24h naturally, but for GTFS seconds we need absolute
            # GTFS often goes 24, 25, etc. We'll simplisticly handle day boundaries
            # by covering a wide enough range or just 0-24 logic.
            # Ideally: window_start = (h-1)*3600, window_end = (h+4)*3600
            
            # Using seconds since midnight
            w_start_h = current_hour - 1
            w_end_h = current_hour + 4
            
            # Simple clamp or wrap? For simplicity, we assume day-of operations.
            # If w_start_h < 0, it technically means previous day, but we'll just clamp to 0 
            # or handle wrap if our GTFS loader supports it. 
            # Our loader assumes 0-28+ hours.
            
            # Start: max(0, (h-1)*3600)
            # End: (h+4)*3600 (can go > 86400 which is fine for GTFS)
            
            # SPECIAL CASE: if h=0 (midnight), h-1 = -1. We might want late night trips.
            # We'll just define window strictly in seconds.
            
            start_seconds = (current_hour - 1) * 3600
            end_seconds   = (current_hour + 4) * 3600
            
            # Clamp start to 0 if needed, or allow negative? Transit seconds usually positive.
            # If it's 5AM, we want 4AM trips. (14400s)
            
            print(f"Loading Window: {start_seconds}s to {end_seconds}s")
            
            if os.path.exists(DATA_DIR):
                # Reload data in a thread to not block event loop? 
                # load_all_data is CPU bound and IO bound. 
                # For simplicity in this script, we run it directly (blocking briefly).
                stops, routes, trips, shapes = load_all_data(DATA_DIR, start_seconds, end_seconds)
                
                # Replace global router
                router_instance = RaptorRouter(stops, routes, trips, shapes)
                stops_cache = stops
                shapes_cache = shapes
                last_synced_hour = current_hour
                print(f"SYNC COMPLETE. Last Synced Hour: {last_synced_hour}:00. Total Stops: {len(stops)}")
            else:
                print("Data dir missing!")

        await asyncio.sleep(60) # Check every minute

@asynccontextmanager
async def lifespan(app: FastAPI):
    global router_instance, stops_cache, shapes_cache
    print("--- RAPTOR SERVER VERSION 3.0 (Dynamic Sync) ---")
    
    # 1. Start background task
    asyncio.create_task(background_sync_task())
    
    yield

app = FastAPI(lifespan=lifespan)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = r"C:\Users\bilal\Desktop\Raptor\Raptor\gtfs_data"
router_instance = None
stops_cache = {}
shapes_cache = {}

@app.get("/api/status")
async def get_status():
    global last_synced_hour
    now = datetime.now(CALIF_TZ)
    return {
        "server_time": now.strftime("%H:%M:%S"),
        "last_synced_hour": last_synced_hour,
        "trip_window_start": f"{last_synced_hour-1}:00",
        "trip_window_end": f"{last_synced_hour+4}:00"
    }

@app.get("/api/all-stops-geojson")
async def get_all_stops():
    # Defensive check
    if not stops_cache:
        print("WARNING: stops_cache is empty during request!")
        return {"type": "FeatureCollection", "features": []}

    features = []
    for sid, stop in stops_cache.items():
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [stop.lon, stop.lat]
            },
            "properties": {
                "id": sid,
                "name": stop.name,
                "agency": stop.agency_id
            }
        })
    return {"type": "FeatureCollection", "features": features}

def format_time(seconds):
    """Formats seconds since midnight into HH:MM:SS, wrapping at 24 hours."""
    s = int(seconds)
    total_minutes = s // 60
    m = total_minutes % 60
    h = (total_minutes // 60) % 24
    s = s % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def slice_shape(shape_pts, from_lat, from_lon, to_lat, to_lon):
    if not shape_pts: return []
    
    def dist_sq(p, lat, lon):
        return (p[0] - lat)**2 + (p[1] - lon)**2

    # Find the index in shape closest to the boarding stop
    best_start_idx = 0
    min_d_start = float('inf')
    for i, pt in enumerate(shape_pts):
        d = dist_sq(pt, from_lat, from_lon)
        if d < min_d_start:
            min_d_start = d
            best_start_idx = i
            
    # Find the index in shape closest to the alighting stop (must be AFTER or AT start_idx)
    best_end_idx = best_start_idx
    min_d_end = float('inf')
    for i in range(best_start_idx, len(shape_pts)):
        d = dist_sq(shape_pts[i], to_lat, to_lon)
        if d <= min_d_end:
            min_d_end = d
            best_end_idx = i
            
    # Return the segment
    return shape_pts[best_start_idx : best_end_idx + 1]

@app.get("/api/route")
async def get_route(source: str, target: str, earliest_dep: str):
    # earliest_dep is HH:MM:SS or HH:MM
    parts = earliest_dep.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    dep_seconds = h * 3600 + m * 60 + s
    
    if not router_instance:
        return JSONResponse({"error": "Router not initialized"}, status_code=503)

    # Use query_range to find all rides within a 12-hour (43200s) window
    result = router_instance.query_range(source, target, dep_seconds, window=43200)
    
    formatted_journeys = []
    for journey in result['journeys']:
        steps = []
        for leg in journey['legs']:
            from_stop = stops_cache[leg['from_stop_id']]
            to_stop = stops_cache[leg['to_stop_id']]
            
            step = {
                "FromStop": from_stop.name,
                "FromStopId": leg['from_stop_id'],
                "FromStopCoords": {"lat": from_stop.lat, "lon": from_stop.lon},
                "ToStop": to_stop.name,
                "ToStopId": leg['to_stop_id'],
                "ToStopCoords": {"lat": to_stop.lat, "lon": to_stop.lon},
                "DepartureTime": format_time(leg['departure_time']),
                "ArrivalTime": format_time(leg['arrival_time']),
            }
            
            if leg['type'] == 'transit':
                step["RouteId"] = leg['route_name']
                step["RouteLongId"] = leg['route_id']
                
                # Shape Slicing
                if leg['shape_id'] and leg['shape_id'] in shapes_cache:
                    full_shape = shapes_cache[leg['shape_id']]
                    step["Shape"] = slice_shape(full_shape, from_stop.lat, from_stop.lon, to_stop.lat, to_stop.lon)
                else:
                    # Basic shape if no shape_id
                    step["Shape"] = [[from_stop.lat, from_stop.lon], [to_stop.lat, to_stop.lon]]
            else:
                step["RouteId"] = None
                step["Shape"] = [[from_stop.lat, from_stop.lon], [to_stop.lat, to_stop.lon]]
            
            steps.append(step)
        formatted_journeys.append(steps)
        
    return formatted_journeys

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    # Enabling 'reload=True' provides the 'watchdog' functionality 
    # that restarts the server automatically when code changes.
    uvicorn.run("server:app", host="127.0.0.1", port=5001, reload=True)
