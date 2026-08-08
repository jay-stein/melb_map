/* Static port of the Dash app's map page: renders the exported figure JSON
 * with plotly.js and re-implements the click → side-panel logic in JS. */

const $ = (sel) => document.querySelector(sel);

const CATEGORY_COLORS = {
  hipster: "#7E57C2", posh: "#D4AF37", student: "#26A69A", family: "#66BB6A",
  nightlife: "#EC407A", industrial: "#8D6E63", sleepy: "#90A4AE",
  multicultural: "#FFA726", unknown: "#BDBDBD",
};

let SUBURBS = {};
let MASCOTS = {};

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

function fmtInt(n) {
  return Number(n).toLocaleString("en-US");
}

/* [flag] Group ........ 4.8× · top 10% */
function quirkRow(q) {
  const flag = q.iso
    ? `<img class="flag" src="assets/flags/${esc(q.iso)}.svg" alt="">`
    : `<span class="flag flag-slot"></span>`;
  return `<div class="quirk-row">${flag}<span class="quirk-group">${esc(q.group)}</span>` +
         `<span class="quirk-lq">${Number(q.lq).toFixed(1)}× · top ${esc(q.top_pct)}%</span></div>`;
}

function censusCard(census) {
  if (!census || !Object.keys(census).length) return "";
  let card = "";
  if (census.headline) {
    card += `<div class="census-headline">${esc(census.headline)}</div>`;
  }
  const bits = [`${fmtInt(census.population)} people`];
  if (census.born_overseas_pct != null) bits.push(`${Math.round(census.born_overseas_pct)}% born overseas`);
  if (census.both_parents_overseas_pct != null) bits.push(`${Math.round(census.both_parents_overseas_pct)}% both parents overseas`);
  card += `<div class="census-bits">${esc(bits.join(" · "))}</div>`;

  const sub = (label) => `<div class="census-sub">${esc(label)}</div>`;
  if (census.language && census.language.length) {
    card += sub("Languages above the city average") + census.language.map(quirkRow).join("");
  }
  if (census.birthplace && census.birthplace.length) {
    card += sub("Born overseas, over-represented") + census.birthplace.map(quirkRow).join("");
  }
  if (census.ancestry && census.ancestry.length) {
    const names = census.ancestry.map((q) => q.group).join(" · ");
    card += `<div class="census-heritage"><span class="label">Heritage: </span>${esc(names)}</div>`;
  }
  if (census.emerging && census.emerging.length) {
    const note = census.emerging.map((q) => `${q.group} (top ${q.top_pct}%)`).join(" · ");
    card += `<div class="census-emerging"><span class="italic">Also notable: </span>${esc(note)}</div>`;
  }
  return `<h4>origins &amp; language</h4><div class="card">${card}</div>`;
}

function mascotBlock(r) {
  const imgSrc = MASCOTS[r.suburb] ? `assets/mascots/${esc(MASCOTS[r.suburb])}` : null;
  const m = r.mascot || {};
  if (!imgSrc && !m.name) return "";
  let html = "";
  if (imgSrc) html += `<img class="mascot-img" src="${imgSrc}" alt="">`;
  if (m.name) html += `<div class="mascot-name">${esc(m.name)}</div>`;
  if (m.tagline) html += `<div class="mascot-tagline">“${esc(m.tagline)}”</div>`;
  if (m.description) html += `<p class="mascot-desc">${esc(m.description)}</p>`;
  return `<div class="card mascot-card">${html}</div>`;
}

function historyBlock(r) {
  if (!r.history) return "";
  let srcText = "";
  if (r.history_source === "emelbourne") {
    srcText = "eMelbourne";
    if (r.history_source_author) srcText += ` — ${r.history_source_author}`;
  } else if (r.history_source === "wikipedia") {
    srcText = "Wikipedia";
  }
  let attrs = "";
  if (srcText) attrs += `<span class="attr">— ${esc(srcText)}</span>`;
  if (r.history_source_url) {
    attrs += `<a class="attr-link" href="${esc(r.history_source_url)}" target="_blank" rel="noopener">[source]</a>`;
  }
  return `<h4>history</h4>` +
         `<p class="history-body">${esc(r.history)}</p>` +
         (attrs ? `<div class="history-attrs">${attrs}</div>` : "");
}

