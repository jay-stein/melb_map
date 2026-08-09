/* Streetwise — static port of streets.py.
 * One suburb per game: 5 rounds. Each round shows a clue about the person or
 * thing one of the suburb's streets is named after; pick the namesake from 3
 * options (2 attempts). Hints cost points but reveal a tidbit. Solving a
 * round flips the street card + explainer. After 5 rounds the theme and the
 * suburb are revealed with the suburb's mascot/vibe, plus a share grid.
 * Scoring mirrors streets.py: first-try 100, second-try 50, hint halves. */

const $ = (sel) => document.querySelector(sel);

const ROUNDS_PER_GAME = 5;
const ATTEMPTS = 2;
const POINTS_FIRST = 100;
const POINTS_SECOND = 50;

const SQUARES = {
  first: "🟩", second: "🟨", hint_first: "🟦", hint_second: "🟦", fail: "⬛",
};

let THEMES = null;    // data/street_themes.json
let SUBURBS = {};     // data/suburbs.json (mascot/vibe payoff)

let suburb = null;
let puzzle = null;
let rounds = [];
let idx = 0;
let attempts = 0;
let hintUsed = false;
let points = 0;
let results = [];
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

function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* --- game setup ----------------------------------------------------------- */

function newGame() {
  const names = Object.keys(THEMES);
  suburb = pick(names);
  puzzle = pick(THEMES[suburb].puzzles);
  rounds = puzzle.rounds.map((r) => ({ ...r, options: shuffle(r.options) }));
  idx = 0;
  attempts = 0;
  hintUsed = false;
  points = 0;
  results = [];
  gameOver = false;
}

function roundValue(attempt) {
  const v = attempt === 1 ? POINTS_FIRST : POINTS_SECOND;
  return hintUsed ? v / 2 : v;
}

/* --- rendering ------------------------------------------------------------ */

function renderBackground() {
  $("#background").innerHTML = `<p class="streetwise-bg-text">${esc(puzzle.background)}</p>`;
}

function streetCard(r, full) {
  return `<div class="street-card${full ? " street-card-full" : ""}">` +
    `<div class="street-name">📍 ${esc(r.street)}</div>` +
    `<div class="street-namesake">named after ${esc(r.namesake)}</div>` +
    (full ? `<p class="street-explainer">${esc(r.explainer)}</p>` : "") +
    `</div>`;
}

function solvedCards() {
  return rounds.slice(0, idx).map((r) => streetCard(r, false)).join("");
}

function optionButton(opt, i, locked) {
  return `<button class="street-opt" id="opt-${i}" data-opt="${i}" ` +
    `${locked ? "disabled" : ""}>${esc(opt)}</button>`;
}

function renderPlay(feedbackHtml, solved) {
  if (gameOver) {
    renderFinale();
    return;
  }
  const r = rounds[idx];
  const locked = solved || attempts >= ATTEMPTS;
  const nextVal = roundValue(Math.min(attempts + 1, ATTEMPTS));

  let hintHtml = "";
  if (hintUsed) {
    hintHtml = `<div class="hint-tidbit">💡 ${esc(r.tidbit)}</div>`;
  } else if (!locked) {
    hintHtml = `<div class="hint-row"><button class="hint-btn" id="hint-btn">` +
      `💡 Hint — halves this round's points</button>` +
      `<span class="hint-value">round worth ${nextVal}</span></div>`;
  }

  $("#streets-play").innerHTML =
    `<div class="round-label">Round ${idx + 1}/${ROUNDS_PER_GAME}</div>` +
    `<p class="street-intro">One of the mystery suburb's streets is named after…</p>` +
    `<div class="clue-card clue-new"><div class="clue-text">${esc(r.clue)}</div></div>` +
    `<div class="street-options">` +
    r.options.map((o, i) => optionButton(o, i, locked)).join("") +
    `</div>` +
    hintHtml +
    (feedbackHtml || "") +
    (solved ? streetCard(r, true) : "") +
    (solved ? `<button class="next-btn" id="next-btn">Next street →</button>` : "");
}

