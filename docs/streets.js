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

/* Icon per theme for the theme-select chiclets (several LLM surface labels
 * map to the same icon, e.g. "English towns" vs "British towns/suburbs"). */
const THEME_ICONS = {
  "Literary Poets": "📜",
  "Native Flora": "🌿",
  "British Towns & Rivers": "🏰",
  "English towns/villages": "🏰",
  "British towns/suburbs": "🏰",
  "English towns": "🏰",
  "Prime Ministers": "🏛️",
  "Astronomy & Space": "🪐",
  "constellations/stars": "🪐",
  "Precious Gemstones": "💎",
  "Crimean War": "💣",
  "World War II battles and aircraft": "💣",
  "World War I battles": "💣",
  "Arthurian Legend": "🐉",
  "Elite English Schools": "🎓",
  "aircraft": "✈️",
  "Aviation Pioneers & Aircraft": "✈️",
  "Viticulture & Wine": "🍷",
  "Camera & Photography": "📷",
  "ANA Aviation Estate": "🛫",
  "Renaissance artists/writers": "🎨",
  "Golf Courses": "⛳",
};

let THEMES = null;    // data/street_themes.json
let SUBURBS = {};     // data/suburbs.json (mascot/vibe payoff)

let suburb = null;
let puzzle = null;
let rounds = [];
let themeFilter = null;
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

function themeIcon(label) {
  return THEME_ICONS[label] || "🧩";
}

function availableThemes() {
  const counts = {};
  for (const entry of Object.values(THEMES)) {
    for (const p of entry.puzzles) {
      counts[p.theme] = (counts[p.theme] || 0) + 1;
    }
  }
  return Object.entries(counts)
    .map(([label, n]) => ({ label, icon: themeIcon(label), n }))
    .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
}

