/* Suburble — static port of suburble.py. The daily puzzle order, centroids and
 * max distance are exported from the Python code path (game-state.json), so the
 * daily target here is identical to the Dash app's.
 * Chiclet tiles replace the dropdown; a choropleth tracks guesses — wrong
 * guesses fill light red, the correct suburb glows green. */

const $ = (sel) => document.querySelector(sel);

const ARROWS = ["⬆️", "↗️", "➡️", "↘️", "⬇️", "↙️", "⬅️", "↖️"];
const NEUTRAL = "🎯";

let STATE = null;        // game-state.json
let BOUNDARIES = null;   // boundaries.geojson (FeatureCollection)
let TARGET = null;
let PUZZLE_NO = 0;
let GUESSES = [];
let gameOver = false;

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* Python's round() is round-half-even; JS Math.round is half-up. Match the
 * Python code path exactly so distances/proximities agree. */
function pyRound(n) {
  const d = Math.floor(n);
  const f = n - d;
  if (f < 0.5) return d;
  if (f > 0.5) return d + 1;
  return d % 2 === 0 ? d : d + 1;
}

function haversine(a, b) {
  const toRad = (x) => (x * Math.PI) / 180;
  const dlat = toRad(b[0] - a[0]);
  const dlon = toRad(b[1] - a[1]);
  const h = Math.sin(dlat / 2) ** 2 +
    Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dlon / 2) ** 2;
  return 2 * 6371.0 * Math.asin(Math.sqrt(h));
}

function bearing(frm, to) {
  const lat1 = (frm[0] * Math.PI) / 180;
  const lat2 = (to[0] * Math.PI) / 180;
  const dlon = ((to[1] - frm[1]) * Math.PI) / 180;
  const x = Math.sin(dlon) * Math.cos(lat2);
  const y = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dlon);
  const deg = (((Math.atan2(x, y) * 180) / Math.PI) + 360) % 360;
  return ARROWS[pyRound(deg / 45) % 8];
}

function proximityPct(km) {
  return Math.max(0, pyRound((100 * (STATE.maxDist - km)) / STATE.maxDist));
}

function squares(prox) {
  const filled = Math.max(0, Math.min(5, pyRound(prox / 20)));
  return "🟩".repeat(filled) + "⬛".repeat(5 - filled);
}

function emojiGrid(target) {
  const solved = GUESSES.length > 0 && GUESSES[GUESSES.length - 1] === target;
  const score = solved ? `${GUESSES.length}/${STATE.maxGuesses}` : `X/${STATE.maxGuesses}`;
  const lines = [`Suburble #${PUZZLE_NO} ${score}`];
  const tc = STATE.centroids[target];
  for (const g of GUESSES) {
    if (g === target) {
      lines.push("🟩🟩🟩🟩🟩🎉");
      continue;
    }
    const km = haversine(STATE.centroids[g], tc);
    lines.push(squares(proximityPct(km)) + bearing(STATE.centroids[g], tc));
  }
  lines.push("melb-map · Suburble");
  return lines.join("\n");
}

function dailyTarget() {
  const [y, m, d] = STATE.epoch.split("-").map(Number);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const epoch = new Date(y, m - 1, d);
  const puzzleNo = Math.round((today - epoch) / 86400000);
  return [puzzleNo, STATE.order[((puzzleNo % STATE.order.length) + STATE.order.length) % STATE.order.length]];
}

