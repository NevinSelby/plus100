/* Plus100 web app. Same brain as the phone app, desktop presentation. */
"use strict";
const ICONS = {
  ball: `<svg viewBox="0 0 512 512" width="34" height="34" fill="#16251A"><path d="M256 48C141.31 48 48 141.31 48 256s93.31 208 208 208 208-93.31 208-208S370.69 48 256 48zm143 304h-45.22a8 8 0 01-6.91-4l-16.14-27.68a8 8 0 01-.86-6l14.86-59.92a8 8 0 014.65-5.45l28.1-11.9a8 8 0 018.34 1.3l41.63 35.82a8 8 0 012.69 7.26 174.75 174.75 0 01-24.28 66.68A8 8 0 01399 352zM134.52 237.13l28.1 11.9a8 8 0 014.65 5.45l14.86 59.92a8 8 0 01-.86 6L165.13 348a8 8 0 01-6.91 4H113a8 8 0 01-6.82-3.81 174.75 174.75 0 01-24.28-66.68 8 8 0 012.69-7.26l41.63-35.82a8 8 0 018.3-1.3zm256.94-87.24l-18.07 51.38A8 8 0 01369 206l-29.58 12.53a8 8 0 01-8.26-1.24L274.9 170.1a8 8 0 01-2.9-6.1v-33.58a8 8 0 013.56-6.65l42.83-28.54a8 8 0 017.66-.67A176.92 176.92 0 01390 142a8 8 0 011.46 7.89zM193.6 95.23l42.84 28.54a8 8 0 013.56 6.65V164a8 8 0 01-2.86 6.13l-56.26 47.19a8 8 0 01-8.26 1.24L143 206a8 8 0 01-4.43-4.72L120.5 149.9a8 8 0 011.5-7.9 176.92 176.92 0 0164-47.48 8 8 0 017.6.71zm17.31 327.46L191.18 373a8 8 0 01.52-7l15.17-26a8 8 0 016.91-4h84.44a8 8 0 016.91 4l15.18 26a8 8 0 01.53 7l-19.59 49.67a8 8 0 01-5.69 4.87 176.58 176.58 0 01-79 0 8 8 0 01-5.65-4.85z"/></svg>`,
  pin: `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:-2px"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 1116 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
  warn: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" style="flex-shrink:0;margin-top:2px"><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>`,
  up: `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" style="vertical-align:-2px"><path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/></svg>`,
};
const FE = (paths, w=16) => `<svg viewBox="0 0 24 24" width="${w}" height="${w}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
Object.assign(ICONS, {
  clock: FE('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  grid: FE('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'),
  users: FE('<path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/>'),
  list: FE('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
  target: FE('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>'),
  repeat: FE('<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 014-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 01-4 4H3"/>'),
  star: FE('<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>'),
  divide: FE('<circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8h.01M12 16h.01"/>'),
  bar: FE('<path d="M12 20V10M18 20V4M6 20v-4"/>'),
  check: FE('<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><path d="M22 4L12 14.01l-9 -9"/>'),
  dollar: FE('<path d="M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/>'),
});
const LOADER = (label) => `<div class="loading"><div class="ball">${ICONS.ball}</div><div class="ballshadow"></div>${label}</div>`;
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
const api = (p, q) => {
  const ctl = new AbortController();
  const to = setTimeout(() => ctl.abort(), 90000);
  return fetch(p + (q ? "?" + new URLSearchParams(q) : ""), { signal: ctl.signal })
    .then(async r => {
      if (!r.ok) {
        let d = null; try { d = await r.json(); } catch { /* not json */ }
        throw new Error((d && (d.detail || d.error)) || ("server error " + r.status));
      }
      return r.json();
    })
    .finally(() => clearTimeout(to));
};
const h = (tag, cls, html) => { const e = document.createElement(tag);
  if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pct = (x, d=1) => (x * 100).toFixed(d) + "%";
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
function animateIn(root) {
  if (REDUCE) return;
  $$(".hero, .card", root).forEach((c, i) => {
    c.classList.add("a-rise"); c.style.animationDelay = (i * 70) + "ms"; });
  $$(".pdot", root).forEach((d, i) => {
    d.classList.add("a-pop"); d.style.animationDelay = (200 + (i % 22) * 45) + "ms"; });
  $$(".pitchwrap", root).forEach(pw => pw.classList.add("a-tilt"));
  $$(".hbar > div, .stack > div", root).forEach(b => {
    const w = b.style.width; b.style.width = "0%";
    requestAnimationFrame(() => requestAnimationFrame(() => { b.style.width = w; }));
  });
  $$(".count", root).forEach(el => {
    const m = el.textContent.match(/([\d.]+)/); if (!m) return;
    const target = parseFloat(m[1]), dec = (m[1].split(".")[1] || "").length;
    const pre = el.textContent.slice(0, m.index), post = el.textContent.slice(m.index + m[1].length);
    const t0 = performance.now();
    (function tick(t) {
      const k = Math.min((t - t0) / 900, 1), e = 1 - Math.pow(1 - k, 3);
      el.textContent = pre + (target * e).toFixed(dec) + post;
      if (k < 1) requestAnimationFrame(tick);
    })(t0);
  });
}
const odds = (x) => (x && isFinite(x) && x > 1) ? Number(x).toFixed(2) : "–";

/* ---- kit colors (ported from the app) ---- */
const hexRgb = (x) => { if (!x || x.length < 7) return null;
  const v = [1,3,5].map(i => parseInt(x.slice(i, i+2), 16));
  return v.some(isNaN) ? null : v; };
const rgbHex = (v) => "#" + v.map(c => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, "0")).join("");
const lum = (v) => (0.299*v[0] + 0.587*v[1] + 0.114*v[2]) / 255;
const mix = (v, t, to) => v.map((c, i) => c + (to[i] - c) * t);
function kitColor(hx, onDark) {
  const v = hexRgb(hx); if (!v) return null;
  const L = lum(v);
  if (onDark && L < 0.42) return rgbHex(mix(v, 0.5 - L, [255,255,255]));
  if (!onDark && L > 0.68) return rgbHex(mix(v, L - 0.45, [30,40,34]));
  return rgbHex(v);
}
const cdist = (a, b) => { const x = hexRgb(a), y = hexRgb(b);
  return x && y ? Math.hypot(x[0]-y[0], x[1]-y[1], x[2]-y[2]) : 999; };
function matchColors(home, away, onDark) {
  const fbH = onDark ? "#5CE690" : "#17A54B", fbA = onDark ? "#8FC1FF" : "#2D7FF0";
  const kh = kitColor(home?.colors?.[0], onDark) || fbH;
  let ka = kitColor(away?.colors?.[0], onDark);
  if (!ka || cdist(kh, ka) < 95) ka = kitColor(away?.colors?.[1], onDark);
  if (!ka || cdist(kh, ka) < 95) ka = cdist(kh, fbA) < 95 ? "#D97E06" : fbA;
  return [kh, ka];
}
const rateColor = (v, good, ok) => v >= good ? "#0FA152" : v >= ok ? "#E8890C" : "#7C917E";
const initialsOf = (n) => n.split(" ").map(w => w[0]).filter(Boolean).slice(0, 2).join("");
const lastName = (n) => n.split(" ").slice(-1)[0];

/* ---- global state ---- */
const S = { home: null, away: null, prediction: null, meta: null, run: 0 };

/* ---- navigation ---- */
$("#nav").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $$("#nav button").forEach(x => x.classList.toggle("on", x === b));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "page-" + b.dataset.page));
  if (b.dataset.page === "fantasy" && !S.fplLoaded) loadFPL();
  if (b.dataset.page === "parlays") loadParlays();
  if (b.dataset.page === "about") renderAbout();
});

/* ---- health + meta ---- */
(async function boot() {
  const dot = $("#srvdot"), txt = $("#srvtxt");
  for (;;) {
    try {
      const hz = await api("/healthz");
      if (hz.model_ready) {
        dot.classList.add("ok"); txt.textContent = "model ready";
        S.meta = await api("/api/meta").catch(() => null);
        if (S.meta) {
          $("#tagline").textContent = `Probabilities for every football match, from ${S.meta.matches.toLocaleString()} games of history, live team news and each side's probable players.`;
          $("#datastamp").textContent = "results through " + S.meta.data_to;
        }
        loadFixtures();
        return;
      }
      txt.textContent = "model warming (~2 min)…";
    } catch { txt.textContent = "waking the server…"; }
    await new Promise(r => setTimeout(r, 7000));
  }
})();

