import csv
import os
import math
from collections import defaultdict
from datetime import datetime
from pytz import timezone
from typing import List, Dict, Tuple, Optional

CALIF_TZ = timezone('America/Los_Angeles')

class TransitStop:
    def __init__(self, stop_id, name, lat, lon, agency_id):
        self.stop_id = stop_id
        self.name = name
        self.lat = lat
        self.lon = lon
        self.agency_id = agency_id
        self.routes = []  # List of route_ids serving this stop
        self.footpaths = []  # List of (to_stop_id, walk_time_seconds)

class TransitTrip:
    def __init__(self, trip_id, route_id, service_id):
        self.trip_id = trip_id
        self.route_id = route_id
        self.service_id = service_id
        self.arrival_times = []  # seconds-since-midnight
        self.departure_times = []  # seconds-since-midnight
        self.stop_sequence = []  # stop_ids
        self.shape_id = None

class TransitRoute:
    def __init__(self, route_id, agency_id, route_name):
        self.route_id = route_id
        self.agency_id = agency_id
        self.route_name = route_name
        self.stops = []  # List of stop_ids in order
        self.trips = []  # List of trip_ids (sorted by departure time at first stop)

class RaptorRouter:
    def __init__(self, stops_dict, routes_dict, trips_dict, shapes_dict=None):
        self.stops = stops_dict        # {stop_id: TransitStop}
        self.routes = routes_dict      # {route_id: TransitRoute}
        self.trips = trips_dict        # {trip_id: TransitTrip}
        self.shapes = shapes_dict or {} # {shape_id: [(lat, lon)]}
        
        # Build reverse mapping
        self.stop_to_routes = self._build_stop_to_routes()
        self.route_stop_index = self._build_route_stop_index()
    
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

        # 1. Identify all "Starting Opportunities" in the window
        # An opportunity is (departure_time, stop_id, trip_id_if_transit)
        opportunities = []
        
        # Direct transit from source
        for route_id in self.stop_to_routes.get(source_stop_id, []):
            route = self.routes[route_id]
            pos = self.route_stop_index[route_id][source_stop_id]
            for tid in route.trips:
                dep = self.trips[tid].departure_times[pos]
                if start_time_seconds <= dep <= start_time_seconds + window:
                    opportunities.append((dep, source_stop_id))
        
        # Transit from stops reachable by foot
        for to_stop_id, walk_time in self.stops[source_stop_id].footpaths:
            reach_time = start_time_seconds + walk_time
            for route_id in self.stop_to_routes.get(to_stop_id, []):
                route = self.routes[route_id]
                pos = self.route_stop_index[route_id][to_stop_id]
                for tid in route.trips:
                    dep = self.trips[tid].departure_times[pos]
                    if reach_time <= dep <= start_time_seconds + window:
                        opportunities.append((dep, source_stop_id)) 
                        # We still query from 'source' but RAPTOR will handle the rest
        
        # De-duplicate departure times to avoid redundant queries
        unique_start_times = sorted(list(set([o[0] for o in opportunities])))
        # Also always include the very first possible departure
        if not unique_start_times or unique_start_times[0] > start_time_seconds:
             unique_start_times.insert(0, start_time_seconds)

        all_results = []
        seen_journeys = set() # (tuple of trip_ids)

        for dep_t in unique_start_times:
            res = self.query(source_stop_id, target_stop_id, dep_t)
            for j in res['journeys']:
                # Create a signature for the journey to de-duplicate
                # We use (departure_time, arrival_time, transfers, trip_sequence)
                trip_sig = tuple(leg.get('trip_id') or 'walk' for leg in j['legs'])
                full_sig = (j['legs'][0]['departure_time'], j['arrival_time'], trip_sig)
                
                if full_sig not in seen_journeys:
                    all_results.append(j)
                    seen_journeys.add(full_sig)

        # Final Pareto Filtering: (DepTime, ArrTime, Transfers)
        # For multi-option view, we want to KEEP a journey if there is no journey that:
        # Starts LATER and Arrives EARLIER with LESS transfers.
        # But usually just showing all distinct starting trips is what users want.
        all_results.sort(key=lambda x: x['legs'][0]['departure_time'])
        
        return {'journeys': all_results}

    def query(self, source_stop_id, target_stop_id, departure_time_seconds):
        if source_stop_id not in self.stops or target_stop_id not in self.stops:
            return {'journeys': []}

        # arrival_times[k][stop_id] = earliest arrival time at stop_id with exactly k-1 transfers
        max_rounds = 5 # Reduced rounds for performance in range-query
        arrival_times = {k: {sid: float('inf') for sid in self.stops} for k in range(max_rounds + 1)}
        best_arrival = {sid: float('inf') for sid in self.stops}
        
        # parent_pointers[round][stop_id] = (prev_stop, trip_id, board_stop, type, dep_time, arr_time)
        parent_pointers = defaultdict(dict)
        
        arrival_times[0][source_stop_id] = departure_time_seconds
        best_arrival[source_stop_id] = departure_time_seconds
        
        marked_stops = {source_stop_id}
        
        for k in range(1, max_rounds + 1):
            routes_to_scan = {}
            for stop_id in marked_stops:
                for route_id in self.stop_to_routes.get(stop_id, []):
                    pos = self.route_stop_index[route_id][stop_id]
                    if route_id not in routes_to_scan or pos < routes_to_scan[route_id]:
                        routes_to_scan[route_id] = pos
            
            marked_stops = set()
            for route_id, start_pos in routes_to_scan.items():
                route = self.routes[route_id]
                current_trip_id = None
                boarding_stop = None
                boarding_time = None
                
                for pos in range(start_pos, len(route.stops)):
                    stop_id = route.stops[pos]
                    if current_trip_id is not None:
                        trip = self.trips[current_trip_id]
                        arr_time = trip.arrival_times[pos]
                        if arr_time < min(best_arrival[stop_id], best_arrival[target_stop_id]):
                            arrival_times[k][stop_id] = arr_time
                            best_arrival[stop_id] = arr_time
                            marked_stops.add(stop_id)
                            parent_pointers[k][stop_id] = (boarding_stop, current_trip_id, boarding_stop, 'transit', boarding_time, arr_time)
                    
                    prev_round_arrival = arrival_times[k-1][stop_id]
                    if prev_round_arrival < float('inf'):
                        new_trip_id = self._find_earliest_trip(route_id, stop_id, prev_round_arrival)
                        if new_trip_id is not None:
                            new_trip = self.trips[new_trip_id]
                            new_boarding_time = new_trip.departure_times[self.route_stop_index[route_id][stop_id]]
                            if current_trip_id is None or new_boarding_time < boarding_time:
                                current_trip_id = new_trip_id
                                boarding_stop = stop_id
                                boarding_time = new_boarding_time

            transit_stops = list(marked_stops)
            for stop_id in transit_stops:
                for to_stop_id, walk_time in self.stops[stop_id].footpaths:
                    new_arrival = arrival_times[k][stop_id] + walk_time
                    if new_arrival < min(best_arrival[to_stop_id], best_arrival[target_stop_id]):
                        arrival_times[k][to_stop_id] = new_arrival
                        best_arrival[to_stop_id] = new_arrival
                        marked_stops.add(to_stop_id)
                        parent_pointers[k][to_stop_id] = (stop_id, None, None, 'walk', arrival_times[k][stop_id], new_arrival)
            
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
        
        final_journeys = self._filter_pareto_optimal(raw_journeys)
        results = []
        for j in final_journeys:
            path = self._reconstruct_path(target_stop_id, j['round'], parent_pointers, source_stop_id)
            if path:
                results.append({
                    'arrival_time': j['arrival_time'],
                    'num_transfers': j['num_transfers'],
                    'legs': path
                })
        return {'journeys': results}

    def _find_earliest_trip(self, route_id, stop_id, min_departure_time):
        route = self.routes[route_id]
        stop_pos = self.route_stop_index[route_id][stop_id]
        for trip_id in route.trips:
            trip = self.trips[trip_id]
            if trip.departure_times[stop_pos] >= min_departure_time:
                return trip_id
        return None

    def _filter_pareto_optimal(self, journeys):
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

    def _reconstruct_path(self, target_stop_id, last_round, parent_pointers, source_stop_id):
        path = []
        current_stop = target_stop_id
        current_round = last_round
        
        while current_stop != source_stop_id:
            if current_round <= 0: break
            
            if current_stop in parent_pointers[current_round]:
                prev_stop, trip_id, board_stop, link_type, dep_t, arr_t = parent_pointers[current_round][current_stop]
                
                if link_type == 'transit':
                    trip = self.trips[trip_id]
                    route = self.routes[trip.route_id]
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
                    current_round -= 1 # Dec transfers
                else:
                    path.append({
                        'type': 'walk',
                        'from_stop_id': prev_stop,
                        'to_stop_id': current_stop,
                        'departure_time': dep_t,
                        'arrival_time': arr_t
                    })
                    current_stop = prev_stop
                    # Round count stays same for walks in same round
            else:
                # If not found in current round, check previous (could happen with footpaths)
                current_round -= 1
                if current_round < 0: break
                
        return list(reversed(path))