function renderSilhouette(suburb) {
  const feat = BOUNDARIES.features.find((f) => f.properties.suburb === suburb);
  const ring = feat.geometry.coordinates[0];
  const k = Math.cos((STATE.centroids[suburb][0] * Math.PI) / 180);
  const xs = ring.map((p) => p[0] * k);
  const ys = ring.map((p) => p[1]);
  const fig = {
    data: [{
      x: xs, y: ys, type: "scatter", fill: "toself", mode: "lines",
      line: { color: "#242424", width: 1.5 }, fillcolor: "#cfdaf5",
      hoverinfo: "skip",
    }],
    layout: {
      margin: { l: 0, r: 0, t: 0, b: 0 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      showlegend: false, height: 300,
      xaxis: { visible: false, scaleanchor: "y", scaleratio: 1 },
      yaxis: { visible: false },
      dragmode: false,
    },
  };
  Plotly.newPlot("silhouette", fig.data, fig.layout, {
    displayModeBar: false, staticPlot: true, responsive: true,
  });
}

/* --- guess-progress choropleth ------------------------------------------- */

function computeGeoBounds(geojson) {
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  for (const f of geojson.features) {
    for (const ring of f.geometry.coordinates) {
      for (const [lon, lat] of ring) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
    }
  }
  return { lon: [minLon, maxLon], lat: [minLat, maxLat] };
}

function buildMap() {
  const allNames = BOUNDARIES.features.map((f) => f.properties.suburb);
  const bounds = computeGeoBounds(BOUNDARIES);
  const dx = (bounds.lon[1] - bounds.lon[0]) * 0.04;
  const dy = (bounds.lat[1] - bounds.lat[0]) * 0.04;
  Plotly.newPlot("suburble-map", [
    {
      type: "choropleth",
      geojson: BOUNDARIES,
      locations: allNames,
      featureidkey: "properties.suburb",
      z: allNames.map(() => 0),
      colorscale: [[0, "#e7e3e0"], [1, "#e7e3e0"]],
      showscale: false,
      marker: { line: { color: "white", width: 0.5 } },
      hoverinfo: "skip",
    },
  ], {
    margin: { l: 0, r: 0, t: 0, b: 0 },
    paper_bgcolor: "#f6f3f1",
    plot_bgcolor: "#f6f3f1",
    showlegend: false,
    // plotly.js needs the nested form (projection.type, lonaxis.range) —
    // the flattened plotly.py keys are silently ignored.
    geo: {
      visible: false,
      projection: { type: "mercator" },
      lonaxis: { range: [bounds.lon[0] - dx, bounds.lon[1] + dx] },
      lataxis: { range: [bounds.lat[0] - dy, bounds.lat[1] + dy] },
      bgcolor: "#f6f3f1",
    },
    dragmode: false,
    height: 280,
    uirevision: "static",
  }, { displayModeBar: false, responsive: true });
}

function highlightSuburb(suburb, correct) {
  if (!BOUNDARIES) return;
  const color = correct ? "#d7fbe4" : "#ffd9cc";
  const lineColor = correct ? "#4cc98a" : "#ff9473";
  Plotly.addTraces("suburble-map", [{
    type: "choropleth",
    geojson: BOUNDARIES,
    locations: [suburb],
    featureidkey: "properties.suburb",
    z: [1],
    colorscale: [[0, color], [1, color]],
    showscale: false,
    marker: { line: { color: lineColor, width: correct ? 2 : 1 } },
    hoverinfo: "skip",
  }]);
}

/* --- chiclet tiles -------------------------------------------------------- */

function renderTiles() {
  const grid = $("#tiles");
  grid.innerHTML = "";
  const sorted = STATE.order.slice().sort();
  for (const name of sorted) {
    const tile = document.createElement("button");
    tile.className = "tile";
    tile.type = "button";
    tile.textContent = name;
    tile.dataset.suburb = name;
    tile.addEventListener("click", () => makeGuess(name));
    grid.appendChild(tile);
  }
}

function setTileState(suburb, state) {
  const tile = document.querySelector(`.tile[data-suburb="${CSS.escape(suburb)}"]`);
  if (!tile) return;
  tile.classList.remove("tile-wrong", "tile-correct");
  if (state === "wrong") {
    tile.classList.add("tile-wrong");
    tile.disabled = true;
  } else if (state === "correct") {
    tile.classList.add("tile-correct");
    tile.disabled = true;
  }
}

/* --- game flow ------------------------------------------------------------ */

function guessRow(suburb, target) {
  const gc = STATE.centroids[suburb];
  const tc = STATE.centroids[target];
  let km, arrow, prox, win;
  if (suburb === target) {
    km = 0.0; arrow = NEUTRAL; prox = 100; win = true;
  } else {
    km = haversine(gc, tc);
    arrow = bearing(gc, tc);
    prox = proximityPct(km);
    win = false;
  }
  return `<div class="guess-row-card${win ? " win" : ""}">` +
    `<span class="g-suburb">${esc(suburb)}</span>` +
    `<span class="g-km">${km.toFixed(1)} km</span>` +
    `<span class="g-arrow">${arrow}</span>` +
    `<span class="g-prox${prox >= 80 ? " hot" : ""}">${prox}%</span></div>`;
}

function renderRows(target) {
  $("#rows").innerHTML = GUESSES.map((g) => guessRow(g, target)).join("");
}

function renderResult(msg, grid) {
  $("#result").innerHTML =
    `<div class="result-msg">${esc(msg)}</div>` +
    `<button id="share-btn">Share 📋</button><span id="share-status"></span>` +
    `<pre class="grid-pre">${esc(grid)}</pre>`;
  $("#share-btn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(grid);
      $("#share-status").textContent = "Copied!";
    } catch (e) {
      $("#share-status").textContent = "Copy failed — select manually";
    }
  });
}

function makeGuess(name) {
  if (!name || gameOver) return;
  if (GUESSES.includes(name)) return;
  GUESSES.push(name);

  const solved = name === TARGET;
  highlightSuburb(name, solved);
  setTileState(name, solved ? "correct" : "wrong");
  renderRows(TARGET);

  const outOf = GUESSES.length >= STATE.maxGuesses && !solved;
  if (solved || outOf) {
    gameOver = true;
    const grid = emojiGrid(TARGET);
    renderResult(
      solved ? `🎉 Solved in ${GUESSES.length}/${STATE.maxGuesses}!` : `Out of guesses — it was ${TARGET}.`,
      grid,
    );
  }
}

async function main() {
  STATE = await fetchJSON("data/game-state.json");
  BOUNDARIES = await fetchJSON("data/boundaries.geojson");

  [PUZZLE_NO, TARGET] = dailyTarget();
  $("#puzzle-line").textContent = `#${PUZZLE_NO} · guess the mystery Melbourne suburb from its shape`;

  renderSilhouette(TARGET);
  buildMap();
  renderTiles();
}

main().catch((e) => {
  console.error(e);
  $("#result").innerHTML = `<p class="empty">Failed to load: ${esc(e.message)}</p>`;
});
