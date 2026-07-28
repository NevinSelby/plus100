/* Plus100 frontend */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { home: null, away: null };

const pct = (p) => (p * 100).toFixed(1) + "%";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, params) {
  const u = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => u.searchParams.set(k, v));
  const r = await fetch(u);
  if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
  return r.json();
}

/* ---------------- meta strip ---------------- */
api("/api/meta").then((m) => {
  $("metaStrip").innerHTML =
    `<b>${m.matches.toLocaleString()}</b> matches · <b>${m.teams.toLocaleString()}</b> teams · ` +
    `<b>${m.leagues}</b> competitions · ${m.data_from} → ${m.data_to}` +
    (m.refresh ? ` · auto-updates ${m.refresh.auto}${m.refresh.refreshing ? " (refreshing now…)" : ""}` : "");
  $("footerModel").textContent =
    `Model: time-weighted attack/defence strengths + Elo ratings (ClubElo-anchored) blended into a ` +
    `Dixon-Coles-corrected Poisson score matrix. Backtest ${m.backtest.period}: ` +
    `${m.backtest.test_matches.toLocaleString()} matches, model Brier ${m.backtest.model_brier} vs ` +
    `bookmaker ${m.backtest.bookmaker_brier}. ${m.backtest.note}`;
  const ab = $("aboutAccuracy");
  if (ab) {
    const le = m.live_eval;
    const live = le ? `<b>Re-measured automatically at every data refresh</b>: latest check ` +
      `(${le.matches.toLocaleString()} matches, ${le.from} to ${le.to}): correct result ` +
      `<b>${(le.model_accuracy * 100).toFixed(1)}%</b> vs the bookmakers' ` +
      `<b>${(le.book_accuracy * 100).toFixed(1)}%</b>. ` : "";
    ab.innerHTML = live +
      `Full-model benchmark (${esc(m.backtest.period)}, ${m.backtest.test_matches.toLocaleString()} matches): ` +
      `<b>${(m.backtest.model_accuracy * 100).toFixed(1)}%</b> vs ` +
      `<b>${(m.backtest.bookmaker_accuracy * 100).toFixed(1)}%</b>, with calibrated probabilities: ` +
      `when it says 40%, that outcome happens about 40% of the time. That is the realistic ceiling ` +
      `of football prediction, for anyone.`;
  }
}).catch(() => { $("metaStrip").textContent = "dataset unavailable"; });

/* ---------------- team picker ---------------- */
function setupPicker(side) {
  const input = $(side === "home" ? "inputHome" : "inputAway");
  const drop = $(side === "home" ? "dropHome" : "dropAway");
  const badge = $(side === "home" ? "badgeHome" : "badgeAway");
  const empty = $(side === "home" ? "emptyHome" : "emptyAway");
  const sub = $(side === "home" ? "subHome" : "subAway");
  let timer = null;

  input.addEventListener("input", () => {
    state[side] = null;
    updateButton();
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { drop.classList.remove("open"); return; }
    timer = setTimeout(async () => {
      const teams = await api("/api/teams", { q });
      drop.innerHTML = teams.map((t, i) =>
        `<div class="opt" data-i="${i}">
           <span class="nm">${esc(t.name)}${t.active ? "" : ' <span class="inactive">†</span>'}</span>
           <span class="lg">${esc(t.league)} · ${Math.round(t.elo)}</span>
         </div>`).join("") || `<div class="opt"><span class="lg">no teams found</span></div>`;
      drop.classList.toggle("open", teams.length > 0);
      drop.querySelectorAll(".opt[data-i]").forEach((el) => {
        el.addEventListener("mousedown", (e) => {
          e.preventDefault();
          pick(side, teams[+el.dataset.i]);
        });
      });
    }, 180);
  });
  input.addEventListener("blur", () => setTimeout(() => drop.classList.remove("open"), 150));

  async function pick(s, t) {
    state[s] = t;
    input.value = t.name;
    drop.classList.remove("open");
    sub.innerHTML = `${esc(t.league)}${t.country ? " · " + esc(t.country) : ""} · elo <b>${Math.round(t.elo)}</b>`;
    updateButton();
    badge.hidden = true; empty.hidden = false;
    try {
      const { badge: url } = await api("/api/logo", { team_id: t.id });
      if (url && state[s] && state[s].id === t.id) {
        badge.src = url; badge.hidden = false; empty.hidden = true;
      }
    } catch { /* keep placeholder */ }
  }
}
setupPicker("home");
setupPicker("away");

