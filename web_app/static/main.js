"use strict";

// -- tabs -----------------------------------------------------------------

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    // Leaflet needs a nudge after its container becomes visible.
    setTimeout(() => {
      if (townMap) townMap.invalidateSize();
      if (realworldMap) realworldMap.invalidateSize();
    }, 50);
  });
});

function setStatus(id, message, isError) {
  const el = document.getElementById(id);
  el.textContent = message || "";
  el.classList.toggle("error", !!isError);
}

// -- shared GeoJSON -> Leaflet layer helpers -------------------------------

function fieldLayer(geojson) {
  return L.geoJSON(geojson, {
    style: (feature) => ({
      fillColor: feature.properties.color,
      color: feature.properties.color,
      weight: 0,
      fillOpacity: 0.65,
    }),
  });
}

function pointsLayer(geojson, labelPrefix) {
  return L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) =>
      L.circleMarker(latlng, {
        radius: 6, color: "#222", weight: 1,
        fillColor: feature.properties.color, fillOpacity: 0.9,
      }),
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      const persistence = p.persistence === null ? "infinite (global extremum)" : p.persistence.toFixed(3);
      layer.bindPopup(
        `<b>${labelPrefix} - ${p.kind}</b><br>value: ${p.value.toFixed(3)}<br>persistence: ${persistence}`
      );
    },
  });
}

// -- Town Scenario ----------------------------------------------------------

let townMap;
let townLayerControl;
const townOverlays = {};
let townDynamicLayerNames = []; // layers added per-query (e.g. morse field/points) -- cleared each run

function addTownOverlay(name, layer, defaultOn) {
  if (townOverlays[name]) {
    townMap.removeLayer(townOverlays[name]);
  }
  townOverlays[name] = layer;
  if (defaultOn) layer.addTo(townMap);
  if (townLayerControl) townMap.removeControl(townLayerControl);
  townLayerControl = L.control.layers(null, townOverlays, { collapsed: false }).addTo(townMap);
}

// Removes only the layers added by the previous query (roads/zones/hazard/
// population are untouched) -- without this, switching topics (e.g. an
// elevation question followed by a hazard_intensity question) left the old
// field's heatmap and critical-point markers stuck on the map forever.
function clearTownDynamicOverlays() {
  townDynamicLayerNames.forEach((name) => {
    if (townOverlays[name]) {
      townMap.removeLayer(townOverlays[name]);
      delete townOverlays[name];
    }
  });
  townDynamicLayerNames = [];
  if (townLayerControl) townMap.removeControl(townLayerControl);
  townLayerControl = L.control.layers(null, townOverlays, { collapsed: false }).addTo(townMap);
}

async function initTownMap() {
  townMap = L.map("town-map", { crs: L.CRS.Simple });

  const res = await fetch("/api/town/base_map");
  const data = await res.json();
  townMap.fitBounds(data.bounds);

  const roads = L.geoJSON(data.roads, {
    style: (f) => f.properties.is_bridge_structure
      ? { color: "#c0392b", weight: 5 }
      : { color: "#888", weight: 2 },
  });
  const zones = L.geoJSON(data.zones, {
    style: () => ({ color: "#2b6cb0", weight: 1, fillOpacity: 0.1 }),
    onEachFeature: (f, layer) => layer.bindTooltip(f.properties.name),
  });
  const hazard = L.geoJSON(data.hazard_zone, {
    style: () => ({ color: "#c0392b", weight: 1, fillOpacity: 0.15, dashArray: "4" }),
  });
  const population = L.geoJSON(data.population, {
    pointToLayer: (f, latlng) =>
      L.circleMarker(latlng, {
        radius: 4 + Math.sqrt(f.properties.population) / 8,
        color: "#27632a", fillColor: "#4caf50", weight: 1, fillOpacity: 0.7,
      }),
    onEachFeature: (f, layer) => layer.bindPopup(`population: ${f.properties.population}`),
  });

  addTownOverlay("roads", roads, true);
  addTownOverlay("zones", zones, true);
  addTownOverlay("hazard zone", hazard, true);
  addTownOverlay("population", population, true);
}

function renderTownResult(result) {
  const box = document.getElementById("town-result");
  box.hidden = false;
  const label = document.getElementById("town-route-label");
  const rec = document.getElementById("town-recommendation");
  const feedbackBox = document.getElementById("town-feedback-box");

  clearTownDynamicOverlays(); // start every new query from a clean map, regardless of route

  if (result.route === "geometry") {
    label.textContent = "Geometry tool result";
    rec.textContent = JSON.stringify(result.tool_result, null, 2);
    feedbackBox.style.display = "none"; // matches CLI: geometry route has no human review step
  } else {
    label.textContent = result.route === "morse" ? "Morse topology recommendation" : "Topology recommendation";
    rec.textContent = result.recommendation || "(no recommendation returned)";
    feedbackBox.style.display = "";
  }

  if (result.route === "morse" && result.geojson) {
    const field = fieldLayer(result.geojson.field);
    const points = pointsLayer(result.geojson.points, result.field);
    const fieldName = result.field + " (field)";
    const pointsName = result.field + " (critical points)";
    addTownOverlay(fieldName, field, true);
    addTownOverlay(pointsName, points, true);
    townDynamicLayerNames.push(fieldName, pointsName);
  }
}

