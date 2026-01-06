from flask import Flask, request, jsonify
from flask_cors import CORS
import raptor
import os
import json

app = Flask(__name__)
CORS(app)

BASE_PATH = "C:/Users/bilal/Desktop/Raptor/Raptor"
OPERATORS = [
    "CE", "SS", "PE", "SR", "SA", "CT", "MA", "CM", "BA", "CC", "SC", "SM", "AC", "EE", "GP", "SF"
]
GTFS_DIRS = [os.path.join(BASE_PATH, f"GTFSTransitData_{op}") for op in OPERATORS]
PATH_TO_SAVE_RAPTOR_OBJECTS = "raptor_data.pkl"

print("Initializing RAPTOR Data...")
data = raptor.RAPTORData(GTFS_DIRS)

# Check if pickles exist to load faster, otherwise read
if os.path.exists(PATH_TO_SAVE_RAPTOR_OBJECTS + ".stops"):
    print("Loading RAPTOR data from disk...")
    data.loadFromDisk(PATH_TO_SAVE_RAPTOR_OBJECTS)
else:
    print("Reading GTFS data (this may take a while)...")
    data.readGTFS()
    print("Saving RAPTOR data to disk...")
    # data.saveToDisk(PATH_TO_SAVE_RAPTOR_OBJECTS) 
    # Saving might take time and disk space, but it's good for restart.
    # Commented out if user didn't explicitly ask for caching mechanism updates, but they asked for "perfect backend".
    # I'll enable it.
    try:
        data.saveToDisk(PATH_TO_SAVE_RAPTOR_OBJECTS)
    except Exception as e:
        print(f"Warning: Could not save cache to disk: {e}")

print("RAPTOR Global Data Initialized.")

@app.route('/api/nearest-stop', methods=['GET'])
def get_nearest_stop():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    if not lat or not lon:
        return jsonify({"error": "Missing lat/lon"}), 400
    
    # Radius in KM
    radius = float(request.args.get('radius', 2.0))
    limit = int(request.args.get('limit', 5))
    
    stops = data.findStopsNear(lat, lon, radius_km=radius, limit=limit)
    if not stops:
        return jsonify([])
    
    results = []
    for s_id in stops:
        idx = data.stopMap[s_id]
        results.append({
            "id": s_id,
            "name": data.stopNames[idx],
            "lat": float(data.stopLats[idx]),
            "lon": float(data.stopLons[idx]),
            "agency": s_id.split(':')[0] if ':' in s_id else "UNKNOWN"
        })
    return jsonify(results)

@app.route('/api/route', methods=['GET'])
def get_route():
    source_id = request.args.get('source')
    target_id = request.args.get('target')
    
    # Optional coordinate support
    s_lat = request.args.get('s_lat')
    s_lon = request.args.get('s_lon')
    t_lat = request.args.get('t_lat')
    t_lon = request.args.get('t_lon')
    
    # If coordinates provided, find nearest stops
    if s_lat and s_lon and not source_id:
        source_stops = data.findStopsNear(s_lat, s_lon, radius_km=3.0, limit=1)
        if source_stops: source_id = source_stops[0]
    
    if t_lat and t_lon and not target_id:
        target_stops = data.findStopsNear(t_lat, t_lon, radius_km=3.0, limit=1)
        if target_stops: target_id = target_stops[0]

    # Simple routing as requested
    dep_time_str = request.args.get('earliest_dep', '08:00:00')
    parts = dep_time_str.split(':')
    dep_time = int(parts[0])*3600 + int(parts[1])*60 + (int(parts[2]) if len(parts)>2 else 0)

    if not source_id or not target_id:
        return jsonify({"error": "Missing source or target"}), 400
    
    print(f"Finding best route from {source_id} to {target_id} at {dep_time_str}")
    
    # Use the original run method (single departure time)
    # The original RAPTORData.run(source, target, depTime)
    data.run(source_id, target_id, dep_time)
    
    # Get all journeys found in different rounds
    results = data.getAllJourneys()
    
    # Convert dict to simple sorted list of journeys
    journeys_list = [results[k] for k in sorted(results.keys())]
    
    return jsonify(journeys_list)

@app.route('/api/all-stops-geojson', methods=['GET'])
def get_all_stops_geojson():
    features = []
    for i, stop_id in enumerate(data.stops):
        features.append({
            "type": "Feature",
            "id": stop_id,
            "geometry": {
                "type": "Point",
                "coordinates": [float(data.stopLons[i]), float(data.stopLats[i])]
            },
            "properties": {
                "name": data.stopNames[i],
                "agency": stop_id.split(':')[0] if ':' in stop_id else "UNKNOWN",
                "id": stop_id
            }
        })
    return jsonify({
        "type": "FeatureCollection",
        "features": features
    })

@app.route('/api/stops', methods=['GET'])
def get_stops():
    query = request.args.get('q', '').lower()
    results = []
    for i, stop_id in enumerate(data.stops):
        name = data.stopNames[i]
        agency = stop_id.split(':')[0] if ':' in stop_id else "UNKNOWN"
        if not query or query in name.lower() or query in stop_id.lower() or query in agency.lower():
            results.append({
                "id": stop_id,
                "name": name,
                "lat": float(data.stopLats[i]),
                "lon": float(data.stopLons[i]),
                "agency": agency,
                "displayName": f"{name} ({agency})"
            })
            if query and len(results) > 200: break
    return jsonify(results)

@app.route('/api/agencies', methods=['GET'])
def get_agencies():
    return jsonify(OPERATORS)

if __name__ == '__main__':
    # Increase recursion depth for deep RAPTOR objects if necessary
    import sys
    sys.setrecursionlimit(2000)
    app.run(port=5001, debug=True)

