/* Overfished replay viewer: a Pattachitra lake with miniature-painting fishers, an Ink & Print ledger, one file. */
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
  const FRAME = 32; // outer red band + gold + ink + patterned band + ink
  const LAKE_RX = 430, LAKE_RY = 250; // the water ellipse
  const SEAT_RX = 470, SEAT_RY = 275; // the bank ring the warehouses stand on

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

  // ---------------------------------------------------------------- palette

  const INK = "#2b1a10"; // the one dark outline the whole painting shares
  const CREAM = "#f3e7cc";
  const GOLD = "#dfb44a";
  const RED = "#c8452b";
  const GROUND = "#8fb1ab"; // flat teal bank, as in the elephant folio
  const WATER = "#274f8c";
  const WATER_SCALE = "#2f5f9e";
  const CREST = "#dfe9f3";

  // ---------------------------------------------------------------- avatars: miniature-painting busts

  // One bust per pseudonym (src/overfished/names.py). `color` is the seat's identity everywhere:
  // garment, ledger swatch, warehouse trim, rower's turban.
  const AVATARS = {
    Kamal: { color: "#c8452b", skin: "#d9a06b", ground: "#7fa39b", gear: "pagri", gearColor: "#f3ecd8", plume: true, beard: "moustache", cloth: "cloth-stripes" },
    Neela: { color: "#3b4f8c", skin: "#e6c39d", ground: "#c99a3a", gear: "veil", gearColor: "#3b4f8c", bindi: true, earring: "drop", necklace: true, cloth: "cloth-dots" },
    Hansa: { color: "#3f8f9a", skin: "#e2b98f", ground: "#5a3348", gear: "veil", gearColor: "#f3ecd8", bindi: true, earring: "ring", necklace: true },
    Meena: { color: "#3f7f5c", skin: "#c78e5c", ground: "#d9b25a", gear: "bun", earring: "drop", necklace: true, bindi: true, cloth: "cloth-dots" },
    Padma: { color: "#c24a7a", skin: "#e6c39d", ground: "#4c6a5a", gear: "veil", gearColor: "#e58aa8", lotus: true, earring: "ring", bindi: true },
    Tara: { color: "#5d8fc7", skin: "#e2b98f", ground: "#2f3d66", gear: "tiara", gearColor: "#e5b53f", earring: "drop", necklace: true, bindi: true, cloth: "cloth-dots" },
    Ravi: { color: "#e39a2f", skin: "#c78e5c", ground: "#7fa39b", gear: "safa", gearColor: "#e39a2f", beard: "full", cloth: "cloth-stripes" },
    Sarasi: { color: "#7a4f9a", skin: "#d9a06b", ground: "#c99a3a", gear: "veil", gearColor: "#9a6cb8", nosering: true, earring: "ring", bindi: true },
    Jalaj: { color: "#b6862a", skin: "#b57a4c", ground: "#6f8fa6", gear: "topi", gearColor: "#f3ecd8", beard: "moustache", cloth: "cloth-stripes" },
    Indu: { color: "#6d3a5d", skin: "#e6c39d", ground: "#3f5f7a", gear: "veil", gearColor: "#f3ecd8", moon: true, earring: "drop", necklace: true },
    Kairav: { color: "#e0704f", skin: "#cf9a6a", ground: "#5b7d6a", gear: "pagri", gearColor: "#e0704f", plume: true, cloth: "cloth-dots" },
    Manasa: { color: "#7f8a3a", skin: "#c78e5c", ground: "#8b4a3a", gear: "bun", earring: "ring", necklace: true, bindi: true, cloth: "cloth-stripes" },
    Nalini: { color: "#d4b02c", skin: "#d9a06b", ground: "#3d6b6b", gear: "veil", gearColor: "#d4b02c", lotus: true, earring: "drop", bindi: true, cloth: "cloth-dots" },
    Ambu: { color: "#2f7a7a", skin: "#b57a4c", ground: "#c9a86a", gear: "safa", gearColor: "#f3ecd8", beard: "full", beardColor: "#bdb4a4", hair: "#8c8378" },
    Pushkar: { color: "#9c2436", skin: "#d9a06b", ground: "#7fa39b", gear: "mukut", gearColor: "#e5b53f", beard: "moustache", earring: "ring", necklace: true, cloth: "cloth-dots" },
    Varsha: { color: "#a5522a", skin: "#e2b98f", ground: "#5f7f5f", gear: "veil", gearColor: "#7c8fa8", earring: "drop", bindi: true, necklace: true, cloth: "cloth-stripes" },
  };
  const AVATAR_LIST = Object.values(AVATARS);
  let seatSpecs = [];

  function drawBust(defs, id, s) {
    const sym = el("symbol", { id, viewBox: "0 0 60 72" }, defs);
    const P = (d, attrs) => el("path", { d, ...attrs }, sym);
    const C = (cx, cy, r, attrs) => el("circle", { cx, cy, r, ...attrs }, sym);
    const hair = s.hair || "#221611";
    const line = (w) => ({ fill: "none", stroke: INK, "stroke-width": w, "stroke-linecap": "round" });
    el("rect", { x: 0, y: 0, width: 60, height: 72, fill: s.ground }, sym);
    // hair mass behind the head
    P("M24,14 C30,5 47,6 46,24 C46,33 45,41 40,47 L30,44 Z", { fill: hair });
    // neck, then shoulders and garment
    P("M27,40 L27,53 L39,53 L39,40 Z", { fill: s.skin, stroke: INK, "stroke-width": 1 });
    const shoulders = "M4,72 C6,57 17,50 32,49 C47,50 55,57 57,72 Z";
    P(shoulders, { fill: s.color, stroke: INK, "stroke-width": 1.2 });
    if (s.cloth) P(shoulders, { fill: `url(#${s.cloth})` });
    P("M25,52.5 C28,58 37,58 41,52.5", line(0.9));
    // head in profile, facing left; the miniature convention keeps the eye frontal
    P("M25,13 C22,16 21,21 21,25 C20,28 18,30 16.5,31.5 C17.5,32.6 19.6,32.6 20,33.6 C20,35 19,36 19,37.5 C19,39 20,40 21,40.5 C22,41 23,43 25,44 C29,46 34,46.4 37,44.4 C40,42.4 42,39.4 42,36 L43,26 C44,18 41,11 34,10 C30,9.5 27,10.5 25,13 Z",
      { fill: s.skin, stroke: INK, "stroke-width": 1.1, "stroke-linejoin": "round" });
    P("M22,26.5 C24.5,23.4 30,23.4 33,26.5 C30,29.6 24.5,29.6 22,26.5 Z", { fill: "#f7efe1", stroke: INK, "stroke-width": 0.8 });
    C(26.6, 26.7, 1.9, { fill: INK });
    P("M21,26.2 L34.6,26.9", line(0.7));
    P("M21.5,22.4 C25,20.4 30.5,20.4 34.2,22.6", line(1.1));
    P("M18.4,32.3 l1.6,0", line(0.7));
    P("M18.8,36.7 C19.8,36 20.9,36 21.7,36.7", { fill: "none", stroke: "#b3402e", "stroke-width": 1.2, "stroke-linecap": "round" });
    P("M19.6,38.7 C20.6,38.3 21.4,38.4 22,38.9", line(0.6));
    P("M40.5,27 C44,24.5 45.6,30 41.6,33.2", { fill: s.skin, stroke: INK, "stroke-width": 0.8 });
    if (s.earring) {
      C(41.6, 35.6, 1.7, { fill: GOLD, stroke: INK, "stroke-width": 0.6 });
      if (s.earring === "drop") {
        P("M41.6,37.2 L41.6,41", { stroke: GOLD, "stroke-width": 1 });
        C(41.6, 42.2, 1.5, { fill: "#f3ede0", stroke: INK, "stroke-width": 0.5 });
      }
    }
    if (s.beard === "moustache" || s.beard === "full") {
      P("M19.4,34.3 C22,33 24.6,33.4 26.2,34.8 C25.4,33.9 24.8,34.8 25.6,35.4", { fill: "none", stroke: s.beardColor || hair, "stroke-width": 1.2, "stroke-linecap": "round" });
    }
    if (s.beard === "full") {
      P("M21,40.5 C22,44.5 26,47.5 31,47.5 C36,47.5 40,44.5 42,38 L43.2,30 C43.6,40 42,49 36,51 C29,53 22,49 20,41.5 Z", { fill: s.beardColor || hair, stroke: INK, "stroke-width": 0.8 });
      P("M24,44 C28,49 35,49 40,44", { fill: "none", stroke: INK, "stroke-width": 0.5, opacity: 0.6 });
    }
    if (s.bindi) C(22.3, 21.4, 1.1, { fill: RED });
    if (s.moon) P("M23.6,19 A2.4,2.4 0 1 0 23.6,23.6 A1.9,1.9 0 1 1 23.6,19 Z", { fill: RED });
    if (s.nosering) C(16.6, 33.6, 1.3, { fill: "none", stroke: GOLD, "stroke-width": 0.9 });
    if (s.necklace) {
      for (let i = 0; i < 6; i++) {
        const t = i / 5;
        C(26 + t * 14, 53 + Math.sin(t * Math.PI) * 4.5, 1.15, { fill: "#f3ede0", stroke: INK, "stroke-width": 0.5 });
      }
      C(33, 58.6, 1.6, { fill: RED, stroke: INK, "stroke-width": 0.5 });
    }
    const gc = s.gearColor;
    const gear = { fill: gc, stroke: INK, "stroke-width": 1.1, "stroke-linejoin": "round" };
    if (s.gear === "pagri") {
      P("M17,22 C16,9 28,3 38,5 C46,7 48.5,14 46.5,22 Z", gear);
      P("M18,18 C26,12 38,11 46.5,15", line(0.7));
      P("M17.4,21 C27,16.5 38,16 46.5,19", line(0.7));
      P("M17,22 L46.5,22 L46.5,24.2 L17,24.2 Z", { fill: s.color, stroke: INK, "stroke-width": 0.8 });
      if (s.plume) {
        P("M24,11 C23,4 27,0 31,1 C28,2.5 26,5.5 26,11", { fill: "#f3ede0", stroke: INK, "stroke-width": 0.7 });
        C(24.5, 12.5, 2.3, { fill: GOLD, stroke: INK, "stroke-width": 0.7 });
        C(24.5, 12.5, 0.9, { fill: RED });
      }
    } else if (s.gear === "safa") {
      P("M46,15 C53,15 56,23 52,33 C51.5,27 49.5,23.5 46,22 Z", gear);
      P("M17,22 C17,10 26,3.5 36,4 C46,4.5 48.5,12 46.5,22 Z", gear);
      P("M18.5,15 C28,9 40,9 46.5,13", line(0.7));
      P("M17.5,20 C28,14.5 40,14 46.5,18", line(0.7));
      C(32, 5, 1.8, { fill: GOLD, stroke: INK, "stroke-width": 0.6 });
    } else if (s.gear === "topi") {
      P("M19,21.5 C19,11 30,7 40,8 C45.5,9 46.5,15 45.5,21.5 Z", gear);
      P("M19,21.5 L45.5,21.5", line(1));
      P("M22,15 C30,11 38,11 44,13", { fill: "none", stroke: INK, "stroke-width": 0.6, "stroke-dasharray": "1.2 1.8" });
    } else if (s.gear === "mukut") {
      P("M22,24 C24,17 32,13 41,14 L44,22 C36,19 28,20 22,24 Z", { fill: hair });
      P("M18,17 L22,6 L26.5,16 L32,3 L37.5,16 L42,6 L46,17 L46,22 L18,22 Z", gear);
      C(22, 9.5, 1.3, { fill: RED }); C(32, 7, 1.5, { fill: RED }); C(42, 9.5, 1.3, { fill: RED });
      P("M18,19.5 L46,19.5", { stroke: RED, "stroke-width": 1.2 });
    } else if (s.gear === "tiara") {
      P("M22,25 C23,16 32,11 42,13 L45,21 C36,17 27,19 22,25 Z", { fill: hair });
      C(45, 23, 6.5, { fill: hair, stroke: INK, "stroke-width": 0.8 });
      P("M20,20 C26,15 34,13.5 43,15.5", { fill: "none", stroke: GOLD, "stroke-width": 2.2 });
      P("M29,15.5 L30.5,10.5 L32,15.5 L35,13.5 L32.5,16.5 L37,17 L32.5,18 L34.5,21.5 L31.5,18.5 L30,23 L29.2,18.5 L26,21 L28,17.5 L24,17 L28,16 L25,13.5 Z", { fill: GOLD, stroke: INK, "stroke-width": 0.5 });
      C(30.6, 16.9, 1, { fill: RED });
    } else if (s.gear === "bun") {
      P("M22,25 C23,16 32,11 42,13 L45,21 C36,17 27,19 22,25 Z", { fill: hair });
      P("M24,24 C28,18 35,15.5 41,16", { fill: "none", stroke: "#4a342a", "stroke-width": 0.6 });
      C(46, 22, 7, { fill: hair, stroke: INK, "stroke-width": 0.8 });
      P("M41,18 C45,16 50,18 51,23", { fill: "none", stroke: "#4a342a", "stroke-width": 0.6 });
      for (const [fx, fy] of [[50, 17], [53, 22.5], [49.5, 27]]) {
        C(fx, fy, 2.4, { fill: s.flower || "#f0d4c0", stroke: INK, "stroke-width": 0.5 });
        C(fx, fy, 0.8, { fill: GOLD });
      }
    } else if (s.gear === "veil") {
      P("M21,26 C23,18 30,15 38,16 L40,23 C32,20 25,21 21,26 Z", { fill: hair });
      P("M14.5,27 C13,9 36,-2 48,10 C54,16 54,30 55,44 L57.5,72 L45,72 C46,55 47,44 46,36 C45,26 40,18.5 32,18.5 C26,18.5 18.5,22 14.5,27 Z",
        { fill: gc, "fill-opacity": 0.96, stroke: INK, "stroke-width": 1.1, "stroke-linejoin": "round" });
      P("M15.5,26 C19.5,22.5 26,19.5 32.5,19.5", { fill: "none", stroke: GOLD, "stroke-width": 1.1, "stroke-dasharray": "1.4 2" });
      P("M47,38 C48,48 47,60 46.5,71", { fill: "none", stroke: GOLD, "stroke-width": 0.9, "stroke-dasharray": "1.4 2" });
      if (s.lotus) {
        for (const rot of [-40, -15, 10, 35]) {
          P("M0,0 C-2.2,-3 -2,-7 0,-8.5 C2,-7 2.2,-3 0,0 Z", { fill: "#f0a9c2", stroke: INK, "stroke-width": 0.5, transform: `translate(22 22) rotate(${rot})` });
        }
        C(22, 21.4, 1.3, { fill: GOLD });
      }
    }
    return sym;
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

  function mulberry32(a) {
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function drawDefs() {
    const defs = el("defs", {}, svg);

    // garment cloths for the busts
    const dots = el("pattern", { id: "cloth-dots", width: 5, height: 5, patternUnits: "userSpaceOnUse" }, defs);
    el("circle", { cx: 2.5, cy: 2.5, r: 0.8, fill: "#f7efe1", opacity: 0.85 }, dots);
    const stripes = el("pattern", { id: "cloth-stripes", width: 4, height: 4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(35)" }, defs);
    el("rect", { x: 0, y: 0, width: 1.1, height: 4, fill: "#f7efe1", opacity: 0.55 }, stripes);

    // Madhubani fish: one ink, cross-hatched bands, curling fins, ringed eye
    const hatch = el("pattern", { id: "fish-hatch", width: 2.4, height: 2.4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
    el("rect", { x: 0, y: 0, width: 0.55, height: 2.4, fill: INK }, hatch);
    el("rect", { x: 0, y: 0, width: 2.4, height: 0.55, fill: INK }, hatch);
    const scales = el("pattern", { id: "fish-scales", width: 5, height: 3.4, patternUnits: "userSpaceOnUse" }, defs);
    el("path", { d: "M0,3.4 a2.5,2.5 0 0 1 5,0", fill: "none", stroke: INK, "stroke-width": 0.55 }, scales);
    el("path", { d: "M-2.5,1.7 a2.5,2.5 0 0 1 5,0 M2.5,1.7 a2.5,2.5 0 0 1 5,0", fill: "none", stroke: INK, "stroke-width": 0.55 }, scales);
    const vhatch = el("pattern", { id: "fish-vhatch", width: 1.9, height: 4, patternUnits: "userSpaceOnUse" }, defs);
    el("rect", { x: 0, y: 0, width: 0.5, height: 4, fill: INK }, vhatch);
    const fish = el("symbol", { id: "fish", viewBox: "-3 -13 56 26" }, defs);
    const body = "M0,0 C5,-9 24,-10 35,0 C24,10 5,9 0,0 Z";
    const fishClip = el("clipPath", { id: "fish-body" }, defs);
    el("path", { d: body }, fishClip);
    const finInk = { fill: "none", stroke: INK, "stroke-width": 0.9, "stroke-linecap": "round" };
    // curling fins (pectoral, dorsal, ventral) drawn before the body so they sit behind its edge
    el("path", { d: "M11,-6 C11,-11 16,-13 18,-9.5 C18.6,-7.6 16.4,-7.4 16.6,-9", finInk }, fish);
    el("path", { d: "M11,6 C11,11 16,13 18,9.5 C18.6,7.6 16.4,7.4 16.6,9", finInk }, fish);
    el("path", { d: "M21,-7 C22,-11.5 27,-12 28,-9 C28.4,-7.4 26.4,-7.2 26.6,-8.6", finInk }, fish);
    el("path", { d: "M21,7 C22,11.5 27,12 28,9 C28.4,7.4 26.4,7.2 26.6,8.6", finInk }, fish);
    // tail: forked, tips curling inward
    el("path", { d: "M33,0 C37,-5 41,-10 47,-11 C45,-7.5 42,-4 39.5,-1 C40,0 40,0 39.5,1 C42,4 45,7.5 47,11 C41,10 37,5 33,0 Z", fill: CREAM, stroke: INK, "stroke-width": 1, "stroke-linejoin": "round" }, fish);
    el("path", { d: "M35,-1 L43,-8 M36,0 L40,-3 M35,1 L43,8 M36,0 L40,3", fill: "none", stroke: INK, "stroke-width": 0.55 }, fish);
    el("path", { d: "M46.4,-10.6 c1.6,-0.4 2.6,1 1.4,2.2 M46.4,10.6 c1.6,0.4 2.6,-1 1.4,-2.2", finInk }, fish);
    el("path", { d: body, fill: CREAM, stroke: INK, "stroke-width": 1.1 }, fish);
    const inner = el("g", { "clip-path": "url(#fish-body)" }, fish);
    el("rect", { x: 0, y: -10, width: 9.5, height: 20, fill: "url(#fish-vhatch)", opacity: 0.75 }, inner);
    el("rect", { x: 11.5, y: -10, width: 8, height: 20, fill: "url(#fish-scales)" }, inner);
    el("rect", { x: 19.5, y: -10, width: 6, height: 20, fill: "url(#fish-hatch)", opacity: 0.8 }, inner);
    el("rect", { x: 25.5, y: -10, width: 8, height: 20, fill: "url(#fish-scales)" }, inner);
    el("path", { d: "M9.5,-10 L9.5,10 M11.5,-10 L11.5,10 M19.5,-10 L19.5,10 M25.5,-10 L25.5,10 M33,-10 L33,10", fill: "none", stroke: INK, "stroke-width": 0.7 }, inner);
    el("path", { d: "M9.5,-8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8", fill: "none", stroke: INK, "stroke-width": 0.55 }, inner);
    el("circle", { cx: 5.6, cy: -1.6, r: 2.6, fill: CREAM, stroke: INK, "stroke-width": 0.8 }, fish);
    el("circle", { cx: 5.6, cy: -1.6, r: 1.5, fill: "none", stroke: INK, "stroke-width": 0.5 }, fish);
    el("circle", { cx: 5.6, cy: -1.6, r: 0.8, fill: INK }, fish);
    el("path", { d: "M1.5,1.5 C3,2.5 4.5,2.8 6,2.6", finInk }, fish);

    // lotus petal, re-outlined
    const petal = el("symbol", { id: "petal", viewBox: "-10 -30 20 34" }, defs);
    el("path", { d: "M0,0 C-10,-12 -8,-26 0,-30 C8,-26 10,-12 0,0 Z", fill: "#f2c4d3", stroke: INK, "stroke-width": 1.2 }, petal);
    el("path", { d: "M0,-3 C-5,-11 -4,-22 0,-27 C4,-22 5,-11 0,-3 Z", fill: "#e07a9c", opacity: 0.85 }, petal);
    el("path", { d: "M0,-5 L0,-24", fill: "none", stroke: "#fff1f5", "stroke-width": 0.9 }, petal);

    // Pattachitra border tile: cream square, ink cross, red and green seeds
    const tile = el("pattern", { id: "border-tile", width: 18, height: 18, patternUnits: "userSpaceOnUse" }, defs);
    el("rect", { x: 0, y: 0, width: 18, height: 18, fill: CREAM }, tile);
    el("rect", { x: 1, y: 1, width: 16, height: 16, fill: "none", stroke: INK, "stroke-width": 1 }, tile);
    el("path", { d: "M9,2.5 L11.2,9 L9,15.5 L6.8,9 Z M2.5,9 L9,6.8 L15.5,9 L9,11.2 Z", fill: INK }, tile);
    el("circle", { cx: 9, cy: 9, r: 1.3, fill: CREAM }, tile);
    el("circle", { cx: 3.4, cy: 3.4, r: 0.9, fill: RED }, tile);
    el("circle", { cx: 14.6, cy: 14.6, r: 0.9, fill: RED }, tile);
    el("circle", { cx: 14.6, cy: 3.4, r: 0.9, fill: "#3f7f5c" }, tile);
    el("circle", { cx: 3.4, cy: 14.6, r: 0.9, fill: "#3f7f5c" }, tile);

    // busts, one symbol per seat
    seatSpecs = replay.players.map((p, i) => AVATARS[p.pseudonym] || AVATAR_LIST[i % AVATAR_LIST.length]);
    seatSpecs.forEach((s, i) => drawBust(defs, `av-${i}`, s));

    const clip = el("clipPath", { id: "frame-clip" }, defs);
    el("rect", { x: FRAME, y: FRAME, width: W - 2 * FRAME, height: H - 2 * FRAME }, clip);
    const lakeClip = el("clipPath", { id: "lake-clip" }, defs);
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX, ry: LAKE_RY }, lakeClip);
  }

  function drawWater(scene) {
    el("rect", { x: 0, y: 0, width: W, height: H, fill: GROUND }, scene);
    // sand lip, then the water and its scalloped Pattachitra waves, then the ink shoreline
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX + 9, ry: LAKE_RY + 9, fill: "#dcc48b", stroke: INK, "stroke-width": 2 }, scene);
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX, ry: LAKE_RY, fill: WATER }, scene);
    const waves = el("g", { "clip-path": "url(#lake-clip)", stroke: CREST, "stroke-width": 1.3, "stroke-linejoin": "round" }, scene);
    const r = 20, rowStep = 12;
    let row = 0;
    for (let y = CY - LAKE_RY + 4; y < CY + LAKE_RY + r; y += rowStep, row++) {
      const offset = (row % 2) * r;
      let d = "";
      for (let x = CX - LAKE_RX - 2 * r + offset; x < CX + LAKE_RX + r; x += 2 * r) d += `M${x},${y} a${r},${r} 0 0 1 ${2 * r},0 Z `;
      el("path", { d, fill: row % 3 === 0 ? WATER : WATER_SCALE }, waves);
    }
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX, ry: LAKE_RY, fill: "none", stroke: INK, "stroke-width": 2 }, scene);
  }

  function drawFlora(rng) {
    // grass tufts on the bank corners, ink only
    const tufts = el("g", { fill: "none", stroke: INK, "stroke-width": 1.1, "stroke-linecap": "round", opacity: 0.75 }, layers.flora);
    for (let i = 0; i < 44; i++) {
      const x = FRAME + 20 + rng() * (W - 2 * FRAME - 40);
      const y = rng() < 0.5 ? FRAME + 30 + rng() * 90 : H - FRAME - 30 - rng() * 90;
      const dx = x - CX, dy = y - CY;
      if ((dx * dx) / ((SEAT_RX + 50) ** 2) + (dy * dy) / ((SEAT_RY + 50) ** 2) < 1) continue;
      el("path", { d: `M${x},${y} c-2,-5 -5,-8 -7,-9 M${x},${y} c0,-6 1,-10 1,-12 M${x},${y} c2,-5 5,-8 8,-9`, transform: `rotate(${(rng() - 0.5) * 20} ${x} ${y})` }, tufts);
    }
    // banana leaves entering from the corners
    const leafShape = (x, y, rot, len) => {
      const g = el("g", { transform: `translate(${x} ${y}) rotate(${rot})` }, layers.flora);
      el("path", { d: `M0,0 C${len * 0.25},-${len * 0.25} ${len * 0.7},-${len * 0.3} ${len},-${len * 0.05} C${len * 0.7},${len * 0.18} ${len * 0.3},${len * 0.22} 0,0 Z`, fill: "#4a8a56", stroke: INK, "stroke-width": 2 }, g);
      el("path", { d: `M4,0 L${len - 6},-${len * 0.06}`, stroke: INK, "stroke-width": 1.6, fill: "none" }, g);
      for (let i = 1; i < 8; i++) {
        const t = i / 8;
        el("path", { d: `M${len * t},-${len * 0.06 * t} l${len * 0.06},-${len * 0.16} M${len * t},-${len * 0.06 * t} l${len * 0.04},${len * 0.12}`, stroke: INK, "stroke-width": 0.8, fill: "none", opacity: 0.7 }, g);
      }
      el("path", { d: `M${len * 0.15},-${len * 0.02} C${len * 0.4},-${len * 0.14} ${len * 0.7},-${len * 0.16} ${len * 0.9},-${len * 0.08}`, stroke: "#8fd08a", "stroke-width": 1.2, fill: "none", opacity: 0.7 }, g);
    };
    leafShape(FRAME + 4, FRAME + 16, -22, 210); leafShape(FRAME + 4, FRAME + 16, 18, 150); leafShape(FRAME + 4, FRAME + 16, 2, 250);
    leafShape(W - FRAME - 4, FRAME + 16, 202, 210); leafShape(W - FRAME - 4, FRAME + 16, 162, 150); leafShape(W - FRAME - 4, FRAME + 16, 178, 250);
    leafShape(FRAME + 4, H - FRAME - 16, 22, 200); leafShape(FRAME + 4, H - FRAME - 16, -28, 150);
    leafShape(W - FRAME - 4, H - FRAME - 16, 158, 200); leafShape(W - FRAME - 4, H - FRAME - 16, 208, 150);

    // lotus pads on the water margin between the seats
    const lotus = (x, y, s, bloom) => {
      const g = el("g", { transform: `translate(${x} ${y}) scale(${s})` }, layers.flora);
      el("ellipse", { cx: 0, cy: 14, rx: 34, ry: 12, fill: "#4e8b5a", stroke: INK, "stroke-width": 1.6 }, g);
      el("path", { d: "M0,14 L16,8", stroke: INK, "stroke-width": 1.2 }, g);
      for (let a = -60; a <= 240; a += 40) {
        const rad = (a * Math.PI) / 180;
        el("path", { d: `M0,14 L${Math.cos(rad) * 30},${14 + Math.sin(rad) * 10}`, stroke: INK, "stroke-width": 0.7, opacity: 0.55 }, g);
      }
      if (bloom) {
        el("line", { x1: 0, y1: 12, x2: 0, y2: -14, stroke: "#5f9a5f", "stroke-width": 2.4 }, g);
        el("line", { x1: 0, y1: 12, x2: 0, y2: -14, stroke: INK, "stroke-width": 0.6 }, g);
        for (const rot of [-50, -25, 0, 25, 50]) {
          el("use", { href: "#petal", x: -7, y: -14, width: 14, height: 24, transform: `rotate(${rot} 0 -14)` }, g);
        }
        el("circle", { cx: 0, cy: -16, r: 3, fill: GOLD, stroke: INK, "stroke-width": 0.7 }, g);
      }
    };
    const n = replay.players.length;
    for (let i = 0; i < n; i++) {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
      lotus(CX + Math.cos(a) * (LAKE_RX - 34), CY + Math.sin(a) * (LAKE_RY - 30), 0.75 + (i % 3) * 0.15, i % 2 === 0);
      const a2 = a + Math.PI / n / 2.2;
      lotus(CX + Math.cos(a2) * (LAKE_RX - 18), CY + Math.sin(a2) * (LAKE_RY - 14), 0.55, false);
    }
  }

  function drawStation(i, spec) {
    const n = replay.players.length;
    const ang = -Math.PI / 2 + ((i + 0.5) * 2 * Math.PI) / n;
    const hx = CX + Math.cos(ang) * SEAT_RX;
    const hy = CY + Math.sin(ang) * SEAT_RY;
    const color = spec.color;
    const toward = Math.atan2(CY - hy, CX - hx);
    const g = el("g", { transform: `translate(${hx} ${hy})` }, layers.huts);
    const side = hx < CX ? -1 : 1; // the portrait niche stands on the side away from the lake's middle
    // jetty out to the water, under everything else at the station
    const jg = el("g", { transform: `rotate(${(toward * 180) / Math.PI})` }, g);
    el("rect", { x: 10, y: -7, width: 72, height: 14, fill: "#b5742f", stroke: INK, "stroke-width": 1.4 }, jg);
    for (let px = 16; px < 80; px += 9) el("line", { x1: px, y1: -7, x2: px, y2: 7, stroke: INK, "stroke-width": 0.8 }, jg);
    for (const px of [24, 52, 78]) el("rect", { x: px - 2, y: -10, width: 4, height: 20, fill: "#7a4a1e", stroke: INK, "stroke-width": 0.8 }, jg);
    // speaker halo around the portrait niche
    const glow = el("rect", { class: "hut-glow", x: side * 70 - 32, y: -44, width: 64, height: 76, rx: 4, fill: "rgba(242,201,76,0.28)", stroke: GOLD, "stroke-width": 4 }, g);
    // warehouse: cream walls, terracotta tile roof, cusped door, the seat colour as trim
    el("rect", { x: -30, y: -20, width: 60, height: 36, fill: CREAM, stroke: INK, "stroke-width": 1.6 }, g);
    el("rect", { x: -30, y: 9, width: 60, height: 7, fill: color, stroke: INK, "stroke-width": 1 }, g);
    el("path", { d: "M-40,-20 L0,-48 L40,-20 Z", fill: "#c4553a", stroke: INK, "stroke-width": 1.6, "stroke-linejoin": "round" }, g);
    el("path", { d: "M-30,-27 L30,-27 M-20,-34 L20,-34 M-10,-41 L10,-41", fill: "none", stroke: INK, "stroke-width": 0.8, opacity: 0.7 }, g);
    el("path", { d: "M-40,-20 L40,-20", stroke: INK, "stroke-width": 1.6 }, g);
    el("rect", { x: -1.5, y: -58, width: 3, height: 10, fill: GOLD, stroke: INK, "stroke-width": 0.7 }, g);
    el("circle", { cx: 0, cy: -59, r: 3.5, fill: GOLD, stroke: INK, "stroke-width": 0.8 }, g);
    el("path", { d: "M-8,9 L-8,-2 Q-8,-8 -4,-8 Q-2,-11 0,-13 Q2,-11 4,-8 Q8,-8 8,-2 L8,9 Z", fill: "#3b2413", stroke: INK, "stroke-width": 1 }, g);
    el("rect", { x: -26, y: -14, width: 12, height: 10, fill: color, stroke: INK, "stroke-width": 1 }, g);
    el("rect", { x: 14, y: -14, width: 12, height: 10, fill: color, stroke: INK, "stroke-width": 1 }, g);
    el("path", { d: "M-20,-14 L-20,-4 M20,-14 L20,-4", stroke: INK, "stroke-width": 0.7 }, g);
    // portrait niche
    el("rect", { x: side * 70 - 27, y: -39, width: 54, height: 66, fill: GOLD, stroke: INK, "stroke-width": 1.4 }, g);
    el("use", { href: `#av-${i}`, x: side * 70 - 23, y: -35, width: 46, height: 58 }, g);
    el("rect", { x: side * 70 - 23, y: -35, width: 46, height: 58, fill: "none", stroke: INK, "stroke-width": 1 }, g);
    // plaque: name and the fish tally, above for the top row and below for the bottom
    const below = hy < CY ? -1 : 1;
    const plaqueY = below < 0 ? -92 : 34;
    el("rect", { x: -54, y: plaqueY, width: 108, height: 32, rx: 3, fill: "#7a1f1f", stroke: GOLD, "stroke-width": 1.6 }, g);
    el("rect", { x: -51, y: plaqueY + 3, width: 102, height: 26, rx: 2, fill: "none", stroke: INK, "stroke-width": 0.8 }, g);
    text(0, plaqueY + 14, replay.players[i].pseudonym, { "text-anchor": "middle", fill: "#f6e7b2", "font-family": "Georgia, serif", "font-size": 13.5, "font-weight": 700 }, g);
    const tally = text(0, plaqueY + 27, "0 fish", { "text-anchor": "middle", fill: "#f6e7b2", "font-family": "Georgia, serif", "font-size": 11.5 }, g);
    huts.push({ x: hx, y: hy, ang, glow, tally, color });
    // boat: ochre hull, red gunwale, a rower whose turban carries the seat colour
    const deg = (toward * 180) / Math.PI;
    const home = { x: hx + Math.cos(toward) * 92, y: hy + Math.sin(toward) * 92 };
    const bg = el("g", { class: "boat", transform: `translate(${home.x} ${home.y})` }, layers.boats);
    const inner = el("g", { transform: `rotate(${deg})` }, bg);
    el("path", { d: "M-26,0 Q0,17 26,0 Q0,6 -26,0 Z", fill: "#b5742f", stroke: INK, "stroke-width": 1.5, "stroke-linejoin": "round" }, inner);
    el("path", { d: "M-22,1 Q0,6 22,1", fill: "none", stroke: RED, "stroke-width": 2 }, inner);
    el("path", { d: "M-26,0 q-6,-8 -3,-16", fill: "none", stroke: INK, "stroke-width": 2.2, "stroke-linecap": "round" }, inner);
    el("path", { d: "M-2,-3 L0,-10 L8,-10 L10,-3 Z", fill: CREAM, stroke: INK, "stroke-width": 1 }, inner);
    el("circle", { cx: 4, cy: -13, r: 4, fill: "#d9a06b", stroke: INK, "stroke-width": 1 }, inner);
    el("path", { d: "M-0.5,-14 A4.5,4.5 0 0 1 8.5,-14 Z", fill: color, stroke: INK, "stroke-width": 1 }, inner);
    el("line", { x1: 10, y1: -6, x2: 24, y2: -18, stroke: INK, "stroke-width": 1.6, "stroke-linecap": "round" }, inner);
    boats.push({ node: bg, home, ang: toward, out: null });
    const label = text(home.x, home.y - 30, "", { class: "catch-label", "text-anchor": "middle" }, layers.effects);
    catchLabels.push(label);
  }

  function drawFrame() {
    // Pattachitra border: red-ochre outer band, gold and ink rules, a patterned cream band, an ink rule
    el("rect", { x: 0, y: 0, width: W, height: 8, fill: "#b5432c" }, layers.frame);
    el("rect", { x: 0, y: H - 8, width: W, height: 8, fill: "#b5432c" }, layers.frame);
    el("rect", { x: 0, y: 0, width: 8, height: H, fill: "#b5432c" }, layers.frame);
    el("rect", { x: W - 8, y: 0, width: 8, height: H, fill: "#b5432c" }, layers.frame);
    el("rect", { x: 8, y: 8, width: W - 16, height: H - 16, fill: "none", stroke: GOLD, "stroke-width": 3 }, layers.frame);
    el("rect", { x: 10.5, y: 10.5, width: W - 21, height: H - 21, fill: "none", stroke: INK, "stroke-width": 1.5 }, layers.frame);
    const band = el("path", { d: `M12,12 H${W - 12} V${H - 12} H12 Z M${FRAME - 2},${FRAME - 2} V${H - FRAME + 2} H${W - FRAME + 2} V${FRAME - 2} Z`, fill: "url(#border-tile)", "fill-rule": "evenodd" }, layers.frame);
    band.setAttribute("stroke", "none");
    el("rect", { x: FRAME - 1, y: FRAME - 1, width: W - 2 * FRAME + 2, height: H - 2 * FRAME + 2, fill: "none", stroke: INK, "stroke-width": 2 }, layers.frame);
  }

  function drawStatic() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    drawDefs();
    const scene = el("g", { "clip-path": "url(#frame-clip)" }, svg);
    drawWater(scene);
    layers.fish = el("g", { class: "fish-school" }, scene);
    layers.flora = el("g", {}, scene);
    layers.huts = el("g", {}, scene);
    layers.boats = el("g", {}, scene);
    layers.effects = el("g", {}, scene);
    layers.frame = el("g", {}, svg);

    const rng = mulberry32(replay.seed || 1);
    drawFlora(rng);

    // the school: count visible follows the true stock
    fishGlyphs = [];
    for (let i = 0; i < 64; i++) {
      const a = rng() * Math.PI * 2;
      const r = Math.sqrt(rng());
      const x = CX + Math.cos(a) * r * 330;
      const y = CY + Math.sin(a) * r * 190;
      const dir = rng() < 0.5 ? -1 : 1;
      const size = 34 + rng() * 18;
      const node = el("use", { href: "#fish", x: -size / 2, y: -size * 0.23, width: size, height: size * 0.46, transform: `translate(${x} ${y}) scale(${dir} 1) rotate(${(rng() - 0.5) * 30})` }, layers.fish);
      fishGlyphs.push(node);
    }

    huts = []; boats = []; catchLabels = [];
    seatSpecs.forEach((spec, i) => drawStation(i, spec));
    punishLayer = el("g", {}, layers.effects);

    // council mandala: a lotus in the middle of the lake
    mandala = el("g", { class: "council-mandala", transform: `translate(${CX} ${CY})` }, layers.effects);
    el("circle", { cx: 0, cy: 0, r: 108, fill: "rgba(39,79,140,0.78)", stroke: INK, "stroke-width": 2 }, mandala);
    el("circle", { cx: 0, cy: 0, r: 104, fill: "none", stroke: GOLD, "stroke-width": 2 }, mandala);
    el("circle", { cx: 0, cy: 0, r: 94, fill: "none", stroke: CREAM, "stroke-width": 1, "stroke-dasharray": "3 5" }, mandala);
    for (let a = 0; a < 360; a += 30) {
      el("use", { href: "#petal", x: -12, y: -92, width: 24, height: 40, transform: `rotate(${a})` }, mandala);
    }
    for (let a = 15; a < 360; a += 30) {
      el("use", { href: "#petal", x: -9, y: -74, width: 18, height: 30, transform: `rotate(${a})` }, mandala);
    }
    el("circle", { cx: 0, cy: 0, r: 44, fill: GOLD, stroke: INK, "stroke-width": 1.6 }, mandala);
    el("circle", { cx: 0, cy: 0, r: 39, fill: "none", stroke: RED, "stroke-width": 1 }, mandala);
    text(0, -5, "COUNCIL", { "text-anchor": "middle", fill: INK, "font-family": "Georgia, serif", "font-size": 12, "font-weight": 700, "letter-spacing": 2 }, mandala);
    mandalaText = text(0, 12, "", { "text-anchor": "middle", fill: INK, "font-family": "Georgia, serif", "font-size": 11.5 }, mandala);

    // council caption inside the painting, between the mandala and the bottom piers
    captionBox = el("foreignObject", { class: "caption-box", x: 250, y: 520, width: 700, height: 100 }, layers.effects);
    captionNode = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
    captionNode.className = "caption";
    captionBox.appendChild(captionNode);

    // lake cartouche (spectator-only truth), on the bank above the lake
    const cart = el("g", { transform: `translate(${CX} ${FRAME + 16})` }, layers.frame);
    el("rect", { x: -128, y: -13, width: 256, height: 26, rx: 3, fill: CREAM, stroke: INK, "stroke-width": 1.4 }, cart);
    el("rect", { x: -125, y: -10, width: 250, height: 20, rx: 2, fill: "none", stroke: GOLD, "stroke-width": 1 }, cart);
    lakeCartouche = text(0, 4.5, "", { "text-anchor": "middle", fill: INK, "font-family": "Georgia, serif", "font-size": 13.5 }, cart);

    drawFrame();
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
    lakeCartouche.textContent = dying
      ? `Lake ${Math.round(stock)} fish · ${pct}% · past no return`
      : `Lake ${Math.round(stock)} fish · ${pct}% of ${Math.round(replay.lake.capacity)}`;
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
      el("path", { class: "punish-line show", d: `M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}` }, punishLayer);
      const lx = (a.x + 2 * mx + b.x) / 4, ly = (a.y + 2 * my + b.y) / 4;
      text(lx, ly - 6, `−${p.fish}`, { class: "punish-label show", "text-anchor": "middle" }, punishLayer);
      const toward = Math.atan2(CY - a.y, CX - a.x);
      text(a.x + Math.cos(toward) * 132, a.y + Math.sin(toward) * 132 + 5, `burned ${p.cost}`, { class: "punish-label show", "text-anchor": "middle", "font-size": 13 }, punishLayer);
    }
  }

  // ---------------------------------------------------------------- panel

  const ledgerBody = $("ledger-table").querySelector("tbody");

  function avatarChip(slot) {
    return `<svg class="av" viewBox="0 0 60 72" aria-hidden="true"><use href="#av-${slot}"/></svg>`;
  }

  function renderLedger(fish, lastTurn, upTo) {
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
        `<td class="name">${avatarChip(s)}<span class="who"><span class="swatch" style="background:${seatSpecs[s].color}"></span>${escapeHtml(player.pseudonym)}` +
        `<span class="model">${escapeHtml(player.policy)}${player.model ? " · " + escapeHtml(player.model) : ""}</span></span></td>` +
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
        const p = document.createElement("div");
        p.className = "speech" + (s.text ? "" : " silent") + (s.auto ? " auto" : "");
        const name = replay.players[s.slot].pseudonym;
        p.innerHTML = `${avatarChip(s.slot)}<p><b>${escapeHtml(name)}</b> ${s.text ? escapeHtml(s.text) : "says nothing."}</p>`;
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