function quotesBlock(r) {
  const topQuote = (r.top_quote || "").trim();
  let quotes = Array.isArray(r.quotes) ? r.quotes.slice() : [];
  if (!topQuote && !quotes.length) return "";
  const head = `<div class="reddit-head">` +
               `<img class="reddit-logo" src="assets/reddit_logo.svg" alt="">` +
               `<span>straight from </span><a href="https://www.reddit.com/r/melbourne/" target="_blank" rel="noopener">r/melbourne</a>` +
               `</div>`;
  let html = head;
  if (topQuote) html += `<div class="quote-top">“${esc(topQuote)}”</div>`;
  const tqLower = topQuote.toLowerCase();
  quotes = quotes.filter((q) => {
    const ql = q.toLowerCase();
    return !(tqLower && (tqLower.includes(ql) || ql.includes(tqLower)));
  });
  if (quotes.length) {
    html += quotes.map((q) => `<blockquote class="quote">${esc(q)}</blockquote>`).join("");
  }
  return html;
}

function renderPanel(suburb) {
  const el = $("#suburb-detail");
  const r = SUBURBS[suburb];
  if (!r) {
    el.innerHTML = `<h3>${esc(suburb)}</h3>` +
      `<p class="empty">Not in the dataset — this map focuses on inner/middle Melbourne plus select outer suburbs.</p>`;
    return;
  }
  const nickname = r.nickname || "";
  const heading = `<h3>${esc(suburb)}${nickname ? ` <span class="nickname">(${esc(nickname)})</span>` : ""}</h3>`;

  if (!(r.tags && r.tags.length) && !r.vibe) {
    el.innerHTML = heading + `<p class="empty">No quirks gathered for this suburb yet.</p>`;
    return;
  }

  const catColor = CATEGORY_COLORS[r.primary_category] || "#BDBDBD";
  let html = heading +
    `<span class="cat-chip" style="background:${catColor}">${esc(r.primary_category)}</span>` +
    mascotBlock(r);
  if (r.vibe) html += `<p class="vibe">${esc(r.vibe)}</p>`;
  html += censusCard(r.census || {});
  if (r.tags && r.tags.length) {
    html += `<h4>tags</h4><div>${r.tags.map((t) => `<span class="tag-chip">${esc(t)}</span>`).join("")}</div>`;
  }
  if (r.lore && r.lore.length) {
    html += `<h4>lore</h4><ul class="lore-list">${r.lore.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  }
  html += historyBlock(r);
  html += quotesBlock(r);
  el.innerHTML = html;
}

async function main() {
  const fig = await fetchJSON("data/figure.json");
  Plotly.newPlot("map", fig.data, Object.assign({}, fig.layout, { autosize: true }), {
    scrollZoom: true,
    displayModeBar: true,
    displaylogo: false,
    responsive: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "toImage"],
  });

  [SUBURBS, MASCOTS] = await Promise.all([
    fetchJSON("data/suburbs.json"),
    fetchJSON("data/mascots.json"),
  ]);

  const withTags = Object.values(SUBURBS).filter((e) => (e.tags || []).length).length;
  $("#stat-line").textContent =
    `${withTags} of ${Object.keys(SUBURBS).length} suburbs have quirks gathered. ` +
    "Sourced from r/melbourne, summarised by an LLM.";

  const gd = $("#map");
  gd.on("plotly_click", (evt) => {
    const pt = evt.points[0];
    let suburb = pt.location;
    if (!suburb) {
      const cd = pt.customdata;
      suburb = Array.isArray(cd) ? (cd[0] ?? null) : cd ?? null;
    }
    if (!suburb) return;
    renderPanel(suburb);
  });
}

main().catch((e) => {
  console.error(e);
  $("#suburb-detail").innerHTML = `<p class="empty">Failed to load: ${esc(e.message)}</p>`;
});
