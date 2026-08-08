/* Suburb Detective — trivia game. 5 rounds, 5 progressive clues per round.
 * Guess with fewer clues for a higher score. Trophy after 5 rounds.
 * Live map: light-grey base, dark outline for in-scope region,
 * wrong guesses glow red, correct suburb glows green. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const TOTAL_ROUNDS = 5;
const MAX_CLUES = 5;

let STATE = null;   // game-state.json (centroids, regions, order)
let SUBURBS = {};   // suburbs.json data
let BOUNDARIES = null;  // boundaries.geojson (cached)

let region = null;
let targets = [];         // 5 suburb names for this game
let round = 0;
let revealed = 0;
let clues = [];
let roundScores = [];
let gameOver = false;
let currentPool = [];

let guessTraceCount = 0;  // traces added beyond base + outline (reset each round)

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

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* --- clue generation (name-redacted) ------------------------------------ */

function escapeRegex(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function redact(text, suburb, nickname) {
  let r = text;
  const nameRE = new RegExp(escapeRegex(suburb), "gi");
  r = r.replace(nameRE, "this suburb");
  const possRE = new RegExp(escapeRegex(suburb) + "'s\\b", "gi");
  r = r.replace(possRE, "this suburb's");
  if (nickname) {
    const nnRE = new RegExp("\\b" + escapeRegex(nickname) + "\\b", "gi");
    r = r.replace(nnRE, "this suburb");
    const nnPossRE = new RegExp("\\b" + escapeRegex(nickname) + "'s\\b", "gi");
    r = r.replace(nnPossRE, "this suburb's");
  }
  return r;
}

function buildClues(suburb) {
  const d = SUBURBS[suburb];
  const nickname = d.nickname || "";
  const census = d.census || {};
  const c = [];
  const cat = esc(d.primary_category || "unknown");
  const nnBit = nickname ? ` (aka "${esc(nickname)}")` : "";
  const lang = (census.language && census.language[0]) ? census.language[0] : null;
  const birth = (census.birthplace && census.birthplace[0]) ? census.birthplace[0] : null;

  if (lang || birth) {
    const quirk = lang
      ? `in the top ${esc(lang.top_pct)}% of Melbourne suburbs for <b>${esc(lang.group)}</b> speakers`
      : `with an over-represented <b>${esc(birth.group)}</b>-born community (top ${esc(birth.top_pct)}%)`;
    c.push(`This <b>${cat}</b> suburb${nnBit} is ${quirk}.`);
  } else if (d.tags && d.tags.length) {
    c.push(`This <b>${cat}</b> suburb${nnBit} — the locals say: <i>"${esc(pick(d.tags))}"</i>`);
  } else {
    c.push(`This <b>${cat}</b> suburb${nnBit} has around ${(census.population || "?").toLocaleString()} residents.`);
  }

  if (d.tags && d.tags.length > 0) {
    c.push(`A local take: <i>"${esc(pick(d.tags))}"</i>`);
  } else if (census.born_overseas_pct != null) {
    c.push(`Around <b>${Math.round(census.born_overseas_pct)}%</b> of residents were born overseas.`);
  } else {
    c.push("The vibe is hard to pin down — but your intuition might help.");
  }

  if (d.vibe) {
    c.push(esc(redact(d.vibe, suburb, nickname)));
  } else {
    c.push("We don't have a vibe summary for this one.");
  }

  if (census.population || census.both_parents_overseas_pct != null || (census.ancestry && census.ancestry.length)) {
    const bits = [];
    if (census.population) bits.push(`<b>${census.population.toLocaleString()}</b> residents`);
    if (census.born_overseas_pct != null) bits.push(`<b>${Math.round(census.born_overseas_pct)}%</b> born overseas`);
    if (census.both_parents_overseas_pct != null) bits.push(`<b>${Math.round(census.both_parents_overseas_pct)}%</b> both parents overseas`);
    if (census.ancestry && census.ancestry.length) {
      bits.push(`heritage: ${census.ancestry.map((a) => esc(a.group)).join(", ")}`);
    }
    c.push(`Census: ${bits.join(" · ")}.`);
  } else {
    c.push("Census data doesn't reveal much — this one's a sleeper.");
  }

  if (d.lore && d.lore.length > 0) {
    c.push("Local lore: " + esc(redact(pick(d.lore), suburb, nickname)));
  } else if (d.history) {
    c.push("History: " + esc(redact(d.history.split(".")[0] + ".", suburb, nickname)));
  } else {
    c.push("We're out of clues — take your best shot!");
  }
  return c;
}

/* --- map ---------------------------------------------------------------- */

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

async function loadMap() {
  if (!BOUNDARIES) BOUNDARIES = await fetchJSON("data/boundaries.geojson");
  guessTraceCount = 0;
  const allNames = BOUNDARIES.features.map((f) => f.properties.suburb);
  const inScopeNames = region === "all" ? allNames : (STATE.regions[region] || []);
  const bounds = computeGeoBounds(BOUNDARIES);
  const dx = (bounds.lon[1] - bounds.lon[0]) * 0.04;
  const dy = (bounds.lat[1] - bounds.lat[0]) * 0.04;

  Plotly.newPlot("trivia-map", [
    {
      type: "choropleth",
      geojson: BOUNDARIES,
      locations: allNames,
      featureidkey: "properties.suburb",
      z: allNames.map(() => 0),
      colorscale: [[0, "#E4E6E8"], [1, "#E4E6E8"]],
      showscale: false,
      marker: { line: { color: "white", width: 0.5 } },
      hoverinfo: "skip",
    },
    {
      type: "choropleth",
      geojson: BOUNDARIES,
      locations: inScopeNames,
      featureidkey: "properties.suburb",
      z: inScopeNames.map(() => 0),
      colorscale: [[0, "rgba(255,255,255,0)"], [1, "rgba(255,255,255,0)"]],
      showscale: false,
      marker: { line: { color: "#37474F", width: 1.5 } },
      hoverinfo: "skip",
    },
  ], {
    margin: { l: 0, r: 0, t: 0, b: 0 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "#F2F3F4",
    showlegend: false,
    geo: {
      visible: false,
      projection_type: "mercator",
      lonaxis_range: [bounds.lon[0] - dx, bounds.lon[1] + dx],
      lataxis_range: [bounds.lat[0] - dy, bounds.lat[1] + dy],
      bgcolor: "#F2F3F4",
    },
    dragmode: false,
    height: 220,
    uirevision: "static",
  }, { displayModeBar: false, responsive: true });
}

function highlightGuess(suburb, correct) {
  if (!BOUNDARIES) return;
  const color = correct ? "#C8E6C9" : "#FFCDD2";
  const lineColor = correct ? "#43A047" : "#EF5350";
  const lineWidth = correct ? 2 : 1;
  Plotly.addTraces("trivia-map", [{
    type: "choropleth",
    geojson: BOUNDARIES,
    locations: [suburb],
    featureidkey: "properties.suburb",
    z: [1],
    colorscale: [[0, color], [1, color]],
    showscale: false,
    marker: { line: { color: lineColor, width: lineWidth } },
    hoverinfo: "skip",
  }], [2 + guessTraceCount]);  // append after base + outline + previous guesses
  guessTraceCount++;
}

function clearMapHighlights() {
  if (!BOUNDARIES || guessTraceCount === 0) return;
  const indices = [];
  for (let i = 0; i < guessTraceCount; i++) indices.push(2);
  Plotly.deleteTraces("trivia-map", indices);
  guessTraceCount = 0;
}

/* --- region screen ----------------------------------------------------- */

function showRegionScreen() {
  $("#region-screen").style.display = "block";
  $("#game-screen").style.display = "none";
  $("#results-screen").style.display = "none";

  const regions = STATE.regions;
  const allCount = Object.keys(SUBURBS).filter((s) => (SUBURBS[s].tags || []).length).length;
  const html = [];
  html.push(`<button class="region-btn" data-region="all">
    <span class="region-name">All</span><span class="region-count">${allCount} suburbs</span></button>`);
  for (const [name, names] of Object.entries(regions)) {
    const withTags = names.filter((s) => SUBURBS[s] && (SUBURBS[s].tags || []).length).length;
    html.push(`<button class="region-btn" data-region="${esc(name)}">
      <span class="region-name">${esc(name[0].toUpperCase() + name.slice(1))}</span>
      <span class="region-count">${withTags} suburbs</span></button>`);
  }
  $("#region-btns").innerHTML = html.join("");
  $$(".region-btn").forEach((btn) => {
    btn.addEventListener("click", () => startGame(btn.dataset.region));
  });
}

/* --- game flow ---------------------------------------------------------- */

async function startGame(selected) {
  region = selected;
  if (region === "all") {
    currentPool = Object.keys(SUBURBS).filter((s) => (SUBURBS[s].tags || []).length);
  } else {
    currentPool = (STATE.regions[region] || []).filter((s) => SUBURBS[s] && (SUBURBS[s].tags || []).length);
  }
  if (currentPool.length < TOTAL_ROUNDS) {
    alert(`Not enough suburbs with data in ${region} — try a different region.`);
    return;
  }
  targets = shuffle(currentPool).slice(0, TOTAL_ROUNDS);
  round = 0;
  roundScores = [];
  gameOver = false;

  await loadMap();

  $("#region-screen").style.display = "none";
  $("#game-screen").style.display = "block";
  $("#results-screen").style.display = "none";

  const allNames = shuffle(currentPool).sort();
  $("#suburb-list").innerHTML = allNames.map((n) => `<option value="${esc(n)}">`).join("");
  startRound();
}

function startRound() {
  revealed = 0;
  clues = buildClues(targets[round]);
  $("#round-label").textContent = `Round ${round + 1} of ${TOTAL_ROUNDS}`;
  $("#feedback").innerHTML = "";
  $("#reveal-btn").style.display = "block";
  $("#guess-btn").disabled = false;
  $("#guess").disabled = false;
  $("#guess").value = "";
  clearMapHighlights();
  showClue();
  $("#guess").focus();
}

function showClue() {
  let html = "";
  for (let i = 0; i < clues.length; i++) {
    if (i <= revealed) {
      html += `<div class="clue-card ${i === revealed ? "clue-new" : ""}">`;
      html += `<span class="clue-num">${i + 1}</span>`;
      html += `<span class="clue-text">${clues[i]}</span>`;
      html += `</div>`;
    }
  }
  $("#clues-area").innerHTML = html;

  const remaining = MAX_CLUES - 1 - revealed;
  if (remaining > 0) {
    $("#reveal-btn").textContent = `Show next clue (${remaining} left)`;
    $("#clue-cost").textContent = `Fewer clues used = better score`;
    $("#reveal-btn").style.display = "block";
    $("#clue-cost").style.display = "block";
  } else {
    $("#reveal-btn").style.display = "none";
    $("#clue-cost").textContent = "Last clue — make your guess!";
    $("#clue-cost").style.display = "block";
  }
}

function makeGuess(name) {
  if (!name || gameOver) return;
  const target = targets[round];

  if (name === target) {
    highlightGuess(name, true);
    const score = MAX_CLUES - revealed;
    roundScores.push(score);
    renderFeedback(true, target, score);
    $("#reveal-btn").style.display = "none";
    $("#clue-cost").style.display = "none";
    $("#guess-btn").disabled = true;
    $("#guess").disabled = true;
    round++;
    if (round >= TOTAL_ROUNDS) {
      setTimeout(showResults, 1200);
    } else {
      setTimeout(startRound, 1500);
    }
  } else {
    highlightGuess(name, false);
    if (revealed < MAX_CLUES - 1) {
      revealed++;
      showClue();
      renderFeedback(false);
    } else {
      highlightGuess(target, true);
      roundScores.push(0);
      renderFeedback(false, target);
      $("#reveal-btn").style.display = "none";
      $("#clue-cost").style.display = "none";
      $("#guess-btn").disabled = true;
      $("#guess").disabled = true;
      round++;
      if (round >= TOTAL_ROUNDS) {
        setTimeout(showResults, 1200);
      } else {
        setTimeout(startRound, 1500);
      }
    }
  }
}

function renderFeedback(correct, answer, score) {
  if (correct) {
    $("#feedback").innerHTML =
      `<div class="feedback-correct">✓ Correct! It's <b>${esc(answer)}</b> (+${score} pts)</div>`;
  } else if (answer) {
    $("#feedback").innerHTML =
      `<div class="feedback-wrong">✗ It was <b>${esc(answer)}</b> — 0 pts</div>`;
  } else {
    $("#feedback").innerHTML =
      `<div class="feedback-wrong">✗ Try again — a new clue has been revealed</div>`;
  }
}

/* --- results ------------------------------------------------------------ */

function showResults() {
  gameOver = true;
  $("#game-screen").style.display = "none";
  $("#results-screen").style.display = "block";

  const total = roundScores.reduce((a, b) => a + b, 0);
  const max = TOTAL_ROUNDS * MAX_CLUES;
  let trophy, trophyName;
  if (total === max) { trophy = "🏆🏆🏆"; trophyName = "Perfect Detective"; }
  else if (total >= 20) { trophy = "🏆"; trophyName = "Master Detective"; }
  else if (total >= 15) { trophy = "🥇"; trophyName = "Senior Detective"; }
  else if (total >= 10) { trophy = "🥈"; trophyName = "Detective"; }
  else if (total >= 5) { trophy = "🥉"; trophyName = "Junior Detective"; }
  else { trophy = "🔎"; trophyName = "Rookie"; }

  $("#trophy").innerHTML = `${trophy} ${trophyName}`;
  $("#score-summary").textContent = `You scored ${total}/${max} points.`;

  let roundHTML = "";
  for (let i = 0; i < TOTAL_ROUNDS; i++) {
    const s = roundScores[i];
    roundHTML += `<div class="round-result">
      <span class="rr-suburb">${esc(targets[i])}</span>
      <span class="rr-score">${s > 0 ? `${s} pts` : "missed"}</span>
    </div>`;
  }
  $("#round-results").innerHTML = roundHTML;

  const grid = buildShareGrid();
  $("#share-grid").textContent = grid;

  $("#share-btn").onclick = async () => {
    try { await navigator.clipboard.writeText(grid); alert("Copied to clipboard!"); }
    catch (e) { alert("Couldn't copy — here you go:\n\n" + grid); }
  };
  $("#play-again-btn").onclick = showRegionScreen;
}

function buildShareGrid() {
  const total = roundScores.reduce((a, b) => a + b, 0);
  const max = TOTAL_ROUNDS * MAX_CLUES;
  let trophy;
  if (total === max) trophy = "🏆🏆🏆";
  else if (total >= 20) trophy = "🏆";
  else if (total >= 15) trophy = "🥇";
  else if (total >= 10) trophy = "🥈";
  else if (total >= 5) trophy = "🥉";
  else trophy = "🔎";
  const regionLabel = region === "all" ? "All" : region[0].toUpperCase() + region.slice(1);
  const lines = [`Suburb Detective — ${regionLabel}`];
  lines.push(`Score: ${total}/${max} ${trophy}`);
  const squares = roundScores.map((s) => {
    if (s === 5) return "🟩🟩🟩🟩🟩";
    if (s === 4) return "🟩🟩🟩🟩⬛";
    if (s === 3) return "🟩🟩🟩⬛⬛";
    if (s === 2) return "🟩🟩⬛⬛⬛";
    if (s === 1) return "🟩⬛⬛⬛⬛";
    return "⬛⬛⬛⬛⬛";
  }).join(" ");
  lines.push(squares);
  lines.push("melb-map · Suburb Detective");
  return lines.join("\n");
}

/* --- init --------------------------------------------------------------- */

async function main() {
  [STATE, SUBURBS] = await Promise.all([
    fetchJSON("data/game-state.json"),
    fetchJSON("data/suburbs.json"),
  ]);
  showRegionScreen();

  const input = $("#guess");
  const btn = $("#guess-btn");
  btn.addEventListener("click", () => makeGuess(input.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") makeGuess(input.value); });
  $("#reveal-btn").addEventListener("click", () => {
    if (revealed < MAX_CLUES - 1) { revealed++; showClue(); }
  });
}

main().catch((e) => {
  console.error(e);
  document.body.innerHTML = `<p style="color:red;padding:40px">Failed to load: ${esc(e.message)}</p>`;
});
