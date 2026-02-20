import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from pytz import timezone
import os
import socketio

from raptor_engine import load_all_data, RaptorRouter, CALIF_TZ, TransitStop, TransitTrip, TransitRoute, haversine, run_raptor_worker
import math

import asyncio
from contextlib import asynccontextmanager

# Global state for sync status
last_synced_hour = -1
carpool_counter = 0
carpool_routes_cache = {}  # route_id -> {stop_ids, dep_times, arr_times, trip_id}

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

# ===================== CARPOOL ROUTE GENERATION =====================

class CarpoolRouteRequest(BaseModel):
    road_geometry: Optional[List[List[float]]] = None  # list of [lon, lat]

def find_stops_right_of_road(road_geometry, all_stops, buffer_m=400, source_id=None, target_id=None):
    """
    Given a road_geometry (list of [lon, lat] from Mapbox Directions),
    find stops that lie within buffer_m of the road AND on the RIGHT side
    of the direction of travel (California right-side traffic).
    Returns list of (stop_id, cum_dist_m) sorted by position along route.
    """
    if not road_geometry or len(road_geometry) < 2:
        return []

    # Precompute segment cumulative lengths (meters)
    seg_lengths = []
    cum = 0.0
    for i in range(len(road_geometry) - 1):
        p1_lon, p1_lat = road_geometry[i]
        p2_lon, p2_lat = road_geometry[i + 1]
        seg_len = haversine(p1_lat, p1_lon, p2_lat, p2_lon) * 1000.0  # km -> m
        seg_lengths.append((cum, seg_len))
        cum += seg_len
    total_length = cum

    results = []

    for sid, stop in all_stops.items():
        if sid == source_id or sid == target_id:
            continue

        best_perp_m = float('inf')
        best_cum_dist = 0.0
        best_is_right = False

        for i in range(len(road_geometry) - 1):
            p1_lon, p1_lat = road_geometry[i]
            p2_lon, p2_lat = road_geometry[i + 1]

            # Scale lon by cos(lat) so degrees are equal-area (critical at CA latitudes ~37N)
            cos_lat = math.cos(math.radians((p1_lat + p2_lat) / 2))
            dlon = (p2_lon - p1_lon) * cos_lat
            dlat = p2_lat - p1_lat
            seg_len_sq = dlon * dlon + dlat * dlat
            if seg_len_sq < 1e-18:
                continue

            # Project stop onto segment in scaled coordinates
            px = (stop.lon - p1_lon) * cos_lat
            py = stop.lat - p1_lat
            t = max(0.0, min(1.0, (px * dlon + py * dlat) / seg_len_sq))

            # Closest point on segment (in real lon/lat)
            proj_lon = p1_lon + t * (p2_lon - p1_lon)
            proj_lat = p1_lat + t * (p2_lat - p1_lat)

            # Actual perpendicular distance (meters)
            perp_m = haversine(stop.lat, stop.lon, proj_lat, proj_lon) * 1000.0

            if perp_m < best_perp_m:
                best_perp_m = perp_m
                seg_cum, seg_len_m = seg_lengths[i]
                best_cum_dist = seg_cum + t * seg_len_m

                # Right-side check: cross product in scaled coordinates
                # d x p_scaled: positive dlon * py - dlat * px_scaled
                # Negative cross product means stop is on RIGHT side of direction of travel
                cross = dlon * py - dlat * px
                best_is_right = (cross < 0)

        if best_perp_m <= buffer_m and best_is_right:
            results.append((sid, best_cum_dist))

    results.sort(key=lambda x: x[1])
    return results


def validate_journeys(formatted_journeys):
    """
    Remove logically invalid journeys:
    1. Walk from a carpool alighting stop to re-board the SAME carpool route  (walk -> same_carpool)
    2. Carpool then walk then board same carpool again (carpool -> walk -> same_carpool)
    3. Carpool leg immediately followed by the same carpool leg (redundant split)
    4. Any journey with a zero-duration step that isn't a direct boarding point
    """
    valid = []
    for journey in formatted_journeys:
        skip = False

        for i in range(len(journey) - 1):
            curr = journey[i]
            nxt  = journey[i + 1]

            # Rule 1 & 2: walk → carpool where the walk START is on the same carpool route
            # and the walk adds no value (you could have just stayed on the carpool)
            curr_is_walk   = not curr.get('RouteId')
            nxt_is_carpool = (nxt.get('RouteId') or '').startswith('Carpool')

            if curr_is_walk and nxt_is_carpool:
                # Check if any EARLIER leg in the journey used the same carpool
                same_earlier = any(
                    step.get('RouteLongId') == nxt.get('RouteLongId')
                    for step in journey[:i]
                )
                if same_earlier:
                    skip = True
                    break

            # Rule 3: same carpool id appears twice consecutively
            curr_is_carpool = (curr.get('RouteId') or '').startswith('Carpool')
            if curr_is_carpool and nxt_is_carpool:
                if curr.get('RouteLongId') == nxt.get('RouteLongId'):
                    skip = True
                    break

        if not skip:
            valid.append(journey)
    return valid


