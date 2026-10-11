/*
 * DentDeID burned-in text detector — JavaScript port of dentdeid/imaging/textdetect.py.
 *
 * Integer arithmetic only, same steps in the same order, so the browser app returns the SAME
 * boxes (and the same image class) as the Python library — checked by tests/test_web_parity.py
 * and tests/test_parity_cli_bench.py. MIT licence.
 *
 * Usage (browser):  DentDeIDDetector.detectText(imageData)                 // ImageData (RGBA)
 *                   DentDeIDDetector.imageClass(imageData)                 // "radiograph" | "screenshot" | "photo"
 * Usage (node):     require("./detector.js").detectText({width, height, data, channels}, params, forceColor)
 * Usage (worker):   importScripts("detector.js"); DentDeIDDetector.scanViews([...views])
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DentDeIDDetector = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ALGORITHM_VERSION = "dentdeid-text-3";
  const DEFAULTS = Object.freeze({
    bright_min: 150, dark_max: 90, contrast: 80, radius: 3, chroma_min: 48, gray_chroma: 24,
    gray_percent: 2, min_h: 5, max_h_percent: 5, min_pixels: 6, max_fill_percent: 85,
    min_components: 3, margin_percent: 15, detect_bright: true, detect_dark: true,
    detect_color: true, sensitive_pass: true, sensitive_bright_min: 120, sensitive_contrast: 50,
    neutral_chroma: 8, screenshot_neutral_percent: 50, screenshot_mid_percent: 30, screenshot_flat_percent: 40,
  });
  const params = (p) => Object.assign({}, DEFAULTS, p || {});

  // ---- pixels -> gray / chroma --------------------------------------------------------
  function toGray(img) {
    const w = img.width, h = img.height, n = w * h;
    const ch = img.channels || 4;
    const gray = new Uint8Array(n);
    let chroma = null;
    if (ch === 1) {
      gray.set(img.data.subarray ? img.data.subarray(0, n) : img.data.slice(0, n));
      return { gray, chroma, w, h };
    }
    chroma = new Uint8Array(n);
    const d = img.data;
    for (let i = 0, k = 0; i < n; i++, k += ch) {
      const r = d[k], g = d[k + 1], b = d[k + 2];
      gray[i] = (77 * r + 150 * g + 29 * b + 128) >> 8;
      const mx = r > g ? (r > b ? r : b) : (g > b ? g : b);
      const mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
      chroma[i] = mx - mn;
    }
    return { gray, chroma, w, h };
  }

  // Separable (2r+1)^2 min or max filter with edge replication (same result as the Python
  // _local: window [x-r, x+r] with indices clamped to the image). Interior pixels take a
  // branch-free fast path; only the r-pixel borders clamp.
  function pass1d(src, dst, len, count, stride, step, r, isMin) {
    // `count` lines of `len` samples; sample k of line j is at j*stride + k*step
    for (let j = 0; j < count; j++) {
      const base = j * stride;
      for (let k = 0; k < len; k++) {
        const lo = k - r < 0 ? 0 : k - r, hi = k + r > len - 1 ? len - 1 : k + r;
        let v = src[base + lo * step];
        if (isMin) { for (let q = lo + 1; q <= hi; q++) { const u = src[base + q * step]; if (u < v) v = u; } }
        else { for (let q = lo + 1; q <= hi; q++) { const u = src[base + q * step]; if (u > v) v = u; } }
        dst[base + k * step] = v;
        if (k === r - 1 && len > 2 * r + 1) {          // jump to the interior fast path
          const end = len - r;
          if (isMin) {
            for (let kk = r; kk < end; kk++) {
              let p = base + (kk - r) * step, m = src[p];
              for (let q = 1; q <= 2 * r; q++) { p += step; const u = src[p]; if (u < m) m = u; }
              dst[base + kk * step] = m;
            }
          } else {
            for (let kk = r; kk < end; kk++) {
              let p = base + (kk - r) * step, m = src[p];
              for (let q = 1; q <= 2 * r; q++) { p += step; const u = src[p]; if (u > m) m = u; }
              dst[base + kk * step] = m;
            }
          }
          k = end - 1;
        }
      }
    }
  }
  function local(src, w, h, r, isMin) {
    const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h);
    if (r <= 0) { out.set(src); return out; }
    pass1d(src, tmp, w, h, w, 1, r, isMin);   // rows
    // columns, processed row by row (sequential memory access): out[y] = min/max of tmp[y-r..y+r]
    for (let y = 0; y < h; y++) {
      const lo = y - r < 0 ? 0 : y - r, hi = y + r > h - 1 ? h - 1 : y + r;
      const o = y * w;
      out.set(tmp.subarray(lo * w, lo * w + w), o);
      for (let q = lo + 1; q <= hi; q++) {
        const t = q * w;
        if (isMin) { for (let x = 0; x < w; x++) { const u = tmp[t + x]; if (u < out[o + x]) out[o + x] = u; } }
        else { for (let x = 0; x < w; x++) { const u = tmp[t + x]; if (u > out[o + x]) out[o + x] = u; } }
      }
    }
    return out;
  }

  // 8-connected components via run-length union-find -> [x0, y0, x1, y1, count] sorted
  function components(mask, w, h) {
    let cap = 1024;
    let rx0 = new Int32Array(cap), rx1 = new Int32Array(cap), ry = new Int32Array(cap);
    const rowStart = new Int32Array(h + 1);
    let n = 0;
    for (let y = 0; y < h; y++) {
      rowStart[y] = n;
      const row = y * w;
      let x = 0;
      while (x < w) {
        if (mask[row + x]) {
          const s = x;
          while (x < w && mask[row + x]) x++;
          if (n === cap) {
            cap *= 2;
            const a = new Int32Array(cap), b = new Int32Array(cap), c = new Int32Array(cap);
            a.set(rx0); b.set(rx1); c.set(ry); rx0 = a; rx1 = b; ry = c;
          }
          rx0[n] = s; rx1[n] = x; ry[n] = y; n++;
        } else x++;
      }
    }
    rowStart[h] = n;
    const parent = new Int32Array(n);
    for (let i = 0; i < n; i++) parent[i] = i;
    const find = (i) => {
      while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; }
      return i;
    };
    for (let y = 1; y < h; y++) {
      const a1 = rowStart[y], b1 = rowStart[y + 1];
      let i = rowStart[y - 1], j = rowStart[y];
      while (i < a1 && j < b1) {
        if (rx0[j] <= rx1[i] && rx0[i] <= rx1[j]) {
          const ri = find(i), rj = find(j);
          if (ri !== rj) { if (ri < rj) parent[rj] = ri; else parent[ri] = rj; }
        }
        if (rx1[i] < rx1[j]) i++; else j++;
      }
    }
    // one box per root, in order of first appearance (like the Python dict)
    const slot = new Int32Array(n).fill(-1);
    const out = [];
    for (let k = 0; k < n; k++) {
      const root = find(k);
      const s = slot[root];
      if (s < 0) { slot[root] = out.length; out.push([rx0[k], ry[k], rx1[k], ry[k] + 1, rx1[k] - rx0[k]]); }
      else {
        const b = out[s];
        if (rx0[k] < b[0]) b[0] = rx0[k];
        if (rx1[k] > b[2]) b[2] = rx1[k];
        if (ry[k] + 1 > b[3]) b[3] = ry[k] + 1;
        b[4] += rx1[k] - rx0[k];
      }
    }
    out.sort((a, b) => a[1] - b[1] || a[0] - b[0] || a[3] - b[3] || a[2] - b[2]);
    return out;
  }

  function glyphs(comps, maxH, p) {
    const out = [];
    for (const c of comps) {
      const [x0, y0, x1, y1, cnt] = c;
      const h = y1 - y0, w = x1 - x0;
      if (h < p.min_h || h > maxH) continue;
      if (w > 4 * h + 4) continue;
      if (cnt < p.min_pixels) continue;
      if (w > 3 && 100 * cnt > p.max_fill_percent * w * h) continue;
      out.push(c);
    }
    return out;
  }

  // Chain glyphs into text lines. Glyph pairs are linked when their heights are similar, their
  // rows overlap and the horizontal gap is small (same tests as textdetect._lines); candidate pairs
  // are looked up through horizontal bands of the image instead of scanning every glyph, which
  // finds exactly the same pairs (linked glyphs always share a row) in a fraction of the time.
  const BAND = 16;
  function lines(gl) {
    const n = gl.length;
    const order = Array.from({ length: n }, (_, i) => i);
    order.sort((a, b) => gl[a][0] - gl[b][0] || gl[a][1] - gl[b][1] || gl[a][2] - gl[b][2] || gl[a][3] - gl[b][3] || a - b);
    const g = order.map((i) => gl[i]);
    const parent = new Int32Array(n);
    for (let i = 0; i < n; i++) parent[i] = i;
    const find = (i) => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
    let maxhAll = 0, maxY = 0;
    for (const b of g) { maxhAll = Math.max(maxhAll, b[3] - b[1]); maxY = Math.max(maxY, b[3]); }
    const reach = Math.floor((3 * maxhAll + 4) / 2) + 1;
    // bands: glyph indices (ascending, i.e. in x order) whose rows touch each band
    const nb = n ? Math.floor((maxY - 1) / BAND) + 1 : 0;
    const start = new Int32Array(nb + 1);
    for (const b of g) for (let k = Math.floor(b[1] / BAND); k <= Math.floor((b[3] - 1) / BAND); k++) start[k + 1]++;
    for (let k = 0; k < nb; k++) start[k + 1] += start[k];
    const fill = start.slice(0, nb), items = new Int32Array(nb ? start[nb] : 0);
    for (let i = 0; i < n; i++) {
      const b = g[i];
      for (let k = Math.floor(b[1] / BAND); k <= Math.floor((b[3] - 1) / BAND); k++) items[fill[k]++] = i;
    }
    for (let i = 0; i < n; i++) {
      const gi = g[i];
      const yi0 = gi[1], xi1 = gi[2], yi1 = gi[3];
      const hi = yi1 - yi0, xmax = xi1 + reach;
      for (let k = Math.floor(yi0 / BAND); k <= Math.floor((yi1 - 1) / BAND); k++) {
        let lo = start[k], hiP = start[k + 1];
        while (lo < hiP) { const mid = (lo + hiP) >> 1; if (items[mid] <= i) lo = mid + 1; else hiP = mid; }
        for (let p = lo; p < start[k + 1]; p++) {
          const j = items[p], gj = g[j];
          const xj0 = gj[0], yj0 = gj[1], yj1 = gj[3];
          if (xj0 > xmax) break;
          const hj = yj1 - yj0;
          const hmin = Math.min(hi, hj), hmax = Math.max(hi, hj);
          if (2 * hmax > 5 * hmin) continue;
          const overlap = Math.min(yi1, yj1) - Math.max(yi0, yj0);
          if (2 * overlap < hmin) continue;
          const gap = xj0 - xi1;
          if (2 * gap > 3 * hmax + 4) continue;
          const ri = find(i), rj = find(j);
          if (ri !== rj) parent[Math.max(ri, rj)] = Math.min(ri, rj);
        }
      }
    }
    const groups = new Map();
    for (let i = 0; i < n; i++) {
      const r = find(i);
      if (!groups.has(r)) groups.set(r, []);
      groups.get(r).push(i);
    }
    const roots = Array.from(groups.keys()).sort((a, b) => a - b);
    return roots.map((r) => groups.get(r).sort((a, b) => a - b).map((i) => g[i]));
  }

  function isScale(members) {
    if (members.length < 8) return false;
    for (const b of members) if (b[2] - b[0] > 2) return false;
    const xs = members.slice().sort((a, b) => a[0] - b[0]);
    const gaps = [];
    for (let k = 0; k < xs.length - 1; k++) gaps.push(xs[k + 1][0] - xs[k][2]);
    if (Math.min(...gaps) <= 0) return false;
    let s = 0, s2 = 0;
    for (const x of gaps) { s += x; s2 += x * x; }
    const m = gaps.length;
    return (m * s2 - s * s) * 400 <= 9 * s * s;
  }

  function inMargin(x0, y0, x1, y1, w, h, pct) {
    const mby = Math.floor((h * pct) / 100), mbx = Math.floor((w * pct) / 100);
    const cy2 = y0 + y1, cx2 = x0 + x1;
    return cy2 < 2 * mby || cy2 > 2 * (h - mby) || cx2 < 2 * mbx || cx2 > 2 * (w - mbx);
  }

  function regionsFrom(mask, source, h, w, maxH, p, allowPairs) {
    const gl = glyphs(components(mask, w, h), maxH, p);
    const out = [];
    for (const members of lines(gl)) {
      const k = members.length;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const b of members) {
        if (b[0] < x0) x0 = b[0]; if (b[1] < y0) y0 = b[1];
        if (b[2] > x1) x1 = b[2]; if (b[3] > y1) y1 = b[3];
      }
      const margin = inMargin(x0, y0, x1, y1, w, h, p.margin_percent);
      let kind, conf;
      if (isScale(members)) { kind = "scale"; conf = "high"; }
      else if (k >= p.min_components) { kind = "text"; conf = (k >= 5 || margin) ? "high" : "medium"; }
      else if (allowPairs && k === 2 && margin && members.every((b) => b[0] > 0 && b[1] > 0 && b[2] < w && b[3] < h)) {
        kind = "text"; conf = "low";   // two glyphs in a margin; anatomy cut off by the edge is excluded
      }
      else continue;
      const hs = members.map((b) => b[3] - b[1]).sort((a, b) => a - b);
      const med = hs[Math.floor((hs.length - 1) / 2)];
      const pad = Math.floor((med + 3) / 4) + 2;
      out.push({ x0: Math.max(0, x0 - pad), y0: Math.max(0, y0 - pad), x1: Math.min(w, x1 + pad),
                 y1: Math.min(h, y1 + pad), kind, source, confidence: conf, n: k });
    }
    return out;
  }

  const RANK = { low: 0, medium: 1, high: 2 };
  const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const regionCmp = (a, b) => a.y0 - b.y0 || a.x0 - b.x0 || a.y1 - b.y1 || a.x1 - b.x1 ||
    cmpStr(a.kind, b.kind) || cmpStr(a.source, b.source);

  // Port of textdetect._merge / merge_regions: overlapping (or touching) regions of the same kind
  // are joined until none overlap. The fixed point does not depend on the order of the merges
  // (every overlap forces its merge), so it is computed in rounds with a sweep over x instead of
  // restarting a pairwise scan after every merge — same result, near-linear time.
  function mergeKind(list) {
    let cur = list;
    for (;;) {
      const n = cur.length;
      const idx = Array.from({ length: n }, (_, i) => i).sort((a, b) => cur[a].x0 - cur[b].x0 || a - b);
      const parent = new Int32Array(n);
      for (let i = 0; i < n; i++) parent[i] = i;
      const find = (i) => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
      let joined = false;
      for (let a = 0; a < n; a++) {
        const A = cur[idx[a]];
        for (let b = a + 1; b < n; b++) {
          const B = cur[idx[b]];
          if (B.x0 > A.x1) break;
          if (A.y0 <= B.y1 && B.y0 <= A.y1) {
            const ra = find(idx[a]), rb = find(idx[b]);
            if (ra !== rb) { parent[Math.max(ra, rb)] = Math.min(ra, rb); joined = true; }
          }
        }
      }
      if (!joined) return cur;
      const acc = new Map();
      for (let i = 0; i < n; i++) {
        const r = find(i), x = cur[i], m = acc.get(r);
        if (!m) { acc.set(r, Object.assign({}, x)); continue; }
        m.x0 = Math.min(m.x0, x.x0); m.y0 = Math.min(m.y0, x.y0); m.x1 = Math.max(m.x1, x.x1); m.y1 = Math.max(m.y1, x.y1);
        if (m.source !== x.source) m.source = "mixed";
        if (RANK[x.confidence] > RANK[m.confidence]) m.confidence = x.confidence;
        m.n += x.n;
      }
      cur = Array.from(acc.values());
    }
  }
  function mergeRegions(regions) {
    const byKind = new Map();
    for (const r of regions) {
      const c = { x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1, kind: r.kind, source: r.source, confidence: r.confidence, n: r.n };
      if (!byKind.has(r.kind)) byKind.set(r.kind, []);
      byKind.get(r.kind).push(c);
    }
    let out = [];
    for (const list of byKind.values()) out = out.concat(mergeKind(list));
    out.sort(regionCmp);
    return out;
  }

  function isGrayscaleLike(chroma, p) {
    p = p || DEFAULTS;
    if (!chroma) return true;
    let c = 0;
    const t = p.gray_chroma;
    for (let i = 0; i < chroma.length; i++) if (chroma[i] > t) c++;
    return c * 100 <= p.gray_percent * chroma.length;
  }

  // Port of textdetect.image_class (on already converted gray/chroma planes)
  function classify(gray, chroma, p, w) {
    if (!chroma || isGrayscaleLike(chroma, p)) return "radiograph";
    const n = chroma.length;
    let neutral = 0, mid = 0;
    for (let i = 0; i < n; i++) {
      if (chroma[i] <= p.neutral_chroma) {
        neutral++;
        const g = gray[i];
        if (g > 16 && g < 240) mid++;
      }
    }
    if (neutral * 100 >= p.screenshot_neutral_percent * n && mid * 100 >= p.screenshot_mid_percent * n) return "screenshot";
    // flat mid-tone areas (software backgrounds and panels)
    const h = w ? n / w : 0;
    if (w > 1 && h > 1) {
      let flat = 0;
      for (let y = 0; y < h - 1; y++) {
        for (let x = 0, i = y * w; x < w - 1; x++, i++) {
          const g = gray[i], c = chroma[i];
          if (g > 8 && g < 250 && g === gray[i + 1] && g === gray[i + w] && c === chroma[i + 1] && c === chroma[i + w]) flat++;
        }
      }
      if (flat * 100 >= p.screenshot_flat_percent * (h - 1) * (w - 1)) return "screenshot";
    }
    return "photo";
  }

  /** "radiograph" (greyscale), "screenshot" (greyscale content in coloured chrome) or "photo". */
  function imageClass(img, prm) {
    const p = params(prm);
    const { gray, chroma, w } = toGray(img);
    return classify(gray, chroma, p, w);
  }

  // The opening / closing only depend on the image and the radius, so the sensitive pass
  // reuses those of the first pass (identical result, half the work).
  function strokeRegions(gray, w, h, maxH, brightMin, darkMax, contrast, p, allowPairs, cache, tick) {
    const out = [];
    const n = w * h;
    if (p.detect_bright) {
      if (!cache.opening) cache.opening = local(local(gray, w, h, p.radius, true), w, h, p.radius, false);
      const opening = cache.opening;
      tick();
      const m = new Uint8Array(n);
      for (let i = 0; i < n; i++) m[i] = (gray[i] >= brightMin && gray[i] - opening[i] >= contrast) ? 1 : 0;
      out.push(...regionsFrom(m, "bright", h, w, maxH, p, allowPairs));
      tick();
    }
    if (p.detect_dark) {
      if (!cache.closing) cache.closing = local(local(gray, w, h, p.radius, false), w, h, p.radius, true);
      const closing = cache.closing;
      tick();
      const m = new Uint8Array(n);
      for (let i = 0; i < n; i++) m[i] = (gray[i] <= darkMax && closing[i] - gray[i] >= contrast) ? 1 : 0;
      out.push(...regionsFrom(m, "dark", h, w, maxH, p, allowPairs));
      tick();
    }
    return out;
  }

  function detectPlanes(gray, chroma, w, h, p, forceColor, onProgress) {
    // progress ticks: (opening, bright, closing, dark) for each of the two stroke passes
    const steps = 9;
    let done = 0;
    const tick = () => { done++; if (onProgress) onProgress(Math.min(1, done / steps)); };
    const cache = {};
    const maxH = Math.max(16, Math.floor((Math.min(h, w) * p.max_h_percent) / 100));
    let regions = strokeRegions(gray, w, h, maxH, p.bright_min, p.dark_max, p.contrast, p, true, cache, tick);
    if (p.sensitive_pass) {
      // the sensitive pass needs >= 3 aligned glyphs (no two-glyph margin rule): faint noise
      // specks near root apices must not become "text"
      regions = regions.concat(strokeRegions(gray, w, h, maxH, p.sensitive_bright_min, p.dark_max, p.sensitive_contrast,
                                             p, false, cache, tick));
    }
    if (p.detect_color && chroma && (forceColor || isGrayscaleLike(chroma, p))) {
      const m = new Uint8Array(w * h);
      for (let i = 0; i < m.length; i++) m[i] = chroma[i] >= p.chroma_min ? 1 : 0;
      regions = regions.concat(regionsFrom(m, "color", h, w, maxH, p, true));
    }
    if (onProgress) onProgress(1);
    return mergeRegions(regions);
  }

  /**
   * Detect burned-in text lines (and scales) in a uint8 greyscale or RGB(A) image.
   * The colour-overlay pass runs on greyscale-like images, or on any colour image when
   * `forceColor` is set (screenshots). `opts.onProgress(fraction)` reports progress.
   */
  function detectText(img, prm, forceColor, opts) {
    const p = params(prm);
    const { gray, chroma, w, h } = toGray(img);
    return detectPlanes(gray, chroma, w, h, p, !!forceColor, opts && opts.onProgress);
  }

  /**
   * Mirror of the Python raster/DICOM pipeline: the image class comes from the first (display)
   * view; every view is scanned (colour pass forced for screenshots and photos) and the results merged.
   * Returns {imageClass, regions}.
   */
  function scanViews(views, prm, opts) {
    const p = params(prm);
    const onProgress = opts && opts.onProgress;
    let cls = null, all = [];
    views.forEach((v, i) => {
      const { gray, chroma, w, h } = toGray(v);
      if (i === 0) cls = classify(gray, chroma, p, w);
      const prog = onProgress ? (f) => onProgress((i + f) / views.length) : null;
      // colour-labelled text is looked for in screenshots and (as suggestions) in photographs
      all = all.concat(detectPlanes(gray, chroma, w, h, p, cls !== "radiograph", prog));
    });
    return { imageClass: cls, regions: mergeRegions(all) };
  }

  return { ALGORITHM_VERSION, DEFAULTS, detectText, imageClass, scanViews, mergeRegions, components, toGray,
           isGrayscaleLike };
});
