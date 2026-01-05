import raptor
import json # for this purpose
## loading


PATH_TO_GTFS_DIR = "C:/Users/bilal/Desktop/Raptor/RAPTORPython/GTFSTransitData_BA"
PATH_TO_SAVE_RAPTOR_OBJECTS = "C:/Users/bilal/Desktop/Raptor/RAPTORPython/raptor_data.pkl"
data = raptor.RAPTORData(PATH_TO_GTFS_DIR)
data.readGTFS()
data.saveToDisk(PATH_TO_SAVE_RAPTOR_OBJECTS)
# data.loadFromDisk(PATH_TO_SAVE_RAPTOR_OBJECTS") # if you want to laod the previously computed timetable information

## query
# Define source and target stop IDs (as they appear in stops.txt)
source = "905202"
target = "905302"
# Verify that the stop IDs exist in the loaded GTFS data
if source not in data.stopMap:
    print(f"Source stop ID {source} not found in GTFS data.")
else:
    print(f"Source stop ID {source} found at index {data.stopMap[source]}")
if target not in data.stopMap:
    print(f"Target stop ID {target} not found in GTFS data.")
else:
    print(f"Target stop ID {target} found at index {data.stopMap[target]}")
# Show routes serving source and target
print("Routes serving source stop:")
for route, idx in data.routesOperatingAtStops[data.stopMap[source]]:
    print(f"  Route {route} at position {idx}")
print("Routes serving target stop:")
for route, idx in data.routesOperatingAtStops[data.stopMap[target]]:
    print(f"  Route {route} at position {idx}")
# Define departure time window (full day)
earliest_dep = 5*3600 + 15*60       # midnight
latest_dep = earliest_dep + 3600          # end of day (24h)
print(f"Running RAPTOR from {source} to {target} between {earliest_dep} and {latest_dep} seconds")
# Run forward direction
data.run(source, target, earliest_dep, latest_dep)
journeys_fwd = data.resultJourney if hasattr(data, 'resultJourney') else []
print(f"Forward direction journeys count: {len(journeys_fwd)}")
# Run reverse direction to see if trips exist the other way
print(f"Running RAPTOR from {target} to {source} (reverse) between {earliest_dep} and {latest_dep} seconds")
data.run(target, source, earliest_dep, latest_dep)
journeys_rev = data.resultJourney if hasattr(data, 'resultJourney') else []
print(f"Reverse direction journeys count: {len(journeys_rev)}")
# Print journeys if any
if journeys_fwd:
    print("--- Forward journeys ---")
    for idx, j in enumerate(journeys_fwd):
        print(f"Window {idx}:")
        print(json.dumps(j, indent=4))
if journeys_rev:
    print("--- Reverse journeys ---")
    for idx, j in enumerate(journeys_rev):
        print(f"Window {idx}:")
        print(json.dumps(j, indent=4))