#!/usr/local/bin/python3
# -*- coding: utf-8 -*-
import csv
import bisect
import pickle
from datetime import timedelta
import os
import numpy as np
try:
    from scipy.spatial import KDTree
except ImportError:
    KDTree = None

## dirty hack
class KeyifyList(object):
	def __init__(self, inner, key):
		self.inner = inner
		self.key = key

	def __len__(self):
		return len(self.inner)

	def __getitem__(self, k):
		return self.key(self.inner[k])

def stringHHMMSSToSeconds(time):
	[h, m, s] = time.split(":")
	return (int(h) * 60 + int(m)) * 60 + int(s)

def secondsToHHMMSSString(sec):
	if (sec == float("inf")):
		return "inf"
	h = int(sec // 3600)
	m = int((sec % 3600) // 60)
	s = int(sec % 60)
	return f"{h:02d}:{m:02d}:{s:02d}"

# advanced classes to look up things faster

class IndexedSet(object):
	def __init__(self, size):
		super(IndexedSet, self).__init__()
		self.size = size

		self.clear()

	def isEmpty(self):
		return (len(self.elements) == 0)

	def clear(self):
		self.visited = [False for _ in range(self.size)]
		self.elements = []

	def insert(self, element):
		if (not self.visited[element]):
			self.visited[element] = True
			self.elements.append(element)

	def getElements(self):
		return self.elements

	def contains(self, element):
		return self.visited[element]
		

class IndexedHash(IndexedSet):
	def __init__(self, size):
		IndexedSet.__init__(self, size)
		self.elements = {}
		self.clear()

	def clear(self):
		self.visited = [False for _ in range(self.size)]
		self.elements = {}

	def insert(self, element, addElement):
		if (not self.visited[element]):
			self.visited[element] = True
			self.elements[element] = addElement

	def getAdditionalElement(self, element):
		return self.elements[element]

	def setAdditionalElement(self, element, addElement):
		if (not self.visited[element]):
			self.elements[element] = addElement

	def getElements(self):
		return list(zip(self.elements.keys(), self.elements.values()))


# just classes to have an overview

class Transfer(object):
	def __init__(self, fromStopId, toStopId, duration):
		super(Transfer, self).__init__()
		self.fromStopId = fromStopId
		self.toStopId = toStopId
		self.duration = duration

	def __str__(self):
		return "From: " +  str(self.fromStopId) + ";To: " + str(self.toStopId) + ";Duration: " + str(self.duration)

class StopEvent(object):
	def __init__(self, depTime, arrTime):
		super(StopEvent, self).__init__()
		self.depTime = depTime
		self.arrTime = arrTime

class EarliestArrivalLabel(object):
	def __init__(self, arrTime = float("inf"), parentDepTime = float("inf"), parent = -1, usesRoute = False, routeId = None):
		super(EarliestArrivalLabel, self).__init__()
		self.arrTime = arrTime
		self.parentDepTime = parentDepTime
		self.parent = parent
		self.usesRoute = usesRoute
		self.routeId = routeId

	def __str__(self):
		return "ArrTime: " + secondsToHHMMSSString(self.arrTime) + ";ParentDepTime: " + secondsToHHMMSSString(self.parentDepTime) + ";Parent: " + str(self.parent) + ";UsesRoute: " + str(self.usesRoute) + ";RouteId: " + str(self.routeId)

class DepartureLabel(object):
	def __init__(self, depTime, stop):
		super(DepartureLabel, self).__init__()
		self.depTime = depTime
		self.stop = stop
		

# Now the main file
class RAPTORData(object):
	def __init__(self, directoryNames):
		super(RAPTORData, self).__init__()
		if isinstance(directoryNames, str):
			self.directoryNames = [directoryNames]
		else:
			self.directoryNames = directoryNames
		self.rounds = []
		self.earliestArrival = []
		self.resultJourney = []
		self.source = 0
		self.target = 0
		self.depTime = 0
		self.earliestDepTime = 0
		self.latestDepTime = 0
		self.stopsUpdatedByRoute = None
		self.stopsUpdatedByTransfer = None
		self.stopsReached = None


		## route contains routeId (from gtfs)
		self.routes = []
		self.gtfsRoutes = []

		## stopSequenceOfRoute[i] is a list of stops (sorted by stop sequence)
		self.stopSequenceOfRoute = []

		## stopEventsOfTrip[i] contains a list of StopEvents
		self.stopEventsOfTrip = []

		## route id of trip
		self.trips = []

		## first trip of route
		self.firstTripOfRoute = []

		## transfers, sorted by fromVertex
		self.transfers = []

		## stops, list of stopId (the gtfs stop id)
		self.stops = []
		self.stopNames = []
		self.stopLats = []
		self.stopLons = []
		self.routesOperatingAtStops = []

		self.collectedDepTimes = []

		self.stopMap = {}
		self.tripMap = {}
		self.tripOriginalRoute = {}
		self.routeMap = {}
		
		## NEW: Shapes
		self.shapes = {} # shape_id -> list of (lat, lon)
		self.tripShapes = {} # trip_id (gtfs) -> shape_id
		self.routeShapes = [] # internal route id -> shape_id
		
		## Spatial search
		self.kdTree = None
		self.stopCoordsArr = None

	def numberOfRoutes(self):
		return len(self.stopSequenceOfRoute)

	def numberOfStops(self):
		return len(self.stops)

	def saveToDisk(self, filename):
		with open(filename + '.routes', 'wb') as file:
			pickle.dump(self.routes, file)
		with open(filename + '.stops', 'wb') as file:
			pickle.dump(self.stops, file)
		with open(filename + '.routesOperatingAtStops', 'wb') as file:
			pickle.dump(self.routesOperatingAtStops, file)
		with open(filename + '.stopNames', 'wb') as file:
			pickle.dump(self.stopNames, file)
		with open(filename + '.stopLats', 'wb') as file:
			pickle.dump(self.stopLats, file)
		with open(filename + '.stopLons', 'wb') as file:
			pickle.dump(self.stopLons, file)
		with open(filename + '.transfers', 'wb') as file:
			pickle.dump(self.transfers, file)
		with open(filename + '.routes', 'wb') as file:
			pickle.dump(self.routes, file)
		with open(filename + '.trips', 'wb') as file:
			pickle.dump(self.trips, file)
		with open(filename + '.stopMap', 'wb') as file:
			pickle.dump(self.stopMap, file)
		with open(filename + '.tripMap', 'wb') as file:
			pickle.dump(self.tripMap, file)
		with open(filename + '.stopEventsOfTrip', 'wb') as file:
			pickle.dump(self.stopEventsOfTrip, file)
		with open(filename + '.stopSequenceOfRoute', 'wb') as file:
			pickle.dump(self.stopSequenceOfRoute, file)
		with open(filename + '.firstTripOfRoute', 'wb') as file:
			pickle.dump(self.firstTripOfRoute, file)
		# Save shapes
		with open(filename + '.shapes', 'wb') as file:
			pickle.dump(self.shapes, file)
		with open(filename + '.routeShapes', 'wb') as file:
			pickle.dump(self.routeShapes, file)

	def loadFromDisk(self, filename):
		with open(filename + '.routes', 'rb') as file:
			self.routes = pickle.load(file)
		with open(filename + '.stops', 'rb') as file:
			self.stops = pickle.load(file)
		with open(filename + '.routesOperatingAtStops', 'rb') as file:
			self.routesOperatingAtStops = pickle.load(file)
		with open(filename + '.stopNames', 'rb') as file:
			self.stopNames = pickle.load(file)
		with open(filename + '.stopLats', 'rb') as file:
			self.stopLats = pickle.load(file)
		with open(filename + '.stopLons', 'rb') as file:
			self.stopLons = pickle.load(file)
		with open(filename + '.transfers', 'rb') as file:
			self.transfers = pickle.load(file)
		with open(filename + '.routes', 'rb') as file:
			self.routes = pickle.load(file)
		with open(filename + '.trips', 'rb') as file:
			self.trips = pickle.load(file)
		with open(filename + '.stopMap', 'rb') as file:
			self.stopMap = pickle.load(file)
		with open(filename + '.tripMap', 'rb') as file:
			self.tripMap = pickle.load(file)
		with open(filename + '.stopEventsOfTrip', 'rb') as file:
			self.stopEventsOfTrip = pickle.load(file)
		with open(filename + '.stopSequenceOfRoute', 'rb') as file:
			self.stopSequenceOfRoute = pickle.load(file)
		with open(filename + '.firstTripOfRoute', 'rb') as file:
			self.firstTripOfRoute = pickle.load(file)
		# Load shapes
		try:
			with open(filename + '.shapes', 'rb') as file:
				self.shapes = pickle.load(file)
			with open(filename + '.routeShapes', 'rb') as file:
				self.routeShapes = pickle.load(file)
		except FileNotFoundError:
			print("Shape data not found in cache. Please re-initialize.")
		
		self.buildSpatialIndex()

	def readGTFS(self):
		for dirName in self.directoryNames:
			agency = os.path.basename(dirName)
			if '_' in agency:
				parts = agency.split('_')
				if len(parts) > 1:
					agency = parts[1]
			
			print(f"Reading GTFS from {dirName} (Agency: {agency})...")

			self.__readStops(dirName, agency)
			self.__readRoutes(dirName, agency)
			self.__readShapes(dirName, agency)
			self.__readTrips(dirName, agency)
			self.__readTransfers(dirName, agency)
			self.__readStopTimes(dirName, agency)
		
		# Post-processing: Sort trips in all routes
		print("Sorting trips within routes...")
		for route in range(self.numberOfRoutes()):
			lowerTrip = self.getFirstTripOfRoute(route)
			upperTrip = self.getLastTripOfRoute(route)
			if lowerTrip < upperTrip and upperTrip <= len(self.stopEventsOfTrip):
				copy = self.stopEventsOfTrip[lowerTrip:upperTrip][:]
				copy.sort(key=lambda x: (x[0].depTime, x[0].arrTime))
				self.stopEventsOfTrip[lowerTrip:upperTrip] = copy[:]

		# Sort transfers
		self.transfers.append(Transfer(float("inf"), float("inf"), float("inf")))
		self.transfers.sort(key=lambda x: x.fromStopId)

		# Build spatial index for nearby stop search
		self.buildSpatialIndex()

	def buildSpatialIndex(self):
		print("Building spatial index for stops...")
		if len(self.stopLats) == 0:
			return
		
		# Convert to float array
		lats = np.array(self.stopLats, dtype=float)
		lons = np.array(self.stopLons, dtype=float)
		self.stopCoordsArr = np.column_stack((lats, lons))
		
		if KDTree:
			self.kdTree = KDTree(self.stopCoordsArr)
		else:
			print("Scipy KDTree not available, using simple numpy fallback.")

	def findNearestStop(self, lat, lon, max_distance_km=2.0):
		"""Finds the nearest stop within a certain distance."""
		if self.stopCoordsArr is None:
			return None
		
		query_pt = np.array([float(lat), float(lon)])
		
		if self.kdTree:
			dist, idx = self.kdTree.query(query_pt)
			# Dist is in degrees approx, let's just check raw distance for now
			# A more accurate check would use Haversine, but KDTree query is fast
			# 0.01 deg is ~1km
			if dist < 0.1: # Loose check
				return self.stops[idx]
		else:
			# Simple numpy fallback
			dists = np.sum((self.stopCoordsArr - query_pt)**2, axis=1)
			idx = np.argmin(dists)
			return self.stops[idx]
		
		return None

	def findStopsNear(self, lat, lon, radius_km=1.0, limit=5):
		"""Finds multiple stops near a location."""
		if self.stopCoordsArr is None:
			return []
		
		query_pt = np.array([float(lat), float(lon)])
		# Approx radius in degrees (1 degree lat ~= 111km, 1 degree lon ~= 111km * cos(lat))
		# For small radii, this approximation is fine
		radius_deg = radius_km / 111.0 # Very rough
		
		if self.kdTree:
			indices = self.kdTree.query_ball_point(query_pt, radius_deg)
			# Sort by distance
			if not indices: return []
			dists = np.sum((self.stopCoordsArr[indices] - query_pt)**2, axis=1)
			sorted_indices = [indices[i] for i in np.argsort(dists)]
			return [self.stops[i] for i in sorted_indices[:limit]]
		else:
			dists = np.sum((self.stopCoordsArr - query_pt)**2, axis=1)
			mask = dists < (radius_deg**2)
			indices = np.where(mask)[0]
			if len(indices) == 0: return []
			sorted_indices = indices[np.argsort(dists[indices])]
			return [self.stops[i] for i in sorted_indices[:limit]]


	def __readShapes(self, dirName, agency):
		print(f"Reading shapes.txt for {agency}...")
		try:
			with open(dirName + "/shapes.txt", "r", encoding="utf-8") as csvFile:
				reader = csv.reader(csvFile, skipinitialspace=True)
				header = next(reader)
				try:
					shapeIdIndex = header.index("shape_id")
					latIndex = header.index("shape_pt_lat")
					lonIndex = header.index("shape_pt_lon")
					seqIndex = header.index("shape_pt_sequence")
				except ValueError:
					print("shapes.txt missing required columns")
					return

				# Read all points
				temp_shapes = {} # id -> list of (seq, lat, lon)
				for line in reader:
					shape_id = agency + ":" + line[shapeIdIndex]
					lat = float(line[latIndex])
					lon = float(line[lonIndex])
					seq = int(line[seqIndex])
					
					if shape_id not in temp_shapes:
						temp_shapes[shape_id] = []
					temp_shapes[shape_id].append((seq, lat, lon))
				
				# Sort and store
				for shape_id, points in temp_shapes.items():
					points.sort(key=lambda x: x[0])
					self.shapes[shape_id] = [(p[1], p[2]) for p in points]
		except FileNotFoundError:
			print(f"shapes.txt not found for {agency}, shapes will not be available.")

	def __readStops(self, dirName, agency):
		with open(dirName + "/stops.txt", "r", encoding="utf-8") as csvFile:
			reader = csv.reader(csvFile, skipinitialspace=True)
			
			stopIdIndex = -1
			stopNameIndex = -1
			stopLatIndex = -1
			stopLonIndex = -1
			currentIndex = len(self.stops) # Append to existing

			for line in reader:
				if (stopIdIndex == -1):
					stopIdIndex = line.index("stop_id")
					stopNameIndex = line.index("stop_name")
					stopLatIndex = line.index("stop_lat")
					stopLonIndex = line.index("stop_lon")
				else:
					stop_id = agency + ":" + line[stopIdIndex]
					self.stopMap[stop_id] = currentIndex
					self.stops.append(stop_id)
					self.stopNames.append(line[stopNameIndex])
					self.stopLats.append(line[stopLatIndex])
					self.stopLons.append(line[stopLonIndex])
					self.routesOperatingAtStops.append([])
					currentIndex += 1

	def __readTransfers(self, dirName, agency):
		try:
			with open(dirName + "/transfers.txt", "r", encoding="utf-8") as csvFile:
				reader = csv.reader(csvFile, skipinitialspace=True)
				
				fromStopIdIndex = -1
				toStopIdIndex = -1
				durationIndex = -1
				transferTypeIndex = -1

				for line in reader:
					if (fromStopIdIndex == -1):
						fromStopIdIndex = line.index("from_stop_id")
						toStopIdIndex = line.index("to_stop_id")
						durationIndex = line.index("min_transfer_time")
						transferTypeIndex = line.index("transfer_type")
					else:
						if (int(line[transferTypeIndex]) == 2):
							fromStop = agency + ":" + line[fromStopIdIndex]
							toStop = agency + ":" + line[toStopIdIndex]
							if fromStop in self.stopMap and toStop in self.stopMap:
								self.transfers.append(Transfer(self.stopMap[fromStop], self.stopMap[toStop], int(line[durationIndex])))
		except FileNotFoundError:
			print(f"transfers.txt not found for {agency}, skipping transfers.")

	def __readRoutes(self, dirName, agency):
		with open(dirName + "/routes.txt", "r", encoding="utf-8") as csvFile:
			reader = csv.reader(csvFile, skipinitialspace=True)
			
			routeIdIndex = -1

			currentIndex = len(self.gtfsRoutes)

			for line in reader:
				if (routeIdIndex == -1):
					routeIdIndex = line.index("route_id")
				else:
					rid = agency + ":" + line[routeIdIndex]
					self.gtfsRoutes.append(rid)
					self.routeMap[rid] = currentIndex

					currentIndex += 1

	def __readTrips(self, dirName, agency):
		with open(dirName + "/trips.txt", "r", encoding="utf-8") as csvFile:
			reader = csv.reader(csvFile, skipinitialspace=True)

			routeIdIndex = -1
			tripIdIndex = -1
			shapeIdIndex = -1
			
			for line in reader:
				if (routeIdIndex == -1):
					routeIdIndex = line.index("route_id")
					tripIdIndex = line.index("trip_id")
					try:
						shapeIdIndex = line.index("shape_id")
					except:
						pass
				else:
					trip_id = agency + ":" + line[tripIdIndex]
					route_id = agency + ":" + line[routeIdIndex]
					
					if route_id in self.routeMap:
						self.tripOriginalRoute[trip_id] = self.routeMap[route_id]
						if shapeIdIndex != -1:
							self.tripShapes[trip_id] = agency + ":" + line[shapeIdIndex]

	def __readStopTimes(self, dirName, agency):
		stopSequenceMap = {}

		with open(dirName + "/stop_times.txt", "r", encoding="utf-8") as csvFile:
			reader = csv.reader(csvFile, skipinitialspace=True)
			
			tripIdIndex = -1
			stopIdIndex = -1

			lastTripId = ""

			currentTripIndex = 0
			currentStopSeq = []

			for line in reader:
				if (tripIdIndex == -1):
					tripIdIndex = line.index("trip_id")
					stopIdIndex = line.index("stop_id")
				else:
					currentTrip = agency + ":" + line[tripIdIndex]
					currentStopId = agency + ":" + line[stopIdIndex]

					if currentStopId not in self.stopMap:
						continue

					if (lastTripId == currentTrip):
						currentStopSeq.append(self.stopMap[currentStopId])
					else:
						if (lastTripId == ""):
							lastTripId = currentTrip
						else:
							if (tuple(currentStopSeq) in stopSequenceMap.keys()):
								stopSequenceMap[tuple(currentStopSeq)].append(lastTripId)
							else:
								stopSequenceMap[tuple(currentStopSeq)] = [lastTripId]
							currentStopSeq = []
							lastTripId = currentTrip
							currentTripIndex += 1
			# last one
			if (tuple(currentStopSeq) in stopSequenceMap.keys()):
				stopSequenceMap[tuple(currentStopSeq)].append(lastTripId)
			else:
				stopSequenceMap[tuple(currentStopSeq)] = [lastTripId]
			
			if self.firstTripOfRoute:
				self.firstTripOfRoute.pop()

			routeId = len(self.stopSequenceOfRoute)
			tripIndex = len(self.trips)
			
			# Grow arrays
			self.trips.extend([0 for _ in range(currentTripIndex+1)])
			self.firstTripOfRoute.extend([0 for _ in range(len(stopSequenceMap) + 1)])
			self.routeShapes.extend([None for _ in range(len(stopSequenceMap))])

			for key in stopSequenceMap:
				self.firstTripOfRoute[routeId] = tripIndex
				stopSeq = list(key)
				for i, stop in enumerate(stopSeq):
					if stop < len(self.routesOperatingAtStops):
						self.routesOperatingAtStops[stop].append((routeId, i))
				
				first_trip = stopSequenceMap[key][0]
				if first_trip in self.tripOriginalRoute:
					# Map back to GTFS Route ID string using gtfsRoutes
					if self.tripOriginalRoute[first_trip] < len(self.gtfsRoutes):
						self.routes.append(self.gtfsRoutes[self.tripOriginalRoute[first_trip]])
					else:
						# Should not happen
						self.routes.append("UNKNOWN_ROUTE")
				else:
					self.routes.append("UNKNOWN")
				
				if first_trip in self.tripShapes:
					self.routeShapes[routeId] = self.tripShapes[first_trip]

				for tripId in stopSequenceMap[key]:
					self.trips[tripIndex] = routeId
					self.tripMap[tripId] = tripIndex 
					tripIndex += 1
				self.stopSequenceOfRoute.append(stopSeq)
				routeId += 1
			# sentinel
			self.firstTripOfRoute[routeId] = tripIndex

		with open(dirName + "/stop_times.txt", "r", encoding="utf-8") as csvFile:
			reader = csv.reader(csvFile, skipinitialspace=True)

			# Extend stop events
			self.stopEventsOfTrip.extend([[] for _ in range(currentTripIndex+1)])

			tripIdIndex = -1
			arrTimeIndex = -1
			depTimeIndex = -1

			for line in reader:
				if (tripIdIndex == -1):
					tripIdIndex = line.index("trip_id")
					arrTimeIndex = line.index("arrival_time")
					depTimeIndex = line.index("departure_time")
				else:
					currentTrip = agency + ":" + line[tripIdIndex]
					currentArrTime = line[arrTimeIndex]
					currentDepTime = line[depTimeIndex]

					if currentTrip in self.tripMap:
						idx = self.tripMap[currentTrip]
						self.stopEventsOfTrip[idx].append(StopEvent(stringHHMMSSToSeconds(currentDepTime), stringHHMMSSToSeconds(currentArrTime)))

		# We sort trips at the end of readGTFS now

	## Helper
	def getFirstTripOfRoute(self, route):
		return self.firstTripOfRoute[route]

	def lengthOfRoute(self, route):
		return len(self.stopSequenceOfRoute[route])

	def getLastTripOfRoute(self, route):
		return self.firstTripOfRoute[route+1]

	def firstTransferOfStop(self, stop):
		return bisect.bisect_left(KeyifyList(self.transfers, lambda x: x.fromStopId), stop)

	def lastTransferOfStop(self, stop):
		return bisect.bisect_right(KeyifyList(self.transfers, lambda x: x.fromStopId), stop)

	def routesContainingStop(self, stop):
		return self.routesOperatingAtStops[stop]

	## Query stuff
	def clear(self):
		self.earliestArrival = [float("inf") for _ in self.stops]
		self.rounds = [[EarliestArrivalLabel() for _ in self.stops]]
		self.stopsUpdated = IndexedSet(self.numberOfStops())
		self.stopsReached = IndexedSet(self.numberOfStops())
		self.routesServingUpdatedStops = IndexedHash(self.numberOfRoutes())

	def startNewRound(self):
		self.rounds.append([EarliestArrivalLabel() for _ in self.stops])

	def currentRound(self):
		return self.rounds[-1]

	def previousRound(self):
		return self.rounds[-2]

	def relaxTransfers(self):
		self.routesServingUpdatedStops.clear()
		stopsUpdatedElements = self.stopsUpdated.getElements()[:]
		for stop in stopsUpdatedElements:
			for trans in self.transfers[self.firstTransferOfStop(stop):self.lastTransferOfStop(stop)]:
				if (self.updateArrivalTime(trans.toStopId, self.currentRound()[stop].arrTime + trans.duration)):
					self.stopsReached.insert(trans.toStopId)
					self.currentRound()[trans.toStopId].parent = stop
					self.currentRound()[trans.toStopId].usesRoute = False
					self.currentRound()[trans.toStopId].parentDepTime = self.currentRound()[stop].arrTime
					self.currentRound()[trans.toStopId].routeId = trans

	def collectRoutesServingUpdatedStops(self):
		for stop in self.stopsUpdated.getElements():
			arrivalTime = self.previousRound()[stop].arrTime
			for (route, stopIndex) in self.routesContainingStop(stop):
				if (stopIndex + 1 == self.lengthOfRoute(route)):
					continue
				if (self.stopEventsOfTrip[self.getLastTripOfRoute(route)-1][stopIndex].depTime < arrivalTime):
					continue
				if (self.routesServingUpdatedStops.contains(route)):
					self.routesServingUpdatedStops.setAdditionalElement(route, min(self.routesServingUpdatedStops.getAdditionalElement(route), stopIndex))
				else:
					self.routesServingUpdatedStops.insert(route, stopIndex)

	def updateArrivalTime(self, stopId, time):
		if (self.earliestArrival[self.target] <= time):
			return False
		if (self.earliestArrival[stopId] <= time):
			return False
		self.earliestArrival[stopId] = time
		self.currentRound()[stopId].arrTime = time
		self.stopsUpdated.insert(stopId)
		return True

	def initialize(self, rangeQuery=False):
		self.clear()
		if (rangeQuery):
			self.updateArrivalTime(self.source, self.sourceDepTime)
			self.currentRound()[self.source].parentDepTime = self.sourceDepTime
		else:
			self.updateArrivalTime(self.source, self.depTime)
			self.currentRound()[self.source].parentDepTime = self.depTime
		self.currentRound()[self.source].parent = self.source
		self.currentRound()[self.source].usesRoute = False
		self.currentRound()[self.source].routeId = None

	def run(self, sourceGTFSId, targetGTFSId, depTime):
		self.source = self.stopMap[sourceGTFSId]
		self.target = self.stopMap[targetGTFSId]
		self.depTime = depTime
		
		self.initialize()
		
		k = 0
		maxRounds = 16
		while (k < maxRounds and not self.stopsUpdated.isEmpty()):
			self.relaxTransfers()

			self.startNewRound()
			# collect all routes
			self.collectRoutesServingUpdatedStops()

			# scan all route collected earlier
			self.scanRoutes()
			k += 1

	def scanRoutes(self):
		self.stopsUpdated.clear()
		for (route, index) in self.routesServingUpdatedStops.getElements():
			firstTrip = self.getFirstTripOfRoute(route)
			trip = self.getLastTripOfRoute(route) - 1

			currentStopIndex = index
			parentIndex = index
			stop = self.stopSequenceOfRoute[route][currentStopIndex]

			# loop over the stops
			while (currentStopIndex < self.lengthOfRoute(route) - 1):
				# find trip to "hop on"
				while (trip > firstTrip and self.stopEventsOfTrip[(trip-1)][currentStopIndex].depTime >= self.previousRound()[stop].arrTime):
					trip -= 1
					parentIndex = currentStopIndex
				
				currentStopIndex += 1
				stop = self.stopSequenceOfRoute[route][currentStopIndex]

				if (self.updateArrivalTime(stop, self.stopEventsOfTrip[trip][currentStopIndex].arrTime)):
					self.stopsReached.insert(stop)
					self.currentRound()[stop].parent = self.stopSequenceOfRoute[route][parentIndex]
					self.currentRound()[stop].usesRoute = True
					self.currentRound()[stop].parentDepTime = self.stopEventsOfTrip[trip][parentIndex].depTime
					self.currentRound()[stop].routeId = route

				currentStopIndex += 1


	def getResult(self):
		result = []
		bestArrTime = float("inf")
		for i in range(len(self.rounds)):
			if (self.rounds[i][self.target].arrTime < bestArrTime):
				bestArrTime = self.rounds[i][self.target].arrTime
				result.append([i, self.rounds[i][self.target]])
		return result

	def getAllJourneys(self):
		journeys = {}
		for i in range(len(self.rounds)):
			if (self.rounds[i][self.target].arrTime == float("inf")):
				continue
			journeys[i] = self.getJourney(i, self.target)
		return journeys

	def get_sliced_shape(self, shape_id, from_lat, from_lon, to_lat, to_lon):
		if not shape_id or shape_id not in self.shapes:
			return []
		
		full_shape = self.shapes[shape_id]
		if not full_shape:
			return []

		# Find closest point indices
		# Simple Euclidean distance is sufficient for finding the closest point in this context
		def dist_sq(p1, lat, lon):
			return (p1[0] - lat)**2 + (p1[1] - lon)**2
		
		start_idx = 0
		min_start_dist = float('inf')
		
		end_idx = len(full_shape) - 1
		min_end_dist = float('inf')

		# Optimization: assume shapes are relatively sequential. But stops might be far from shape points if data is bad.
		# We'll just scan all for correctness.
		for i, pt in enumerate(full_shape):
			d_start = dist_sq(pt, from_lat, from_lon)
			if d_start < min_start_dist:
				min_start_dist = d_start
				start_idx = i
			
			d_end = dist_sq(pt, to_lat, to_lon)
			if d_end < min_end_dist:
				min_end_dist = d_end
				end_idx = i
		
		# If start is after end (wrong direction), swap or handle?
		# Transit shapes are directional. If start > end, it's problematic or wrapped. 
		# But we know the stops are ordered.
		if start_idx <= end_idx:
			return full_shape[start_idx : end_idx + 1]
		else:
			# If found indices are reversed, it implies either we matched wrong points or the shape loop is weird.
			# But RAPTOR guarantees we move forward in time/stops. 
			# In a loop, it might happen. For now, let's just return the segment as is if valid? No, if start > end this returns empty.
			# Let's try to assume we just want the path between them.
			return []

	def transformEAToJourney(self, ea, currentStop):
		j = {
			"DepartureTime": secondsToHHMMSSString(ea.parentDepTime),
			"ArrivalTime": secondsToHHMMSSString(ea.arrTime),
			"FromStop": str(self.stopNames[ea.parent]),
			"FromStopId": str(self.stops[ea.parent]),
			"FromStopCoords": {"lat": self.stopLats[ea.parent], "lon": self.stopLons[ea.parent]},
			"ToStop": str(self.stopNames[currentStop]),
			"ToStopId": str(self.stops[currentStop]),
			"ToStopCoords": {"lat": self.stopLats[currentStop], "lon": self.stopLons[currentStop]}
		}
		if (ea.usesRoute):
			j["RouteId"] = self.routes[ea.routeId]
			# Shape Logic
			shape_id = self.routeShapes[ea.routeId]
			if shape_id:
				from_lat = float(self.stopLats[ea.parent])
				from_lon = float(self.stopLons[ea.parent])
				to_lat = float(self.stopLats[currentStop])
				to_lon = float(self.stopLons[currentStop])
				
				j["Shape"] = self.get_sliced_shape(shape_id, from_lat, from_lon, to_lat, to_lon)
		return j

	def getJourney(self, roundIndex, stop):
		if (self.rounds[roundIndex][stop].arrTime == float("inf")):
			return []

		currentStop = stop
		ea = self.rounds[roundIndex][stop]

		journey = []
		journey.append(self.transformEAToJourney(ea, currentStop))
		currentStop = ea.parent

		while (currentStop != self.source and roundIndex > 0):
			if (currentStop == -1):
				break
			if (ea.usesRoute):
				roundIndex -= 1
			ea = self.rounds[roundIndex][currentStop]
			journey.append(self.transformEAToJourney(ea, currentStop))
			currentStop = ea.parent
		return journey[::-1]

