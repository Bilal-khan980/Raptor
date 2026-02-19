import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime
from pytz import timezone
import os
import socketio

from raptor_engine import load_all_data, RaptorRouter, CALIF_TZ

import asyncio
from contextlib import asynccontextmanager

# Global state for sync status
last_synced_hour = -1

# Socket.IO Setup
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

async def background_sync_task():
    global router_instance, stops_cache, shapes_cache, last_synced_hour
    
    while True:
        try:
            # Only load once (first run) — no hourly re-sync needed
            if last_synced_hour == -1:
                print("--- LOADING ALL TRIPS (NO TIME WINDOW FILTERING) ---")
                
                if os.path.exists(DATA_DIR):
                    # No start/end seconds => loads ALL trips for the current day
                    stops, routes, trips, shapes = load_all_data(DATA_DIR)
                    
                    # Replace global router
                    router_instance = RaptorRouter(stops, routes, trips, shapes)
                    stops_cache = stops
                    shapes_cache = shapes
                    last_synced_hour = 0  # Mark as loaded
                    print(f"LOAD COMPLETE. Total Stops: {len(stops)}")
                    
                    # Emit Sync Complete Event
                    await sio.emit('sync_complete', {
                        'total_stops': len(stops)
                    })
                else:
                    print("Data dir missing!")

        except Exception as e:
            print(f"ERROR IN SYNC TASK: {e}")
            import traceback
            traceback.print_exc()
        
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

# Wrap FastAPI with Socket.IO
socket_app = socketio.ASGIApp(sio, app)

DATA_DIR = r"C:\Users\bilal\Desktop\Raptor\Raptor\gtfs_data"
router_instance = None
stops_cache = {}
shapes_cache = {}

@app.get("/api/status")
async def get_status():
    now = datetime.now(CALIF_TZ)
    return {
        "server_time": now.strftime("%H:%M:%S"),
        "search_window": "21:00 - 23:00",
        "data_loaded": last_synced_hour != -1
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
async def get_route(source: str, target: str, earliest_dep: str = "21:00:00"):
    # Hardcoded search window: 9 PM to 11 PM California time
    dep_seconds = 21 * 3600  # 21:00:00 = 75600 seconds
    search_window = 2 * 3600  # 2-hour window = 7200 seconds
    
    if not router_instance:
        return JSONResponse({"error": "Router not initialized"}, status_code=503)

    # Search within the hardcoded 2-hour window (9 PM - 11 PM)
    result = router_instance.query_range(source, target, dep_seconds, window=search_window)
    
    formatted_journeys = []
    for journey in result['journeys']:
        legs = journey['legs']
        
        # --- FILTER 1: Skip if consecutive walk legs ---
        has_consecutive_walks = False
        for i in range(len(legs) - 1):
            if legs[i]['type'] != 'transit' and legs[i+1]['type'] != 'transit':
                has_consecutive_walks = True
                break
        if has_consecutive_walks:
            continue
        
        # --- FILTER 2: Skip if any single walk segment > 10 minutes ---
        has_long_walk = False
        for leg in legs:
            if leg['type'] != 'transit':
                walk_duration = leg['arrival_time'] - leg['departure_time']
                if walk_duration < 0:
                    walk_duration += 86400  # midnight wrap
                if walk_duration > 600:  # 10 minutes = 600 seconds
                    has_long_walk = True
                    break
        if has_long_walk:
            continue
        
        # --- Format the journey ---
        steps = []
        for leg in legs:
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
    # IMPORTANT: We must run 'socket_app' now
    uvicorn.run("server:socket_app", host="127.0.0.1", port=5001, reload=True)
