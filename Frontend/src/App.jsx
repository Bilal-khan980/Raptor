import React, { useState, useEffect, useMemo, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import 'mapbox-gl/dist/mapbox-gl.css';

// START: MAPBOX TOKEN
const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
if (!MAPBOX_TOKEN) {
    console.error("Mapbox Token not found. Please set VITE_MAPBOX_TOKEN in your .env file.");
}
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
    // Get current time in California (America/Los_Angeles)
    const getCalifTime = () => {
        const now = new Date();
        const califDate = new Date(now.toLocaleString("en-US", { timeZone: "America/Los_Angeles" }));
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(califDate.getHours())}:${pad(califDate.getMinutes())}:${pad(califDate.getSeconds())}`;
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
        // Sort by Earliest Departure, then Duration
        data.sort((a, b) => {
            const getMetrics = (j) => {
                if (!j || !j.length) return { dep: 999999, dur: 999999 };
                const parse = (t) => {
                    const [h, m, s] = t.split(':').map(Number);
                    return h * 3600 + m * 60 + (s || 0);
                };
                let sSec = parse(j[0].DepartureTime);
                let eSec = parse(j[j.length - 1].ArrivalTime);
                if (eSec < sSec) eSec += 86400; // Handle midnight wrap
                return { dep: sSec, dur: eSec - sSec };
            };
            
            const mA = getMetrics(a);
            const mB = getMetrics(b);
            
            // Primary: Departure Time (Earliest first)
            if (mA.dep !== mB.dep) return mA.dep - mB.dep;
            
            // Secondary: Duration (Shortest first)
            return mA.dur - mB.dur;
        });

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

  // Time & Status State
  const [times, setTimes] = useState({ 
      calif: '', 
      local: '', 
      range: '', 
      lastSynced: '--:--', 
      dataWindow: '--:-- to --:--' 
  });

  // Time & Status Logic extracted to component scope to avoid nested hooks
  const updateTime = useRef(() => {}).current; // Placeholder if needed, but better to just define function
  
  // We use functional state updates, so these don't strictly need to be dependencies, 
  // but let's define them inside the component body, not inside a useEffect.
  
  const updateTimeLogic = () => {
      const now = new Date();
      // Robust way:
      const califDate = new Date(now.toLocaleString("en-US", { timeZone: "America/Los_Angeles" }));
      
      // Pad helper
      const pad = (n) => String(n).padStart(2, '0');
      const h = califDate.getHours();
      const m = califDate.getMinutes();
      const s = califDate.getSeconds();
      
      const califTime = `${pad(h)}:${pad(m)}:${pad(s)}`;

      // Search Window: current to +1 hour
      const endH = (h + 1) % 24;
      const windowRange = `${pad(h)}:${pad(m)} - ${pad(endH)}:${pad(m)}`;

      setTimes(prev => ({
          ...prev,
          calif: califTime,
          local: now.toTimeString().split(' ')[0],
          range: windowRange
      }));
  };

  const fetchStatusLogic = async () => {
      try {
          const res = await axios.get(`${API_BASE}/status`);
          if (res.data) {
              const { last_synced_hour, trip_window_start, trip_window_end } = res.data;
              setTimes(prev => ({
                  ...prev,
                  lastSynced: `${String(last_synced_hour).padStart(2,'0')}:00`,
                  dataWindow: `${trip_window_start} - ${trip_window_end}`
              }));
          }
      } catch (e) {
          console.error("Status fetch failed", e);
      }
  };

  // Initial load and Interval Effect
  useEffect(() => {
    updateTimeLogic();
    fetchStatusLogic();

    const timer = setInterval(updateTimeLogic, 1000);
    const statusTimer = setInterval(fetchStatusLogic, 30000); // Poll status every 30s
    
    return () => {
        clearInterval(timer);
        clearInterval(statusTimer);
    };
  }, []);

  // Socket.IO Integration Effect
  useEffect(() => {
      import('socket.io-client').then(({ io }) => {
          const socket = io('http://127.0.0.1:5001', {
              transports: ['websocket'],
              reconnection: true,
          });

          socket.on('connect', () => {
              console.log('Connected to socket server');
          });

          socket.on('sync_complete', (data) => {
              console.log('Sync Complete Event Received:', data);
              // Refresh status and map data immediately
              fetchStatusLogic();
              
              // Let's re-fetch geojson manually and set it
              axios.get(`${API_BASE}/all-stops-geojson`).then(res => {
                  if (map.current) {
                      const source = map.current.getSource('all-stops');
                      if (source) {
                          source.setData(res.data);
                      }
                  }
              }).catch(err => console.error("Failed to refresh stops after sync", err));
          });
          
          return () => {
              socket.disconnect();
          };
      });
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
                <div className="time-row">
                    <span className="time-label">Trip Data Window:</span>
                    <span className="time-value">{times.dataWindow}</span>
                </div>
                <div className="time-row">
                    <span className="time-label">Last Hourly Sync:</span>
                    <span className="time-value">{times.lastSynced}</span>
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



        <div className="button-group">
          <button className="search-button" onClick={handleSearch} disabled={loading || !sourceId || !targetId}>
            {loading ? 'Searching...' : 'Find Route'}
          </button>

          <button className="clear-button" onClick={handleClear}>
            Clear
          </button>
        </div>

        {error && <div className="error">
            <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
            {error}
        </div>}

        <div className="journey-list">
          {journeys && journeys.map((journey, idx) => {
            const startTimeString = journey[0]?.DepartureTime;
            const endTimeString = journey[journey.length-1]?.ArrivalTime;
            const isActive = idx === selectedJourneyIndex;
            const transitLegs = journey.filter(step => step.RouteId);
            const transferCount = transitLegs.length - 1;

            // Total Duration Calculation with Midnight Wrap Fix
            const parseToSec = (t) => {
                if (!t) return 0;
                const [h, m, s] = t.split(':').map(Number);
                return h * 3600 + m * (60) + (s || 0);
            };
            
            let startSec = parseToSec(startTimeString);
            let endSec = parseToSec(endTimeString);
            
            // If arrival is earlier than departure, it crossed midnight
            if (endSec < startSec) {
                endSec += 86400; // Add 24 hours
            }
            
            const totalSec = endSec - startSec;
            const durationMin = Math.round(totalSec / 60);
            
            return (
              <motion.div 
                key={idx}
                className={`journey-card ${isActive ? 'active' : ''}`}
                onClick={() => setSelectedJourneyIndex(idx)}
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.04 }}
              >
                <div className="journey-card-inner">
                  <div className="journey-summary-main">
                    {/* Time & Duration Row */}
                    <div className="summary-header">
                       <div className="time-range">
                          <span className="t-start">{startTimeString?.slice(0,5)}</span>
                          <div className="t-arrow">→</div>
                          <span className="t-end">{endTimeString?.slice(0,5)}</span>
                       </div>
                       <div className="duration-pill">{durationMin} min</div>
                    </div>

                    {/* From/To Node Preview */}
                    <div className="summary-locations">
                        <div className="loc-node">
                            <div className="loc-marker origin"></div>
                            <span className="loc-name">{journey[0].FromStop}</span>
                        </div>
                        <div className="loc-node">
                            <div className="loc-marker destination"></div>
                            <span className="loc-name">{journey[journey.length-1].ToStop}</span>
                        </div>
                    </div>
                  </div>
                  
                  <div className="journey-route-preview">
                    {transitLegs.map((step, sIdx) => (
                        <React.Fragment key={sIdx}>
                            <div className="route-pill">
                                <span className="route-code">{step.RouteId}</span>
                                <span className="agency-code">{step.ToStopId.split(':')[0]}</span>
                            </div>
                            {sIdx < transitLegs.length - 1 && <div className="transfer-dot"></div>}
                        </React.Fragment>
                    ))}
                    <div className="transfer-summary">
                        {transferCount === 0 ? 'Direct' : `${transferCount} Transfer${transferCount > 1 ? 's' : ''}`}
                    </div>
                  </div>
                </div>

                <AnimatePresence>
                  {isActive && (
                    <motion.div 
                        className="journey-details-drawer"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                    >
                        <div className="details-scroll-content">
                            {journey.map((step, sIdx) => (
                                <div key={sIdx} className="itinerary-node">
                                    {/* The Stop Point */}
                                    <div className="node-marker-row">
                                        <div className="node-time">{step.DepartureTime.slice(0,5)}</div>
                                        <div className="node-connector">
                                            <div className={`node-dot ${sIdx === 0 ? 'origin' : 'transfer'}`}></div>
                                            <div className="node-line-down"></div>
                                        </div>
                                        <div className="node-stop-name">{step.FromStop}</div>
                                    </div>

                                    {/* The Leg Content (Transit/Walk) */}
                                    <div className="node-leg-row">
                                        <div className="node-time-spacer"></div>
                                        <div className="node-connector-spacer">
                                            <div className={`leg-line-visual ${step.RouteId ? 'transit' : 'walk'}`}></div>
                                        </div>
                                        <div className="node-leg-card">
                                            <div className="leg-main">
                                                <div className="leg-icon">
                                                    {step.RouteId ? (
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M6 20l-1 2M19 20l1 2M4 12h16M8 8V6a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                                                    ) : (
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M13 4v2M13 18v2M12 9l-4 4 4 4"/></svg>
                                                    )}
                                                </div>
                                                <div className="leg-info-group">
                                                    <div className="leg-title">
                                                        {step.RouteId ? `${step.RouteId} to ${step.ToStop}` : `Walk to ${step.ToStop}`}
                                                    </div>
                                                    {step.RouteId && (
                                                        <div className="leg-subtitle">
                                                            {step.ToStopId.split(':')[0]} Agency • Trip #{step.RouteLongId.split(':').pop()}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Final Exit Node for the very last step */}
                                    {sIdx === journey.length - 1 && (
                                        <div className="node-marker-row exit">
                                            <div className="node-time">{step.ArrivalTime.slice(0,5)}</div>
                                            <div className="node-connector">
                                                <div className="node-dot destination"></div>
                                            </div>
                                            <div className="node-stop-name">{step.ToStop}</div>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
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