def load_all_data(data_dir):
    stops_dict = {}
    routes_base = {} # GTFS routes to get names
    trips_dict = {}
    shapes_dict = {}
    
    # helper for time
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
        
        # 1. Stops
        stops_file = os.path.join(op_path, 'stops.txt')
        if os.path.exists(stops_file):
            with open(stops_file, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    sid = f"{operator}:{row['stop_id']}"
                    stops_dict[sid] = TransitStop(sid, row['stop_name'], float(row['stop_lat']), float(row['stop_lon']), operator)

        # 2. GTFS Routes (for names)
        routes_file = os.path.join(op_path, 'routes.txt')
        if os.path.exists(routes_file):
            with open(routes_file, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    rid = f"{operator}:{row['route_id']}"
                    name = row.get('route_short_name') or row.get('route_long_name') or rid
                    routes_base[rid] = {"name": name, "agency": operator}

        # 3. Shapes
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

        # 4. Trips (preliminary)
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

        # 5. Stop Times
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

    # Now group trips into UNIQUE ROUTES based on stop sequence
    final_routes = {}
    pattern_to_route_id = {} # (tuple_of_stops) -> route_id
    
    for tid, trip in trips_dict.items():
        if not trip.stop_sequence: continue
        
        pattern = tuple(trip.stop_sequence)
        base_rid = trip.route_id
        
        if (base_rid, pattern) not in pattern_to_route_id:
            # Create a new unique route for this pattern
            unique_rid = f"{base_rid}:p{len(pattern_to_route_id)}"
            base_info = routes_base.get(base_rid, {"name": "Unknown", "agency": "Unknown"})
            new_route = TransitRoute(unique_rid, base_info['agency'], base_info['name'])
            new_route.stops = list(pattern)
            final_routes[unique_rid] = new_route
            pattern_to_route_id[(base_rid, pattern)] = unique_rid
            
        unique_rid = pattern_to_route_id[(base_rid, pattern)]
        trip.route_id = unique_rid # Re-assign trip to unique route
        final_routes[unique_rid].trips.append(tid)

    # Sort trips in each unique route
    for rid, route in final_routes.items():
        route.trips.sort(key=lambda tid: trips_dict[tid].departure_times[0])

    # 6. Inter-agency transfers (footpaths) 
    print("Computing footpaths using spatial grid...")
    grid = defaultdict(list)
    grid_size = 0.005 # ~500m
    
    for sid, stop in stops_dict.items():
        gx = int(stop.lat / grid_size)
        gy = int(stop.lon / grid_size)
        grid[(gx, gy)].append(stop)
        
    for sid, stop in stops_dict.items():
        gx = int(stop.lat / grid_size)
        gy = int(stop.lon / grid_size)
        
        # Check current and 8 neighboring grid cells
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                neighbor_stops = grid.get((gx + dx, gy + dy), [])
                for other in neighbor_stops:
                    if sid == other.stop_id: continue
                    dist = haversine(stop.lat, stop.lon, other.lat, other.lon)
                    if dist < 0.2: # 200 meters for tighter transfers
                        walk_time = int((dist / 1.1) * 1000) # 1.1 m/s (approx 4km/h)
                        stop.footpaths.append((other.stop_id, walk_time))
    
    print(f"Footpaths computed for {len(stops_dict)} stops.")
    return stops_dict, final_routes, trips_dict, shapes_dict

def haversine(lat1, lon1, lat2, lon2):
    R = 6371 # Earth radius in km
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c
