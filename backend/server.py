import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime
from pytz import timezone
import os

from raptor_engine import load_all_data, RaptorRouter, CALIF_TZ

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global router_instance, stops_cache, shapes_cache
    print("--- RAPTOR SERVER VERSION 2.0 (Midnight Fix) ---")
    print("Loading GTFS data...")
    if not os.path.exists(DATA_DIR) or not os.listdir(DATA_DIR):
        print("Data directory is empty. Please run data_downloader.py first.")
        # We'll initialize with empty if no data, but ideally it should have data
        stops_dict, routes_dict, trips_dict, shapes_dict = {}, {}, {}, {}
    else:
        stops_dict, routes_dict, trips_dict, shapes_dict = load_all_data(DATA_DIR)
    
    router_instance = RaptorRouter(stops_dict, routes_dict, trips_dict, shapes_dict)
    stops_cache = stops_dict
    shapes_cache = shapes_dict
    print("Data loaded successfuly.")
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

# Routes and middleware below

@app.get("/api/all-stops-geojson")
async def get_all_stops():
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

    # Use query_range to find all rides within a 1-hour (3600s) window
    result = router_instance.query_range(source, target, dep_seconds, window=3600)
    
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
    uvicorn.run(app, host="127.0.0.1", port=5001)