/* ---- team pickers ---- */
function wireSlot(id, key) {
  const slot = $(id), input = $("input", slot), dd = $(".dd", slot),
        badge = $("img.badge", slot), meta = $(".meta", slot);
  let timer;
  input.addEventListener("input", () => {
    S[key] = null; slot.classList.remove("filled"); badge.hidden = true;
    meta.textContent = ""; updateGo();
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { dd.hidden = true; return; }
    timer = setTimeout(async () => {
      try {
        const opts = await api("/api/teams", { q });
        dd.innerHTML = "";
        opts.slice(0, 6).forEach(t => {
          const b = h("button", "", `<div class="l">${esc(t.name)}</div><div class="s">${esc(t.league)} · elo ${Math.round(t.elo)}</div>`);
          b.onclick = () => pickTeam(key, t);
          dd.appendChild(b);
        });
        dd.hidden = opts.length === 0;
      } catch {
        if (!S.meta) {
          dd.innerHTML = `<button disabled><div class="l">The server is still waking up…</div><div class="s">search again in a few seconds</div></button>`;
          dd.hidden = false;
        } else dd.hidden = true;
      }
    }, 220);
  });
  document.addEventListener("click", (e) => { if (!slot.contains(e.target)) dd.hidden = true; });
}
const logoWaits = {};
function pickTeam(key, t, keepNeutral) {
  S[key] = t;
  const slot = $(key === "home" ? "#slot-home" : "#slot-away");
  $("input", slot).value = t.name;
  $(".dd", slot).hidden = true;
  slot.classList.add("filled");
  $(".meta", slot).textContent = (t.league || "") + (isFinite(t.elo) ? ` · elo ${Math.round(t.elo)}` : "");
  const oldBadge = $("img.badge", slot);
  if (t.badge) { oldBadge.src = t.badge; oldBadge.hidden = false; }
  else oldBadge.hidden = true;      // never leave the previous team's crest up
  updateGo(); sweepLabel(); updateSwap();
  if (S.home && S.away && !keepNeutral)   // real home fixture unless country vs country
    $("#neutral").checked = S.home.scope === "intl" && S.away.scope === "intl";
  logoWaits[key] = api("/api/logo", { team_id: t.id }).then(info => {
    Object.assign(t, info);
    if (S[key] !== t) return;       // user re-picked while this was in flight
    const img = $("img.badge", slot);
    if (info.badge) { img.src = info.badge; img.hidden = false; }
  }).catch(() => {});
  // live team condition: dynamic rating + who is missing today
  api("/api/teamstate", { team_id: t.id }).then(st => {
    Object.assign(t, st);
    if (S[key] !== t) return;
    const bits = [t.league || ""];
    if (st.elo_delta) {
      bits.push(`elo ${st.elo} → now ${st.elo_effective}`);
      bits.push(`${st.outs.length} out`);
    } else if (isFinite(st.elo)) {
      bits.push(`elo ${st.elo}`);
    }
    $(".meta", slot).textContent = bits.filter(Boolean).join(" · ");
    $(".meta", slot).title = (st.outs || []).length
      ? "Missing per live availability and news: " + st.outs.join(", ") : "";
  }).catch(() => {});
}
const updateGo = () => { $("#go").disabled = !(S.home && S.away); };
const updateSwap = () => { const b = $("#swap"); if (b) b.disabled = !(S.home && S.away); };
wireSlot("#slot-home", "home"); wireSlot("#slot-away", "away");
$("#go").onclick = () => predictNow();
$("#swap").onclick = () => {
  if (!S.home || !S.away) return;
  const keepNeutral = $("#neutral").checked;
  const a = S.home, b = S.away;
  pickTeam("home", b, true); pickTeam("away", a, true);
  $("#neutral").checked = keepNeutral;   // switching venue roles, not the venue type
  if (S.prediction) predictNow();
};

