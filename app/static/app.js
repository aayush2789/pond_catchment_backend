/* Village Pond Planning System - frontend.
   The frontend performs NO terrain/hydrology/rainfall/runoff calculations:
   it sends the selected GeoJSON to the backend and renders the response. */

"use strict";

const map = L.map("map").setView([21.2475, 81.293], 14);

const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
});
const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics" }
);
osm.addTo(map);
L.control.layers({ "OpenStreetMap": osm, "Satellite (Esri)": satellite }).addTo(map);

/* --- state ---------------------------------------------------------------- */

let drawMode = false;
let vertices = [];
let draftLine = null;
let landPolygon = null;
let landGeometry = null;
let catchmentLayer = null;
let pondMarker = null;
let contoursLayer = null;

/* --- element refs --------------------------------------------------------- */

const el = (id) => document.getElementById(id);
const startBtn = el("startDraw");
const finishBtn = el("finishDraw");
const clearBtn = el("clearAll");
const analyzeBtn = el("analyzeBtn");
const statusBox = el("status");
const statusText = el("statusText");
const errorBox = el("error");
const resultsCard = el("results");
const showContours = el("showContours");

/* --- polygon drawing (click or double-click = one vertex; Finish button closes) --- */

function redrawDraft() {
  if (draftLine) map.removeLayer(draftLine);
  if (vertices.length >= 1) {
    draftLine = L.polyline(vertices, { color: "#2563eb", dashArray: "4 4" }).addTo(map);
  }
}

function startDrawing() {
  clearAll();
  drawMode = true;
  vertices = [];
  map.doubleClickZoom.disable();
  startBtn.disabled = true;
  finishBtn.disabled = false;
  el("drawHint").textContent = "Click (or double-click) to add vertices; press Finish to close. Minimum 3 vertices.";
}

function finishDrawing() {
  // Drop consecutive duplicate vertices (e.g. from rapid clicking) before validation.
  vertices = vertices.filter(
    (v, i) => i === 0 || v[0] !== vertices[i - 1][0] || v[1] !== vertices[i - 1][1]
  );
  if (!drawMode || vertices.length < 3) {
    showError(
      `A polygon needs at least 3 distinct vertices (currently ${vertices.length}). Keep clicking on the map to add them, then press Finish.`
    );
    return;
  }
  drawMode = false;
  map.doubleClickZoom.enable();
  if (draftLine) { map.removeLayer(draftLine); draftLine = null; }

  const ring = vertices.map((v) => [v[1], v[0]]); // GeoJSON [lon, lat]
  ring.push([ring[0][0], ring[0][1]]); // close the ring (GeoJSON requirement)

  landGeometry = { type: "Polygon", coordinates: [ring] };
  landPolygon = L.polygon(vertices, { color: "#2563eb", weight: 2, fillOpacity: 0.15 }).addTo(map);

  startBtn.disabled = false;
  finishBtn.disabled = true;
  clearBtn.disabled = false;
  analyzeBtn.disabled = false;
  el("drawHint").textContent = "Land polygon selected. Ready to analyze.";
}

function clearAll() {
  drawMode = false;
  map.doubleClickZoom.enable();
  vertices = [];
  if (draftLine) { map.removeLayer(draftLine); draftLine = null; }
  if (landPolygon) { map.removeLayer(landPolygon); landPolygon = null; }
  if (catchmentLayer) { map.removeLayer(catchmentLayer); catchmentLayer = null; }
  if (pondMarker) { map.removeLayer(pondMarker); pondMarker = null; }
  if (contoursLayer) { map.removeLayer(contoursLayer); contoursLayer = null; }
  landGeometry = null;
  startBtn.disabled = false;
  finishBtn.disabled = true;
  clearBtn.disabled = true;
  analyzeBtn.disabled = true;
  resultsCard.hidden = true;
  errorBox.hidden = true;
  statusBox.hidden = true;
  el("drawHint").textContent = "Click (or double-click) to add vertices, then press Finish to close the polygon. Minimum 3 vertices.";
}

let lastVertexTime = 0;

map.on("click", (e) => {
  if (!drawMode) return;
  // A double-click fires click -> click -> dblclick. Debounce the second click so
  // a double-click marks exactly ONE vertex; the polygon is closed only via Finish.
  const now = Date.now();
  if (now - lastVertexTime < 300) return;
  lastVertexTime = now;
  vertices.push([e.latlng.lat, e.latlng.lng]);
  redrawDraft();
});
startBtn.addEventListener("click", startDrawing);
finishBtn.addEventListener("click", finishDrawing);
clearBtn.addEventListener("click", clearAll);
showContours.addEventListener("change", () => {
  if (!contoursLayer) return;
  if (showContours.checked) map.addLayer(contoursLayer);
  else map.removeLayer(contoursLayer);
});

/* --- analysis request ------------------------------------------------------ */