@app.post("/api/carpool-route")
async def create_carpool_route(
    body: CarpoolRouteRequest,
    source: str = Query(...),
    target: str = Query(...)
):
    global router_instance, stops_cache, shapes_cache, carpool_counter

    if not router_instance:
        return JSONResponse({"error": "Router not initialized yet"}, status_code=503)
    if source not in stops_cache or target not in stops_cache:
        return JSONResponse({"error": "Invalid source or target stop ID"}, status_code=400)

    src_stop = stops_cache[source]
    tgt_stop = stops_cache[target]

    road_geometry = body.road_geometry  # [[lon, lat], ...] from Mapbox Directions

    # 1. Find stops on the right side of the road geometry
    if road_geometry and len(road_geometry) >= 2:
        intermediates = find_stops_right_of_road(
            road_geometry, stops_cache,
            buffer_m=400,
            source_id=source, target_id=target
        )
    else:
        # Fallback: no geometry provided, no intermediate stops
        intermediates = []

    # 2. Build stop sequence: source → right-side intermediates → target
    stop_ids = [source] + [sid for sid, _ in intermediates] + [target]

    print(f"\n--- CARPOOL ROUTE (ROAD-FOLLOWING) ---")
    print(f"Source: {src_stop.name} | Target: {tgt_stop.name}")
    print(f"Road-side stops picked: {len(intermediates)}")
    for sid, dist_m in intermediates:
        print(f"  [{dist_m:.0f}m] {stops_cache[sid].name} ({sid})")

    # 3. Create synthetic route & trip
    carpool_counter += 1
    route_id = f"CARPOOL:cp{carpool_counter}:p0"
    trip_id = f"CARPOOL:cp{carpool_counter}:t1"
    
    # Estimate times: start at 21:00:00 (75600s), travel at ~30 km/h
    CARPOOL_START_TIME = 75600  # 21:00:00 in seconds
    SPEED_KMH = 30.0
    
    departure_times = []
    arrival_times = []
    current_time = CARPOOL_START_TIME
    
    for i, sid in enumerate(stop_ids):
        if i == 0:
            departure_times.append(current_time)
            arrival_times.append(current_time)
        else:
            prev_stop = stops_cache[stop_ids[i - 1]]
            curr_stop = stops_cache[sid]
            dist_km = haversine(prev_stop.lat, prev_stop.lon, curr_stop.lat, curr_stop.lon)
            travel_seconds = int((dist_km / SPEED_KMH) * 3600)
            travel_seconds = max(travel_seconds, 30)  # At least 30 seconds between stops
            current_time += travel_seconds
            arrival_times.append(current_time)
            departure_times.append(current_time)  # No dwell — carpooler slows but doesn't fully stop
    
    # Fix last stop: departure = arrival (terminus)
    departure_times[-1] = arrival_times[-1]
    
    # Create the TransitTrip
    trip = TransitTrip(trip_id, route_id, "carpool")
    trip.stop_sequence = stop_ids
    trip.departure_times = departure_times
    trip.arrival_times = arrival_times
    trip.shape_id = None
    
    # Create the TransitRoute
    route = TransitRoute(route_id, "CARPOOL", f"Carpool #{carpool_counter}")
    route.stops = stop_ids
    route.trips = [trip_id]
    
    print(f"Route ID: {route_id}")
    print(f"Trip ID: {trip_id}")
    print(f"Total stops: {len(stop_ids)}")
    print(f"Departure: {format_time(CARPOOL_START_TIME)} → Arrival: {format_time(arrival_times[-1])}")
    
    # 4. Inject into existing data and rebuild router
    router_instance.trips[trip_id] = trip
    router_instance.routes[route_id] = route
    
    # Rebuild acceleration structures (this is the critical step)
    print("Rebuilding router with carpool route...")
    router_instance = RaptorRouter(
        router_instance.stops,
        router_instance.routes,
        router_instance.trips,
        router_instance.shapes
    )
    print("Router rebuild complete.")
    
    # 5b. Save to carpool cache for explicit route injection in search
    carpool_routes_cache[route_id] = {
        'stop_ids': stop_ids,
        'departure_times': departure_times,
        'arrival_times': arrival_times,
        'trip_id': trip_id,
        'route_name': f"Carpool #{carpool_counter}"
    }

    # 5c. Return carpool route info with geometry for frontend map preview
    stop_details = []
    stop_markers = []  # For frontend to pin carpool stops
    for i, sid in enumerate(stop_ids):
        s = stops_cache[sid]
        stop_details.append({
            "stop_id": sid,
            "name": s.name,
            "lat": s.lat,
            "lon": s.lon,
            "departure": format_time(departure_times[i]),
            "arrival": format_time(arrival_times[i])
        })
        stop_markers.append({"lat": s.lat, "lon": s.lon, "name": s.name, "stop_id": sid})

    return {
        "route_id": route_id,
        "trip_id": trip_id,
        "route_name": f"Carpool #{carpool_counter}",
        "total_stops": len(stop_ids),
        "stops": stop_details,
        "stop_markers": stop_markers,
        "road_geometry": road_geometry or [],  # Echo back to frontend for immediate map draw
        "start_time": format_time(CARPOOL_START_TIME),
        "end_time": format_time(arrival_times[-1])
    }

