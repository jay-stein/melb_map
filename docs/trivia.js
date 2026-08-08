/* Suburb Detective — trivia game. 5 rounds, 5 progressive clues per round.
 * Guess with fewer clues for a higher score. Trophy after 5 rounds. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const TOTAL_ROUNDS = 5;
const MAX_CLUES = 5;

let STATE = null;   // game-state.json (centroids, regions, order)
let SUBURBS = {};   // suburbs.json data

let region = null;
let targets = [];         // 5 suburb names for this game
let round = 0;
let revealed = 0;         // how many clues currently shown
let clues = [];           // current round's clue strings
let roundScores = [];     // clues used per round (MAX_CLUES = 5 = revealed+1)
let gameOver = false;
let currentPool = [];     // suburbs in selected region

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

/* Pick one random item from an array. */
function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

/* Shuffle (Fisher-Yates). */
function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* --- clue generation ---------------------------------------------------- */

function buildClues(suburb) {
  const d = SUBURBS[suburb];
  const c = [];

  // Clue 1: category
  const cat = esc(d.primary_category || "unknown");
  const nn = d.nickname ? `, aka "${esc(d.nickname)}"` : "";
  c.push(`This <b>${cat}</b> suburb${nn} has a population of ${(d.census && d.census.population) ? d.census.population.toLocaleString() : "?"}.`);

  // Clue 2: a tag
  if (d.tags && d.tags.length) {
    c.push(`The locals say things like: <i>"${esc(pick(d.tags))}"</i>`);
  } else {
    c.push("The suburb's vibe is hard to pin down from the data we have.");
  }

  // Clue 3: vibe (cropped slightly if very long)
  if (d.vibe) {
    c.push(esc(d.vibe));
  } else {
    c.push("We don't have a vibe summary for this one — but your intuition might!");
  }

  // Clue 4: census quirk
  const census = d.census || {};
  const lines = [];
  if (census.born_overseas_pct != null) lines.push(`${Math.round(census.born_overseas_pct)}% born overseas`);
  if (census.language && census.language.length) {
    lines.push("over-represented languages: " + census.language.map((l) => esc(l.group)).join(", "));
  } else if (census.birthplace && census.birthplace.length) {
    lines.push("over-represented birthplaces: " + census.birthplace.map((b) => esc(b.group)).join(", "));
  }
  if (lines.length) {
    c.push(`Census says: ${lines.join("; ")}.`);
  } else {
    c.push("Census data doesn't reveal much — this one's a sleeper.");
  }

  // Clue 5: lore
  if (d.lore && d.lore.length > 0) {
    const lore = pick(d.lore);
    c.push(`Local lore: ${esc(lore)}`);
  } else if (d.history) {
    const hist = d.history.split(".")[0] + ".";
    c.push(`History: ${esc(hist)}`);
  } else {
    c.push("The clue well has run dry — take your best guess!");
  }

  return c;
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

function startGame(selected) {
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

  $("#region-screen").style.display = "none";
  $("#game-screen").style.display = "block";
  $("#results-screen").style.display = "none";

  // populate datalist for this region's pool
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
    // correct
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
    // wrong — show next clue if available, otherwise reveal answer
    if (revealed < MAX_CLUES - 1) {
      revealed++;
      showClue();
      renderFeedback(false);
    } else {
      // all clues revealed, wrong guess — auto-fail this round
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
    try {
      await navigator.clipboard.writeText(grid);
      alert("Copied to clipboard!");
    } catch (e) {
      alert("Couldn't copy — here you go:\n\n" + grid);
    }
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
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") makeGuess(input.value);
  });
  $("#reveal-btn").addEventListener("click", () => {
    if (revealed < MAX_CLUES - 1) {
      revealed++;
      showClue();
    }
  });
}

main().catch((e) => {
  console.error(e);
  document.body.innerHTML = `<p style="color:red;padding:40px">Failed to load: ${esc(e.message)}</p>`;
});