function updateButton() {
  $("predictBtn").disabled = !(state.home && state.away && state.home.id !== state.away.id);
}

/* ---------------- prediction run ---------------- */
const outPlayers = { home: new Set(), away: new Set() };

$("predictBtn").addEventListener("click", async () => {
  outPlayers.home.clear();
  outPlayers.away.clear();
  runPrediction();
});

async function runPrediction() {
  const { home, away } = state;
  $("results").hidden = true;
  $("loading").hidden = false;
  try {
    const neutral = $("neutralChk").checked;
    const [pred, h2h] = await Promise.all([
      api("/api/predict", { home: home.id, away: away.id, neutral,
                            context: $("ctxSel").value,
                            out_home: [...outPlayers.home].join("|"),
                            out_away: [...outPlayers.away].join("|") }),
      api("/api/h2h", { home: home.id, away: away.id }),
    ]);
    render(pred, h2h);
    $("results").hidden = false;
    api("/api/buzz", { home: home.id, away: away.id }).then(renderBuzz)
      .catch(() => renderBuzz({ posts: [], note: "buzz unavailable" }));
    renderNews("newsHomeTitle", "newsHome", home);
    renderNews("newsAwayTitle", "newsAway", away);
  } catch (e) {
    alert("Prediction failed: " + e.message);
  } finally {
    $("loading").hidden = true;
  }
}