function fmt(n, digits = 2) {
  return Number(n).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

async function runAnalysis() {
  if (!landGeometry) return;
  errorBox.hidden = true;
  statusBox.hidden = false;
  statusText.textContent = "Analyzing terrain, siting the pond, delineating the catchment, fetching rainfall…";
  analyzeBtn.disabled = true;

  try {
    const body = new FormData();
    body.append("request", JSON.stringify({ geometry: landGeometry }));

    const response = await fetch("/api/v1/analyzePondSite", { method: "POST", body });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      showError(`Analysis failed (HTTP ${response.status}):\n${detail}`);
      return;
    }
    renderResults(data);
  } catch (err) {
    showError(`Could not reach the backend: ${err.message}`);
  } finally {
    statusBox.hidden = true;
    analyzeBtn.disabled = false;
  }
}
analyzeBtn.addEventListener("click", runAnalysis);

/* --- rendering ------------------------------------------------------------- */

function renderResults(data) {
  if (catchmentLayer) map.removeLayer(catchmentLayer);
  if (pondMarker) map.removeLayer(pondMarker);
  if (contoursLayer) map.removeLayer(contoursLayer);

  // Catchment overlay (upstream contributing area; may extend beyond the land).
  if (data.catchment && data.catchment.boundary) {
    catchmentLayer = L.geoJSON(data.catchment.boundary, {
      style: { color: "#16a34a", weight: 2, dashArray: "6 4", fillOpacity: 0.1 },
    }).addTo(map);
  }

  // Pond marker.
  if (data.pond) {
    pondMarker = L.circleMarker([data.pond.latitude, data.pond.longitude], {
      radius: 9,
      color: "#dc2626",
      fillColor: "#dc2626",
      fillOpacity: 0.9,
    })
      .bindTooltip(`Suggested pond site`, { permanent: false })
      .addTo(map);
  }

  // Contours.
  let contourCount = 0;
  if (data.contours && Array.isArray(data.contours.features)) {
    contoursLayer = L.layerGroup(
      data.contours.features.map((f) =>
        L.polyline(
          f.geometry.coordinates.map((c) => [c[1], c[0]]),
          { color: "#6b7280", weight: 1, opacity: 0.55 }
        ).bindTooltip(`${f.properties.elevation_m} m`, { sticky: true })
      )
    );
    contourCount = data.contours.features.length;
    if (showContours.checked) contoursLayer.addTo(map);
  }

  // Fit to the catchment if available (visualizes land ⊂ catchment), else the land.
  const fitLayer = catchmentLayer || landPolygon;
  if (fitLayer) map.fitBounds(fitLayer.getBounds().pad(0.15));

  const land = data.selected_land;
  el("resLand").textContent = land
    ? `${fmt(land.area_m2, 0)} m² (${fmt(land.area_hectares, 3)} ha)`
    : "Not applicable (KML terrain input).";

  el("resPond").textContent = data.pond
    ? `Latitude ${fmt(data.pond.latitude, 6)}, longitude ${fmt(data.pond.longitude, 6)} — elevation ${fmt(data.pond.elevation, 1)} m, slope ${fmt(data.pond.slope_degrees, 1)}°, suitability ${fmt(data.pond.suitability_score, 3)}`
    : "No pond site found.";

  el("resCatchment").textContent = data.catchment
    ? `${fmt(data.catchment.catchment_area_sq_meters, 0)} m² (${fmt(data.catchment.catchment_area_hectares, 3)} ha), ${data.catchment.contributing_cells_count} contributing grid cells`
    : "No catchment delineated.";

  el("resRainfall").textContent = data.rainfall
    ? `${fmt(data.rainfall.rainfall_mm, 1)} mm/year — ${data.rainfall.period} — source: ${data.rainfall.source} (${data.rainfall.dataset})`
    : "Rainfall unavailable.";

  el("resWater").textContent = data.water
    ? `Runoff coefficient ${data.water.runoff_coefficient} → theoretical runoff ${fmt(data.water.theoretical_runoff_m3, 1)} m³; collection efficiency ${data.water.collection_efficiency} → expected collectible water ${fmt(data.water.expected_collectible_water_m3, 1)} m³`
    : "Water estimation unavailable.";

  el("resStorage").textContent = data.pond_storage
    ? `Conceptual basin ~${fmt(data.pond_storage.storage_capacity_m3, 1)} m³: depth ${fmt(data.pond_storage.depth_m, 1)} m, top ${fmt(data.pond_storage.top_length_m, 1)} × ${fmt(data.pond_storage.top_width_m, 1)} m, surface ${fmt(data.pond_storage.surface_area_m2, 0)} m² — ${data.pond_storage.note}`
    : "Storage sizing unavailable.";

  const sources = [];
  if (data.dem_source) {
    sources.push(`DEM: ${data.dem_source.provider} (${data.dem_source.dataset}).`);
    if (data.dem_source.attribution) sources.push(data.dem_source.attribution);
  }
  if (data.rainfall && data.rainfall.attribution) sources.push(data.rainfall.attribution);
  sources.push(`${contourCount} contour lines derived from the DEM (interval ${data.contours ? data.contours.properties.interval_m : "n/a"} m).`);
  el("resSources").textContent = sources.join(" ");

  resultsCard.hidden = false;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}