/* ---- fixtures rail ---- */
async function loadFixtures() {
  const box = $("#fixtures");
  try {
    const d = await api("/api/fixtures/upcoming", { days: 8, limit: 26, _: Date.now() });
    box.innerHTML = "";
    $("#fxnote").textContent = d.fixtures.length + (d.count > d.fixtures.length ? ` of ${d.count}` : "") + " matches";
    if (!d.fixtures.length) { box.append(h("div", "mini", "No confirmed fixtures in the feed right now — between rounds this list can be empty.")); return; }
    d.fixtures.forEach(f => {
      const ko = new Date(f.kickoff);
      // offset-carrying kickoffs render in the VIEWER's local time; a bare
      // string (older payloads) falls back to the raw UK clock time
      const hasOffset = /[+-]\d\d:\d\d$|Z$/.test(f.kickoff);
      const when = (f.kicked_off ? "in play · " : "")
        + ko.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
        + " · " + (hasOffset ? ko.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                             : f.kickoff.slice(11, 16) + " UK");
      const card = h("div", "fx",
        `<div class="lg">${esc(f.league)}</div>
         <div class="teams">${esc(f.home)} <span style="color:var(--muted)">v</span> ${esc(f.away)}</div>
         <div class="when">${esc(when)}</div>` +
        (f.odds ? `<div class="odds">books: H ${odds(f.odds.home)} · D ${odds(f.odds.draw)} · A ${odds(f.odds.away)}</div>` : ""));
      card.onclick = () => {
        pickTeam("home", { id: f.home_id, name: f.home, elo: f.home_elo, scope: "club", league: f.league });
        pickTeam("away", { id: f.away_id, name: f.away, elo: f.away_elo, scope: "club", league: f.league });
        $("#neutral").checked = false;   // a listed fixture is a real home game
        predictNow();
        window.scrollTo({ top: 0, behavior: "smooth" });
      };
      box.appendChild(card);
    });
    if (!REDUCE) $$(".fx", box).forEach((c, i) => {
      c.classList.add("a-rise"); c.style.animationDelay = (i * 40) + "ms"; });
  } catch {
    box.innerHTML = "";
    box.append(h("div", "mini", "Couldn't load the live schedule. It retries automatically."));
  }
}
setInterval(loadFixtures, 15 * 60 * 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadFixtures(); });

/* ---- prediction flow ---- */
async function predictNow() {
  const out = $("#results");
  const rid = ++S.run;   // only the latest request may render
  out.innerHTML = LOADER(`replaying ${S.meta ? S.meta.matches.toLocaleString() : "every stored"} match${S.meta ? "es" : ""} of history…`);
  const params = { home: S.home.id, away: S.away.id, neutral: $("#neutral").checked };
  try {
    await Promise.allSettled([logoWaits.home, logoWaits.away]);
    const [p, hh, lu] = await Promise.all([
      api("/api/predict", params),
      api("/api/h2h", { home: S.home.id, away: S.away.id }).catch(() => null),
      api("/api/lineup", { home: S.home.id, away: S.away.id, neutral: params.neutral }).catch(() => null),
    ]);
    if (rid !== S.run) return;
    S.prediction = p;
    renderPrediction(out, p, hh, lu);
  } catch (e) {
    if (rid !== S.run) return;
    out.innerHTML = `<div class="err">${esc(e.message)}. If the server was asleep, give it a minute and try again.</div>`;
  }
}

function heroHTML(p) {
  const m = p.markets.one_x_two;
  const [kh, ka] = matchColors(S.home, S.away, true);
  const likelier = m.home >= m.away ? S.home : S.away;
  const art = likelier?.fanart || S.home?.fanart || S.away?.fanart;
  const lightKit = hexRgb(kh) && hexRgb(ka) && (lum(hexRgb(kh)) > .75 || lum(hexRgb(ka)) > .75);
  const neutral = $("#neutral").checked;
  const sideTag = (label) => `<div class="sidetag">${neutral ? "neutral venue" : label}</div>`;
  return `<div class="hero">
    ${art ? `<img class="bgimg" src="${esc(art)}"><div class="shade"></div>` : ""}
    <div class="inner">
      <div class="cols">
        <div>${S.home?.badge ? `<img class="badge" src="${esc(S.home.badge)}">` : ""}
          <div class="team">${esc(p.home.name)}</div>${sideTag("home")}
          <div class="pct count" style="color:${kh}">${pct(m.home)}</div>
          <div class="fair">fair ${odds(m.fair_odds.home)}</div></div>
        <div><div class="drawlbl">DRAW</div><div class="draw count">${pct(m.draw)}</div>
          <div class="fair">fair ${odds(m.fair_odds.draw)}</div></div>
        <div>${S.away?.badge ? `<img class="badge" src="${esc(S.away.badge)}">` : ""}
          <div class="team">${esc(p.away.name)}</div>${sideTag("away")}
          <div class="pct count" style="color:${ka}">${pct(m.away)}</div>
          <div class="fair">fair ${odds(m.fair_odds.away)}</div></div>
      </div>
      <div class="stack">
        <div style="width:${m.home*100}%;background:${kh}"></div>
        <div style="width:${m.draw*100}%;background:${lightKit ? "rgba(30,42,34,.55)" : "rgba(255,255,255,.35)"}"></div>
        <div style="width:${m.away*100}%;background:${ka}"></div>
      </div>
      ${S.home?.stadium && !$("#neutral").checked ? `<div class="venue">${ICONS.pin} ${esc(S.home.stadium)}${S.home.capacity ? ` · ${Number(S.home.capacity).toLocaleString()} seats` : ""}</div>` : ""}
      <div class="tiles">
        <div class="tile"><b>${Number(p.expected_goals.home).toFixed(1)}–${Number(p.expected_goals.away).toFixed(1)}</b><span>expected goals</span></div>
        <div class="tile" title="${esc(p.elo_note || "")}">${(() => { const eff = p.home.elo_effective != null && p.away.elo_effective != null; const d = eff ? p.home.elo_effective - p.away.elo_effective : p.model_detail.elo_diff; return `<b style="color:#5CE690">${(d > 0 ? "+" : "") + Math.round(d)}</b><span>${eff ? "elo edge, today's squads" : "elo edge"}</span>`; })()}</div>
        <div class="tile"><b style="color:#FFD27A">${(100 - Math.max(m.home, m.draw, m.away) * 100).toFixed(0)}%</b><span>misses anyway</span></div>
      </div>
      <button class="mathbtn" id="mathbtn">${ICONS.divide} The math, with this match's numbers</button>
    </div></div>`;
}