function render(p, h) {
  renderParlayChips(p);

  /* verdict */
  $("verdictCall").textContent = p.verdict.call.toUpperCase();
  $("verdictConf").textContent =
    `${pct(p.verdict.confidence)} probability · elo diff ${p.model_detail.elo_diff > 0 ? "+" : ""}${p.model_detail.elo_diff}` +
    (p.model_detail.uses_xg ? " · xG-powered" : "");
  $("bigScore").textContent = p.verdict.predicted_score.replace("-", " – ");
  $("xgLine").textContent =
    `expected goals ${p.expected_goals.home} – ${p.expected_goals.away}${p.neutral_venue ? " · neutral venue" : ""}`;

  /* probability bar */
  const m = p.markets.one_x_two;
  $("segH").style.width = m.home * 100 + "%"; $("segH").textContent = m.home > 0.12 ? pct(m.home) : "";
  $("segD").style.width = m.draw * 100 + "%"; $("segD").textContent = m.draw > 0.12 ? pct(m.draw) : "";
  $("segA").style.width = m.away * 100 + "%"; $("segA").textContent = m.away > 0.12 ? pct(m.away) : "";
  $("plH").textContent = `${p.home.name} ${pct(m.home)}`;
  $("plD").textContent = `draw ${pct(m.draw)}`;
  $("plA").textContent = `${p.away.name} ${pct(m.away)}`;
  const fo = m.fair_odds;
  $("fairOdds").innerHTML =
    `fair odds: ${esc(p.home.name)} <b>${fo.home ?? "n/a"}</b> · draw <b>${fo.draw ?? "n/a"}</b> · ${esc(p.away.name)} <b>${fo.away ?? "n/a"}</b>` +
    ` &nbsp;<small>(bookmaker odds above these = model sees value; below = avoid)</small>`;

  /* caveats */
  $("caveats").innerHTML = (p.caveats || []).map((c) => `<div class="caveat">⚠ ${esc(c)}</div>`).join("");

  /* heatmap 0-6 with header row/col */
  const mat = p.score_matrix;
  let maxP = 0;
  mat.forEach((row) => row.forEach((v) => { maxP = Math.max(maxP, v); }));
  let cells = `<div class="hcell hdr"></div>`;
  for (let j = 0; j < 7; j++) cells += `<div class="hcell hdr">${j}</div>`;
  for (let i = 0; i < 7; i++) {
    cells += `<div class="hcell hdr">${i}</div>`;
    for (let j = 0; j < 7; j++) {
      const v = mat[i][j];
      const t = Math.pow(v / maxP, 0.7);
      const bg = `rgba(234, 179, 8, ${(t * 0.85).toFixed(3)})`;
      const col = t > 0.55 ? "#241A00" : "var(--chalk)";
      cells += `<div class="hcell" style="background:${bg};color:${col}" title="P(${i}-${j}) = ${pct(v)}">
                  <span class="sc">${i}-${j}</span><span class="pv">${(v * 100).toFixed(1)}</span>
                </div>`;
    }
  }
  $("heatmap").innerHTML = cells;
  $("topScores").innerHTML = p.markets.correct_scores.map((s) =>
    `<span class="chip"><b>${s.score}</b> ${pct(s.prob)} <span class="odds">@${s.fair_odds ?? "n/a"}</span></span>`).join("");

  /* markets */
  const mk = p.markets;
  const hi = (a, b) => a >= b ? ["hi", ""] : ["", "hi"];
  const rows = [];
  rows.push(marketBox("Totals (over/under)", Object.entries(mk.totals).map(([line, t]) => {
    const [ho, hu] = hi(t.over, t.under);
    return `<div class="mk-row"><span>O/U ${line}</span>
      <span><span class="v ${ho}">${pct(t.over)}</span> <small>@${t.fair_over ?? "n/a"}</small> /
      <span class="v ${hu}">${pct(t.under)}</span> <small>@${t.fair_under ?? "n/a"}</small></span></div>`;
  }).join("")));
  const [by, bn] = hi(mk.btts.yes, mk.btts.no);
  rows.push(marketBox("Both teams to score", `
    <div class="mk-row"><span>Yes</span><span class="v ${by}">${pct(mk.btts.yes)} <small>@${mk.btts.fair_yes ?? "n/a"}</small></span></div>
    <div class="mk-row"><span>No</span><span class="v ${bn}">${pct(mk.btts.no)} <small>@${mk.btts.fair_no ?? "n/a"}</small></span></div>`));
  rows.push(marketBox("Double chance", `
    <div class="mk-row"><span>1X (home or draw)</span><span class="v">${pct(mk.double_chance["1X"])}</span></div>
    <div class="mk-row"><span>X2 (away or draw)</span><span class="v">${pct(mk.double_chance["X2"])}</span></div>
    <div class="mk-row"><span>12 (no draw)</span><span class="v">${pct(mk.double_chance["12"])}</span></div>`));
  rows.push(marketBox("Draw no bet", `
    <div class="mk-row"><span>${esc(p.home.name)}</span><span class="v">${pct(mk.draw_no_bet.home)}</span></div>
    <div class="mk-row"><span>${esc(p.away.name)}</span><span class="v">${pct(mk.draw_no_bet.away)}</span></div>`));
  rows.push(marketBox("Clean sheet", `
    <div class="mk-row"><span>${esc(p.home.name)}</span><span class="v">${pct(mk.clean_sheet.home)}</span></div>
    <div class="mk-row"><span>${esc(p.away.name)}</span><span class="v">${pct(mk.clean_sheet.away)}</span></div>`));
  rows.push(marketBox("Handicap (home)", Object.entries(mk.handicaps).map(([k, v]) =>
    `<div class="mk-row"><span>${k}</span><span class="v">${pct(v)}</span></div>`).join("")));
  $("marketGrid").innerHTML = rows.join("");

  /* scorers */
  const sc = p.likely_scorers || {};
  const names = Object.keys(sc).filter((k) => sc[k].length);
  $("scorersRow").hidden = names.length === 0;
  if (names.length) {
    const [n1, n2] = [p.home.name, p.away.name];
    fillScorers("scorersHomeTitle", "scorersHome", n1, sc[n1] || [], "home");
    fillScorers("scorersAwayTitle", "scorersAway", n2, sc[n2] || [], "away");
  }

  /* h2h */
  const s = h.summary;
  $("h2hSummary").innerHTML = s.played ? `
    <div class="h2h-stat"><div class="num home">${s.wins_home}</div><div class="lbl">${esc(p.home.name)} wins</div></div>
    <div class="h2h-stat"><div class="num">${s.draws}</div><div class="lbl">draws</div></div>
    <div class="h2h-stat"><div class="num away">${s.wins_away}</div><div class="lbl">${esc(p.away.name)} wins</div></div>
    <div class="h2h-extra">${s.played} meetings since ${s.first_meeting} · goals ${s.goals_home}–${s.goals_away} · ${s.avg_goals_per_match} goals/match</div>`
    : `<div class="h2h-extra">These teams have never met in our dataset.</div>`;
  $("meetings").innerHTML = h.meetings.map((mt) => `
    <tr><td class="d">${mt.date}</td><td>${esc(mt.home)}</td>
    <td class="s">${mt.score}</td><td>${esc(mt.away)}</td><td class="c">${esc(mt.competition)}</td></tr>`).join("");

  /* form */
  fillForm("formHome", p.home.name, h.form.home);
  fillForm("formAway", p.away.name, h.form.away);

  /* elo chart */
  drawElo(h.elo_history, p.home.name, p.away.name);
}

