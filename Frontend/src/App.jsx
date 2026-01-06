import React, { useState, useEffect, useMemo, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import 'mapbox-gl/dist/mapbox-gl.css';

// START: MAPBOX TOKEN
const MAPBOX_TOKEN = 'pk.eyJ1IjoiYWhtYWQtYXNsYW0iLCJhIjoiY2xpaW5iYXI1MXJlNzNmcWY4Y3pxenlsYyJ9.Ua15JiMsRNy3GOcz9cn4dw';
mapboxgl.accessToken = MAPBOX_TOKEN;
// END: MAPBOX TOKEN

const API_BASE = 'http://127.0.0.1:5001/api';

function App() {
  const [sourceId, setSourceId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [targetName, setTargetName] = useState('');
  
  const [journeys, setJourneys] = useState(null);
  const [selectedJourneyIndex, setSelectedJourneyIndex] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Reducer-like logic for state to avoid closure issues in map events
  const stateRef = useRef({ sourceId: '', targetId: '' });
  useEffect(() => {
    stateRef.current = { sourceId, targetId };
  }, [sourceId, targetId]);

  const mapContainer = useRef(null);
  const map = useRef(null);
  const markersRef = useRef([]);

  // Initialize Mapbox Map
  useEffect(() => {
    if (map.current) return;

    map.current = new mapboxgl.Map({
      container: mapContainer.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [-122.27163, 37.803482],
      zoom: 10
    });

    map.current.on('load', () => {
        // Source for all transit stops
        map.current.addSource('all-stops', {
            type: 'geojson',
            data: `${API_BASE}/all-stops-geojson`,
            cluster: true,
            clusterMaxZoom: 14,
            clusterRadius: 50
        });

        // Layer for clusters
        map.current.addLayer({
            id: 'clusters',
            type: 'circle',
            source: 'all-stops',
            filter: ['has', 'point_count'],
            paint: {
                'circle-color': ['step', ['get', 'point_count'], '#51bbd6', 10, '#f1f075', 50, '#f28cb1'],
                'circle-radius': ['step', ['get', 'point_count'], 15, 10, 20, 50, 25],
                'circle-opacity': 0.6,
                'circle-stroke-width': 1,
                'circle-stroke-color': '#fff'
            }
        });

        map.current.addLayer({
            id: 'cluster-count',
            type: 'symbol',
            source: 'all-stops',
            filter: ['has', 'point_count'],
            layout: {
                'text-field': '{point_count_abbreviated}',
                'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
                'text-size': 12
            },
            paint: { 'text-color': '#000' }
        });

        // Layer for individual stops
        map.current.addLayer({
            id: 'unclustered-point',
            type: 'circle',
            source: 'all-stops',
            filter: ['!', ['has', 'point_count']],
            paint: {
                'circle-color': '#11b4da',
                'circle-radius': 6,
                'circle-stroke-width': 2,
                'circle-stroke-color': '#fff'
            }
        });

        // Hover effect for stops
        map.current.on('mouseenter', 'unclustered-point', () => {
            map.current.getCanvas().style.cursor = 'pointer';
        });
        map.current.on('mouseleave', 'unclustered-point', () => {
            map.current.getCanvas().style.cursor = '';
        });

        // Route Source
        map.current.addSource('route-source', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });

        map.current.addLayer({
            id: 'route-layer',
            type: 'line',
            source: 'route-source',
            paint: {
                'line-color': ['case', ['get', 'isWalk'], '#888', '#00f2fe'],
                'line-width': 5,
                'line-opacity': 0.8,
                'line-dasharray': ['case', ['get', 'isWalk'], [2, 2], [1, 0]]
            },
            layout: { 'line-join': 'round', 'line-cap': 'round' }
        });

        map.current.addLayer({
            id: 'route-layer-glow',
            type: 'line',
            source: 'route-source',
            paint: {
                'line-color': '#4facfe',
                'line-width': 12,
                'line-opacity': 0.2,
                'line-blur': 8
            },
            filter: ['!', ['get', 'isWalk']]
        });

        // Event listener for station selection
        map.current.on('click', 'unclustered-point', (e) => {
            const feature = e.features[0];
            const { id, name, agency } = feature.properties;
            const displayName = `${name} (${agency})`;

            const { sourceId: currentSourceId, targetId: currentTargetId } = stateRef.current;

            if (!currentSourceId) {
                setSourceId(id);
                setSourceName(displayName);
            } else if (!currentTargetId) {
                setTargetId(id);
                setTargetName(displayName);
            } else {
                // Both set, replace target
                setTargetId(id);
                setTargetName(displayName);
            }
        });
    });
  }, []);

  const handleClear = () => {
      setSourceId('');
      setSourceName('');
      setTargetId('');
      setTargetName('');
      setJourneys(null);
      setSelectedJourneyIndex(null);
      setError(null);
      
      const sourceObj = map.current.getSource('route-source');
      if (sourceObj) sourceObj.setData({ type: 'FeatureCollection', features: [] });
      markersRef.current.forEach(m => m.remove());
      markersRef.current = [];
  };

  const getTimeString = (date) => {
      const h = String(date.getHours()).padStart(2, '0');
      const m = String(date.getMinutes()).padStart(2, '0');
      const s = String(date.getSeconds()).padStart(2, '0');
      return `${h}:${m}:${s}`;
  };

  const handleSearch = async () => {
    if (!sourceId || !targetId) return;
    setLoading(true);
    setError(null);
    setJourneys(null);
    setSelectedJourneyIndex(null);

    // Get current time in California (America/Los_Angeles)
    const getCalifTime = () => {
        const now = new Date();
        const califStr = now.toLocaleString("en-US", { timeZone: "America/Los_Angeles", hour12: false });
        // Format: "MM/DD/YYYY, HH:MM:SS"
        const timePart = califStr.split(', ')[1]; 
        return timePart; // HH:MM:SS
    };

    const earliest = getCalifTime();
    console.log("Searching from California current time:", earliest);

    try {
      const response = await axios.get(`${API_BASE}/route`, {
        params: { 
            source: sourceId, 
            target: targetId, 
            earliest_dep: earliest
        }
      });
      const data = response.data;
      if (data && data.length > 0) {
        setJourneys(data);
        setSelectedJourneyIndex(0);
        
        // Fly to source
        const firstStep = data[0][0];
        if (firstStep && firstStep.FromStopCoords) {
             map.current?.flyTo({
                center: [parseFloat(firstStep.FromStopCoords.lon), parseFloat(firstStep.FromStopCoords.lat)],
                zoom: 12,
                duration: 2000,
                essential: true
            });
        }
      } else {
        setError("No optimal routes found. Try increasing max transfers or picking different stops.");
      }
    } catch (err) {
      console.error(err);
      setError("Failed to calculate route. Ensure the backend server is active.");
    } finally {
      setLoading(false);
    }
  };

  const selectedJourney = useMemo(() => {
    if (journeys && selectedJourneyIndex !== null) {
      return journeys[selectedJourneyIndex];
    }
    return null;
  }, [journeys, selectedJourneyIndex]);

  const routeGeoJSON = useMemo(() => {
    if (!selectedJourney) return null;
    const features = selectedJourney.map((step, index) => {
      const start = [parseFloat(step.FromStopCoords.lon), parseFloat(step.FromStopCoords.lat)];
      const end = [parseFloat(step.ToStopCoords.lon), parseFloat(step.ToStopCoords.lat)];
      let coordinates = [start, end];
      if (step.Shape && step.Shape.length > 0) {
          coordinates = step.Shape.map(pt => [pt[1], pt[0]]);
      }
      return {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates },
        properties: { isWalk: !step.RouteId }
      };
    });
    return { type: 'FeatureCollection', features };
  }, [selectedJourney]);

  useEffect(() => {
      if (!map.current) return;
      const sourceObj = map.current.getSource('route-source');
      if (sourceObj) sourceObj.setData(routeGeoJSON || { type: 'FeatureCollection', features: [] });

      markersRef.current.forEach(m => m.remove());
      markersRef.current = [];
      if (!selectedJourney) return;

      const stopsInJourney = [];
      const seen = new Set();
      
      // Explicitly mark source and target
      selectedJourney.forEach((step, idx) => {
          const isFirst = idx === 0;
          const isLast = idx === selectedJourney.length - 1;

          if (!seen.has(step.FromStopId)) {
              stopsInJourney.push({
                  id: step.FromStopId, name: step.FromStop, coords: step.FromStopCoords,
                  type: isFirst ? 'source' : 'transfer'
              });
              seen.add(step.FromStopId);
          }
          
          if (!seen.has(step.ToStopId)) {
                stopsInJourney.push({
                  id: step.ToStopId, name: step.ToStop, coords: step.ToStopCoords,
                  type: isLast ? 'target' : 'transfer'
              });
              seen.add(step.ToStopId);
          } else if (isLast) {
              // Ensure the final destination always gets the 'target' class
              const s = stopsInJourney.find(s => s.id === step.ToStopId);
              if (s) s.type = 'target';
          }
      });

      stopsInJourney.forEach(m => {
          const el = document.createElement('div');
          el.className = `marker ${m.type}`;
          el.style.width = '14px';
          el.style.height = '14px';

          const marker = new mapboxgl.Marker({ element: el })
              .setLngLat([parseFloat(m.coords.lon), parseFloat(m.coords.lat)])
              .setPopup(new mapboxgl.Popup({ offset: 15 }).setHTML(`<b>${m.name}</b>`))
              .addTo(map.current);
          markersRef.current.push(marker);
      });
  }, [selectedJourney, routeGeoJSON]);

  // Time State for UI
  const [times, setTimes] = useState({ calif: '', local: '', range: '' });

  useEffect(() => {
    const updateTime = () => {
        const now = new Date();
        const califStr = now.toLocaleString("en-US", { timeZone: "America/Los_Angeles", hour12: false });
        const califTime = califStr.split(', ')[1];
        
        // Window Calculation
        const [h, m, s] = califTime.split(':').map(Number);
        const endH = (h + 1) % 24;
        const pad = (n) => String(n).padStart(2, '0');
        const windowRange = `${pad(h)}:${pad(m)} - ${pad(endH)}:${pad(m)}`;

        setTimes({
            calif: califTime,
            local: now.toTimeString().split(' ')[0],
            range: windowRange
        });
    };
    updateTime();
    const timer = setInterval(updateTime, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="app-container">
      <div className="sidebar">
        <motion.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }}>
            <h1>RAPTOR</h1>
            
            <div className="time-display-container">
                <div className="time-row">
                    <span className="time-label">California (PT):</span>
                    <span className="time-value accent">{times.calif}</span>
                </div>
                <div className="time-row">
                    <span className="time-label">Search Window:</span>
                    <span className="time-value">{times.range}</span>
                </div>
                <div className="time-row small">
                    <span className="time-label">Your Local Time:</span>
                    <span className="time-value">{times.local}</span>
                </div>
            </div>
        </motion.div>
        
        <div className="form-group">
          <label>Source Location</label>
          <input 
            type="text" 
            value={sourceName} 
            placeholder="Click a station on the map"
            readOnly 
            className="readonly-input"
          />
        </div>

        <div className="form-group">
          <label>Target Location</label>
          <input 
            type="text" 
            value={targetName} 
            placeholder="Select a destination station"
            readOnly 
            className="readonly-input"
          />
        </div>



        <button className="search-button" onClick={handleSearch} disabled={loading || !sourceId || !targetId}>
          {loading ? 'Finding Best Connection...' : 'Find Route'}
        </button>

        <button className="clear-button" onClick={handleClear}>
          Clear Selection
        </button>

        {error && <div className="error">
            <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
            {error}
        </div>}

        <div className="journey-list">
          {journeys && journeys.map((journey, idx) => {
            const startTime = journey[0]?.DepartureTime;
            const endTime = journey[journey.length-1]?.ArrivalTime;
            const isActive = idx === selectedJourneyIndex;
            const transferCount = journey.filter(step => step.RouteId).length - 1;

            // Total Duration Calculation
            const parseToSec = (t) => {
                const [h, m, s] = t.split(':').map(Number);
                return h * 3600 + m * 60 + s;
            };
            const totalSec = parseToSec(endTime) - parseToSec(startTime);
            const durationMin = Math.round(totalSec / 60);
            
            return (
              <motion.div 
                key={idx}
                className={`journey-card ${isActive ? 'active' : ''}`}
                onClick={() => setSelectedJourneyIndex(idx)}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.05 }}
              >
                <div className="journey-header">
                  <div className="journey-time">
                    {startTime?.slice(0,5)} <span style={{fontSize: '0.8rem', opacity: 0.5, verticalAlign: 'middle', margin: '0 4px'}}>→</span> {endTime?.slice(0,5)}
                  </div>
                  <div className="journey-meta">
                    <div className="journey-duration">{durationMin} min</div>
                    <div className="journey-transfers">
                        {transferCount === 0 ? 'Direct' : `${transferCount} Transfer${transferCount > 1 ? 's' : ''}`}
                    </div>
                  </div>
                </div>
                
                <div className="journey-legs-simple">
                  {journey.map((step, sIdx) => {
                      if (step.RouteId) {
                          return (
                            <div key={sIdx} className="leg-badge transit">
                                <span className="leg-route">{step.RouteId}</span>
                            </div>
                          );
                      }
                      return null;
                  })}
                </div>

                <AnimatePresence>
                  {isActive && (
                    <motion.div 
                        className="journey-details"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                    >
                        {journey.map((step, sIdx) => (
                            <div key={sIdx} className="detail-row">
                                <div className="detail-marker"></div>
                                <div className="detail-content">
                                    <div className="detail-path">
                                        <span>Board {step.FromStop}</span>
                                        <span className="time">{step.DepartureTime.slice(0,5)}</span>
                                    </div>
                                    
                                    <div className={`detail-leg ${step.RouteId ? 'transit' : 'walk'}`}>
                                        <div className="leg-info">
                                            {step.RouteId ? (
                                                <>
                                                    <span className="route-name">{step.RouteId} ({step.ToStopId.split(':')[0]})</span>
                                                    <span className="trip-id">Trip: {step.RouteLongId}</span>
                                                </>
                                            ) : (
                                                <span className="walk-info">Transfer / Walking</span>
                                            )}
                                        </div>
                                    </div>

                                    <div className="detail-path end">
                                        <span>Alight {step.ToStop}</span>
                                        <span className="time">{step.ArrivalTime.slice(0,5)}</span>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </div>
      </div>

      <div className="map-container">
          <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
      </div>
    </div>
  );
}


export default App;
