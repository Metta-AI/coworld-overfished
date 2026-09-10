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
  const LAKE_RX = 400, LAKE_RY = 250; // the water ellipse
  const SEAT_RX = 440, SEAT_RY = 262; // the bank ring the pavilions stand on
  const SKY_BOTTOM = 100;

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
  const GRASS = "#86a94b"; // flat grass ground, as in the Pahari folios
  const HILL = "#b9cb5c";
  const SKY = "#b7d3df";
  const TREE = "#3d6b38";
  const TREE_LIGHT = "#6f9a4c";
  const PATH = "#c39a55";
  const WATER = "#1f3f78"; // deep indigo so ivory fish stand off it
  const WATER_SCALE = "#24488a";
  const CREST = "rgba(214,226,240,0.42)";
  const IVORY = "#f7eed6";
  const FISH_GOLD = "#e8b73a"; // marigold school, a third of it red-ochre
  const FISH_RED = "#c8442a";

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
    P("M21.5,26.5 C24.5,23.2 30.5,23.2 34,26.5 C30.5,29.8 24.5,29.8 21.5,26.5 Z", { fill: "#f7efe1", stroke: INK, "stroke-width": 0.9 });
    C(26.8, 26.6, 2.1, { fill: INK });
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
  let lakeFill = null;
  let keepOut = []; // rectangles the ground tufts stay out of: {x0, y0, x1, y1}
  let leaf = null; // the palm-leaf speech folio: { group, textNode, portrait, caption }

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

    // Madhubani fish: one ink, cross-hatched bands, curling fins, ringed eye; body colour comes from the glyph
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
    el("path", { d: "M33,0 C37,-5 41,-10 47,-11 C45,-7.5 42,-4 39.5,-1 C40,0 40,0 39.5,1 C42,4 45,7.5 47,11 C41,10 37,5 33,0 Z", fill: "currentColor", stroke: INK, "stroke-width": 1.2, "stroke-linejoin": "round" }, fish);
    el("path", { d: "M35,-1 L43,-8 M36,0 L40,-3 M35,1 L43,8 M36,0 L40,3", fill: "none", stroke: INK, "stroke-width": 0.55 }, fish);
    el("path", { d: "M46.4,-10.6 c1.6,-0.4 2.6,1 1.4,2.2 M46.4,10.6 c1.6,0.4 2.6,-1 1.4,-2.2", finInk }, fish);
    el("path", { d: body, fill: "currentColor", stroke: INK, "stroke-width": 1.5 }, fish);
    const inner = el("g", { "clip-path": "url(#fish-body)" }, fish);
    el("rect", { x: 0, y: -10, width: 9.5, height: 20, fill: "url(#fish-vhatch)", opacity: 0.55 }, inner);
    el("rect", { x: 11.5, y: -10, width: 8, height: 20, fill: "url(#fish-scales)", opacity: 0.85 }, inner);
    el("rect", { x: 19.5, y: -10, width: 6, height: 20, fill: "url(#fish-hatch)", opacity: 0.55 }, inner);
    el("rect", { x: 25.5, y: -10, width: 8, height: 20, fill: "url(#fish-scales)", opacity: 0.85 }, inner);
    el("path", { d: "M9.5,-10 L9.5,10 M11.5,-10 L11.5,10 M19.5,-10 L19.5,10 M25.5,-10 L25.5,10 M33,-10 L33,10", fill: "none", stroke: INK, "stroke-width": 0.7 }, inner);
    el("path", { d: "M9.5,-8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8 q1,1.4 0,2.8", fill: "none", stroke: INK, "stroke-width": 0.55 }, inner);
    el("circle", { cx: 5.6, cy: -1.6, r: 2.6, fill: IVORY, stroke: INK, "stroke-width": 0.8 }, fish);
    el("circle", { cx: 5.6, cy: -1.6, r: 1.5, fill: "none", stroke: INK, "stroke-width": 0.5 }, fish);
    el("circle", { cx: 5.6, cy: -1.6, r: 0.8, fill: INK }, fish);
    el("path", { d: "M1.5,1.5 C3,2.5 4.5,2.8 6,2.6", finInk }, fish);

    // floral band under the pavilion eaves, and the scalloped foliage of the tree clumps
    const floral = el("pattern", { id: "eaves-floral", width: 9, height: 8, patternUnits: "userSpaceOnUse" }, defs);
    el("path", { d: "M4.5,7 L4.5,3.5 M4.5,5 l-2,-1.5 M4.5,5 l2,-1.5", fill: "none", stroke: "#4f7d3a", "stroke-width": 0.7 }, floral);
    el("circle", { cx: 4.5, cy: 2.4, r: 1.1, fill: RED }, floral);
    const foliage = el("pattern", { id: "tree-scallops", width: 12, height: 8, patternUnits: "userSpaceOnUse" }, defs);
    el("path", { d: "M0,8 a6,6 0 0 1 12,0 M-6,4 a6,6 0 0 1 12,0 M6,4 a6,6 0 0 1 12,0", fill: "none", stroke: TREE_LIGHT, "stroke-width": 0.9 }, foliage);

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

    // palm-leaf folio: fibre grain and worn, darkened edges
    const grain = el("pattern", { id: "leaf-grain", width: 120, height: 7, patternUnits: "userSpaceOnUse" }, defs);
    el("rect", { x: 0, y: 0, width: 120, height: 7, fill: "#d3b57a" }, grain);
    el("path", { d: "M0,1.5 L120,1.5 M0,4 L52,4 M70,4 L120,4 M0,6 L120,6", fill: "none", stroke: "#b8985a", "stroke-width": 0.6, opacity: 0.8 }, grain);
    el("path", { d: "M18,3 l14,0 M84,2 l20,0", fill: "none", stroke: "#e2ca93", "stroke-width": 0.9 }, grain);
    const edgeX = el("linearGradient", { id: "leaf-edge-x", x1: 0, x2: 1, y1: 0, y2: 0 }, defs);
    for (const [o, a] of [[0, 0.62], [0.06, 0.18], [0.14, 0], [0.86, 0], [0.94, 0.18], [1, 0.62]]) el("stop", { offset: o, "stop-color": "#4a2e12", "stop-opacity": a }, edgeX);
    const edgeY = el("linearGradient", { id: "leaf-edge-y", x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
    for (const [o, a] of [[0, 0.5], [0.12, 0.08], [0.3, 0], [0.7, 0], [0.88, 0.08], [1, 0.5]]) el("stop", { offset: o, "stop-color": "#4a2e12", "stop-opacity": a }, edgeY);

    drawCreatureSymbols(defs);

    // busts, one symbol per seat
    seatSpecs = replay.players.map((p, i) => AVATARS[p.pseudonym] || AVATAR_LIST[i % AVATAR_LIST.length]);
    seatSpecs.forEach((s, i) => drawBust(defs, `av-${i}`, s));

    const clip = el("clipPath", { id: "frame-clip" }, defs);
    el("rect", { x: FRAME, y: FRAME, width: W - 2 * FRAME, height: H - 2 * FRAME }, clip);
    const lakeClip = el("clipPath", { id: "lake-clip" }, defs);
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX, ry: LAKE_RY }, lakeClip);
  }

  function drawCreatureSymbols(defs) {
    const rng = mulberry32(7);
    const PALE = "#e8e4da", SAGE = "#7f9d57", SAGE_DARK = "#54713f", TIP = "#d9e4a6";
    // tree: pale branching trunk, broad canopy of tiny leaf clusters, hanging tendrils, two birds
    const tree = el("symbol", { id: "tree", viewBox: "-72 -142 144 150", overflow: "visible" }, defs);
    let canopy = "";
    for (let a = 0; a < 360; a += 15) {
      const r1 = (a * Math.PI) / 180, r2 = ((a + 7.5) * Math.PI) / 180, r3 = ((a + 15) * Math.PI) / 180;
      const p1 = [Math.cos(r1) * 60, -86 + Math.sin(r1) * 44], p2 = [Math.cos(r2) * 66, -86 + Math.sin(r2) * 50], p3 = [Math.cos(r3) * 60, -86 + Math.sin(r3) * 44];
      canopy += (a === 0 ? `M${p1[0]},${p1[1]} ` : "") + `Q${p2[0]},${p2[1]} ${p3[0]},${p3[1]} `;
    }
    el("path", { d: canopy + "Z", fill: SAGE_DARK, stroke: INK, "stroke-width": 1.8, "stroke-linejoin": "round" }, tree);
    el("path", { d: "M-8,0 C-6,-22 -4,-42 -3,-60 L3,-60 C4,-42 6,-22 8,0 Z", fill: PALE, stroke: INK, "stroke-width": 1.2 }, tree);
    const limb = (x0, y0, x1, y1, w0, w1) => {
      const dx = x1 - x0, dy = y1 - y0, l = Math.hypot(dx, dy), nx = -dy / l, ny = dx / l;
      el("path", { d: `M${x0 + nx * w0},${y0 + ny * w0} L${x1 + nx * w1},${y1 + ny * w1} L${x1 - nx * w1},${y1 - ny * w1} L${x0 - nx * w0},${y0 - ny * w0} Z`, fill: PALE, stroke: INK, "stroke-width": 0.9, "stroke-linejoin": "round" }, tree);
    };
    limb(0, -58, -34, -96, 3, 1); limb(0, -58, 30, -100, 3, 1); limb(0, -58, -8, -118, 2.6, 0.8);
    limb(-20, -80, -50, -92, 1.6, 0.6); limb(18, -84, 52, -90, 1.6, 0.6); limb(-4, -100, -28, -122, 1.4, 0.5); limb(14, -88, 36, -118, 1.4, 0.5);
    for (let i = 0; i < 96; i++) {
      const a = rng() * Math.PI * 2, r = Math.sqrt(rng());
      const cx = Math.cos(a) * r * 58, cy = -86 + Math.sin(a) * r * 42;
      const g = el("g", { transform: `translate(${cx.toFixed(1)} ${cy.toFixed(1)}) rotate(${Math.round(rng() * 360)})` }, tree);
      for (let k = 0; k < 6; k++) {
        el("path", { d: "M0,0 q2.2,-2.6 0,-8 q-2.2,5.4 0,8 Z", fill: SAGE, stroke: INK, "stroke-width": 0.35, transform: `rotate(${k * 60})` }, g);
        el("path", { d: "M0,-4.2 L0,-7.4", stroke: TIP, "stroke-width": 0.9, transform: `rotate(${k * 60})` }, g);
      }
    }
    for (const [tx, ty] of [[-40, -52], [-18, -46], [8, -48], [30, -50], [48, -58]]) {
      const g = el("g", { transform: `translate(${tx} ${ty})` }, tree);
      el("path", { d: "M0,0 C2,8 -2,16 1,26", fill: "none", stroke: SAGE_DARK, "stroke-width": 1 }, g);
      for (let k = 1; k <= 4; k++) el("path", { d: "M0,0 q3,-1 5,2 q-3,1 -5,-2 Z", fill: SAGE, stroke: INK, "stroke-width": 0.3, transform: `translate(${k % 2 ? 1 : -1} ${k * 6}) scale(${k % 2 ? 1 : -1} 1)` }, g);
    }
    for (const [bx, by, f] of [[-26, -104, 1], [24, -70, -1]]) {
      const g = el("g", { transform: `translate(${bx} ${by}) scale(${f} 1)` }, tree);
      el("path", { d: "M0,0 c2,-3.5 7,-3.5 9,0 c-2,2 -7,2 -9,0 Z", fill: PALE, stroke: INK, "stroke-width": 0.7 }, g);
      el("path", { d: "M9,-0.5 l4,-2.5 l-3,3 M0,0 l-4,1.5", fill: "none", stroke: INK, "stroke-width": 0.7 }, g);
      el("circle", { cx: 10.5, cy: -1.6, r: 1.6, fill: PALE, stroke: INK, "stroke-width": 0.6 }, g);
    }
    // grass tuft with a lotus bud
    const tuft = el("symbol", { id: "tuft", viewBox: "-8 -15 16 16", overflow: "visible" }, defs);
    el("path", { d: "M0,0 L-6,-8 M0,0 L-3,-11 M0,0 L0,-13 M0,0 L3,-11 M0,0 L6,-8", fill: "none", stroke: "#3d6b38", "stroke-width": 1.2, "stroke-linecap": "round" }, tuft);
    el("path", { d: "M-6,-8 l-1,-1.5 M-3,-11 l-0.5,-2 M3,-11 l0.5,-2 M6,-8 l1,-1.5", fill: "none", stroke: TIP, "stroke-width": 1, "stroke-linecap": "round" }, tuft);
    const bud = el("symbol", { id: "tuft-bud", viewBox: "-8 -15 16 16", overflow: "visible" }, defs);
    el("use", { href: "#tuft" }, bud);
    el("path", { d: "M0,-8 C-3,-11 -2.5,-16 0,-18 C2.5,-16 3,-11 0,-8 Z", fill: "#e58aa8", stroke: INK, "stroke-width": 0.6 }, bud);
    // white cow, in profile facing left: gold harness, red spots and hoof marks
    const cow = el("symbol", { id: "cow", viewBox: "-2 -30 64 32", overflow: "visible" }, defs);
    const WHITE = "#f4efe4";
    for (const [lx, ly] of [[14, -8], [22, -8], [42, -8], [50, -8]]) {
      el("path", { d: `M${lx},${ly} L${lx - 1},0 L${lx + 4},0 L${lx + 3.5},${ly} Z`, fill: WHITE, stroke: INK, "stroke-width": 0.9 }, cow);
      el("path", { d: `M${lx - 1},0 L${lx + 4},0`, stroke: RED, "stroke-width": 2 }, cow);
    }
    el("path", { d: "M10,-8 C8,-16 12,-22 22,-22 L46,-22 C54,-22 56,-16 55,-8 C54,-4 50,-4 48,-6 L14,-6 C11,-5 10,-6 10,-8 Z", fill: WHITE, stroke: INK, "stroke-width": 1 }, cow);
    el("path", { d: "M55,-20 C58,-14 57,-8 55,-2 M55,-2 c-1,1 -2,1 -3,0", fill: "none", stroke: INK, "stroke-width": 1 }, cow);
    el("path", { d: "M12,-20 C8,-24 6,-26 4,-26 C0,-26 -1,-22 0,-18 C1,-15 4,-13 8,-13 L12,-16 Z", fill: WHITE, stroke: INK, "stroke-width": 1 }, cow);
    el("path", { d: "M4,-26 c-2,-3 -3,-6 -2,-8 M7,-26 c0,-3 2,-6 4,-7", fill: "none", stroke: INK, "stroke-width": 1, "stroke-linecap": "round" }, cow);
    el("path", { d: "M9,-25 c3,-1 5,0 6,1 c-2,1 -4,1 -6,-1 Z", fill: WHITE, stroke: INK, "stroke-width": 0.7 }, cow);
    el("circle", { cx: 5, cy: -21.5, r: 1.1, fill: INK }, cow);
    el("circle", { cx: 2, cy: -17, r: 1, fill: RED }, cow);
    el("path", { d: "M12,-21 C11,-17 12,-14 13,-12", fill: "none", stroke: GOLD, "stroke-width": 2 }, cow);
    el("path", { d: "M22,-22 L44,-22 L44,-16 L22,-16 Z", fill: "#e39a2f", stroke: INK, "stroke-width": 0.8 }, cow);
    el("path", { d: "M22,-19 L44,-19", stroke: RED, "stroke-width": 1, "stroke-dasharray": "2 1.5" }, cow);
    for (const [sx, sy] of [[18, -12], [30, -11], [38, -13], [48, -11], [26, -8], [44, -8]]) el("circle", { cx: sx, cy: sy, r: 1.3, fill: RED }, cow);
    // peacock with the tail fanned: concentric blue-green feathers with eye spots
    const pea = el("symbol", { id: "peacock", viewBox: "-40 -66 80 70", overflow: "visible" }, defs);
    for (const [r, col] of [[36, "#2f6f5f"], [30, "#3f8f72"], [24, "#2f6f5f"], [18, "#3f8f72"]]) {
      el("path", { d: `M${-r},-24 A${r},${r} 0 1 1 ${r},-24 Z`, fill: col, stroke: INK, "stroke-width": 1 }, pea);
    }
    for (const [r, count] of [[33, 9], [27, 7], [21, 5]]) {
      for (let k = 0; k < count; k++) {
        const a = Math.PI + (Math.PI * (k + 0.5)) / count;
        const ex = Math.cos(a) * r, ey = -24 + Math.sin(a) * r;
        el("circle", { cx: ex, cy: ey, r: 2.6, fill: "#1f3f78", stroke: GOLD, "stroke-width": 0.9 }, pea);
        el("circle", { cx: ex, cy: ey, r: 0.9, fill: "#e0662a" }, pea);
      }
    }
    for (let k = 0; k < 13; k++) {
      const a = Math.PI + (Math.PI * (k + 0.5)) / 13;
      el("path", { d: `M0,-24 L${Math.cos(a) * 36},${-24 + Math.sin(a) * 36}`, stroke: INK, "stroke-width": 0.45, opacity: 0.6 }, pea);
    }
    el("path", { d: "M-8,-6 C-12,-16 -6,-26 2,-26 C10,-26 12,-16 8,-6 Z", fill: "#2b4c8c", stroke: INK, "stroke-width": 1 }, pea);
    el("path", { d: "M-2,-24 C-6,-30 -6,-38 -8,-44 C-9,-47 -12,-48 -14,-46", fill: "none", stroke: "#2b4c8c", "stroke-width": 4, "stroke-linecap": "round" }, pea);
    el("path", { d: "M-2,-24 C-6,-30 -6,-38 -8,-44 C-9,-47 -12,-48 -14,-46", fill: "none", stroke: INK, "stroke-width": 5.4, "stroke-linecap": "round" }, pea);
    el("path", { d: "M-2,-24 C-6,-30 -6,-38 -8,-44 C-9,-47 -12,-48 -14,-46", fill: "none", stroke: "#2b4c8c", "stroke-width": 3.6, "stroke-linecap": "round" }, pea);
    el("path", { d: "M-14,-46 l-5,1.5", stroke: "#e0662a", "stroke-width": 1.6, "stroke-linecap": "round" }, pea);
    for (const [cx, cy] of [[-13, -52], [-10, -54], [-7, -52]]) el("circle", { cx, cy, r: 1.1, fill: "#3f8f72", stroke: INK, "stroke-width": 0.4 }, pea);
    el("path", { d: "M-12,-49 L-13,-52 M-10,-49 L-10,-54 M-8,-49 L-7,-52", stroke: INK, "stroke-width": 0.5 }, pea);
    el("circle", { cx: -12, cy: -46.5, r: 0.7, fill: "#f4efe4" }, pea);
    el("path", { d: "M-4,-6 L-5,2 M4,-6 L5,2 M-5,2 l-3,1 M-5,2 l3,1 M5,2 l-3,1 M5,2 l3,1", fill: "none", stroke: INK, "stroke-width": 1, "stroke-linecap": "round" }, pea);
  }

  function drawLand(scene) {
    el("rect", { x: 0, y: 0, width: W, height: H, fill: GRASS }, scene);
    // a pale sky band along the top, rounded lime hills rising into it
    el("rect", { x: 0, y: 0, width: W, height: SKY_BOTTOM, fill: SKY }, scene);
    const hill = (cx, rx, ry) => {
      el("path", { d: `M${cx - rx},${SKY_BOTTOM + 2} A${rx},${ry} 0 0 1 ${cx + rx},${SKY_BOTTOM + 2} Z`, fill: HILL, stroke: INK, "stroke-width": 1.6 }, scene);
      el("path", { d: `M${cx - rx * 0.7},${SKY_BOTTOM - ry * 0.55} Q${cx},${SKY_BOTTOM - ry * 1.15} ${cx + rx * 0.7},${SKY_BOTTOM - ry * 0.55}`, fill: "none", stroke: "#8fa53a", "stroke-width": 1, opacity: 0.8 }, scene);
    };
    hill(250, 230, 62); hill(950, 230, 62);
    el("path", { d: `M0,${SKY_BOTTOM + 2} H${W}`, stroke: INK, "stroke-width": 1.4 }, scene);
    // ochre walkway joining the pavilions, then the brown shore path
    el("ellipse", { cx: CX, cy: CY, rx: SEAT_RX + 8, ry: SEAT_RY + 8, fill: "none", stroke: INK, "stroke-width": 24 }, scene);
    el("ellipse", { cx: CX, cy: CY, rx: SEAT_RX + 8, ry: SEAT_RY + 8, fill: "none", stroke: PATH, "stroke-width": 21 }, scene);
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX + 10, ry: LAKE_RY + 10, fill: "#a67a3c", stroke: INK, "stroke-width": 2 }, scene);
    el("ellipse", { cx: CX, cy: CY, rx: LAKE_RX, ry: LAKE_RY, fill: WATER }, scene);
    const waves = el("g", { "clip-path": "url(#lake-clip)", stroke: CREST, "stroke-width": 1.2, "stroke-linejoin": "round" }, scene);
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
      const y = rng() < 0.5 ? SKY_BOTTOM + 8 + rng() * 60 : H - FRAME - 30 - rng() * 90;
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
    // trees, one symbol at varying scale; the top pair stands on the hills, the bottom pair on the bank
    const placeTree = (x, y, sc) => {
      el("use", { href: "#tree", x: -72, y: -142, width: 144, height: 150, transform: `translate(${x} ${y}) scale(${sc})` }, layers.flora);
      keepOut.push({ x0: x - 70 * sc, y0: y - 142 * sc, x1: x + 70 * sc, y1: y - 30 * sc }, { x0: x - 12, y0: y - 60 * sc, x1: x + 12, y1: y + 4 });
    };
    placeTree(108, 196, 1.15); placeTree(206, 218, 0.8); placeTree(W - 108, 196, 1.15); placeTree(W - 206, 218, 0.8);
    placeTree(110, H - FRAME - 6, 1.05); placeTree(W - 110, H - FRAME - 6, 1.05);
    // animals on the grass, decoration only: two cows on the foot band, a peacock by the left shore
    el("use", { href: "#cow", x: -2, y: -30, width: 64, height: 32, transform: "translate(492 742) scale(1.05)" }, layers.flora);
    el("use", { href: "#cow", x: -2, y: -30, width: 64, height: 32, transform: "translate(706 742) scale(-1.05 1.05)" }, layers.flora);
    el("use", { href: "#peacock", x: -40, y: -66, width: 80, height: 70, transform: "translate(94 434) scale(0.8)" }, layers.flora);
    keepOut.push({ x0: 484, y0: 706, x1: 562, y1: 748 }, { x0: 636, y0: 706, x1: 714, y1: 748 }, { x0: 56, y0: 376, x1: 132, y1: 442 });

    leafShape(FRAME + 4, H - FRAME - 16, 26, 170); leafShape(W - FRAME - 4, H - FRAME - 16, 154, 170);

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

  function drawTufts(rng) {
    // grass and lotus-bud tufts on an even grid with a little jitter, over every grass area, clear of everything drawn on it
    const step = 38;
    const blocked = (x, y) => {
      if (y < SKY_BOTTOM + 12 || y > H - FRAME - 4 || x < FRAME + 10 || x > W - FRAME - 10) return true;
      const dx = (x - CX) / (SEAT_RX + 24), dy = (y - CY) / (SEAT_RY + 24);
      if (dx * dx + dy * dy < 1) return true;
      return keepOut.some((r) => x > r.x0 - 6 && x < r.x1 + 6 && y > r.y0 - 4 && y < r.y1 + 8);
    };
    let k = 0;
    for (let gy = SKY_BOTTOM + 24; gy < H - FRAME; gy += step) {
      for (let gx = FRAME + 20 + ((gy / step) % 2) * (step / 2); gx < W - FRAME; gx += step, k++) {
        const x = gx + (rng() - 0.5) * 12, y = gy + (rng() - 0.5) * 10;
        if (blocked(x, y)) continue;
        el("use", { href: k % 3 === 0 ? "#tuft-bud" : "#tuft", x: -8, y: -15, width: 16, height: 16, transform: `translate(${x.toFixed(1)} ${y.toFixed(1)})` }, layers.flora);
      }
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
    const side = hx < CX ? -1 : 1; // the portrait stands on the side away from the lake
    const topRow = hy < CY - SEAT_RY * 0.6;
    // jetty out to the water, under everything else at the station
    const jg = el("g", { transform: `rotate(${(toward * 180) / Math.PI})` }, g);
    el("rect", { x: 10, y: -7, width: 72, height: 14, fill: "#b5742f", stroke: INK, "stroke-width": 1.4 }, jg);
    for (let px = 16; px < 80; px += 9) el("line", { x1: px, y1: -7, x2: px, y2: 7, stroke: INK, "stroke-width": 0.8 }, jg);
    for (const px of [24, 52, 78]) el("rect", { x: px - 2, y: -10, width: 4, height: 20, fill: "#7a4a1e", stroke: INK, "stroke-width": 0.8 }, jg);
    // pavilion: pink walls, cream floral eaves, flat orange roof with chhatris, cusped door, dark windows, balustrade
    const trim = { fill: "none", stroke: color, "stroke-width": 2 };
    el("rect", { x: -34, y: -22, width: 68, height: 38, fill: "#e6b9c6", stroke: INK, "stroke-width": 1.5 }, g);
    el("rect", { x: -30, y: -42, width: 60, height: 10, fill: "#e6b9c6", stroke: INK, "stroke-width": 1.4 }, g);
    el("rect", { x: -39, y: -32, width: 78, height: 10, fill: "#f6efd6", stroke: INK, "stroke-width": 1.4 }, g);
    el("rect", { x: -39, y: -32, width: 78, height: 10, fill: "url(#eaves-floral)" }, g);
    el("rect", { x: -36, y: -48, width: 72, height: 6, fill: "#e0662a", stroke: INK, "stroke-width": 1.4 }, g);
    el("rect", { x: -30, y: -35, width: 60, height: 3, fill: color, stroke: INK, "stroke-width": 0.8 }, g);
    for (const cx of [-27, 27]) {
      el("rect", { x: cx - 5, y: -55, width: 10, height: 7, fill: "#f6efd6", stroke: INK, "stroke-width": 1 }, g);
      el("path", { d: `M${cx - 6},-55 A6,6 0 0 1 ${cx + 6},-55 Z`, fill: "#f6efd6", stroke: INK, "stroke-width": 1.1 }, g);
      el("path", { d: `M${cx - 4},-55 L${cx - 4},-49 M${cx + 4},-55 L${cx + 4},-49`, stroke: INK, "stroke-width": 0.7 }, g);
      el("circle", { cx, cy: -62, r: 1.6, fill: GOLD, stroke: INK, "stroke-width": 0.6 }, g);
    }
    el("path", { d: "M-9,16 L-9,-3 Q-9,-8 -6,-8 Q-4,-12 0,-13 Q4,-12 6,-8 Q9,-8 9,-3 L9,16 Z", fill: "#3b2413", stroke: color, "stroke-width": 2.4, "stroke-linejoin": "round" }, g);
    el("path", { d: "M-9,16 L-9,-3 Q-9,-8 -6,-8 Q-4,-12 0,-13 Q4,-12 6,-8 Q9,-8 9,-3 L9,16 Z", fill: "none", stroke: INK, "stroke-width": 0.9, "stroke-linejoin": "round" }, g);
    for (const wx of [-25, 15]) {
      el("rect", { x: wx, y: -14, width: 10, height: 12, fill: "#3e4a5c", stroke: INK, "stroke-width": 1 }, g);
      el("rect", { x: wx - 1.5, y: -15.5, width: 13, height: 15, ...trim }, g);
    }
    for (const [bx, bw] of [[-38, 27], [11, 27]]) {
      el("rect", { x: bx, y: 9, width: bw, height: 7, fill: "#f6efd6", stroke: INK, "stroke-width": 1 }, g);
      for (let px = bx + 4; px < bx + bw; px += 4) el("line", { x1: px, y1: 10, x2: px, y2: 15, stroke: INK, "stroke-width": 0.7 }, g);
    }
    // portrait: the bust in a gold-and-red painted border, twice the old size
    const nx = side * 99, ny = -6, nw = 102, nh = 121;
    const glow = el("rect", { class: "hut-glow", x: nx - nw / 2 - 6, y: ny - nh / 2 - 6, width: nw + 12, height: nh + 12, rx: 5, fill: "rgba(242,201,76,0.3)", stroke: GOLD, "stroke-width": 5 }, g);
    el("rect", { x: nx - nw / 2, y: ny - nh / 2, width: nw, height: nh, fill: GOLD, stroke: INK, "stroke-width": 1.6 }, g);
    el("rect", { x: nx - nw / 2 + 4, y: ny - nh / 2 + 4, width: nw - 8, height: nh - 8, fill: "#8f2a1e", stroke: INK, "stroke-width": 0.8 }, g);
    el("rect", { x: nx - nw / 2 + 6, y: ny - nh / 2 + 6, width: nw - 12, height: nh - 12, fill: "none", stroke: GOLD, "stroke-width": 1 }, g);
    el("use", { href: `#av-${i}`, x: nx - 43, y: ny - 51.5, width: 86, height: 103 }, g);
    el("rect", { x: nx - 43, y: ny - 51.5, width: 86, height: 103, fill: "none", stroke: INK, "stroke-width": 1.2 }, g);
    // plaque: name and tally on one line, beyond the portrait and centred on it
    const gx = nx;
    const plaqueY = topRow ? ny - nh / 2 - 27 : ny + nh / 2 + 5;
    el("rect", { x: gx - 62, y: plaqueY, width: 124, height: 23, rx: 3, fill: "#7a1f1f", stroke: GOLD, "stroke-width": 1.6 }, g);
    el("rect", { x: gx - 59, y: plaqueY + 3, width: 118, height: 17, rx: 2, fill: "none", stroke: INK, "stroke-width": 0.8 }, g);
    const plaque = text(gx, plaqueY + 16, "", { "text-anchor": "middle", fill: "#f6e7b2", "font-family": "Georgia, serif", "font-size": 13 }, g);
    const nameSpan = el("tspan", { "font-weight": 700 }, plaque);
    nameSpan.textContent = replay.players[i].pseudonym;
    const tally = el("tspan", { "font-size": 12 }, plaque);
    tally.textContent = " · 0 fish";
    huts.push({ x: hx, y: hy, ang, glow, tally, color });
    keepOut.push({ x0: hx - 42, y0: hy - 66, x1: hx + 42, y1: hy + 18 }, { x0: hx + nx - nw / 2 - 4, y0: hy + ny - nh / 2 - 4, x1: hx + nx + nw / 2 + 4, y1: hy + ny + nh / 2 + 4 }, { x0: hx + gx - 64, y0: hy + plaqueY - 2, x1: hx + gx + 64, y1: hy + plaqueY + 25 });
    // boat: always right side up, mirrored to face the lake; ochre hull, red gunwale, a rower whose turban carries the seat colour
    const home = { x: hx + Math.cos(toward) * 92, y: hy + Math.sin(toward) * 92 };
    const facing = home.x < CX ? 1 : -1;
    const bg = el("g", { class: "boat", transform: `translate(${home.x} ${home.y})` }, layers.boats);
    const inner = el("g", { transform: `scale(${facing} 1)` }, bg);
    el("path", { d: "M-26,0 Q0,17 26,0 Q0,6 -26,0 Z", fill: color, stroke: INK, "stroke-width": 1.5, "stroke-linejoin": "round" }, inner);
    el("path", { d: "M-22,1.5 Q0,6.5 22,1.5", fill: "none", stroke: "#f6e7b2", "stroke-width": 1.6 }, inner);
    el("path", { d: "M26,0 q6,-8 3,-16", fill: "none", stroke: INK, "stroke-width": 2.2, "stroke-linecap": "round" }, inner);
    el("path", { d: "M-8,-3 L-6,-10 L2,-10 L4,-3 Z", fill: CREAM, stroke: INK, "stroke-width": 1 }, inner);
    el("circle", { cx: -2, cy: -13, r: 4, fill: "#d9a06b", stroke: INK, "stroke-width": 1 }, inner);
    el("path", { d: "M-6.5,-14 A4.5,4.5 0 0 1 2.5,-14 Z", fill: "#f3ecd8", stroke: INK, "stroke-width": 1 }, inner);
    el("line", { x1: 4, y1: -6, x2: 18, y2: -18, stroke: INK, "stroke-width": 1.6, "stroke-linecap": "round" }, inner);
    boats.push({ node: bg, home, ang: toward, out: null });
    const label = text(home.x, home.y - 30, "", { class: "catch-label", "text-anchor": "middle" }, layers.effects);
    catchLabels.push(label);
  }

  function drawLeaf() {
    // a palm-leaf folio: long tan leaf, frayed ends, a string hole, the speaker painted at the right end
    const x0 = 232, y0 = 482, w = 736, h = 108;
    const g = el("g", { class: "leaf-scroll" }, layers.effects);
    const rng = mulberry32(11);
    const pts = [];
    for (let x = x0; x <= x0 + w; x += 32) pts.push([x, y0 + (rng() - 0.5) * 3]);
    pts.push([x0 + w + 6, y0 + 30], [x0 + w + 2, y0 + 62], [x0 + w + 5, y0 + 90]);
    for (let x = x0 + w; x >= x0; x -= 32) pts.push([x, y0 + h + (rng() - 0.5) * 3]);
    pts.push([x0 - 6, y0 + 84], [x0 - 2, y0 + 50], [x0 - 5, y0 + 22]);
    const d = "M" + pts.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" L") + " Z";
    const clip = el("clipPath", { id: "leaf-clip" }, svg.querySelector("defs"));
    el("path", { d }, clip);
    el("path", { d, fill: "#5a3a1a", transform: "translate(3 4)", opacity: 0.35 }, g);
    el("path", { d, fill: "url(#leaf-grain)", stroke: INK, "stroke-width": 1.4, "stroke-linejoin": "round" }, g);
    const worn = el("g", { "clip-path": "url(#leaf-clip)" }, g);
    el("rect", { x: x0 - 8, y: y0 - 4, width: w + 16, height: h + 8, fill: "url(#leaf-edge-x)" }, worn);
    el("rect", { x: x0 - 8, y: y0 - 4, width: w + 16, height: h + 8, fill: "url(#leaf-edge-y)" }, worn);
    for (let i = 0; i < 14; i++) {
      const bx = x0 + rng() * w, by = y0 + 6 + rng() * (h - 12);
      el("ellipse", { cx: bx, cy: by, rx: 9 + rng() * 22, ry: 2 + rng() * 3, fill: "#8a5f2a", opacity: 0.08 + rng() * 0.08 }, worn);
    }
    // frayed nicks at the ends
    el("path", { d: `M${x0 - 5},${y0 + 22} l9,4 l-7,6 l8,5 l-6,7 M${x0 + w + 6},${y0 + 30} l-8,5 l7,7 l-8,4 l6,8`, fill: "none", stroke: "#6a4620", "stroke-width": 1, opacity: 0.6 }, g);
    // string hole
    el("circle", { cx: x0 + 92, cy: y0 + h / 2, r: 11, fill: "#5a3a1a", opacity: 0.28 }, g);
    el("circle", { cx: x0 + 92, cy: y0 + h / 2, r: 6, fill: "#3a2510", stroke: "#8a6432", "stroke-width": 1.5 }, g);
    // painted panel at the right end: deep blue field, red surround, the speaker's bust
    const px = x0 + w - 118, pw = 106;
    el("rect", { x: px, y: y0 + 6, width: pw, height: h - 12, fill: "#8f2a1e", stroke: INK, "stroke-width": 1.2 }, g);
    el("rect", { x: px + 5, y: y0 + 11, width: pw - 10, height: h - 22, fill: "#274f8c", stroke: GOLD, "stroke-width": 1.5 }, g);
    const portrait = el("use", { href: "#av-0", x: px + 24, y: y0 + 13, width: 58, height: 68 }, g);
    el("rect", { x: px + 24, y: y0 + 13, width: 58, height: 68, fill: "none", stroke: GOLD, "stroke-width": 1.2 }, g);
    for (const [cx, cy] of [[px + 14, y0 + 20], [px + 14, y0 + h - 20], [px + pw - 14, y0 + 20], [px + pw - 14, y0 + h - 20]]) {
      el("circle", { cx, cy, r: 2.2, fill: GOLD }, g);
    }
    el("rect", { x: px + 9, y: y0 + 83, width: pw - 18, height: 15, rx: 2, fill: CREAM, stroke: INK, "stroke-width": 0.9 }, g);
    const caption = text(px + pw / 2, y0 + 94, "", { "text-anchor": "middle", fill: INK, "font-family": "Georgia, serif", "font-size": 10.5, "font-weight": 700 }, g);
    // the words, in a serif that reads as hand-set
    const fo = el("foreignObject", { x: x0 + 112, y: y0 + 7, width: px - x0 - 122, height: h - 14 }, g);
    const textNode = document.createElementNS("http://www.w3.org/1999/xhtml", "div");
    textNode.className = "leaf-text";
    fo.appendChild(textNode);
    leaf = { group: g, textNode, portrait, caption };
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
    drawLand(scene);
    layers.fish = el("g", { class: "fish-school" }, scene);
    layers.flora = el("g", {}, scene);
    layers.huts = el("g", {}, scene);
    layers.boats = el("g", {}, scene);
    layers.effects = el("g", {}, scene);
    layers.frame = el("g", {}, svg);

    const rng = mulberry32(replay.seed || 1);
    keepOut = [];
    drawFlora(rng);

    // the school: count visible follows the true stock
    fishGlyphs = [];
    for (let i = 0; i < 64; i++) {
      const a = rng() * Math.PI * 2;
      const r = Math.sqrt(rng());
      const x = CX + Math.cos(a) * r * 330;
      const y = CY + Math.sin(a) * r * 190;
      const dir = rng() < 0.5 ? -1 : 1;
      const size = 40 + rng() * 20;
      const node = el("use", { href: "#fish", x: -size / 2, y: -size * 0.23, width: size, height: size * 0.46, color: rng() < 0.34 ? FISH_RED : FISH_GOLD, transform: `translate(${x} ${y}) scale(${dir} 1) rotate(${(rng() - 0.5) * 30})` }, layers.fish);
      fishGlyphs.push(node);
    }

    huts = []; boats = []; catchLabels = [];
    seatSpecs.forEach((spec, i) => drawStation(i, spec));
    drawTufts(rng);
    punishLayer = el("g", {}, layers.effects);

    // council mandala: a lotus in the middle of the lake
    mandala = el("g", { class: "council-mandala", transform: `translate(${CX} ${CY - 30})` }, layers.effects);
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

    drawLeaf();

    // lake cartouche (spectator-only truth), on the bank above the lake
    // lake health bar (spectator-only truth): cream track, state-coloured fill to the fullness, a tick at the point of no return
    const cart = el("g", { transform: `translate(${CX} ${FRAME + 17})` }, layers.frame);
    el("rect", { x: -172, y: -14, width: 344, height: 28, rx: 3, fill: CREAM, stroke: INK, "stroke-width": 1.4 }, cart);
    lakeFill = el("rect", { class: "lake-fill-paint", x: -170, y: -12, width: 0, height: 24, rx: 2, fill: "#4f7d3a" }, cart);
    const tickX = -170 + (replay.lake.collapse_threshold / replay.lake.capacity) * 340;
    el("path", { d: `M${tickX},-14 L${tickX},-9 M${tickX},9 L${tickX},14`, stroke: INK, "stroke-width": 2 }, cart);
    el("path", { d: `M${tickX},-9 L${tickX},9`, stroke: INK, "stroke-width": 1, "stroke-dasharray": "2 2" }, cart);
    lakeCartouche = text(0, 4.5, "", { class: "lake-cartouche-text", "text-anchor": "middle" }, cart);

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
    const state = dying ? "past" : share > 0.55 ? "healthy" : "strained";
    const label = { healthy: "healthy", strained: "strained", past: "past the point of no return" }[state];
    lakeCartouche.textContent = `Lake ${pct}% full — ${label}`;
    lakeFill.style.width = `${share * 340}px`;
    lakeFill.setAttribute("fill", { healthy: "#4f7d3a", strained: "#c98a1f", past: "#b5432c" }[state]);
    const lakeText = $("lake-text");
    lakeText.textContent = `Lake ${pct}% full — ${label}`;
    lakeText.className = `lake-text state-${state}`;
    $("lake-detail").textContent = `${Math.round(stock).toLocaleString("en-US")} of ${Math.round(replay.lake.capacity).toLocaleString("en-US")} fish · point of no return at ${Math.round(replay.lake.collapse_threshold).toLocaleString("en-US")}`;
    const fill = $("lake-fill");
    fill.style.width = `${pct}%`;
    fill.className = `lake-fill state-${state}`;
    $("lake-threshold").style.left = `${(replay.lake.collapse_threshold / replay.lake.capacity) * 100}%`;
  }

  function renderTally(fish) {
    huts.forEach((h, i) => { h.tally.textContent = ` · ${fish[i]} fish`; });
  }

  function clearPunish() {
    while (punishLayer.firstChild) punishLayer.removeChild(punishLayer.firstChild);
  }

  function towardLake(h, dist) {
    const a = Math.atan2(CY - h.y, CX - h.x);
    return { x: h.x + Math.cos(a) * dist, y: h.y + Math.sin(a) * dist };
  }

  function renderPunish(turn, show) {
    clearPunish();
    if (!show) return;
    const hits = huts.map(() => 0);
    for (const p of turn.punish) {
      const src = huts[p.frm], dst = huts[p.to];
      const nth = hits[p.to]++;
      // shaft runs from the source jetty to the target jetty root, bowed to one side; sources stagger the bow, the landing depth and the offset
      const A = towardLake(src, 70), B0 = towardLake(dst, 44 + (nth % 3) * 16);
      const dx = B0.x - A.x, dy = B0.y - A.y, len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const bow = ((p.frm + p.to) % 2 ? 1 : -1) * (36 + (p.frm % 4) * 18);
      const land = ((p.frm % 3) - 1) * 20;
      const B = { x: B0.x + nx * land, y: B0.y + ny * land };
      const C = { x: (A.x + B.x) / 2 + nx * bow, y: (A.y + B.y) / 2 + ny * bow };
      const at = (t) => ({ x: (1 - t) ** 2 * A.x + 2 * (1 - t) * t * C.x + t * t * B.x, y: (1 - t) ** 2 * A.y + 2 * (1 - t) * t * C.y + t * t * B.y });
      const tan = (t) => { const x = 2 * (1 - t) * (C.x - A.x) + 2 * t * (B.x - C.x), y = 2 * (1 - t) * (C.y - A.y) + 2 * t * (B.y - C.y); const l = Math.hypot(x, y) || 1; return { x: x / l, y: y / l }; };
      const headLen = 18, tEnd = 1 - headLen / Math.hypot(B.x - A.x, B.y - A.y);
      const e = at(tEnd), d = tan(tEnd), deg = (Math.atan2(d.y, d.x) * 180) / Math.PI;
      const Ce = { x: C.x * tEnd + A.x * (1 - tEnd), y: C.y * tEnd + A.y * (1 - tEnd) }; // control point of the curve truncated at tEnd
      el("path", { class: "punish-shaft show", d: `M${A.x},${A.y} Q${Ce.x},${Ce.y} ${e.x},${e.y}` }, punishLayer);
      el("path", { class: "punish-head show", d: "M20,0 L-2,-9 L-2,9 Z", transform: `translate(${e.x} ${e.y}) rotate(${deg})` }, punishLayer);
      // the loss sits beside the shaft just behind the head, stepped back along the shaft for each further hit on the same seat
      const lp = at(Math.max(0.35, tEnd - (0.14 + nth * 0.1))), ld = tan(tEnd);
      text(lp.x - ld.y * 18 * Math.sign(bow), lp.y + ld.x * 18 * Math.sign(bow) + 6, `−${p.fish}`, { class: "punish-label show", "text-anchor": "middle" }, punishLayer);
      const sx = A.x + nx * 16 * Math.sign(bow), sy = A.y + ny * 16 * Math.sign(bow);
      text(sx, sy + 5, `burned ${p.cost}`, { class: "punish-label burn show", "text-anchor": "middle", "font-size": 13 }, punishLayer);
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

  function showLeaf(speech) {
    const name = replay.players[speech.slot].pseudonym;
    leaf.portrait.setAttribute("href", `#av-${speech.slot}`);
    leaf.caption.textContent = speech.auto ? `${name} · auto` : name;
    const node = leaf.textNode;
    node.classList.toggle("silent", !speech.text);
    node.textContent = speech.text || "says nothing.";
    // long speeches set smaller; then shrink until the words fit the leaf
    const n = (speech.text || "").length;
    let size = n <= 110 ? 18 : n <= 220 ? 15.5 : n <= 340 ? 14 : 12.5;
    node.style.fontSize = `${size}px`;
    while (size > 9.5 && node.scrollHeight > node.clientHeight + 1) {
      size -= 0.5;
      node.style.fontSize = `${size}px`;
    }
    leaf.group.classList.add("show");
  }

  function renderCouncil(commune, visibleCount, showCaption) {
    if (!commune) {
      councilTitle.textContent = "Council";
      councilBody.innerHTML = '<p class="empty">No council yet.</p>';
      leaf.group.classList.remove("show");
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
    if (showCaption && latest) showLeaf(latest);
    else leaf.group.classList.remove("show");
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