function marketBox(name, inner) {
  return `<div class="market"><div class="mk-name">${name}</div>${inner}</div>`;
}

function fillScorers(titleId, bodyId, team, list, side) {
  $(titleId).textContent = `LIKELY SCORERS · ${team}`;
  const outSet = outPlayers[side];
  const outChips = [...outSet].map((p) =>
    `<button class="out-chip" data-side="${side}" data-player="${esc(p)}" title="click to mark available again">
       ${esc(p)} OUT ✕</button>`).join("");
  $(bodyId).innerHTML = (outChips ? `<div class="out-row">${outChips}</div>` : "") +
    (list.length ? list.map((x) => `
    <div class="scorer-row">
      <span class="scorer-name">${esc(x.player)}</span>
      <span class="scorer-goals">${x.recent_xg !== undefined
        ? `${x.apps} apps · ${x.recent_goals} g · ${x.xg_per_match} xG/match` : `${x.recent_goals} goals / 2yr`}</span>
      <span class="scorer-bar"><i style="width:${Math.min(x.prob_to_score * 160, 100)}%"></i></span>
      <span class="scorer-p">${pct(x.prob_to_score)}</span>
      <button class="out-btn" data-side="${side}" data-player="${esc(x.player)}"
        title="mark unavailable (injury/suspension) and re-run prediction">OUT?</button>
    </div>`).join("")
    : `<div class="buzz-note">no recent scorer data${outSet.size ? " (marked-out players hidden)" : ""}</div>`);
  $(bodyId).querySelectorAll(".out-btn, .out-chip").forEach((btn) =>
    btn.addEventListener("click", () => {
      const s = btn.dataset.side, pl = btn.dataset.player;
      if (outPlayers[s].has(pl)) outPlayers[s].delete(pl); else outPlayers[s].add(pl);
      runPrediction();
    }));
}

function fillForm(id, team, form) {
  $(id).innerHTML = `<div class="form-team">${esc(team)}</div><div class="form-chips">` +
    form.map((f) =>
      `<span class="fchip ${f.result}" title="${f.date} ${f.venue === "H" ? "vs" : "@"} ${esc(f.opponent)} ${f.score}">${f.result}</span>`
    ).join("") + `</div>`;
}