function goalRiver(p, kh, ka) {
  const W = 900, H = 150, mid = H / 2, px = 6;
  const weight = (t) => 0.72 + 0.0072*t + 0.55*Math.exp(-((t-45)**2)/12) + 0.95*Math.exp(-((t-90)**2)/16);
  const river = (lam, dir) => {
    const amp = mid - 12, k = Math.min(lam / 2.2, 1);
    let d = `M ${px} ${mid}`;
    for (let t = 0; t <= 90; t += 3)
      d += ` L ${(px + t/90*(W-2*px)).toFixed(1)} ${(mid - dir*(weight(t)/2.62)*amp*k).toFixed(1)}`;
    return d + ` L ${W-px} ${mid} Z`;
  };
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%">
    <path d="${river(p.expected_goals.home, 1)}" fill="${kh}" opacity=".8"/>
    <path d="${river(p.expected_goals.away, -1)}" fill="${ka}" opacity=".8"/>
    <line x1="${W/2}" y1="10" x2="${W/2}" y2="${H-10}" stroke="#7C917E" stroke-dasharray="3 4" opacity=".7"/>
    <line x1="${px}" y1="${mid}" x2="${W-px}" y2="${mid}" stroke="#fff" stroke-width="1.4"/>
  </svg>
  <div style="display:flex;justify-content:space-between" class="mini"><span>kick-off</span><span>half-time</span><span>90'+</span></div>`;
}

function pitchHTML(lu, kh, ka) {
  if (!lu) return `<div class="mini">No public squad data for this pairing.</div>`;
  const ROW_Y = [0.92, 0.80, 0.685, 0.565];
  const W = 100, H = 152;   /* percent-based positioning inside an aspect box */
  let dots = "";
  const place = (team, side, color) => {
    team.players.forEach((pl, i) => {
      const fx = (pl.slot + 1) / (pl.n + 1);
      const x = (side === "home" ? fx : 1 - fx) * 100;
      const y = (side === "home" ? ROW_Y[pl.row] : 1 - ROW_Y[pl.row]) * 100;
      if (pl.placeholder) {
        dots += `<div class="pdot" style="left:${x}%;top:${y}%;opacity:.55" title="The public feed doesn't name this starter">
          <div class="face" style="border-color:${color};border-style:dashed">?</div>
          <div class="nm">unknown</div></div>`;
        return;
      }
      const pill = pl.p_score != null
        ? `<span class="pill" style="background:${rateColor(pl.p_score, .25, .12)}">${Math.round(pl.p_score * 100)}%</span>` : "";
      dots += `<div class="pdot" style="left:${x}%;top:${y}%" title="${esc(pl.name)} — ${esc(pl.pos)}${pl.p_score != null ? " · scores " + Math.round(pl.p_score*100) + "% of the time" : ""}">
        <div class="face" style="border-color:${color}">${pl.img ? `<img src="${esc(pl.img)}" onerror="this.replaceWith('${esc(initialsOf(pl.name))}')">` : esc(initialsOf(pl.name))}</div>
        ${pill}<div class="nm">${esc(lastName(pl.name))}</div></div>`;
    });
  };
  place(lu.away, "away", ka); place(lu.home, "home", kh);
  const stripes = [0,2,4,6].map(i => `<rect y="${i*19}" width="152" height="19" fill="#fff" opacity=".045"/>`).join("");
  const leg = (t, color) => `<div class="item"><span class="sw" style="background:${color}"></span><div><b>${esc(t.name)}</b><br><span>${t.complete ? t.formation : `probable core · ${t.known} of 11 known`}</span></div></div>`;
  return `<div class="legend">${leg(lu.home, kh)}${leg(lu.away, ka)}</div>
  <div class="pitchwrap" style="aspect-ratio:100/152">
    <svg viewBox="0 0 100 152" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%">
      <defs><linearGradient id="turf" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#0F7A3C"/><stop offset=".5" stop-color="#0B5E2F"/><stop offset="1" stop-color="#0F7A3C"/>
      </linearGradient></defs>
      <rect width="100" height="152" fill="url(#turf)"/>${stripes}
      <g stroke="#fff" stroke-opacity=".35" stroke-width=".7" fill="none">
        <rect x="2" y="2" width="96" height="148" rx="1"/>
        <line x1="2" y1="76" x2="98" y2="76"/><circle cx="50" cy="76" r="12"/>
        <rect x="20" y="2" width="60" height="17"/><rect x="20" y="133" width="60" height="17"/>
        <rect x="36" y="2" width="28" height="6.4"/><rect x="36" y="145.6" width="28" height="6.4"/>
      </g>
    </svg>${dots}
  </div>
  ${(lu.home.outs || []).length || (lu.away.outs || []).length
    ? `<div class="caveat">${ICONS.warn} Likely missing per team news: ${esc([...(lu.home.outs||[]).map(n=>`${n} (${lu.home.name})`), ...(lu.away.outs||[]).map(n=>`${n} (${lu.away.name})`)].join(", "))}. The prediction already accounts for them.</div>` : ""}
  <div class="mini">${esc(lu.note)} Hover any player for his role and scoring chance.</div>`;
}

