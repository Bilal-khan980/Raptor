import raptor
import json
import os

BASE_PATH = "C:/Users/bilal/Desktop/Raptor/Raptor"
OPERATORS = [
    "CE", "SS", "PE", "SR", "SA", "CT", "MA", "CM", "BA", "CC", "SC", "SM", "AC", "EE", "GP", "SF"
]
GTFS_DIRS = [os.path.join(BASE_PATH, f"GTFSTransitData_{op}") for op in OPERATORS]
PATH_TO_SAVE_RAPTOR_OBJECTS = "C:/Users/bilal/Desktop/Raptor/Raptor/raptor_data.pkl"

print("Initializing RAPTOR Data from all agencies...")
for d in GTFS_DIRS:
    print(f"Loading {d}...")

data = raptor.RAPTORData(GTFS_DIRS)
data.readGTFS()
print("Saving merged RAPTOR data to disk...")
data.saveToDisk(PATH_TO_SAVE_RAPTOR_OBJECTS)
print("Data saved.")

## query
# Define source and target stop IDs (prefixed with agency code)
# Example: BART (BA) stops
source = "BA:905202" # 12th St. Oakland City Center
target = "BA:905302" # 16th St. Mission

# Example: Caltrain (CT) stops? (Need to know IDs, assuming for now based on previous runs or standard GTFS)
# If IDs are unknown, we can look them up by name in data.

print(f"Stops loaded: {data.numberOfStops()}")
print(f"Routes loaded: {data.numberOfRoutes()}")

if source not in data.stopMap:
    print(f"Source stop ID {source} not found in GTFS data.")
    # Try finding by name to help debug
    # for i, name in enumerate(data.stopNames):
    #     if "12th St" in name:
    #         print(f"Candidate: {data.stops[i]} - {name}")
else:
    print(f"Source stop ID {source} found at index {data.stopMap[source]}")

if target not in data.stopMap:
    print(f"Target stop ID {target} not found in GTFS data.")
else:
    print(f"Target stop ID {target} found at index {data.stopMap[target]}")


if source in data.stopMap and target in data.stopMap:
    earliest_dep = 8*3600 # 8 AM
    latest_dep = earliest_dep + 3600 # 9 AM
    
    print(f"Running RAPTOR from {source} to {target} between {earliest_dep} and {latest_dep} seconds")
    
    data.run(source, target, earliest_dep, latest_dep)
    journeys = data.resultJourney if hasattr(data, 'resultJourney') else []
    
    print(f"Journeys found: {len(journeys)}")
    if journeys:
        print(json.dumps(journeys[0], indent=4))