function drawElo(hist, nameH, nameA) {
  const cv = $("eloChart");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const all = [...hist.home, ...hist.away];
  if (!all.length) return;
  const vals = all.map((d) => d[1]);
  const lo = Math.min(...vals) - 25, hiV = Math.max(...vals) + 25;
  const css = getComputedStyle(document.documentElement);

  const drawLine = (series, color) => {
    if (series.length < 2) return;
    ctx.beginPath();
    series.forEach((pt, i) => {
      const x = (i / (series.length - 1)) * (W - 70) + 10;
      const y = H - 24 - ((pt[1] - lo) / (hiV - lo)) * (H - 40);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
    const last = series[series.length - 1];
    const y = H - 24 - ((last[1] - lo) / (hiV - lo)) * (H - 40);
    ctx.fillStyle = color;
    ctx.font = "11px IBM Plex Mono";
    ctx.fillText(String(Math.round(last[1])), W - 56, y + 4);
  };
  ctx.strokeStyle = css.getPropertyValue("--line"); ctx.lineWidth = 1;
  [0.25, 0.5, 0.75].forEach((f) => {
    ctx.beginPath(); ctx.moveTo(10, H * f); ctx.lineTo(W - 60, H * f); ctx.stroke();
  });
  drawLine(hist.home, "#c6ff4a");
  drawLine(hist.away, "#6ecbff");
  ctx.fillStyle = "#8fa393"; ctx.font = "10px IBM Plex Mono";
  ctx.fillText(nameH + " ▬", 10, 12);
  ctx.fillStyle = "#6ecbff";
  ctx.fillText(nameA + " ▬", 10, 26);
  ctx.fillStyle = "#c6ff4a";
  ctx.fillRect(10, 5, 0, 0);
}

/* ---------------- odds checker: vig, cross-book arbitrage, EV vs model ------- */
/* ---------------- cross-book arb scanner ---------------- */
let scanTimer = null;
$("scanKey").value = localStorage.getItem("oddsApiKey") || "";

function renderScanEvent(e) {
  const legs = e.legs.map((l) =>
    `<span class="se-leg">${esc(l.outcome)} <b>@${l.odds}</b> ${esc(l.book)}${l.at_hardrock ? ' <span class="hr">← Hard Rock</span>' : ""}
      · $${l.stake_per_100}</span>`).join("");
  const when = e.commence ? new Date(e.commence).toLocaleString() : "";
  return `<div class="scan-event ${e.arb ? "arb" : ""}">
    <div class="se-head"><span>${esc(e.match)}</span>
      <span class="se-cov ${e.arb ? "good" : ""}">${e.arb
        ? `ARB +${e.profit_pct}% guaranteed`
        : `coverage ${e.coverage_pct}%`} · ${e.books_count} books${e.hardrock_listed ? " · HR listed" : ""}</span></div>
    <div class="se-legs">${legs}</div>
    <div class="buzz-meta">${esc(e.sport)} · ${when}${e.arb ? " · stakes shown per $100 total. Verify prices before betting, lines move fast" : ""}</div>
  </div>`;
}

async function runScan() {
  const key = $("scanKey").value.trim();   // optional: server has a stored key
  const status = $("scanStatus");
  const out = $("scanOut");
  if (key) localStorage.setItem("oddsApiKey", key);
  status.textContent = "scanning…";
  try {
    const r = await api("/api/scan", { key });
    if (r.error) {
      status.textContent = r.error === "invalid_key"
        ? "key rejected. Check it at the-odds-api.com"
        : "monthly quota used up. It resets next month";
      return;
    }
    status.textContent = `${r.scanned} fixtures (today + tomorrow) · ${r.arbs.length} arbs · ` +
      `${r.credits_spent ?? "?"} credits used, ${r.remaining_credits ?? "?"} left · ${new Date().toLocaleTimeString()}`;
    let html = "";
    if (r.arbs.length) {
      html += r.arbs.map(renderScanEvent).join("");
      document.title = `(${r.arbs.length} ARB) Plus100`;
    } else {
      document.title = "Plus100: Football Prediction App";
      html += `<div class="odds-verdict">No guaranteed-profit combination across books right now, which is the normal state.
        Closest near-misses below; a lock appears when one of these dips under 100%.</div>`;
    }
    if (r.near_misses.length) html += r.near_misses.map(renderScanEvent).join("");
    if (!r.scanned) html = `<div class="odds-verdict">No fixtures currently listed in the scanned competitions.</div>`;
    out.innerHTML = html;
  } catch (e) {
    status.textContent = "scan failed: " + e.message;
  }
}

$("scanBtn").addEventListener("click", runScan);
$("scanAuto").addEventListener("change", (ev) => {
  clearInterval(scanTimer);
  if (ev.target.checked) {
    runScan();
    scanTimer = setInterval(runScan, 5 * 60 * 1000);
  }
});

/* ---------------- app shell: view tabs + sport switching ---------------- */
document.querySelectorAll(".nav-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".view").forEach((v) =>
      v.classList.toggle("active", v.id === "view-" + btn.dataset.view));
    window.scrollTo({ top: 0 });
  }));

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/sw.js").catch(() => {});
}

/* ---------------- parlay lab (same-game, exact joint odds) ---------------- */
const parlayLegs = new Set();