function renderPrediction(out, p, hh, lu) {
  const m = p.markets.one_x_two;
  const [kh, ka] = matchColors(S.home, S.away, true);
  const [khl, kal] = matchColors(S.home, S.away, false);
  const mkRows = [
    ["Over 2.5 goals", p.markets.totals["2.5"].over], ["Under 2.5 goals", p.markets.totals["2.5"].under],
    ["Both teams score", p.markets.btts.yes], [`${p.home.name} or draw`, p.markets.double_chance["1X"]],
    [`${p.home.name} clean sheet`, p.markets.clean_sheet.home], [`${p.away.name} clean sheet`, p.markets.clean_sheet.away],
  ];
  const N = 6; let maxP = 0;
  p.score_matrix.slice(0, N).forEach(r => r.slice(0, N).forEach(v => maxP = Math.max(maxP, v)));
  const heat = p.score_matrix.slice(0, N).map((row, i) =>
    `<tr><td class="mini">${i}</td>` + row.slice(0, N).map(v => {
      const t = Math.pow(v / maxP, 0.7);
      return `<td><div class="cell" style="background:rgba(23,165,75,${(t*.82).toFixed(2)});color:${t > .55 ? "#fff" : "var(--dim)"}">${(v*100).toFixed(1)}</div></td>`;
    }).join("") + "</tr>").join("");
  const scorers = Object.entries(p.likely_scorers || {}).map(([team, list]) => list.length ? `
    <div><h3 class="sec">${ICONS.target} Chance to score · ${esc(team)}</h3>
    ${list.slice(0, 5).map(x => `<div class="pair"><span class="tag" style="width:auto">${esc(x.player)}</span>
      <div class="hbar"><div style="width:${x.prob_to_score*100}%;background:${rateColor(x.prob_to_score,.25,.12)}"></div></div>
      <span class="val" style="color:${rateColor(x.prob_to_score,.25,.12)}">${Math.round(x.prob_to_score*100)}%</span></div>`).join("")}</div>` : "").join("");

  out.innerHTML = heroHTML(p) + `
    <div class="duo">
      <div class="card"><h3 class="sec">${ICONS.clock} When the goals should come <span class="note">stoppage-time spikes are real</span></h3>${goalRiver(p, khl, kal)}
        <div class="mini">Each side's scoring threat minute by minute, in their real colors. The wider the river, the likelier they strike.</div></div>
      <div class="card"><h3 class="sec">${ICONS.grid} Exact score probabilities <span class="note">most likely ≈ 1 in ${Math.max(2, Math.round(1 / p.markets.correct_scores[0].prob))}</span></h3>
        <table class="heat"><tr><td></td>${[...Array(N)].map((_, j) => `<td class="mini">${j}</td>`).join("")}</tr>${heat}</table>
        <div class="mini">rows: ${esc(p.home.name)} goals · columns: ${esc(p.away.name)} goals</div>
        <div class="scores">${p.markets.correct_scores.slice(0, 4).map(cs => `<div class="sc"><b>${esc(cs.score)}</b><span>${pct(cs.prob)}</span></div>`).join("")}</div></div>
    </div>
    <div class="card"><h3 class="sec">${ICONS.users} Probable line-ups</h3><div id="pitchbox">${pitchHTML(lu, kh, ka)}</div></div>
    <div class="duo">
      <div class="card"><h3 class="sec">${ICONS.list} Every market, our fair price</h3>
        ${mkRows.map(([label, prob]) => `<div class="pair"><span class="tag" style="width:auto">${esc(label)}</span>
          <div class="hbar"><div style="width:${prob*100}%;background:var(--blue)"></div></div>
          <span class="val">${pct(prob)} <span style="color:var(--blue)">${odds(1/prob)}</span></span></div>`).join("")}
        <div class="mini">bet any of these only when a book offers MORE than the fair price</div></div>
      <div class="card">${scorers || `<div class="mini">No player scoring data for these teams.</div>`}</div>
    </div>
    ${hh && hh.summary.played ? `<div class="card"><h3 class="sec">${ICONS.repeat} Head to head <span class="note">${hh.summary.played} meetings since ${esc(hh.summary.first_meeting || "")}</span></h3>
      <div class="scores"><div class="sc"><b style="color:${khl}">${hh.summary.wins_home}</b><span>${esc(hh.teams.home.name)} wins</span></div>
        <div class="sc"><b>${hh.summary.draws}</b><span>draws</span></div>
        <div class="sc"><b style="color:${kal}">${hh.summary.wins_away}</b><span>${esc(hh.teams.away.name)} wins</span></div></div>
      <table><tr><th>Date</th><th>Match</th><th class="num">Score</th></tr>
      ${hh.meetings.slice(0, 6).map(mt => `<tr><td class="mini">${esc(mt.date.slice(0, 7))}</td><td>${esc(mt.home)} v ${esc(mt.away)}</td><td class="num"><b>${esc(mt.score)}</b></td></tr>`).join("")}</table></div>` : ""}
    ${(p.caveats || []).map(c => `<div class="caveat">${ICONS.warn} ${esc(c)}</div>`).join("")}`;
  animateIn(out);
  $("#mathbtn", out).onclick = () => openMath(p);
}