# ===================== END CARPOOL =====================

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

def format_transit_legs(legs, stops_cache, shapes_cache):
    """Convert raw RAPTOR leg dicts into frontend-ready step dicts."""
    steps = []
    for leg in legs:
        from_stop = stops_cache.get(leg['from_stop_id'])
        to_stop = stops_cache.get(leg['to_stop_id'])
        if not from_stop or not to_stop:
            continue
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
            step["RouteId"] = leg.get('route_name', '')
            step["RouteLongId"] = leg.get('route_id', '')
            shape_id = leg.get('shape_id')
            if shape_id and shape_id in shapes_cache:
                step["Shape"] = slice_shape(
                    shapes_cache[shape_id],
                    from_stop.lat, from_stop.lon,
                    to_stop.lat, to_stop.lon
                )
            else:
                step["Shape"] = [[from_stop.lat, from_stop.lon], [to_stop.lat, to_stop.lon]]
        else:
            step["RouteId"] = None
            step["Shape"] = [[from_stop.lat, from_stop.lon], [to_stop.lat, to_stop.lon]]
        steps.append(step)
    return steps

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
    
    # ====== EXPLICIT CARPOOL INJECTION ======
    # Regardless of what RAPTOR found, add any carpool routes that cover source->target
    # This bypasses the Pareto filter so carpool always appears as an option.
    for cp_rid, cp in carpool_routes_cache.items():
        stop_ids = cp['stop_ids']
        if source not in stop_ids or target not in stop_ids:
            continue
        src_idx = stop_ids.index(source)
        tgt_idx = stop_ids.index(target)
        if src_idx >= tgt_idx:
            continue  # carpool doesn't go source→target in forward direction
        
        dep_t = cp['departure_times'][src_idx]
        arr_t = cp['arrival_times'][tgt_idx]
        
        # Only include if departure is within the hardcoded search window (21:00-23:00)
        if not (75600 <= dep_t <= 82800):
            continue
        
        from_stop = stops_cache.get(source)
        to_stop = stops_cache.get(target)
        if not from_stop or not to_stop:
            continue
        
        carpool_step = {
            "FromStop": from_stop.name,
            "FromStopId": source,
            "FromStopCoords": {"lat": from_stop.lat, "lon": from_stop.lon},
            "ToStop": to_stop.name,
            "ToStopId": target,
            "ToStopCoords": {"lat": to_stop.lat, "lon": to_stop.lon},
            "DepartureTime": format_time(dep_t),
            "ArrivalTime": format_time(arr_t),
            "RouteId": cp['route_name'],
            "RouteLongId": cp_rid,
            "Shape": [[from_stop.lat, from_stop.lon], [to_stop.lat, to_stop.lon]],
        }
        formatted_journeys.append([carpool_step])
        print(f"[CARPOOL] Direct: {cp['route_name']} ({source} -> {target})")
    # ====== END DIRECT CARPOOL INJECTION ======

    # ====== MULTI-MODAL CARPOOL INJECTION ======
    # Build: [carpool leg] + [transit legs]  or  [transit legs] + [carpool leg]
    # Uses parallel RAPTOR sub-queries for the transit parts.
    MAX_MIDPOINTS = 8  # Max intermediate stops to probe per carpool route

    if router_instance:
        for cp_rid, cp in carpool_routes_cache.items():
            cp_stops = cp['stop_ids']
            cp_deps  = cp['departure_times']
            cp_arrs  = cp['arrival_times']
            cp_name  = cp['route_name']

            # ---- CASE A: carpool FIRST → transit to target ----
            # Source must be on this carpool route
            if source in cp_stops:
                src_pos = cp_stops.index(source)
                # All possible alighting stops AFTER source on carpool (skip if == target, handled above)
                alighting = [(cp_stops[j], j) for j in range(src_pos + 1, len(cp_stops)) if cp_stops[j] != target]
                # Subsample if too many
                if len(alighting) > MAX_MIDPOINTS:
                    step = max(1, len(alighting) // MAX_MIDPOINTS)
                    alighting = alighting[::step][:MAX_MIDPOINTS]

                # Submit parallel sub-RAPTOR: from each alighting stop → target
                futures_a = []
                for sj, sj_idx in alighting:
                    transfer_dep = int(cp_arrs[sj_idx]) + 120  # 2-min buffer
                    f = router_instance.executor.submit(run_raptor_worker, sj, target, transfer_dep)
                    futures_a.append((sj, sj_idx, f))

                for sj, sj_idx, f in futures_a:
                    sub = f.result()
                    for sub_j in sub['journeys']:
                        if not sub_j['legs']:
                            continue
                        # Carpool step: source → sj
                        fs = stops_cache.get(source)
                        ts = stops_cache.get(sj)
                        if not fs or not ts:
                            continue
                        carpool_step = {
                            "FromStop": fs.name, "FromStopId": source,
                            "FromStopCoords": {"lat": fs.lat, "lon": fs.lon},
                            "ToStop": ts.name, "ToStopId": sj,
                            "ToStopCoords": {"lat": ts.lat, "lon": ts.lon},
                            "DepartureTime": format_time(cp_deps[src_pos]),
                            "ArrivalTime": format_time(cp_arrs[sj_idx]),
                            "RouteId": cp_name, "RouteLongId": cp_rid,
                            "Shape": [[fs.lat, fs.lon], [ts.lat, ts.lon]],
                        }
                        transit_steps = format_transit_legs(sub_j['legs'], stops_cache, shapes_cache)
                        if transit_steps:
                            formatted_journeys.append([carpool_step] + transit_steps)
                            print(f"[CARPOOL] Multimodal A: {cp_name} -> transit, via {sj}")

            # ---- CASE B: transit FIRST → carpool to target ----
            # Target must be on this carpool route
            if target in cp_stops:
                tgt_pos = cp_stops.index(target)
                # All possible boarding stops BEFORE target on carpool (skip if == source, handled above)
                boarding = [(cp_stops[i], i) for i in range(tgt_pos) if cp_stops[i] != source]
                if len(boarding) > MAX_MIDPOINTS:
                    step = max(1, len(boarding) // MAX_MIDPOINTS)
                    boarding = boarding[::step][:MAX_MIDPOINTS]

                # Submit parallel sub-RAPTOR: source → each boarding stop
                futures_b = []
                for si, si_idx in boarding:
                    f = router_instance.executor.submit(run_raptor_worker, source, si, dep_seconds)
                    futures_b.append((si, si_idx, f))

                for si, si_idx, f in futures_b:
                    sub = f.result()
                    for sub_j in sub['journeys']:
                        if not sub_j['legs']:
                            continue
                        # Must arrive at si before carpool departs there
                        last_arr = sub_j['legs'][-1]['arrival_time']
                        if last_arr + 120 > cp_deps[si_idx]:
                            continue  # Missed the carpool
                        # Carpool step: si → target
                        fs = stops_cache.get(si)
                        ts = stops_cache.get(target)
                        if not fs or not ts:
                            continue
                        carpool_step = {
                            "FromStop": fs.name, "FromStopId": si,
                            "FromStopCoords": {"lat": fs.lat, "lon": fs.lon},
                            "ToStop": ts.name, "ToStopId": target,
                            "ToStopCoords": {"lat": ts.lat, "lon": ts.lon},
                            "DepartureTime": format_time(cp_deps[si_idx]),
                            "ArrivalTime": format_time(cp_arrs[tgt_pos]),
                            "RouteId": cp_name, "RouteLongId": cp_rid,
                            "Shape": [[fs.lat, fs.lon], [ts.lat, ts.lon]],
                        }
                        transit_steps = format_transit_legs(sub_j['legs'], stops_cache, shapes_cache)
                        if transit_steps:
                            formatted_journeys.append(transit_steps + [carpool_step])
                            print(f"[CARPOOL] Multimodal B: transit -> {cp_name}, via {si}")

    # ====== END MULTI-MODAL CARPOOL INJECTION ======

    # ====== FINAL VALIDATION ======
    formatted_journeys = validate_journeys(formatted_journeys)

    return formatted_journeys


@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    # Enabling 'reload=True' provides the 'watchdog' functionality 
    # that restarts the server automatically when code changes.
    # IMPORTANT: We must run 'socket_app' now
    uvicorn.run("server:socket_app", host="127.0.0.1", port=5001, reload=True)