function parlayGroups(p) {
  const H = p.home.name.toUpperCase();
  const A = p.away.name.toUpperCase();
  const csChips = p.markets.correct_scores.slice(0, 6)
    .map((s) => [`cs:${s.score}`, s.score]);
  const scorerChips = [];
  const sotChips = [];
  for (const [side, team] of [["home", p.home.name], ["away", p.away.name]]) {
    for (const x of (p.likely_scorers[team] || []).slice(0, 4)) {
      scorerChips.push([`scorer:${side}:${x.player}`, `${x.player.toUpperCase()} SCORES`]);
      if (x.sot_rate > 0) {
        sotChips.push([`sot:${side}:${x.player}:1`, `${x.player.toUpperCase()} 1+ SOT`]);
        sotChips.push([`sot:${side}:${x.player}:2`, `${x.player.toUpperCase()} 2+ SOT`]);
      }
    }
  }
  const groups = [
    ["Result", [["home", `${H} WIN`], ["draw", "DRAW"], ["away", `${A} WIN`],
                ["1x", `${H}/DRAW`], ["12", "NO DRAW"], ["x2", `${A}/DRAW`],
                ["margin:home:2", `${H} BY 2+`], ["margin:away:2", `${A} BY 2+`]]],
    ["Correct score", csChips],
    ["Total goals", [["o:1.5", "OVER 1.5"], ["o:2.5", "OVER 2.5"], ["o:3.5", "OVER 3.5"],
                     ["u:2.5", "UNDER 2.5"], ["u:3.5", "UNDER 3.5"],
                     ["odd", "ODD"], ["even", "EVEN"]]],
    ["Both teams", [["btts", "BTTS YES"], ["no_btts", "BTTS NO"]]],
    ["Team goals", [["home_o:0.5", `${H} 1+`], ["home_o:1.5", `${H} 2+`],
                    ["away_o:0.5", `${A} 1+`], ["away_o:1.5", `${A} 2+`],
                    ["home_cs", `${H} CLEAN SHEET`], ["away_cs", `${A} CLEAN SHEET`]]],
    ["Halves & timing", [["ht:home", `HT ${H}`], ["ht:draw", "HT DRAW"], ["ht:away", `HT ${A}`],
                         ["first:home", `${H} SCORES FIRST`], ["first:away", `${A} SCORES FIRST`],
                         ["both_halves_goal", "GOAL EACH HALF"]]],
    ["Corners", [["corners_o:8.5", "OVER 8.5"], ["corners_o:9.5", "OVER 9.5"],
                 ["corners_o:10.5", "OVER 10.5"], ["corners_u:9.5", "UNDER 9.5"],
                 ["corners_u:10.5", "UNDER 10.5"]]],
    ["Cards", [["cards_o:3.5", "OVER 3.5"], ["cards_o:4.5", "OVER 4.5"],
               ["cards_u:4.5", "UNDER 4.5"]]],
  ];
  if (scorerChips.length) groups.push(["Player to score", scorerChips]);
  if (sotChips.length) groups.push(["Shots on target", sotChips]);
  return groups;
}

function toAmerican(dec) {
  return dec >= 2 ? `+${Math.round((dec - 1) * 100)}` : `${Math.round(-100 / (dec - 1))}`;
}

async function renderParlaySuggestions() {
  const box = $("parlaySuggest");
  box.innerHTML = `<div class="buzz-note">building suggested parlays…</div>`;
  try {
    const list = await api("/api/parlay/suggest", {
      home: state.home.id, away: state.away.id, neutral: $("neutralChk").checked,
      context: $("ctxSel").value,
    });
    if (!list.length) { box.innerHTML = ""; return; }
    box.innerHTML = list.map((s, i) => `
      <button class="sugg-card" data-i="${i}">
        <span class="sugg-name">${esc(s.name)}${s.n_legs >= 4 ? ' <span class="hr">BOOST ELIGIBLE</span>' : ""}</span>
        <span class="sugg-legs">${s.labels.map(esc).join(" + ")}</span>
        <span class="sugg-nums">
          <b>${(s.joint_prob * 100).toFixed(1)}%</b> to hit ·
          fair <b>${s.fair_odds}</b> (${toAmerican(s.fair_odds)}) ·
          take if quoted ≥ <b class="good">${s.min_quote}</b> (${toAmerican(s.min_quote)})
          ${s.correlation_boost > 1.15 ? ` · <span class="hr">×${s.correlation_boost} correlated</span>` : ""}
        </span>
      </button>`).join("");
    box.querySelectorAll(".sugg-card").forEach((card) =>
      card.addEventListener("click", () => {
        const s = list[+card.dataset.i];
        parlayLegs.clear();
        s.legs.forEach((l) => parlayLegs.add(l));
        document.querySelectorAll("#parlayChips .leg-chip").forEach((c) =>
          c.classList.toggle("on", parlayLegs.has(c.dataset.leg)));
        updateParlay();
      }));
  } catch (e) {
    box.innerHTML = `<div class="buzz-note">suggestions unavailable: ${esc(e.message)}</div>`;
  }
}

function renderParlayChips(p) {
  const wrap = $("parlayChips");
  parlayLegs.clear();
  $("parlayOut").innerHTML = "";
  renderParlaySuggestions();
  wrap.innerHTML = parlayGroups(p).map(([title, legs]) => legs.length ? `
    <div class="leg-group"><span class="leg-group-title">${title}</span>
      ${legs.map(([k, label]) => `<button class="leg-chip" data-leg="${esc(k)}">${esc(label)}</button>`).join("")}
    </div>` : "").join("");
  wrap.querySelectorAll(".leg-chip").forEach((c) => c.addEventListener("click", () => {
    const k = c.dataset.leg;
    if (parlayLegs.has(k)) { parlayLegs.delete(k); c.classList.remove("on"); }
    else { parlayLegs.add(k); c.classList.add("on"); }
    updateParlay();
  }));
}