function newGame(theme) {
  themeFilter = theme || null;
  const options = [];
  for (const [name, entry] of Object.entries(THEMES)) {
    for (const p of entry.puzzles) {
      if (!themeFilter || p.theme === themeFilter) options.push([name, p]);
    }
  }
  if (!options.length) throw new Error(`no puzzles for theme: ${theme}`);
  const [chosen, chosenPuzzle] = pick(options);
  suburb = chosen;
  puzzle = chosenPuzzle;
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

function renderSelect() {
  const themes = availableThemes();
  const total = themes.reduce((s, t) => s + t.n, 0);
  $("#background").style.display = "none";
  $("#streets-play").innerHTML =
    `<p class="street-intro">Pick a theme, or roll the dice:</p>` +
    `<button class="theme-random" id="theme-random">` +
      `<span class="theme-icon">🎲</span>` +
      `<span class="theme-name">Random</span>` +
      `<span class="theme-count">any of ${total} puzzles</span>` +
    `</button>` +
    `<p class="theme-grid-label">or pick a theme:</p>` +
    `<div class="theme-grid">` +
    themes.map((t) =>
      `<button class="theme-chiclet" data-theme="${esc(t.label)}">` +
        `<span class="theme-icon">${t.icon}</span>` +
        `<span class="theme-name">${esc(t.label)}</span>` +
        `<span class="theme-count">${t.n}</span>` +
      `</button>`).join("") +
    `</div>`;
}

function renderBackground() {
  $("#background").style.display = "block";
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
    `<div class="round-label">Round ${idx + 1}/${ROUNDS_PER_GAME}` +
    ` <button class="themes-link" id="themes-link" title="Back to theme select">← themes</button></div>` +
    `<p class="street-intro">One of the mystery suburb's streets is named after…</p>` +
    `<div class="clue-card clue-new"><div class="clue-text">${esc(r.clue)}</div></div>` +
    `<div class="street-options">` +
    r.options.map((o, i) => optionButton(o, i, locked)).join("") +
    `</div>` +
    hintHtml +
    (feedbackHtml || "") +
    (solved ? streetCard(r, true) : "") +
    (solved ? `<button class="next-btn" id="next-btn">` +
      (idx === ROUNDS_PER_GAME - 1 ? "🎉 Finish" : "Next street →") +
      `</button>` : "");
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

function celebrationCopy(correct) {
  if (correct === 5) return ["Perfect! 🎉", "5/5 streets — absolutely flawless."];
  if (correct >= 4) return ["Congrats! 🎉", `You got ${correct}/5 streets.`];
  if (correct >= 3) return ["Nice work!", `You got ${correct}/5 streets.`];
  if (correct === 2) return ["Not bad!", `You got ${correct}/5 streets.`];
  if (correct === 1) return ["Tough streets!", `You got ${correct}/5 streets.`];
  return ["Brutal!", "The streets won this round."];
}

const CONFETTI_COLORS = ["#26A69A", "#7E57C2", "#EC407A", "#FFA726", "#66BB6A", "#D4AF37", "#42A5F5"];

function confetti() {
  let s = "";
  for (let i = 0; i < 42; i++) {
    s += `<div class="sw-confetti" style="left:${(Math.random() * 100).toFixed(1)}%;` +
      `width:${(6 + Math.random() * 6).toFixed(1)}px;height:${(10 + Math.random() * 8).toFixed(1)}px;` +
      `background:${pick(CONFETTI_COLORS)};animation-duration:${(2.4 + Math.random() * 2).toFixed(2)}s;` +
      `animation-delay:${(Math.random() * 2).toFixed(2)}s;"></div>`;
  }
  return s;
}

function balloons() {
  let s = "";
  for (let i = 0; i < 8; i++) {
    const c = pick(CONFETTI_COLORS);
    s += `<div class="sw-balloon" style="left:${(2 + Math.random() * 88).toFixed(1)}%;` +
      `animation-duration:${(7 + Math.random() * 4).toFixed(1)}s;` +
      `animation-delay:${(Math.random() * 3).toFixed(1)}s;">` +
      `<div class="sw-balloon-body" style="background:${c}"></div>` +
      `<div class="sw-balloon-string"></div></div>`;
  }
  return s;
}

function renderFinale() {
  const grid = emojiGrid();
  const correct = results.filter((r) => r.state !== "fail").length;
  const [headline, sub] = celebrationCopy(correct);
  $("#streets-play").innerHTML =
    confetti() + balloons() +
    `<div class="finale-wrap">` +
    `<div class="sw-trophy">🏆</div>` +
    `<div class="finale-headline">${esc(headline)}</div>` +
    `<div class="finale-sub">${esc(sub)}</div>` +
    `<div class="sw-reveal-card">` +
    `<h2 class="finale-title">It was ${esc(suburb)}!</h2>` +
    `<p class="finale-reveal">${esc(puzzle.reveal)}</p>` +
    mascotPayoff() +
    `<div class="street-cards-label">streets in the mystery suburb:</div>` +
    solvedCards() +
    `<div class="finale-score">Score: ${points}/${ROUNDS_PER_GAME * POINTS_FIRST}</div>` +
    `<pre class="grid-pre">${esc(grid)}</pre>` +
    `<div class="result-actions">` +
    `<button class="play-again" id="play-again-btn">Play again</button>` +
    `<button class="play-again" id="themes-btn">More themes</button>` +
    `<button class="play-again" id="share-btn">Share 📋</button>` +
    `<span id="share-status"></span></div>` +
    `</div></div>`;
  $("#share-btn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(grid);
      $("#share-status").textContent = "Copied!";
    } catch (e) {
      $("#share-status").textContent = "Copy failed — select manually";
    }
  });
  $("#play-again-btn").addEventListener("click", () => {
    newGame(themeFilter);
    renderBackground();
    renderPlay(null, false);
  });
  $("#themes-btn").addEventListener("click", renderSelect);
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
  else if (t.id === "theme-random") { newGame(null); renderBackground(); renderPlay(null, false); }
  else if (t.id === "themes-link" || t.id === "themes-btn") renderSelect();
  else if (t.dataset.theme) { newGame(t.dataset.theme); renderBackground(); renderPlay(null, false); }
  else if (t.dataset.opt !== undefined) onOption(Number(t.dataset.opt));
});

/* --- boot ----------------------------------------------------------------- */

async function main() {
  THEMES = await fetchJSON("data/street_themes.json");
  try {
    SUBURBS = await fetchJSON("data/suburbs.json");
  } catch (e) { /* mascot payoff is optional */ }
  newGame(null);
  renderSelect();
}

main().catch((e) => {
  console.error(e);
  $("#streets-play").innerHTML = `<p class="empty">Failed to load: ${esc(e.message)}</p>`;
});