let currentTownQuery = "";

async function submitTownQuery(feedback) {
  const queryBox = document.getElementById("town-query");
  const query = feedback ? currentTownQuery : queryBox.value.trim();
  if (!query) {
    setStatus("town-status", "Enter a question first.", true);
    return;
  }
  currentTownQuery = query;

  setStatus("town-status", "Running pipeline (this can take a little while on a local model)...");
  document.getElementById("town-result").hidden = true;
  try {
    const res = await fetch("/api/town/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ original_query: query, feedback: feedback || "" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "request failed");
    renderTownResult(data);
    setStatus("town-status", "");
  } catch (err) {
    setStatus("town-status", "Error: " + err.message, true);
  }
}

document.getElementById("town-submit").addEventListener("click", () => submitTownQuery(null));
document.getElementById("town-rerun").addEventListener("click", () => {
  const feedback = document.getElementById("town-feedback").value.trim();
  if (!feedback) {
    setStatus("town-status", "Type feedback first, or click Accept.", true);
    return;
  }
  submitTownQuery(feedback);
});
document.getElementById("town-accept").addEventListener("click", () => {
  document.getElementById("town-feedback-box").style.display = "none";
  setStatus("town-status", "Recommendation accepted.");
});
document.querySelectorAll("#town-examples li").forEach((li) => {
  li.addEventListener("click", () => {
    document.getElementById("town-query").value = li.dataset.example;
  });
});

// -- Real-World Data ---------------------------------------------------------

let realworldMap;
let realworldTileLayer;
let realworldLayerControl;
const realworldOverlays = {};
let sqPreviewRect = null; // outline of the bounding box shown right after parsing, before analysis runs

function initRealworldMap() {
  realworldMap = L.map("realworld-map");
  realworldMap.setView([40.0, -105.3], 11);
  realworldTileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 19,
  }).addTo(realworldMap);
}

function clearRealworldOverlays() {
  Object.values(realworldOverlays).forEach((layer) => realworldMap.removeLayer(layer));
  for (const key in realworldOverlays) delete realworldOverlays[key];
  if (realworldLayerControl) realworldMap.removeControl(realworldLayerControl);
  realworldLayerControl = null;
}

// Switches the map to the resolved location as soon as it's known (right
// after Parse, before the user even clicks "Fetch live & analyze"), so
// they can see where the analysis will run and back out if it's wrong.
function previewBounds(bounds, popupText) {
  const [latMin, latMax, lonMin, lonMax] = bounds;
  const leafletBounds = [[latMin, lonMin], [latMax, lonMax]];
  if (sqPreviewRect) realworldMap.removeLayer(sqPreviewRect);
  sqPreviewRect = L.rectangle(leafletBounds, {
    color: "#2b6cb0", weight: 2, fillOpacity: 0.08, dashArray: "6",
  }).addTo(realworldMap);
  if (popupText) sqPreviewRect.bindPopup(popupText).openPopup();
  realworldMap.fitBounds(leafletBounds, { maxZoom: 13 });
}

let customLayerCount = 0;
document.getElementById("rw-add-layer").addEventListener("click", () => {
  customLayerCount += 1;
  const row = document.createElement("div");
  row.className = "custom-layer-row";
  row.innerHTML = `
    <input type="text" placeholder="layer name" class="rw-layer-name">
    <input type="file" accept=".csv,.zip,.shp" class="rw-layer-file">
    <input type="text" placeholder="what does this measure? (optional, helps the recommendation)" class="rw-layer-description">
    <button type="button" class="secondary rw-remove-layer">x</button>
  `;
  row.querySelector(".rw-remove-layer").addEventListener("click", () => row.remove());
  document.getElementById("rw-custom-layers").appendChild(row);
});

async function submitRealworldAnalyze() {
  const bounds = document.getElementById("rw-bounds").value.trim();
  const resolution = document.getElementById("rw-resolution").value;
  const variable = document.getElementById("rw-variable").value;
  const useSample = document.getElementById("rw-sample").checked;
  const recommend = document.getElementById("rw-recommend").checked;

  const formData = new FormData();
  formData.append("bounds", bounds);
  formData.append("resolution", resolution);
  formData.append("primary_variable", variable);
  formData.append("use_sample_layer", useSample ? "true" : "false");
  formData.append("recommend", recommend ? "true" : "false");

  document.querySelectorAll(".custom-layer-row").forEach((row) => {
    const name = row.querySelector(".rw-layer-name").value.trim();
    const file = row.querySelector(".rw-layer-file").files[0];
    const description = row.querySelector(".rw-layer-description").value.trim();
    if (name && file) {
      formData.append("layer_name", name);
      formData.append(`layer_file::${name}`, file);
      if (description) formData.append(`layer_description::${name}`, description);
    }
  });

  setStatus("rw-status", "Fetching real-world data and running Morse analysis...");
  document.getElementById("rw-result").hidden = true;
  try {
    const res = await fetch("/api/realworld/analyze", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "request failed");
    renderRealworldResult(data);
    setStatus("rw-status", "");
  } catch (err) {
    setStatus("rw-status", "Error: " + err.message, true);
  }
}