let parlayTimer = null;
async function updateParlay() {
  const out = $("parlayOut");
  if (!state.home || !state.away || parlayLegs.size === 0) {
    out.innerHTML = "";
    return;
  }
  const body = {
    home: state.home.id, away: state.away.id,
    neutral: $("neutralChk").checked, legs: [...parlayLegs],
    context: $("ctxSel").value,
  };
  try {
    const r = await apiPost("/api/parlay", body);
    const legRows = r.legs.map((l) =>
      `<tr><td>${esc(l.label)}</td><td>${(l.marginal_prob * 100).toFixed(1)}%</td></tr>`).join("");
    const corr = r.correlation_boost;
    const corrTxt = corr === null ? "" :
      corr > 1.03 ? `<span class="good">Legs are positively correlated (×${corr} vs independent), and books often underpay these.</span>` :
      corr < 0.97 ? `<span class="warn">Legs fight each other (×${corr} vs independent). A book quoting multiplied odds would be overpaying you, and they rarely do.</span>` :
      `Legs are roughly independent (×${corr}).`;
    const gradeHtml = `<div class="odds-verdict">Bet this combo only if your app quotes MORE than the fair odds above.</div>`;
    out.innerHTML = `<div class="odds-result"><div class="odds-verdict">
      <table class="ev-table"><tr><th>leg</th><th>probability alone</th></tr>${legRows}</table>
      Joint probability <b>${(r.joint_prob * 100).toFixed(1)}%</b> → fair odds <b>${r.fair_odds ?? "n/a"}</b>
      <small>(naive independent: ${r.naive_odds ?? "n/a"})</small><br>${corrTxt}
      ${(r.notes || []).map((x) => `<br><small>${esc(x)}</small>`).join("")}</div>${gradeHtml}</div>`;
  } catch (e) {
    out.innerHTML = `<div class="odds-verdict loss">${esc(e.message)}</div>`;
  }
}


/* signature Edge Meter: model-vs-market deviation, clamped to ±15% */
function edgeMeter(edgePct) {
  const span = 15;
  const clamped = Math.max(-span, Math.min(span, edgePct));
  const posPct = 50 + (clamped / span) * 50;
  const color = edgePct > 1 ? "var(--lime)" : edgePct < -1 ? "var(--red)" : "var(--muted)";
  return `<div class="edge-meter" title="How far the offered price deviates from the combined probability estimate">
    <div class="edge-meter-val" style="color:${color}">${edgePct > 0 ? "+" : ""}${edgePct.toFixed(1)}%</div>
    <div class="edge-meter-track"><div class="edge-meter-zero"></div>
      <div class="edge-meter-dot" style="left:${posPct}%;background:${color}"></div></div>
    <div class="edge-meter-label"><span>-${span}</span><span>EDGE</span><span>+${span}</span></div>
  </div>`;
}

