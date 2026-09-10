/* Overfished replay viewer: a Pichwai lake, an Ink & Print ledger, one file. */
(() => {
  "use strict";

  const post = (m) => {
    if (window.parent !== window) window.parent.postMessage({ src: "coworld-replay", ...m }, "*");
  };
  post({ type: "loading" });

  const NS = "http://www.w3.org/2000/svg";
  const params = new URLSearchParams(location.search);
  const hashParams = new URLSearchParams(location.hash.slice(1));
  const chromeOff = (hashParams.get("chrome") || params.get("chrome")) === "off";
  const $ = (id) => document.getElementById(id);
  const app = $("app");
  if (chromeOff) app.classList.add("chrome-off");

  const TARGET_SECONDS = 300;
  const SPEEDS = [0.5, 1, 2, 4];
  const W = 1200, H = 800, CX = 600, CY = 400;

  // ---------------------------------------------------------------- data + timeline

  let replay = null;
  let timeline = null; // { events: [{kind, index, start, duration}], total }
  let live = false;
  let liveDone = false;
  let pendingCouncil = null;

  function buildTimeline(r) {
    const nTurns = r.turns.length;
    const nCouncils = r.communes.length;
    const councilSeconds = nCouncils ? clamp(120 / nCouncils, 4, 15) : 0;
    const turnSeconds = nTurns ? clamp((TARGET_SECONDS - councilSeconds * nCouncils) / nTurns, 0.9, 6) : 0;
    const events = [];
    let t = 0;
    let c = 0;
    for (let i = 0; i < nTurns; i++) {
      const turnNumber = r.turns[i].t;
      while (c < nCouncils && r.communes[c].before_turn <= turnNumber) {
        events.push({ kind: "council", index: c, start: t, duration: councilSeconds });
        t += councilSeconds;
        c += 1;
      }
      events.push({ kind: "turn", index: i, start: t, duration: turnSeconds });
      t += turnSeconds;
    }
    while (c < nCouncils) {
      events.push({ kind: "council", index: c, start: t, duration: councilSeconds });
      t += councilSeconds;
      c += 1;
    }
    return { events, total: t };
  }

  function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

  function fishBefore(turnIndex) {
    if (turnIndex <= 0) return replay.players.map(() => 0);
    return replay.turns[turnIndex - 1].fish.slice();
  }

  function stockBefore(turnIndex) {
    if (turnIndex <= 0) return replay.lake.initial_stock;
    return replay.turns[turnIndex - 1].stock_after;
  }

  // ---------------------------------------------------------------- painting

  const svg = $("painting");
  const layers = {};
  let huts = [];
  let boats = [];
  let fishGlyphs = [];
  let catchLabels = [];
  let punishLayer = null;
  let mandala = null;
  let mandalaText = null;
  let lakeCartouche = null;
  let captionBox = null;
  let captionNode = null;

  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function text(x, y, content, attrs, parent) {
    const node = el("text", { x, y, ...attrs }, parent);
    node.textContent = content;
    return node;
  }

  function seatColor(i, n) {
    return `hsl(${Math.round((i * 360) / n + 20)} 52% 64%)`;
  }

  function mulberry32(a) {
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function drawStatic() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const defs = el("defs", {}, svg);
    const fish = el("symbol", { id: "fish", viewBox: "-4 -8 44 16" }, defs);
    el("path", { d: "M0,0 C7,-8 19,-8 27,0 C19,8 7,8 0,0 Z M27,0 L36,-7 L36,7 Z", fill: "#d9dfe8", stroke: "#8a96a8", "stroke-width": "0.8" }, fish);
    el("circle", { cx: 7, cy: -1.5, r: 1.4, fill: "#c99a2e" }, fish);
    el("path", { d: "M12,-1 q6,-4 11,0", fill: "none", stroke: "#a9b3c2", "stroke-width": "0.8" }, fish);
    const petal = el("symbol", { id: "petal", viewBox: "-10 -30 20 34" }, defs);
    el("path", { d: "M0,0 C-10,-12 -8,-26 0,-30 C8,-26 10,-12 0,0 Z", fill: "#f0a9c2", stroke: "#b5567c", "stroke-width": "1" }, petal);
    el("path", { d: "M0,-4 C-4,-12 -3,-22 0,-27", fill: "none", stroke: "#fff1f5", "stroke-width": "1.2" }, petal);

    // everything inside the frame is clipped to it, so leaves and lotus can enter from the edges
    const clip = el("clipPath", { id: "frame-clip" }, defs);
    el("rect", { x: 14, y: 14, width: W - 28, height: H - 28 }, clip);
    const scene = el("g", { "clip-path": "url(#frame-clip)" }, svg);

    // water
    el("rect", { x: 0, y: 0, width: W, height: H, fill: "#1f4270" }, scene);
    const waves = el("g", { opacity: 0.22, stroke: "#8fb3d9", "stroke-width": 1.4, fill: "none" }, scene);
    for (let y = 60; y < H - 40; y += 34) {
      for (let x = 20 + ((y / 34) % 2) * 22; x < W - 20; x += 46) {
        el("path", { d: `M${x},${y} q10,-6 20,0` }, waves);
      }
    }

    layers.fish = el("g", { class: "fish-school" }, scene);
    layers.flora = el("g", {}, scene);
    layers.huts = el("g", {}, scene);
    layers.boats = el("g", {}, scene);
    layers.effects = el("g", {}, scene);
    layers.frame = el("g", {}, svg);

    // flora: banana leaves in corners, lotus along the shore
    const leaf = (x, y, rot, len, flip) => {
      const g = el("g", { transform: `translate(${x} ${y}) rotate(${rot}) scale(${flip ? -1 : 1} 1)` }, layers.flora);
      el("path", { d: `M0,0 C${len * 0.25},-${len * 0.25} ${len * 0.7},-${len * 0.3} ${len},-${len * 0.05} C${len * 0.7},${len * 0.18} ${len * 0.3},${len * 0.22} 0,0 Z`, fill: "#3f7a4c", stroke: "#1f4a2c", "stroke-width": 2 }, g);
      el("path", { d: `M4,0 L${len - 6},-${len * 0.06}`, stroke: "#245a34", "stroke-width": 2.2, fill: "none" }, g);
      for (let i = 1; i < 7; i++) {
        const t = i / 7;
        el("path", { d: `M${len * t},-${len * 0.06 * t} l${len * 0.06},-${len * 0.16}`, stroke: "#245a34", "stroke-width": 1, fill: "none", opacity: 0.8 }, g);
      }
    };
    leaf(20, 30, -20, 220, false); leaf(20, 30, 20, 160, false); leaf(20, 30, 5, 260, false);
    leaf(W - 20, 30, 200, 220, false); leaf(W - 20, 30, 160, 160, false); leaf(W - 20, 30, 175, 260, false);
    leaf(20, H - 30, 20, 200, false); leaf(20, H - 30, -30, 150, false);
    leaf(W - 20, H - 30, 160, 200, false); leaf(W - 20, H - 30, 210, 150, false);

    const lotus = (x, y, s, bloom) => {
      const g = el("g", { transform: `translate(${x} ${y}) scale(${s})` }, layers.flora);
      el("ellipse", { cx: 0, cy: 14, rx: 34, ry: 12, fill: "#4e8b5a", stroke: "#1f4a2c", "stroke-width": 1.6 }, g);
      el("path", { d: "M0,14 L14,6", stroke: "#1f4a2c", "stroke-width": 1.2 }, g);
      for (let a = -60; a <= 240; a += 40) {
        const rad = (a * Math.PI) / 180;
        el("path", { d: `M0,14 L${Math.cos(rad) * 30},${14 + Math.sin(rad) * 10}`, stroke: "#2e6b3d", "stroke-width": 0.8, opacity: 0.7 }, g);
      }
      if (bloom) {
        el("line", { x1: 0, y1: 12, x2: 0, y2: -14, stroke: "#5f9a5f", "stroke-width": 2.4 }, g);
        for (const rot of [-50, -25, 0, 25, 50]) {
          el("use", { href: "#petal", x: -7, y: -14, width: 14, height: 24, transform: `rotate(${rot} 0 -14)` }, g);
        }
        el("circle", { cx: 0, cy: -16, r: 3, fill: "#f2c94c" }, g);
      }
    };
    const lotusSpots = [
      [140, 130, 1.1, true], [230, 90, 0.8, false], [980, 120, 1.0, true], [1070, 170, 0.8, false],
      [120, 660, 1.0, true], [220, 720, 0.8, false], [1010, 700, 1.1, true], [1090, 640, 0.8, false],
      [600, 70, 0.7, false], [600, 745, 0.7, true], [70, 400, 0.8, true], [1130, 400, 0.8, true],
    ];
    for (const [x, y, s, b] of lotusSpots) lotus(x, y, s, b);

    // fish school positions
    const rng = mulberry32(replay.seed || 1);
    fishGlyphs = [];
    for (let i = 0; i < 64; i++) {
      const a = rng() * Math.PI * 2;
      const r = Math.sqrt(rng());
      const x = CX + Math.cos(a) * r * 330;
      const y = CY + Math.sin(a) * r * 190;
      const dir = rng() < 0.5 ? -1 : 1;
      const size = 22 + rng() * 14;
      const node = el("use", { href: "#fish", x: -size / 2, y: -size / 5, width: size, height: size * 0.4, transform: `translate(${x} ${y}) scale(${dir} 1) rotate(${(rng() - 0.5) * 30})` }, layers.fish);
      fishGlyphs.push(node);
    }

    // huts and boats on the shore ellipse
    const n = replay.players.length;
    huts = []; boats = []; catchLabels = [];
    for (let i = 0; i < n; i++) {
      const ang = -Math.PI / 2 + ((i + 0.5) * 2 * Math.PI) / n;
      const hx = CX + Math.cos(ang) * 490;
      const hy = CY + Math.sin(ang) * 300;
      const color = seatColor(i, n);
      const g = el("g", { transform: `translate(${hx} ${hy})` }, layers.huts);
      const glow = el("circle", { class: "hut-glow", cx: 0, cy: -6, r: 62, fill: "rgba(242,201,76,0.22)", stroke: "#f2c94c", "stroke-width": 3 }, g);
      // pier planks
      el("rect", { x: -46, y: 16, width: 92, height: 12, fill: "#8a5a2b", stroke: "#4a2d12", "stroke-width": 1.5 }, g);
      for (let px = -40; px < 46; px += 12) el("line", { x1: px, y1: 16, x2: px, y2: 28, stroke: "#5b3a1e", "stroke-width": 1 }, g);
      // warehouse
      el("rect", { x: -30, y: -20, width: 60, height: 36, fill: "#f4e7cf", stroke: "#4a2d12", "stroke-width": 1.6 }, g);
      el("path", { d: "M-38,-20 L0,-46 L38,-20 Z", fill: "#c85a3a", stroke: "#4a2d12", "stroke-width": 1.6 }, g);
      el("rect", { x: -2, y: -54, width: 4, height: 10, fill: "#c99a2e" }, g);
      el("circle", { cx: 0, cy: -55, r: 3.5, fill: "#c99a2e" }, g);
      el("path", { d: "M-8,16 L-8,-2 A8,8 0 0 1 8,-2 L8,16 Z", fill: "#3b2413" }, g);
      el("rect", { x: -26, y: -14, width: 12, height: 10, fill: color, stroke: "#4a2d12", "stroke-width": 1 }, g);
      el("rect", { x: 14, y: -14, width: 12, height: 10, fill: color, stroke: "#4a2d12", "stroke-width": 1 }, g);
      // plaque
      const below = hy < CY ? -1 : 1;
      const plaqueY = below < 0 ? -78 : 34;
      el("rect", { x: -44, y: plaqueY, width: 88, height: 20, rx: 3, fill: "#7a1f1f", stroke: "#c99a2e", "stroke-width": 1.4 }, g);
      text(0, plaqueY + 14, replay.players[i].pseudonym, { "text-anchor": "middle", fill: "#f6e7b2", "font-family": "Georgia, serif", "font-size": 14, "font-weight": 700 }, g);
      // fish tally
      const tallyY = below < 0 ? -92 : 68;
      const tally = text(0, tallyY, "0 fish", { "text-anchor": "middle", fill: "#f6e7b2", "font-family": "Georgia, serif", "font-size": 13 }, g);
      huts.push({ x: hx, y: hy, ang, glow, tally, color });
      // boat
      const bAng = Math.atan2(CY - hy, CX - hx);
      const deg = (bAng * 180) / Math.PI;
      const home = { x: hx + Math.cos(bAng) * 58, y: hy + Math.sin(bAng) * 40 };
      const bg = el("g", { class: "boat", transform: `translate(${home.x} ${home.y})` }, layers.boats);
      const inner = el("g", { transform: `rotate(${deg})` }, bg);
      el("path", { d: "M-24,0 Q0,16 24,0 Q0,5 -24,0 Z", fill: "#5b3a1e", stroke: "#2b1a0b", "stroke-width": 1.5 }, inner);
      el("path", { d: "M-24,0 q-6,-8 -4,-14", fill: "none", stroke: "#2b1a0b", "stroke-width": 2 }, inner);
      el("circle", { cx: 2, cy: -7, r: 4, fill: "#e8b98a", stroke: "#2b1a0b", "stroke-width": 1 }, inner);
      el("rect", { x: -2, y: -4, width: 8, height: 8, rx: 2, fill: color, stroke: "#2b1a0b", "stroke-width": 1 }, inner);
      el("line", { x1: 8, y1: -2, x2: 22, y2: -14, stroke: "#2b1a0b", "stroke-width": 1.5 }, inner);
      boats.push({ node: bg, home, ang: bAng, out: null });
      const label = text(home.x, home.y - 30, "", { class: "catch-label", "text-anchor": "middle" }, layers.effects);
      catchLabels.push(label);
    }
    punishLayer = el("g", {}, layers.effects);

    // council mandala
    mandala = el("g", { class: "council-mandala", transform: `translate(${CX} ${CY})` }, layers.effects);
    el("circle", { cx: 0, cy: 0, r: 118, fill: "rgba(31,66,112,0.7)", stroke: "#c99a2e", "stroke-width": 3 }, mandala);
    el("circle", { cx: 0, cy: 0, r: 104, fill: "none", stroke: "#f6e7b2", "stroke-width": 1, "stroke-dasharray": "3 5" }, mandala);
    for (let a = 0; a < 360; a += 30) {
      el("use", { href: "#petal", x: -12, y: -100, width: 24, height: 40, transform: `rotate(${a})` }, mandala);
    }
    el("circle", { cx: 0, cy: 0, r: 46, fill: "#f2c94c", stroke: "#b5567c", "stroke-width": 2 }, mandala);
    text(0, -6, "COUNCIL", { "text-anchor": "middle", fill: "#4a2d12", "font-family": "Georgia, serif", "font-size": 13, "font-weight": 700, "letter-spacing": 2 }, mandala);
    mandalaText = text(0, 12, "", { "text-anchor": "middle", fill: "#4a2d12", "font-family": "Georgia, serif", "font-size": 12 }, mandala);

    // council caption inside the painting, between the mandala and the bottom piers
    captionBox = el("foreignObject", { class: "caption-box", x: 250, y: 540, width: 700, height: 92 }, layers.effects);
    captionNode = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
    captionNode.className = "caption";
    captionBox.appendChild(captionNode);

    // lake cartouche (spectator-only truth)
    const cart = el("g", { transform: `translate(${CX} 34)` }, layers.frame);
    el("rect", { x: -150, y: -18, width: 300, height: 30, rx: 4, fill: "rgba(255,250,240,0.92)", stroke: "#c99a2e", "stroke-width": 1.5 }, cart);
    lakeCartouche = text(0, 3, "", { "text-anchor": "middle", fill: "#111827", "font-family": "Georgia, serif", "font-size": 14 }, cart);

    // frame
    el("rect", { x: 7, y: 7, width: W - 14, height: H - 14, fill: "none", stroke: "#c99a2e", "stroke-width": 14 }, layers.frame);
    el("rect", { x: 17, y: 17, width: W - 34, height: H - 34, fill: "none", stroke: "#7a1f1f", "stroke-width": 2 }, layers.frame);
  }

  function setBoat(i, effort, out) {
    const b = boats[i];
    if (out) {
      const d = 40 + effort * 260;
      const x = b.home.x + Math.cos(b.ang) * d;
      const y = b.home.y + Math.sin(b.ang) * d * 0.72;
      b.node.setAttribute("transform", `translate(${x} ${y})`);
      catchLabels[i].setAttribute("x", x);
      catchLabels[i].setAttribute("y", y - 26);
    } else {
      b.node.setAttribute("transform", `translate(${b.home.x} ${b.home.y})`);
    }
  }

  function renderFish(stock) {
    const share = clamp(stock / replay.lake.capacity, 0, 1);
    const dying = stock < replay.lake.collapse_threshold;
    const visible = Math.round(share * fishGlyphs.length);
    fishGlyphs.forEach((g, i) => {
      g.style.opacity = i < visible ? "1" : "0";
      g.style.filter = dying ? "grayscale(1) brightness(0.7)" : "";
    });
    const pct = Math.round(share * 100);
    lakeCartouche.textContent = `Lake ${Math.round(stock)} fish · ${pct}% of ${Math.round(replay.lake.capacity)}${dying ? " · past the point of no return" : ""}`;
    $("lake-text").textContent = `${Math.round(stock)} fish, ${pct}% of capacity ${Math.round(replay.lake.capacity)}. Point of no return at ${Math.round(replay.lake.collapse_threshold)}.`;
    const fill = $("lake-fill");
    fill.style.width = `${pct}%`;
    fill.classList.toggle("dying", dying);
    $("lake-threshold").style.left = `${(replay.lake.collapse_threshold / replay.lake.capacity) * 100}%`;
  }

  function renderTally(fish) {
    huts.forEach((h, i) => { h.tally.textContent = `${fish[i]} fish`; });
  }

  function clearPunish() {
    while (punishLayer.firstChild) punishLayer.removeChild(punishLayer.firstChild);
  }

  function renderPunish(turn, show) {
    clearPunish();
    if (!show) return;
    for (const p of turn.punish) {
      const a = huts[p.frm], b = huts[p.to];
      const mx = (a.x + b.x) / 2 + (CY - (a.y + b.y) / 2) * 0.25;
      const my = (a.y + b.y) / 2 + (CX - (a.x + b.x) / 2) * -0.25;
      const line = el("path", { class: "punish-line show", d: `M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}` }, punishLayer);
      line.setAttribute("marker-end", "");
      const lx = (a.x + 2 * mx + b.x) / 4, ly = (a.y + 2 * my + b.y) / 4;
      text(lx, ly - 6, `−${p.fish}`, { class: "punish-label show", "text-anchor": "middle" }, punishLayer);
      text(a.x, a.y + 44, `burned ${p.cost}`, { class: "punish-label show", "text-anchor": "middle", "font-size": 13 }, punishLayer);
    }
  }

  // ---------------------------------------------------------------- panel

  const ledgerBody = $("ledger-table").querySelector("tbody");

  function renderLedger(fish, lastTurn, upTo) {
    const n = replay.players.length;
    const totals = replay.players.map(() => 0);
    const hits = replay.players.map(() => 0);
    for (let i = 0; i < upTo; i++) {
      replay.turns[i].catch.forEach((c, s) => { totals[s] += c; });
    }
    if (lastTurn) for (const p of lastTurn.punish) hits[p.to] += p.fish;
    const order = replay.players.map((_, i) => i).sort((a, b) => fish[b] - fish[a] || a - b);
    ledgerBody.innerHTML = "";
    order.forEach((s, rank) => {
      const tr = document.createElement("tr");
      const player = replay.players[s];
      const catchTxt = lastTurn ? `+${lastTurn.catch[s]}` : "";
      const hitTxt = hits[s] ? ` <span class="hit">−${hits[s]}</span>` : "";
      tr.innerHTML = `<td class="num">${rank + 1}</td>` +
        `<td class="name"><span class="swatch" style="background:${seatColor(s, n)}"></span>${escapeHtml(player.pseudonym)}` +
        `<span class="model">${escapeHtml(player.policy)}${player.model ? " · " + escapeHtml(player.model) : ""}</span></td>` +
        `<td class="num">${fish[s]}</td>` +
        `<td class="num"><span class="delta ${lastTurn && lastTurn.catch[s] === 0 ? "zero" : ""}">${catchTxt}</span>${hitTxt}</td>` +
        `<td class="num">${totals[s]}</td>`;
      ledgerBody.appendChild(tr);
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const councilBody = $("council-body");
  const councilTitle = $("council-title");

  function renderCouncil(commune, visibleCount, showCaption) {
    if (!commune) {
      councilTitle.textContent = "Council";
      councilBody.innerHTML = '<p class="empty">No council yet.</p>';
      captionBox.classList.remove("show");
      mandala.classList.remove("show");
      huts.forEach((h) => h.glow.classList.remove("show"));
      return;
    }
    councilTitle.textContent = `Council before turn ${commune.before_turn}`;
    councilBody.innerHTML = "";
    let k = 0;
    let latest = null;
    commune.rounds.forEach((speeches, r) => {
      const label = document.createElement("div");
      label.className = "round";
      label.textContent = `Round ${r + 1}`;
      councilBody.appendChild(label);
      for (const s of speeches) {
        if (visibleCount !== null && k >= visibleCount) return;
        k += 1;
        const p = document.createElement("p");
        p.className = "speech" + (s.text ? "" : " silent") + (s.auto ? " auto" : "");
        const name = replay.players[s.slot].pseudonym;
        p.innerHTML = `<b>${escapeHtml(name)}</b> ${s.text ? escapeHtml(s.text) : "says nothing."}`;
        councilBody.appendChild(p);
        latest = s;
      }
    });
    councilBody.scrollTop = councilBody.scrollHeight;
    huts.forEach((h, i) => h.glow.classList.toggle("show", showCaption && !!latest && latest.slot === i));
    if (showCaption && latest) {
      captionBox.classList.add("show");
      captionNode.innerHTML = `<b>${escapeHtml(replay.players[latest.slot].pseudonym)}</b> ${latest.text ? escapeHtml(latest.text) : "<i>says nothing.</i>"}`;
    } else {
      captionBox.classList.remove("show");
    }
  }

  // ---------------------------------------------------------------- frames

  let lastEventKey = "";

  function renderTurnFrame(turnIndex, p) {
    const turn = replay.turns[turnIndex];
    const key = `turn:${turnIndex}`;
    const fresh = key !== lastEventKey;
    lastEventKey = key;
    if (fresh) {
      mandala.classList.remove("show");
      huts.forEach((h) => h.glow.classList.remove("show"));
      const lastCouncil = replay.communes.filter((c) => c.before_turn <= turn.t).pop() || null;
      renderCouncil(lastCouncil, null, false);
      $("eyebrow").innerHTML = `Turn ${turn.t} of ${replay.game.turns}${nextCouncilText(turn.t)}${live ? liveChip() : ""}`;
    }
    const out = p < 0.62;
    boats.forEach((_, i) => setBoat(i, turn.effort[i], out));
    const showCatch = p > 0.3 && p < 0.9;
    catchLabels.forEach((label, i) => {
      label.textContent = `+${turn.catch[i]}`;
      label.classList.toggle("show", showCatch);
    });
    renderPunish(turn, p > 0.45 && p < 0.95);
    const settled = p > 0.5;
    renderFish(settled ? turn.stock_after : turn.stock_before);
    const fish = settled ? turn.fish : fishBefore(turnIndex);
    renderTally(fish);
    renderLedger(fish, settled ? turn : (turnIndex > 0 ? replay.turns[turnIndex - 1] : null), settled ? turnIndex + 1 : turnIndex);
  }

  function nextCouncilText(t) {
    const next = replay.communes.find((c) => c.before_turn > t);
    if (!next) return "";
    const away = next.before_turn - t - 1;
    return away === 0 ? " · council next" : ` · council in ${away} turn${away === 1 ? "" : "s"}`;
  }

  function liveChip() { return '<span class="live-chip">Live</span>'; }

  function renderCouncilFrame(councilIndex, p) {
    const commune = replay.communes[councilIndex];
    const key = `council:${councilIndex}`;
    const fresh = key !== lastEventKey;
    lastEventKey = key;
    const turnIndex = commune.before_turn - 1;
    if (fresh) {
      boats.forEach((_, i) => setBoat(i, 0, false));
      catchLabels.forEach((l) => l.classList.remove("show"));
      clearPunish();
      mandala.classList.add("show");
      mandalaText.textContent = `before turn ${commune.before_turn}`;
      renderFish(stockBefore(turnIndex));
      const fish = fishBefore(turnIndex);
      renderTally(fish);
      renderLedger(fish, turnIndex > 0 ? replay.turns[turnIndex - 1] : null, turnIndex);
      $("eyebrow").innerHTML = `Council before turn ${commune.before_turn}${live ? liveChip() : ""}`;
    }
    const total = commune.rounds.reduce((a, r) => a + r.length, 0);
    const visible = p === null ? total : Math.min(total, Math.floor(p * (total + 1)));
    renderCouncil(commune, visible, true);
  }

  function renderAt(time) {
    if (!timeline.events.length) return;
    let ev = timeline.events[timeline.events.length - 1];
    for (const e of timeline.events) {
      if (time < e.start + e.duration) { ev = e; break; }
    }
    const p = clamp((time - ev.start) / ev.duration, 0, 1);
    if (ev.kind === "turn") renderTurnFrame(ev.index, p);
    else renderCouncilFrame(ev.index, p);
  }

  function renderLatest() {
    if (pendingCouncil) {
      replay.communes.push(pendingCouncil);
      renderCouncilFrame(replay.communes.length - 1, null);
      replay.communes.pop();
      lastEventKey = "";
      return;
    }
    if (replay.turns.length) renderTurnFrame(replay.turns.length - 1, 1);
    else if (replay.communes.length) renderCouncilFrame(replay.communes.length - 1, null);
    else {
      renderFish(replay.lake.initial_stock);
      renderTally(replay.players.map(() => 0));
      renderLedger(replay.players.map(() => 0), null, 0);
      $("eyebrow").innerHTML = `Waiting for turn 1${liveChip()}`;
    }
    if (liveDone) $("eyebrow").innerHTML = `Episode over · ${replay.turns.length} turns`;
  }

  // ---------------------------------------------------------------- playback

  let time = 0, playing = true, speedIndex = 1, lastTick = null, rafId = null;

  function fmt(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function tick(now) {
    if (lastTick !== null && playing) {
      time += ((now - lastTick) / 1000) * SPEEDS[speedIndex];
      if (time >= timeline.total) { time = 0; lastEventKey = ""; }
    }
    lastTick = now;
    renderAt(time);
    $("scrub-fill").style.width = `${(time / timeline.total) * 100}%`;
    $("clock").textContent = `${fmt(time)} / ${fmt(timeline.total)}`;
    rafId = requestAnimationFrame(tick);
  }

  function wireTransport() {
    const play = $("play");
    play.addEventListener("click", () => { playing = !playing; play.textContent = playing ? "Pause" : "Play"; });
    const speed = $("speed");
    speed.addEventListener("click", () => { speedIndex = (speedIndex + 1) % SPEEDS.length; speed.textContent = `${SPEEDS[speedIndex]}×`; });
    const scrub = $("scrub");
    const seek = (ev) => {
      const r = scrub.getBoundingClientRect();
      time = clamp((ev.clientX - r.left) / r.width, 0, 0.999) * timeline.total;
      lastEventKey = "";
    };
    scrub.addEventListener("click", seek);
    const marks = $("scrub-marks");
    for (const e of timeline.events) {
      if (e.kind !== "council") continue;
      const i = document.createElement("i");
      i.style.left = `${(e.start / timeline.total) * 100}%`;
      marks.appendChild(i);
    }
    document.addEventListener("keydown", (ev) => {
      if (ev.key === " ") { ev.preventDefault(); play.click(); }
      if (ev.key === "ArrowRight") { time = Math.min(timeline.total - 0.01, time + 5); lastEventKey = ""; }
      if (ev.key === "ArrowLeft") { time = Math.max(0, time - 5); lastEventKey = ""; }
    });
  }

  // ---------------------------------------------------------------- loading

  function validate(r) {
    if (!r || r.schema !== "overfished-replay/1") throw new Error(`unexpected replay schema: ${r && r.schema}`);
    if (!Array.isArray(r.players) || !Array.isArray(r.turns) || !Array.isArray(r.communes) || !r.lake) {
      throw new Error("replay is missing players, turns, communes, or lake");
    }
  }

  async function inflate(bytes, format) {
    const ds = new DecompressionStream(format);
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  async function loadReplay(url) {
    post({ type: "phase", phase: "bundle_ready" });
    post({ type: "phase", phase: "replay_fetch_start" });
    const response = await fetch(url);
    if (!response.ok) throw new Error(`replay fetch failed: HTTP ${response.status}`);
    let bytes = new Uint8Array(await response.arrayBuffer());
    const gzip = bytes[0] === 0x1f && bytes[1] === 0x8b;
    const zlib = (bytes[0] & 15) === 8 && bytes[0] >> 4 <= 7 && ((bytes[0] << 8) | bytes[1]) % 31 === 0;
    post({ type: "phase", phase: "replay_fetch_end", bytes: bytes.byteLength, compressed: gzip || zlib });
    if (gzip) bytes = await inflate(bytes, "gzip");
    else if (zlib) bytes = await inflate(bytes, "deflate");
    const parsed = JSON.parse(new TextDecoder().decode(bytes));
    validate(parsed);
    post({ type: "phase", phase: "replay_parsed" });
    return parsed;
  }

  function fail(message) {
    const overlay = $("overlay");
    overlay.hidden = false;
    overlay.classList.add("error");
    overlay.textContent = `Overfished could not show this replay.\n${message}`;
    post({ type: "error", message });
  }

  function startReplay(r) {
    replay = r;
    timeline = buildTimeline(replay);
    drawStatic();
    wireTransport();
    $("overlay").hidden = true;
    const startAt = parseFloat(hashParams.get("t") || params.get("t") || "0");
    if (Number.isFinite(startAt) && startAt > 0) time = clamp(startAt, 0, timeline.total - 0.01);
    if ((hashParams.get("paused") || params.get("paused")) === "1") { playing = false; $("play").textContent = "Play"; }
    renderAt(time);
    setTimeout(() => post({ type: "ready" }), 0);
    rafId = requestAnimationFrame(tick);
  }

  function startLive() {
    live = true;
    $("transport").hidden = true;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/global`);
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "snapshot" || msg.type === "replay") {
        validate(msg.replay);
        replay = msg.replay;
        timeline = buildTimeline(replay);
        drawStatic();
        $("overlay").hidden = true;
        liveDone = msg.type === "snapshot" ? !msg.live : true;
        renderLatest();
        setTimeout(() => post({ type: "ready" }), 0);
        return;
      }
      if (!replay) return;
      if (msg.type === "turn") { replay.turns.push(msg.turn); pendingCouncil = null; renderLatest(); }
      if (msg.type === "speech") {
        if (!pendingCouncil || pendingCouncil.before_turn !== msg.before_turn) pendingCouncil = { before_turn: msg.before_turn, rounds: [] };
        pendingCouncil.rounds[msg.round] = msg.speeches;
        renderLatest();
      }
      if (msg.type === "commune") { pendingCouncil = null; replay.communes.push(msg.commune); renderLatest(); }
      if (msg.type === "end") { liveDone = true; renderLatest(); }
    };
    ws.onerror = () => fail("live connection failed");
    ws.onclose = () => { if (!replay) fail("live connection closed before any state arrived"); };
  }

  const replayUrl = hashParams.get("replay") || params.get("replay");
  const liveRequested = (hashParams.get("live") || params.get("live")) === "1";
  if (replayUrl) {
    loadReplay(replayUrl).then(startReplay).catch((error) => fail(error.message));
  } else if (liveRequested || location.pathname.endsWith("/client/global")) {
    startLive();
  } else if (location.pathname.endsWith("/client/replay")) {
    loadReplay("/replay.json").then(startReplay).catch((error) => fail(error.message));
  } else {
    fail("no replay given: open index.html#replay=<url>");
  }
})();