/* ---- the math, with this match's numbers ---- */
function openMath(p) {
  const md = p.model_detail, m = p.markets.one_x_two;
  const lH = p.expected_goals.home, lA = p.expected_goals.away;
  const gap = Math.round(Math.abs(md.elo_diff));
  const stronger = md.elo_diff >= 0 ? p.home.name : p.away.name;
  const gapWin = (100 / (1 + Math.pow(10, -Math.abs(md.elo_diff) / 400))).toFixed(0);
  const agree = Math.abs(md.dc[0] - md.elo[0]) < .25 && Math.abs(md.dc[1] - md.elo[1]) < .25;
  const top = p.markets.correct_scores[0];
  const p00 = p.score_matrix[0][0];
  const favName = m.home >= m.away ? p.home.name : p.away.name;
  const favProb = Math.max(m.home, m.away);
  const live = S.meta?.live_eval;
  const absences = (p.caveats || []).filter(c => /OUT|unavailable/.test(c));
  const ov = h("div", "overlay");
  ov.innerHTML = `<div class="modal">
    <button class="x">×</button>
    <h2>How we got these numbers</h2>
    <div class="mini" style="margin-top:0">${esc(p.home.name)} v ${esc(p.away.name)}, in plain words with this match's real values.</div>

    <div class="step">${ICONS.bar} Step 1 · How strong is each team?</div>
    <p>Every team carries a strength rating that rises when it beats good opponents and falls when it loses to weak ones, built from ${S.meta ? S.meta.matches.toLocaleString() : "our full history of"} matches with recent games counting most. The rating is not a one-off number: it re-learns from every new result at each data refresh, and today's effective rating additionally discounts players who are missing right now${p.home.elo_delta || p.away.elo_delta ? ` (here: ${esc(p.home.name)} ${p.home.elo} → ${p.home.elo_effective}, ${esc(p.away.name)} ${p.away.elo} → ${p.away.elo_effective})` : ""}. Here the gap is <b class="k">${gap} points in favor of ${esc(stronger)}</b>. Gaps like that historically mean the stronger side gets the better of the matchup about <b class="k">${gapWin}%</b> of the time before anything else is considered.</p>

    <div class="step">${ICONS.users} Step 2 · Who can actually play?</div>
    <p>Before any goals are estimated, we assemble each side's probable players from the official squad lists, then remove anyone the league's availability flags or the day's team news say is out or doubtful. A missing player takes his usual share of his team's goals with him. ${absences.length ? "For this match that mattered: " + esc(absences.map(a => a.split(":")[0].replace("Adjusted for ", "").replace("Team news suggests ", "")).join("; ")) + "." : "For this match, nobody relevant is flagged as missing right now."}</p>

    <div class="step">${ICONS.target} Step 3 · How many goals do we expect?</div>
    <p>Scoring is estimated two independent ways: from <b class="k">recent play</b> (what each team actually scored and conceded lately, weighted by chance quality where shot data exists), which says ${md.dc[0]} to ${md.dc[1]}, and from the <b class="k">rating gap</b> in step 1, which says ${md.elo[0]} to ${md.elo[1]}. ${agree ? "The two views broadly agree here, which makes this prediction more trustworthy than average." : "The two views disagree somewhat here, which makes this prediction a little less certain than average."} Combined (trusting the ratings view more; that weighting was fitted on 10,000 past matches), we land on <b class="k">${lH} goals for ${esc(p.home.name)}</b> and <b class="k">${lA} for ${esc(p.away.name)}</b>.</p>

    <div class="step">${ICONS.grid} Step 4 · From goals to chances</div>
    <p>A team expected to score ${lH} can easily score 0 or 3, so we play the match out across every possible scoreline. That math makes the single most likely score <b class="k">${esc(top.score)} at ${pct(top.prob)}</b>, and 0-0 about ${pct(p00)}. Adding every scoreline where ${esc(p.home.name)} finishes ahead gives their <b class="k">${pct(m.home)}</b>; draws add to ${pct(m.draw)}; ${esc(p.away.name)} gets ${pct(m.away)}. Every number in the app comes from this same set of scorelines, so nothing contradicts anything.</p>

    <div class="step">${ICONS.dollar} Step 5 · Why this matters for betting</div>
    <p>${esc(favName)}'s ${pct(favProb, 0)} converts to fair odds of <b class="k">${odds(1 / favProb)}</b>. If a book pays more than that, the price is in your favor; if it pays less, the bet loses money over time no matter how confident it feels. Comparing these numbers to live prices is what the Vs Market page does.</p>

    <div class="step">${ICONS.check} Step 6 · How much should you trust this?</div>
    <p>${live ? `The model is re-tested automatically at every data refresh: in the latest window it called ${(live.model_accuracy * 100).toFixed(1)}% of ${live.matches.toLocaleString()} results correctly (random guessing gets 33%, the bookmakers ${(live.book_accuracy * 100).toFixed(1)}%).` : ""} Football is mostly luck in any single match, so treat these as honest odds, never promises. Line-ups can still change up to an hour before kickoff.</p>
  </div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  ov.onclick = (e) => { if (e.target === ov) close(); };
  $(".x", ov).onclick = close;
}

/* ---- vs market ---- */
function sweepLabel() {
  $("#sweep").textContent = S.home && S.away
    ? `Grade ${S.home.name} v ${S.away.name}` : "Scan the next two days";
}
$("#sweep").onclick = async () => {
  const btn = $("#sweep"), out = $("#marketout");
  btn.disabled = true;   // each click spends real odds credits; no double-fires
  out.innerHTML = LOADER("shopping 15 sportsbooks for prices…");
  try {
    const params = S.home && S.away ? { home: S.home.id, away: S.away.id } : undefined;
    const d = await api("/api/bestbets", params);
    if (d.error) {
      const msg = d.error === "quota"
        ? "The odds allowance for this month is used up; it resets on the 1st."
        : (d.detail || d.error);
      out.innerHTML = `<div class="err">${esc(msg)}</div>`; return;
    }
    const rows = d.selected?.bets || d.bets || [];
    out.innerHTML = "";
    if (!rows.length) {
      out.append(h("div", "card", `<h3 class="sec">${ICONS.bar} Nothing to grade right now</h3><div class="sub" style="margin:0">${
        params ? "The books aren't listing this match yet, so there's nothing to grade. Prices usually appear a day or two before kickoff."
        : d.fixtures === 0 ? "The books aren't listing football in the next two days. This happens between rounds; check back on a match week."
        : `Checked ${d.fixtures} listed game${d.fixtures === 1 ? "" : "s"}. No price beats the combined estimate right now, which is the normal state. Not betting today costs you nothing.`}</div>`));
      return;
    }
    const grid = h("div", "duo");
    rows.forEach(b => {
      const good = b.edge_pct > 1;
      grid.append(h("div", "card betcard" + (good ? " good" : ""), `
        <div style="display:flex;justify-content:space-between;align-items:center">
          <b>${esc(b.outcome)}</b>${good ? `<span class="edgechip">${ICONS.up} +${b.edge_pct}%</span>` : `<span class="noval">no value</span>`}</div>
        ${b.match ? `<div class="mini" style="margin-top:2px">${esc(b.match)}</div>` : ""}
        <div class="pair"><span class="tag">books say</span><div class="hbar"><div style="width:${b.p_market*100}%;background:var(--blue)"></div></div><span class="val">${pct(b.p_market, 0)}</span></div>
        <div class="pair"><span class="tag">we say</span><div class="hbar"><div style="width:${b.p_model*100}%;background:var(--green)"></div></div><span class="val">${pct(b.p_model, 0)}</span></div>
        <div class="mini">best price ${odds(b.odds)} at ${esc(b.book)}${b.at_hardrock ? " (Hard Rock)" : ""} · implies ${(100 / b.odds).toFixed(0)}%${good ? ` · stake ${b.quarter_kelly_pct}% of bankroll` : ""}</div>`));
    });
    out.append(grid);
    animateIn(out);
    if (d.remaining_credits != null)
      out.append(h("div", "mini", `odds credits left: ${d.remaining_credits} · edges are long-run advantages, not sure things`));
  } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  finally { btn.disabled = false; }
};

/* ---- parlays ---- */
async function loadParlays() {
  const out = $("#parlayout");
  if (!S.home || !S.away) { out.innerHTML = `<div class="card mini">Pick a match on the Predict page first, then come back here.</div>`; return; }
  out.innerHTML = LOADER("simulating this match 150,000 times…");
  try {
    const list = await api("/api/parlay/suggest", { home: S.home.id, away: S.away.id,
      neutral: $("#neutral").checked });
    if (!list.length) {
      out.innerHTML = "";
      out.append(h("div", "card", `<h3 class="sec">${ICONS.divide} No parlay clears the bar</h3>
        <div class="sub" style="margin:0">Every combination for this match either lands under a 1.5% real chance or has no fair price worth quoting. Skipping is the right call here.</div>`));
      return;
    }
    const grid = h("div", "duo"); out.innerHTML = ""; out.append(grid);
    list.forEach(pl => grid.append(h("div", "card", `
      <div style="color:var(--green-deep);font-weight:800;font-size:12.5px;margin-bottom:6px">${esc(pl.name)}${pl.n_legs >= 4 ? " · Boost eligible" : ""}</div>
      <b>${esc(pl.labels.join("  +  "))}</b>
      <div class="hbar" style="margin-top:10px"><div style="width:${pl.joint_prob*100}%;background:var(--green)"></div></div>
      <div class="scores">
        <div class="sc"><b style="color:var(--green-deep)">${pct(pl.joint_prob)}</b><span>hits</span></div>
        <div class="sc"><b>${odds(pl.fair_odds)}</b><span>fair odds</span></div>
        <div class="sc"><b style="color:var(--amber)">${odds(pl.min_quote)}</b><span>take if ≥</span></div>
      </div>
      ${pl.correlation_boost > 1.15 ? `<div class="mini">legs reinforce each other ×${pl.correlation_boost} vs independent; books often underpay these</div>` : ""}`)));
    animateIn(out);
  } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

/* ---- fantasy ---- */
async function loadFPL() {
  const out = $("#fplout");
  try {
    const [gw, team] = await Promise.all([api("/api/fpl/gw"), api("/api/fpl/squad")]);
    if (gw.error) { out.innerHTML = `<div class="card mini">${esc(gw.detail || gw.error)}</div>`; return; }
    $("#fpl-title").textContent = "Fantasy · " + gw.name;
    S.gw = gw; S.team = team.error ? null : team;
    S.fplLoaded = !!S.team;
    renderFPL(out);
  } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

function renderFPL(out) {
  const gw = S.gw, t = S.team;
  if (!t) {
    out.innerHTML = `<div class="grid2"><div class="card mini">The model team is unavailable right now; it retries when you come back to this tab.</div>
      <div class="card" style="max-height:900px;overflow:auto"><h3 class="sec">Top projected players</h3>
      ${gw.players.slice(0, 40).map(p => `<div class="mini" style="padding:6px 0;border-bottom:1px solid var(--panel2)"><b>${esc(p.name)}</b> ${esc(p.pos)} · ${esc(p.team)} ${p.home ? "vs" : "at"} ${esc(p.opp)} · £${p.price.toFixed(1)}m · ${p.xpts.toFixed(1)} xPts</div>`).join("")}</div></div>`;
    return;
  }
  const byId = Object.fromEntries(t.squad.map(p => [p.id, p]));
  const xi = t.xi.map(id => byId[id]).filter(Boolean);
  const bench = t.squad.filter(p => !t.xi.includes(p.id));
  const capName = byId[t.captain] ? byId[t.captain].name : "";
  const XI_ROW = { GK: 0.9, DEF: 0.7, MID: 0.47, FWD: 0.22 };
  const rows = { GK: [], DEF: [], MID: [], FWD: [] };
  xi.forEach(p => rows[p.pos].push(p));
  let dots = "";
  Object.entries(rows).forEach(([pos, list]) => list.forEach((p, i) => {
    dots += `<div class="pdot" style="left:${(i+1)/(list.length+1)*100}%;top:${XI_ROW[pos]*100}%">
      <div class="face" style="border-color:${p.id === t.captain ? "#FFD24A" : "#fff"}">
        ${p.photo ? `<img src="${esc(p.photo)}" onerror="this.replaceWith('${esc(initialsOf(p.name))}')">` : esc(initialsOf(p.name))}</div>
      <span class="pill" style="background:${rateColor(p.xpts, 5, 3)}">${p.xpts.toFixed(1)}</span>
      <div class="nm">${esc(p.name)}${p.id === t.captain ? " (C)" : ""}</div></div>`;
  }));
  const move = t.this_week.length
    ? t.this_week.map(x => `<b>${esc(x.out)}</b> out, <b>${esc(x.in)}</b> in (+${x.gain} projected)`).join("; ")
    : "held — no swap cleared the bar, the free transfer banks for next week";
  const table = (list, title) => `<h3 class="sec">${title}</h3><table>
    <tr><th></th><th>Player</th><th>Fixture</th><th class="num">Price</th><th class="num">xPts</th></tr>
    ${list.map(p => `<tr><td>${p.photo ? `<img class="face-s" src="${esc(p.photo)}" onerror="this.remove()">` : ""}</td>
      <td><b>${esc(p.name)}</b> <span class="mini">${esc(p.pos)}</span></td>
      <td class="mini">${esc(p.team)} ${p.home ? "vs" : "at"} ${esc(p.opp)}</td>
      <td class="num">£${p.price.toFixed(1)}m</td>
      <td class="num"><span class="pill" style="background:${rateColor(p.xpts, 5, 3)}">${p.xpts.toFixed(1)}</span></td></tr>`).join("")}</table>`;

  out.innerHTML = `<div class="grid2">
    <div class="card">
      <h3 class="sec">${ICONS.star} The Plus100 team — plays by the rules</h3>
      <div class="tiles" style="margin-top:12px">
        <div class="tile"><b class="count">${t.season_points}</b><span>actual points so far</span></div>
        <div class="tile"><b class="count">${t.live_gw_points}</b><span>live, this round</span></div>
        <div class="tile"><b class="count">${t.projected_points}</b><span>projected next</span></div>
        <div class="tile"><b>£${t.bank.toFixed(1)}m</b><span>in the bank · ${t.banked_transfers} free transfer${t.banked_transfers === 1 ? "" : "s"}</span></div>
      </div>
      <div class="mini" style="margin:10px 0 12px"><b>This week:</b> ${move}. Captain: <b style="color:var(--green-deep)">${esc(capName)}</b>.</div>
      <div class="pitchwrap" style="aspect-ratio:100/118">
        <svg viewBox="0 0 100 118" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%">
          <defs><linearGradient id="turf2" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#0F7A3C"/><stop offset=".5" stop-color="#0B5E2F"/><stop offset="1" stop-color="#0F7A3C"/></linearGradient></defs>
          <rect width="100" height="118" fill="url(#turf2)"/>
          ${[0,2,4,6].map(i => `<rect y="${i*14.75}" width="100" height="14.75" fill="#fff" opacity=".045"/>`).join("")}
          <g stroke="#fff" stroke-opacity=".35" stroke-width=".7" fill="none">
            <rect x="2" y="2" width="96" height="114" rx="1"/><line x1="2" y1="59" x2="98" y2="59"/>
            <circle cx="50" cy="59" r="11"/><rect x="20" y="2" width="60" height="14"/><rect x="20" y="102" width="60" height="14"/></g>
        </svg>${dots}
      </div>
      <div class="bench"><span class="mini" style="margin-top:0">bench</span>
        ${bench.map(p => `<div class="bp">${p.photo ? `<img src="${esc(p.photo)}" onerror="this.remove()">` : ""}<div>${esc(p.name)}</div><div>£${p.price.toFixed(1)}m</div></div>`).join("")}</div>
      ${t.scores.length ? `<div class="mini" style="margin-top:8px">Finished rounds: ${t.scores.map(s => `GW${s.gw}: <b>${s.points}</b>`).join(" · ")}</div>` : ""}
      <div class="mini" style="margin-top:8px">${esc(t.note)}${t.durable ? "" : " (Warning: durable storage is not connected on this server, so history resets on restart.)"}</div>
    </div>
    <div class="card" style="max-height:900px;overflow:auto">${table(gw.players.slice(0, 40), "Top projected players")}</div>
  </div>`;
  animateIn(out);
}

function renderAbout() {
  const meta = S.meta, live = meta?.live_eval, fe = meta?.fantasy_eval;
  $("#aboutout").innerHTML = `
    <p class="sub">Plus100 is a statistics tool. It estimates the probability of football outcomes from historical data and compares them against sportsbook prices. It is not a sportsbook, takes no bets, and has no tie to any operator.</p>
    ${live ? `<div class="scores" style="margin-bottom:16px">
      <div class="sc"><b>${(live.matches ?? 0).toLocaleString()}</b><span>matches tested</span></div>
      <div class="sc"><b style="color:var(--green-deep)">${(live.model_accuracy*100).toFixed(1)}%</b><span>model accuracy</span></div>
      <div class="sc"><b style="color:var(--blue)">${(live.book_accuracy*100).toFixed(1)}%</b><span>bookmakers</span></div></div>` : ""}
    <h3 class="sec">Probabilities are not certainty</h3>
    <p class="sub">A 60% chance fails 4 times in 10. Even the strongest flagged bet loses regularly; edges only appear across many bets. There is no 100% win rate, here or anywhere.</p>
    <h3 class="sec">Measured, honest accuracy</h3>
    <p class="sub">Accuracy is re-measured automatically at every data refresh. Random guessing on three outcomes gets 33%; always picking the home side about 44%. Probabilities are calibrated: when we say 40%, it happens about 40% of the time.</p>
    ${fe ? `<h3 class="sec">How good the fantasy projections are</h3><p class="sub">${esc(fe.note)}</p>` : ""}
    <h3 class="sec">Where edges really come from</h3>
    <p class="sub">Against closing prices nothing out-predicts the market, including this model — tested on thousands of unseen matches, adding our model to the closing price made predictions slightly worse, not better. Edges come from disagreements between books, soft early prices, and boosts. That is what Vs Market hunts.</p>
    <h3 class="sec">Play it safe</h3>
    <p class="sub">Only bet money you can afford to lose. If it stops being fun, call 1-800-GAMBLER — free, confidential, always open. Nothing here is financial advice; you alone decide whether and how much to bet.</p>`;
}
