import csv
import os
import math
from collections import defaultdict
from datetime import datetime
from pytz import timezone
from typing import List, Dict, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor
from bisect import bisect_left

CALIF_TZ = timezone('America/Los_Angeles')

# --- WORKER GLOBALS ---
# These are initialized in every worker process once
G_STOPS = {}
G_ROUTES = {}
G_TRIPS = {}
G_STOP_TO_ROUTES = {}
G_ROUTE_STOP_INDEX = {}
G_ROUTE_STOP_TIMES_CACHE = {}

def init_worker(stops, routes, trips, s2r, rsi, rstc):
    """
    Initializes global read-only data for worker processes.
    This runs once per process when the pool is created.
    """
    global G_STOPS, G_ROUTES, G_TRIPS, G_STOP_TO_ROUTES, G_ROUTE_STOP_INDEX, G_ROUTE_STOP_TIMES_CACHE
    G_STOPS = stops
    G_ROUTES = routes
    G_TRIPS = trips
    G_STOP_TO_ROUTES = s2r
    G_ROUTE_STOP_INDEX = rsi
    G_ROUTE_STOP_TIMES_CACHE = rstc

def run_raptor_worker(source_stop_id, target_stop_id, departure_time_seconds):
    """
    Standalone RAPTOR query function that runs in a worker process.
    Uses global variables for data access to avoid pickling overhead on every call.
    """
    if source_stop_id not in G_STOPS or target_stop_id not in G_STOPS:
        return {'journeys': []}

    # arrival_times[k][stop_id] = earliest arrival time at stop_id with exactly k-1 transfers
    max_rounds = 30
    
    # OPTIMIZATION: Lazy Initialization
    # O(1) initialization instead of O(N)
    arrival_times = [defaultdict(lambda: float('inf')) for _ in range(max_rounds + 1)]
    best_arrival = defaultdict(lambda: float('inf'))
    
    # parent_pointers[round][stop_id] = (prev_stop, trip_id, board_stop, type, dep_time, arr_time)
    parent_pointers = defaultdict(dict)
    
    # Minimum time needed for a transfer (2 minutes)
    TRANSFER_BUFFER = 120
    
    arrival_times[0][source_stop_id] = departure_time_seconds
    best_arrival[source_stop_id] = departure_time_seconds
    
    marked_stops = {source_stop_id}
    
    # Pre-fetch target coords for A* pruning
    t_stop = G_STOPS[target_stop_id]
    t_lat, t_lon = t_stop.lat, t_stop.lon
    
    # Max speed for heuristic (e.g. 130 km/h ~ 36 m/s) - conservative upper bound for transit
    MAX_SPEED_MPS = 36.0 

    def get_min_time_to_target(s_id):
        s = G_STOPS[s_id]
        # Haversine equivalent inline or helper
        # Approximate distance is fine for pruning
        # Using simple euclidean for specialized speed (lat/lon to meters is complex, assume worst case)
        # Better: use the global haversine if available or duplicate it
        # duplicating minimal haversine for speed
        R = 6371000 # meters
        dLat = math.radians(t_lat - s.lat)
        dLon = math.radians(t_lon - s.lon)
        a = math.sin(dLat/2)**2 + math.cos(math.radians(s.lat)) * math.cos(math.radians(t_lat)) * math.sin(dLon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        dist_m = R * c
        return dist_m / MAX_SPEED_MPS

    for k in range(1, max_rounds + 1):
        routes_to_scan = {}
        for stop_id in marked_stops:
            # PRUNING: A* optimization
            # If current_arrival + min_time_to_target > best_known_arrival_at_target, then skip
            # This is safe because it's physically impossible to beat the best time
            curr_arr = arrival_times[k-1][stop_id]
            best_target_arr = best_arrival[target_stop_id]
            
            if best_target_arr < float('inf'):
                 min_time = get_min_time_to_target(stop_id)
                 if curr_arr + min_time > best_target_arr:
                     continue # Prune this branch
            
            for route_id in G_STOP_TO_ROUTES.get(stop_id, []):
                pos = G_ROUTE_STOP_INDEX[route_id][stop_id]
                if route_id not in routes_to_scan or pos < routes_to_scan[route_id]:
                    routes_to_scan[route_id] = pos
        
        marked_stops = set()
        for route_id, start_pos in routes_to_scan.items():
            route = G_ROUTES[route_id]
            current_trip_id = None
            boarding_stop = None
            boarding_time = None
            
            for pos in range(start_pos, len(route.stops)):
                stop_id = route.stops[pos]
                if current_trip_id is not None:
                    trip = G_TRIPS[current_trip_id]
                    arr_time = trip.arrival_times[pos]
                    
                    if arr_time < min(best_arrival[stop_id], best_arrival[target_stop_id]):
                        arrival_times[k][stop_id] = arr_time
                        best_arrival[stop_id] = arr_time
                        marked_stops.add(stop_id)
                        parent_pointers[k][stop_id] = (boarding_stop, current_trip_id, boarding_stop, 'transit', boarding_time, arr_time)
                
                prev_round_arrival = arrival_times[k-1][stop_id]
                if prev_round_arrival < float('inf'):
                    # Apply transfer buffer
                    min_dep = prev_round_arrival + (TRANSFER_BUFFER if k > 1 else 0)
                    
                    # High-Speed Binary Search using cache
                    if route_id in G_ROUTE_STOP_TIMES_CACHE and pos < len(G_ROUTE_STOP_TIMES_CACHE[route_id]):
                        r_times = G_ROUTE_STOP_TIMES_CACHE[route_id][pos]
                        idx = bisect_left(r_times, min_dep)
                        
                        if idx < len(r_times):
                            new_trip_id = route.trips[idx]
                            new_boarding_time = r_times[idx]
                            if current_trip_id is None or new_boarding_time < boarding_time:
                                current_trip_id = new_trip_id
                                boarding_stop = stop_id
                                boarding_time = new_boarding_time

        # Footpaths scan
        for stop_id in list(marked_stops):
            stop_arrival = arrival_times[k][stop_id]
            for to_stop_id, walk_time in G_STOPS[stop_id].footpaths:
                new_arrival = stop_arrival + walk_time
                if new_arrival < min(best_arrival[to_stop_id], best_arrival[target_stop_id]):
                    arrival_times[k][to_stop_id] = new_arrival
                    best_arrival[to_stop_id] = new_arrival
                    marked_stops.add(to_stop_id)
                    parent_pointers[k][to_stop_id] = (stop_id, None, None, 'walk', stop_arrival, new_arrival)
        
        if not marked_stops: break
            
    raw_journeys = []
    for k in range(1, max_rounds + 1):
        arr = arrival_times[k][target_stop_id]
        if arr < float('inf'):
            raw_journeys.append({
                'arrival_time': arr,
                'num_transfers': k - 1,
                'round': k
            })
    
    final_journeys = _filter_pareto_optimal(raw_journeys)
    results = []
    for j in final_journeys:
        path = _reconstruct_path(target_stop_id, j['round'], parent_pointers, source_stop_id)
        if path:
            results.append({
                'arrival_time': j['arrival_time'],
                'num_transfers': j['num_transfers'],
                'legs': path
            })
    return {'journeys': results}

def _filter_pareto_optimal(journeys):
    if not journeys: return []
    journeys.sort(key=lambda x: x['arrival_time'])
    pareto = []
    for j in journeys:
        dominated = False
        for p in pareto:
            if p['arrival_time'] <= j['arrival_time'] and p['num_transfers'] <= j['num_transfers']:
                dominated = True
                break
        if not dominated:
            pareto = [p for p in pareto if not (j['arrival_time'] <= p['arrival_time'] and j['num_transfers'] <= p['num_transfers'])]
            pareto.append(j)
    return pareto

def _reconstruct_path(target_stop_id, last_round, parent_pointers, source_stop_id):
    path = []
    current_stop = target_stop_id
    current_round = last_round
    
    while current_stop != source_stop_id:
        if current_round <= 0: break
        
        if current_stop in parent_pointers[current_round]:
            prev_stop, trip_id, board_stop, link_type, dep_t, arr_t = parent_pointers[current_round][current_stop]
            
            if link_type == 'transit':
                trip = G_TRIPS[trip_id]
                route = G_ROUTES[trip.route_id]
                path.append({
                    'type': 'transit',
                    'trip_id': trip_id,
                    'route_id': trip.route_id,
                    'route_name': route.route_name,
                    'agency_id': route.agency_id,
                    'from_stop_id': board_stop,
                    'to_stop_id': current_stop,
                    'departure_time': dep_t,
                    'arrival_time': arr_t,
                    'shape_id': trip.shape_id
                })
                current_stop = board_stop
                current_round -= 1 
            else:
                path.append({
                    'type': 'walk',
                    'from_stop_id': prev_stop,
                    'to_stop_id': current_stop,
                    'departure_time': dep_t,
                    'arrival_time': arr_t
                })
                current_stop = prev_stop
        else:
            current_round -= 1
            if current_round < 0: break
            
    return list(reversed(path))

# --- CLASSES ---

class TransitStop:
    def __init__(self, stop_id, name, lat, lon, agency_id):
        self.stop_id = stop_id
        self.name = name
        self.lat = lat
        self.lon = lon
        self.agency_id = agency_id
        self.routes = [] 
        self.footpaths = [] 

class TransitTrip:
    def __init__(self, trip_id, route_id, service_id):
        self.trip_id = trip_id
        self.route_id = route_id
        self.service_id = service_id
        self.arrival_times = []
        self.departure_times = []
        self.stop_sequence = []
        self.shape_id = None

class TransitRoute:
    def __init__(self, route_id, agency_id, route_name):
        self.route_id = route_id
        self.agency_id = agency_id
        self.route_name = route_name
        self.stops = []
        self.trips = []

class RaptorRouter:
    def __init__(self, stops_dict, routes_dict, trips_dict, shapes_dict=None):
        self.stops = stops_dict        # {stop_id: TransitStop}
        self.routes = routes_dict      # {route_id: TransitRoute}
        self.trips = trips_dict        # {trip_id: TransitTrip}
        self.shapes = shapes_dict or {} 
        
        # Build reverse mapping
        print("Building acceleration structures...")
        self.stop_to_routes = self._build_stop_to_routes()
        self.route_stop_index = self._build_route_stop_index()
        self.route_stop_times_cache = self._build_trip_times_cache()
        
        # Initialize Process Pool
        print(f"Initializing Process Pool with {os.cpu_count()} workers...")
        self.executor = ProcessPoolExecutor(
            max_workers=os.cpu_count(),
            initializer=init_worker,
            initargs=(self.stops, self.routes, self.trips, self.stop_to_routes, self.route_stop_index, self.route_stop_times_cache)
        )
        print("Process Pool Ready.")
    
    def _build_trip_times_cache(self):
        cache = {}
        for rid, route in self.routes.items():
            cache[rid] = []
            for pos in range(len(route.stops)):
                stop_times = [self.trips[tid].departure_times[pos] for tid in route.trips]
                cache[rid].append(stop_times)
        return cache
    
    def _build_stop_to_routes(self):
        mapping = defaultdict(list)
        for route_id, route in self.routes.items():
            for stop_id in route.stops:
                mapping[stop_id].append(route_id)
        return dict(mapping)
    
    def _build_route_stop_index(self):
        index = {}
        for route_id, route in self.routes.items():
            index[route_id] = {stop_id: pos for pos, stop_id in enumerate(route.stops)}
        return index
    
    def query_range(self, source_stop_id, target_stop_id, start_time_seconds, window=3600):
        if source_stop_id not in self.stops or target_stop_id not in self.stops:
            return {'journeys': []}

        # 1. Identify "Starting Opportunities"
        opportunities = []
        
        # Windows
        search_windows = [
            (start_time_seconds, start_time_seconds + window),
            (start_time_seconds + 86400, start_time_seconds + 86400 + window),
            (start_time_seconds - 86400, start_time_seconds - 86400 + window)
        ]

        def find_opps(target_stop_id, offset_t):
            for route_id in self.stop_to_routes.get(target_stop_id, []):
                route = self.routes[route_id]
                pos = self.route_stop_index[route_id][target_stop_id]
                for tid in route.trips:
                    if not self.trips[tid].departure_times: continue
                    dep = self.trips[tid].departure_times[pos]
                    for w_start, w_end in search_windows:
                        if w_start <= dep <= w_end:
                            opportunities.append((dep, target_stop_id))

        # Direct transit from source
        find_opps(source_stop_id, start_time_seconds)
        
        # Transit from stops reachable by foot
        for to_stop_id, walk_time in self.stops[source_stop_id].footpaths:
            find_opps(to_stop_id, start_time_seconds + walk_time)
        
        unique_start_times = sorted(list(set([o[0] for o in opportunities])))
        
        # Sampling Limit
        if len(unique_start_times) > 100:
            step = len(unique_start_times) // 100
            unique_start_times = unique_start_times[::step][:100]

        # 2. Run RAPTOR queries in PARALLEL using ProcessPool
        all_results = []
        seen_journeys = set()
        
        # We must submit args as tuple since we use map, or use list comprehension
        # executor.map expects a function and an iterable. Our function run_raptor_worker takes 3 args.
        # So we can fix source/target using a lambda or partial, but partial isn't always pickleable easily.
        # Better: define a helper or use starmap if available (ProcessPoolExecutor doesn't have starmap in older python, but map works if we pack args)
        
        # ACTUALLY: We can just use a list comprehension with submit
        futures = []
        for dep_t in unique_start_times:
            # submit(fn, *args)
            futures.append(self.executor.submit(run_raptor_worker, source_stop_id, target_stop_id, dep_t))
            
        # Collect results
        query_results = [f.result() for f in futures]

        for res in query_results:
            for j in res['journeys']:
                first_leg_dep = j['legs'][0]['departure_time']
                
                in_any_window = any(w_start <= first_leg_dep <= w_end for w_start, w_end in search_windows)
                if not in_any_window:
                    continue

                trip_sig = tuple(leg.get('trip_id') or 'walk' for leg in j['legs'])
                full_sig = (first_leg_dep, j['arrival_time'], trip_sig)
                
                if full_sig not in seen_journeys:
                    all_results.append(j)
                    seen_journeys.add(full_sig)

        all_results.sort(key=lambda x: x['legs'][0]['departure_time'])
        return {'journeys': all_results}

def load_all_data(data_dir):
    stops_dict = {}
    routes_base = {} 
    trips_dict = {}
    shapes_dict = {}
    
    def to_sec(t):
        if not t: return 0
        parts = t.split(':')
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s

    for operator in os.listdir(data_dir):
        op_path = os.path.join(data_dir, operator)
        if not os.path.isdir(op_path): continue
        
        stops_file = os.path.join(op_path, 'stops.txt')
        if os.path.exists(stops_file):
            with open(stops_file, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    sid = f"{operator}:{row['stop_id']}"
                    stops_dict[sid] = TransitStop(sid, row['stop_name'], float(row['stop_lat']), float(row['stop_lon']), operator)

        routes_file = os.path.join(op_path, 'routes.txt')
        if os.path.exists(routes_file):
            with open(routes_file, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    rid = f"{operator}:{row['route_id']}"
                    name = row.get('route_short_name') or row.get('route_long_name') or rid
                    routes_base[rid] = {"name": name, "agency": operator}

        shapes_file = os.path.join(op_path, 'shapes.txt')
        if os.path.exists(shapes_file):
             with open(shapes_file, encoding='utf-8-sig') as f:
                temp_shapes = defaultdict(list)
                for row in csv.DictReader(f):
                    shape_id = f"{operator}:{row['shape_id']}"
                    temp_shapes[shape_id].append((float(row['shape_pt_lat']), float(row['shape_pt_lon']), int(row['shape_pt_sequence'])))
                for sid, pts in temp_shapes.items():
                    pts.sort(key=lambda x: x[2])
                    shapes_dict[sid] = [(p[0], p[1]) for p in pts]

        trips_file = os.path.join(op_path, 'trips.txt')
        if os.path.exists(trips_file):
            with open(trips_file, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    tid = f"{operator}:{row['trip_id']}"
                    rid = f"{operator}:{row['route_id']}"
                    trip = TransitTrip(tid, rid, row.get('service_id'))
                    if row.get('shape_id'):
                        trip.shape_id = f"{operator}:{row['shape_id']}"
                    trips_dict[tid] = trip

        st_file = os.path.join(op_path, 'stop_times.txt')
        if os.path.exists(st_file):
            with open(st_file, encoding='utf-8-sig') as f:
                trip_stop_times = defaultdict(list)
                for row in csv.DictReader(f):
                    tid = f"{operator}:{row['trip_id']}"
                    if tid in trips_dict:
                        trip_stop_times[tid].append(row)
                
                for tid, rows in trip_stop_times.items():
                    rows.sort(key=lambda x: int(x['stop_sequence']))
                    trip = trips_dict[tid]
                    trip.stop_sequence = [f"{operator}:{r['stop_id']}" for r in rows]
                    trip.arrival_times = [to_sec(r['arrival_time']) for r in rows]
                    trip.departure_times = [to_sec(r['departure_time']) for r in rows]

    # FILTER TRIPS (05:00 - 09:00 Window)
    print("Filtering trips to 05:00-09:00 window...")
    trips_to_remove = []
    WINDOW_START, WINDOW_END = 18000, 32400
    
    for tid, trip in trips_dict.items():
        if not trip.departure_times:
            trips_to_remove.append(tid)
            continue
            
        t_start = trip.departure_times[0]
        # t_end = trip.arrival_times[-1] # optimization: end time check removed
        
        # Keep if trip start time is in window
        if not (WINDOW_START <= t_start <= WINDOW_END):
            trips_to_remove.append(tid)
            
    before_count = len(trips_dict)
    for tid in trips_to_remove:
        del trips_dict[tid]
    print(f"Trips filtered: {before_count} -> {len(trips_dict)}")

    final_routes = {}
    pattern_to_route_id = {}
    
    for tid, trip in trips_dict.items():
        if not trip.stop_sequence: continue
        
        pattern = tuple(trip.stop_sequence)
        base_rid = trip.route_id
        
        if (base_rid, pattern) not in pattern_to_route_id:
            unique_rid = f"{base_rid}:p{len(pattern_to_route_id)}"
            base_info = routes_base.get(base_rid, {"name": "Unknown", "agency": "Unknown"})
            new_route = TransitRoute(unique_rid, base_info['agency'], base_info['name'])
            new_route.stops = list(pattern)
            final_routes[unique_rid] = new_route
            pattern_to_route_id[(base_rid, pattern)] = unique_rid
            
        unique_rid = pattern_to_route_id[(base_rid, pattern)]
        trip.route_id = unique_rid 
        final_routes[unique_rid].trips.append(tid)

    for rid, route in final_routes.items():
        route.trips.sort(key=lambda tid: trips_dict[tid].departure_times[0])

    print("Computing footpaths using spatial grid...")
    grid = defaultdict(list)
    grid_size = 0.005 
    
    for sid, stop in stops_dict.items():
        gx = int(stop.lat / grid_size)
        gy = int(stop.lon / grid_size)
        grid[(gx, gy)].append(stop)
        
    for sid, stop in stops_dict.items():
        gx = int(stop.lat / grid_size)
        gy = int(stop.lon / grid_size)
        
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                neighbor_stops = grid.get((gx + dx, gy + dy), [])
                for other in neighbor_stops:
                    if sid == other.stop_id: continue
                    dist = haversine(stop.lat, stop.lon, other.lat, other.lon)
                    if dist < 5.0:
                        walk_time = int((dist / 1.1) * 1000)
                        stop.footpaths.append((other.stop_id, walk_time))
    
    print(f"Footpaths computed for {len(stops_dict)} stops.")
    return stops_dict, final_routes, trips_dict, shapes_dict

def haversine(lat1, lon1, lat2, lon2):
    R = 6371 
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c
