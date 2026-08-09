/* Node smoke test for docs/streets.js — DOM + fetch shims, then simulate the
 * select screen, a themed game, play-again, and the back-to-themes flow. */
const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const elements = {};

function makeEl() {
  return {
    _html: "", style: {}, textContent: "",
    _handlers: {},
    set innerHTML(v) { this._html = v; },
    get innerHTML() { return this._html; },
    addEventListener(evt, fn) { this._handlers[evt] = fn; },
  };
}

global.document = {
  querySelector: (sel) => {
    if (!elements[sel]) elements[sel] = makeEl();
    return elements[sel];
  },
};
global.fetch = async (url) => ({
  ok: true,
  json: async () => JSON.parse(
    fs.readFileSync(path.join(ROOT, "docs", url), "utf-8")),
});
global.navigator = { clipboard: { writeText: async () => {} } };
global.console = console;

require(path.join(ROOT, "docs", "streets.js"));

const play = () => elements["#streets-play"];

/* Click simulation: fire the target's own listeners, then the delegated
 * listener on #streets-play (mirrors real DOM event flow + bubbling). */
function click(sel, dataset) {
  const el = elements[sel];
  const target = { id: String(sel).replace("#", ""), dataset: dataset || {} };
  if (el && el._handlers && el._handlers.click) el._handlers.click({ target: el });
  if (elements["#streets-play"]._handlers.click) {
    elements["#streets-play"]._handlers.click({ target });
  }
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

const failures = [];
function check(cond, msg) { if (!cond) failures.push(msg); }

(async () => {
  await wait(300); // main() fetch + renderSelect

  const selHtml = play().innerHTML;
  check(selHtml.includes('id="theme-random"'), "select screen has random chiclet");
  check(selHtml.includes('data-theme="Native Flora"'), "select screen has Native Flora chiclet");
  check(selHtml.includes("🌿"), "flora icon present");
  check(selHtml.includes("🪐"), "space icon present");
  check(selHtml.includes("🎖️"), "wars icon present");
  check(selHtml.includes('data-theme="Wars &amp; Battles"'), "Wars & Battles consolidated chiclet present");
  check(selHtml.includes('data-theme="Aviation &amp; Aircraft"'), "Aviation & Aircraft consolidated chiclet present");

  // pick a theme
  click("#theme-NativeFlora", { theme: "Native Flora" });
  await wait(10);
  const bg = elements["#background"].innerHTML;
  check(bg.includes("native") || bg.length > 0, "background rendered for flora game");
  check(play().innerHTML.includes("Round 1/5"), "round 1 rendered after theme pick");
  check(elements["#background"].style.display === "block", "background visible in play");

  // answer round 1 correctly (click up to 2 attempts — card appears on solve
  // or exhaustion either way)
  const opt0 = play().innerHTML.match(/id="opt-0"[^>]*/);
  check(!!opt0, "option buttons rendered");
  for (let a = 0; a < 2 && !play().innerHTML.includes("street-card"); a++) {
    click("#opt-0", { opt: "0" });
    await wait(10);
  }
  check(play().innerHTML.includes("street-card"), "street card after answer");
  check(play().innerHTML.includes('id="next-btn"'), "next button after answer");

  // back to themes mid-game
  click("#themes-link");
  await wait(10);
  check(play().innerHTML.includes('id="theme-random"'), "themes link returns to select");

  // random start
  click("#theme-random");
  await wait(10);
  check(play().innerHTML.includes("Round 1/5"), "random starts a game");

  // play a full game to the finale
  for (let i = 0; i < 5; i++) {
    for (let a = 0; a < 2 && !play().innerHTML.includes("street-card"); a++) {
      click("#opt-0", { opt: "0" });
      await wait(5);
    }
    if (play().innerHTML.includes('id="next-btn"')) {
      click("#next-btn");
      await wait(5);
    }
  }
  await wait(10);
  const finale = play().innerHTML;
  check(finale.includes("finale-title"), "finale rendered");
  check(finale.includes("sw-trophy"), "trophy in finale");
  check(finale.includes("sw-confetti"), "confetti in finale");
  check(finale.includes("sw-balloon"), "balloons in finale");
  check(finale.includes("sw-reveal-card"), "reveal card in finale");
  check(finale.includes("finale-headline"), "celebration headline in finale");
  check(finale.includes('id="themes-btn"'), "finale has More themes button");
  check(finale.includes('id="share-btn"'), "finale has share button");
  check(finale.includes("grid-pre"), "share grid present");
  check(finale.includes("street-chip"), "full themed-street chips in finale");
  check(finale.includes("themed streets"), "all-streets label in finale");

  // play again keeps theme
  click("#play-again-btn");
  await wait(10);
  check(play().innerHTML.includes("Round 1/5"), "play again starts new game");

  // more themes back to select
  click("#themes-btn");
  await wait(10);
  check(play().innerHTML.includes('id="theme-random"'), "more themes returns to select");

  console.log(failures.length === 0 ? "PASS — streets.js smoke test clean" :
              `FAIL — ${failures.length}:\n  ` + failures.join("\n  "));
  process.exit(failures.length ? 1 : 0);
})();