function mascotPayoff() {
  const d = SUBURBS[suburb] || {};
  const m = d.mascot || {};
  let html = "";
  if (d.vibe) html += `<p class="streetwise-vibe">${esc(d.vibe)}</p>`;
  if (m.name) {
    html += `<div class="streetwise-mascot"><div class="mascot-name">${esc(m.name)}</div>`;
    if (m.tagline) html += `<div class="mascot-tagline">“${esc(m.tagline)}”</div>`;
    if (m.description) html += `<p class="mascot-desc">${esc(m.description)}</p>`;
    html += `</div>`;
  }
  return html;
}

function emojiGrid() {
  const row = results.map((res) => SQUARES[res.state]).join("");
  return [
    `Streetwise ${points}/${ROUNDS_PER_GAME * POINTS_FIRST}`,
    row,
    puzzle.reveal,
    "melb-map · Streetwise",
  ].join("\n");
}

function renderFinale() {
  const grid = emojiGrid();
  $("#streets-play").innerHTML =
    `<h2 class="finale-title">It was ${esc(suburb)}!</h2>` +
    `<p class="finale-reveal">${esc(puzzle.reveal)}</p>` +
    mascotPayoff() +
    `<div class="street-cards-label">streets in the mystery suburb:</div>` +
    solvedCards() +
    `<div class="finale-score">Score: ${points}/${ROUNDS_PER_GAME * POINTS_FIRST}</div>` +
    `<pre class="grid-pre">${esc(grid)}</pre>` +
    `<div class="result-actions">` +
    `<button class="play-again" id="play-again-btn">Play again</button>` +
    `<button class="play-again" id="share-btn">Share 📋</button>` +
    `<span id="share-status"></span></div>`;
  $("#share-btn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(grid);
      $("#share-status").textContent = "Copied!";
    } catch (e) {
      $("#share-status").textContent = "Copy failed — select manually";
    }
  });
  $("#play-again-btn").addEventListener("click", () => {
    newGame();
    renderBackground();
    renderPlay(null, false);
  });
}

/* --- actions -------------------------------------------------------------- */

function onOption(i) {
  if (gameOver || attempts >= ATTEMPTS) return;
  const r = rounds[idx];
  const picked = r.options[i];
  attempts++;
  let feedback;
  if (picked === r.namesake) {
    const pts = roundValue(attempts);
    points += pts;
    results.push({
      state: (hintUsed ? "hint_" : "") + (attempts === 1 ? "first" : "second"),
      points: pts,
    });
    feedback = `<div class="fb fb-correct">✓ ${esc(r.namesake)} — +${pts}</div>`;
    renderPlay(feedback, true);
  } else if (attempts >= ATTEMPTS) {
    results.push({ state: "fail", points: 0 });
    feedback = `<div class="fb fb-wrong">✗ Out of attempts — it was ${esc(r.namesake)}.</div>`;
    renderPlay(feedback, true);
  } else {
    feedback = `<div class="fb fb-hint">✗ Not that one — ${ATTEMPTS - attempts} attempt(s) left.</div>`;
    renderPlay(feedback, false);
  }
}

function onHint() {
  if (hintUsed || attempts >= ATTEMPTS || gameOver) return;
  hintUsed = true;
  renderPlay(null, false);
}

function onNext() {
  if (gameOver) return;
  idx++;
  attempts = 0;
  hintUsed = false;
  if (idx >= ROUNDS_PER_GAME) {
    gameOver = true;
    renderPlay(null, true);
  } else {
    renderPlay(null, false);
  }
}

$("#streets-play").addEventListener("click", (e) => {
  const t = e.target;
  if (t.id === "next-btn") onNext();
  else if (t.id === "hint-btn") onHint();
  else if (t.dataset.opt !== undefined) onOption(Number(t.dataset.opt));
});

/* --- boot ----------------------------------------------------------------- */

async function main() {
  THEMES = await fetchJSON("data/street_themes.json");
  try {
    SUBURBS = await fetchJSON("data/suburbs.json");
  } catch (e) { /* mascot payoff is optional */ }
  newGame();
  renderBackground();
  renderPlay(null, false);
}

main().catch((e) => {
  console.error(e);
  $("#streets-play").innerHTML = `<p class="empty">Failed to load: ${esc(e.message)}</p>`;
});