function renderRealworldResult(data) {
  realworldMap.fitBounds(data.bounds);
  clearRealworldOverlays();
  if (sqPreviewRect) {
    realworldMap.removeLayer(sqPreviewRect);
    sqPreviewRect = null;
  }

  const summaryContainer = document.getElementById("rw-layer-summaries");
  summaryContainer.innerHTML = "";

  let first = true;
  for (const [name, layer] of Object.entries(data.layers)) {
    const field = fieldLayer(layer.field);
    const points = pointsLayer(layer.points, name);
    realworldOverlays[name + " (field)"] = field;
    realworldOverlays[name + " (critical points)"] = points;
    if (first) {
      field.addTo(realworldMap);
      points.addTo(realworldMap);
      first = false;
    }

    const s = layer.summary;
    const euler = s.euler_characteristic;
    const div = document.createElement("div");
    div.className = "layer-summary";
    div.innerHTML = `<b>${name}</b><br>range: [${s.value_range[0].toFixed(2)}, ${s.value_range[1].toFixed(2)}]
      &nbsp;|&nbsp; significant pit basins: ${s.num_significant_basins}
      &nbsp;|&nbsp; significant peak basins: ${s.num_significant_peak_basins}
      &nbsp;|&nbsp; threshold: ${s.persistence_threshold.toFixed(3)}
      <br>domain Euler characteristic: ${euler.domain_euler_characteristic} (disk, expected 1)`;
    summaryContainer.appendChild(div);
  }

  realworldLayerControl = L.control.layers(null, realworldOverlays, { collapsed: false }).addTo(realworldMap);

  document.getElementById("rw-result").hidden = false;
  document.getElementById("rw-recommendation").textContent =
    data.recommendation || "(not requested)";
}

document.getElementById("rw-submit").addEventListener("click", submitRealworldAnalyze);

// -- Smart Query parsing (free-text -> fills in the fields above) -----------
//
// Parsing only ever fills in rw-bounds/rw-variable below and previews the
// area on the map -- it never fetches data itself. There is exactly one
// "Fetch & Analyze" button (submitRealworldAnalyze, above) and exactly one
// bounds/topic state (the rw-* fields), so there is no way to click a
// "run" action against a stale or different location than what was just
// parsed.

async function submitSmartQueryParse() {
  const query = document.getElementById("sq-query").value.trim();
  if (!query) {
    setStatus("sq-status", "Describe what to analyze first.", true);
    return;
  }
  document.getElementById("sq-clarify").hidden = true;
  setStatus("sq-status", "Parsing bounding box and topic...");

  const formData = new FormData();
  formData.append("query", query);

  try {
    const res = await fetch("/api/realworld/smart_query", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "request failed");
    renderSmartQueryClarify(data);
    setStatus("sq-status", "");
  } catch (err) {
    setStatus("sq-status", "Error: " + err.message, true);
  }
}

function renderSmartQueryClarify(data) {
  const [latMin, latMax, lonMin, lonMax] = data.bounds;
  const place = data.resolved_place;
  const placeLine = place
    ? `<br><b>Resolved place:</b> ${place.name}, ${place.admin1 || ""} ${place.country || ""} ` +
      `(${place.latitude.toFixed(4)}, ${place.longitude.toFixed(4)})`
    : "";

  previewBounds(
    data.bounds,
    place ? `${place.name}${place.admin1 ? ", " + place.admin1 : ""}` : "Bounding box to analyze"
  );

  document.getElementById("rw-bounds").value =
    [latMin, latMax, lonMin, lonMax].map((v) => v.toFixed(5)).join(",");
  document.getElementById("rw-variable").value = data.variable;

  document.getElementById("sq-clarify-summary").innerHTML =
    `<b>Bounding box:</b> ${latMin.toFixed(3)}, ${latMax.toFixed(3)}, ${lonMin.toFixed(3)}, ${lonMax.toFixed(3)}` +
    placeLine +
    `<br><b>Topic:</b> ${data.variable}` +
    `<br>Filled in below -- review and click "Fetch &amp; Analyze" when ready.`;
  document.getElementById("sq-clarify").hidden = false;
}

document.getElementById("sq-parse").addEventListener("click", submitSmartQueryParse);

// -- init ---------------------------------------------------------------

initTownMap();
initRealworldMap();