/* ---------------- best bets ---------------- */
$("bestBetsBtn").addEventListener("click", async () => {
  const key = $("scanKey").value.trim();   // optional: server has a stored key
  const status = $("bbStatus");
  const out = $("bestBetsOut");
  status.textContent = "fetching odds and grading every price…";
  try {
    const params = { key };
    if (state.home && state.away) {
      params.home = state.home.id;
      params.away = state.away.id;
    }
    const r = await api("/api/bestbets", params);
    if (r.error) {
      status.textContent = r.error === "invalid_key" ? "key rejected" : "monthly quota exhausted";
      return;
    }
    status.textContent = `${r.fixtures} fixtures · ${r.all_evaluated} prices graded · credits left: ${r.remaining_credits ?? "?"}`;

    const betRows = (bets) => bets.map((b) => `<tr>
      <td>${esc(b.match)}</td><td>${esc(b.outcome)}</td>
      <td>${b.odds}${b.at_hardrock ? ' <span class="hr">HR</span>' : ""} <small>${esc(b.book)}</small></td>
      <td>${(b.p_blend * 100).toFixed(1)}% <small>(mkt ${(b.p_market * 100).toFixed(0)} / mdl ${(b.p_model * 100).toFixed(0)})</small></td>
      <td>${edgeMeter(b.edge_pct)}</td>
      <td>${b.edge_pct > 1 ? b.quarter_kelly_pct + "%" : "n/a"}</td></tr>`).join("");
    const table = (bets) =>
      `<table class="ev-table"><tr><th>match</th><th>bet</th><th>best price</th><th>probability</th><th>edge</th><th>¼-Kelly</th></tr>${betRows(bets)}</table>`;

    let html = `<div class="odds-result">`;

    if (params.home && r.selected) {
      const good = r.selected.bets.filter((b) => b.edge_pct > 1);
      html += `<div class="odds-verdict ${good.length ? "arb" : ""}"><b>YOUR MATCH: ${esc(r.selected.match)}</b><br>
        ${good.length
          ? `<span class="good">${good.length} bet${good.length > 1 ? "s" : ""} worth taking:</span>`
          : `<span class="warn">No positive-edge bet on this match. Every price is fair or short. The honest play is to skip it.</span>`}
        ${table(r.selected.bets)}</div>`;
    } else if (params.home && !r.selected) {
      html += `<div class="odds-verdict"><span class="warn">Your selected match isn't priced by any book in the
        next 2 days.</span> Books only list fixtures close to kickoff (no API credits were spent).
        Clear the team pickers and re-run to sweep all listed fixtures instead.</div>`;
    }

    if (r.bets.length) {
      const inner = `<div class="odds-verdict">${table(r.bets)}
        <span class="warn">Edges are long-run statistical advantages. Expect to lose these bets routinely.
        Stake the ¼-Kelly % of bankroll or less, never chase.</span></div>`;
      html += params.home
        ? `<details class="bb-details"><summary>Other opportunities across all fixtures (${r.bets.length})</summary>${inner}</details>`
        : inner;
    } else if (!params.home) {
      html += `<div class="odds-verdict">No bet clears the 1% edge bar right now. The market and model agree
        everywhere. That's the normal state; the honest play is to bet nothing today.</div>`;
    }
    if (r.parlays && r.parlays.length) {
      const rows = r.parlays.map((p) => `<tr>
        <td>${p.legs.map((l) => `${esc(l.outcome)} @${l.odds} <small>(${esc(l.match)}, ${esc(l.book)})</small>`).join("<br>")}</td>
        <td>${p.combined_odds}<br><small>fair ${p.fair_odds}</small></td>
        <td>${(p.win_prob * 100).toFixed(1)}%</td>
        <td class="pos">+${p.edge_pct}%</td>
        <td>${p.quarter_kelly_pct}%<br><small>busts ${p.bust_prob_pct}%</small></td></tr>`).join("");
      html += `<div class="odds-verdict"><b>Cross-match parlays from these edges</b>
        <table class="ev-table"><tr><th>legs</th><th>combined</th><th>win prob</th><th>edge</th><th>¼-Kelly</th></tr>${rows}</table>
        <span class="warn">Combined odds use each leg's best price across books. A real slip sits at ONE book and
        pays less. Only take a parlay if the book's quoted total beats the fair odds shown. Singles remain the
        better risk-to-reward; parlays multiply variance faster than edge.</span></div>`;
    }
    out.innerHTML = html + "</div>";
  } catch (e) {
    status.textContent = "failed: " + e.message;
  }
});

async function renderNews(titleId, bodyId, team) {
  $(titleId).textContent = `TEAM NEWS & INJURIES · ${team.name}`;
  $(bodyId).innerHTML = `<div class="buzz-note">loading…</div>`;
  try {
    const n = await api("/api/news", { team_id: team.id });
    $(bodyId).innerHTML = n.items.length ? n.items.map((it) => `
      <div class="buzz-post">
        <a href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
        <div class="buzz-meta">${esc(it.date)}</div>
      </div>`).join("")
      : `<div class="buzz-note">no recent headlines found</div>`;
  } catch {
    $(bodyId).innerHTML = `<div class="buzz-note">news unavailable</div>`;
  }
}

function renderBuzz(b) {
  const el = $("buzz");
  if (b.posts && b.posts.length) {
    el.innerHTML = b.posts.map((pst) => `
      <div class="buzz-post">
        <a href="${esc(pst.url)}" target="_blank" rel="noopener">${esc(pst.title)}</a>
        <div class="buzz-meta">r/${esc(pst.subreddit)} · ▲${pst.score} · ${pst.num_comments} comments</div>
      </div>`).join("");
  } else {
    el.innerHTML = `<div class="buzz-note">${esc(b.note || "no recent posts found")}. Reddit blocks unauthenticated API access from some networks; the statistical prediction above is unaffected.</div>`;
  }
}
