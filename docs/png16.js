/*
 * png16 — 16-bit PNG reader and 16→8-bit display mapping for the DentDeID web app.
 *
 * Browsers decode 16-bit PNGs to 8 bits by dropping the low byte, so a 12-bit sensor export
 * (values 0…4095) shows up almost black and its burned-in text is missed. This module reads
 * the PNG itself (zlib via DecompressionStream, scanline filters 0–4) and maps the samples with
 * the same function as dentdeid.imaging.raster.display8 (numpy "linear" percentiles, top of the
 * range kept open), plus the plain linear view raster.linear8 used as a second detection view.
 * Parity with Python is checked by tests/test_web_parity.py. MIT licence.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DentDeIDPng16 = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };

  function isPng(u8) {
    if (u8.length < 8) return false;
    for (let i = 0; i < 8; i++) if (u8[i] !== SIG[i]) return false;
    return true;
  }

  /** IHDR fields, or null when this is not a PNG. */
  function readHeader(u8) {
    if (!isPng(u8) || u8.length < 33) return null;
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const type = String.fromCharCode(u8[12], u8[13], u8[14], u8[15]);
    if (type !== "IHDR") return null;
    return { width: dv.getUint32(16), height: dv.getUint32(20), bitDepth: u8[24], colorType: u8[25],
             compression: u8[26], filter: u8[27], interlace: u8[28] };
  }

  async function inflate(bytes) {
    if (typeof DecompressionStream === "undefined") throw new Error("no-decompression-stream");
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /**
   * Decode a non-interlaced 16-bit PNG (colour types 0, 2, 4, 6).
   * Returns {width, height, channels, colorType, data: Uint16Array (interleaved samples)}.
   */
  async function decode(u8) {
    const hd = readHeader(u8);
    if (!hd) throw new Error("This is not a PNG file.");
    if (hd.bitDepth !== 16) throw new Error("Not a 16-bit PNG.");
    if (!(hd.colorType in CHANNELS) || hd.colorType === 3) throw new Error("Unsupported PNG colour type.");
    if (hd.interlace) {
      const e = new Error("This 16-bit PNG is interlaced, which the browser version can't read at full depth. " +
        "Export it as a non-interlaced PNG, or use the Python tool (pip install dentdeid).");
      e.userFacing = true;
      throw e;
    }
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    const parts = [];
    let total = 0, p = 8;
    while (p + 8 <= u8.length) {
      const len = dv.getUint32(p);
      const type = String.fromCharCode(u8[p + 4], u8[p + 5], u8[p + 6], u8[p + 7]);
      if (p + 12 + len > u8.length) break;
      if (type === "IDAT") { parts.push(u8.subarray(p + 8, p + 8 + len)); total += len; }
      if (type === "IEND") break;
      p += 12 + len;
    }
    const z = new Uint8Array(total);
    let o = 0;
    for (const part of parts) { z.set(part, o); o += part.length; }
    const raw = await inflate(z);
    const ch = CHANNELS[hd.colorType], bpp = ch * 2, w = hd.width, h = hd.height, stride = w * bpp;
    if (raw.length < h * (stride + 1)) throw new Error("This PNG file is truncated or damaged.");
    const cur = new Uint8Array(stride), prev = new Uint8Array(stride);
    const out = new Uint16Array(w * h * ch);
    for (let y = 0; y < h; y++) {
      const off = y * (stride + 1), ft = raw[off];
      const line = raw.subarray(off + 1, off + 1 + stride);
      if (ft === 0) cur.set(line);
      else if (ft === 1) { for (let i = 0; i < stride; i++) cur[i] = (line[i] + (i >= bpp ? cur[i - bpp] : 0)) & 255; }
      else if (ft === 2) { for (let i = 0; i < stride; i++) cur[i] = (line[i] + prev[i]) & 255; }
      else if (ft === 3) { for (let i = 0; i < stride; i++) cur[i] = (line[i] + (((i >= bpp ? cur[i - bpp] : 0) + prev[i]) >> 1)) & 255; }
      else if (ft === 4) {
        for (let i = 0; i < stride; i++) {
          const a = i >= bpp ? cur[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
          const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c);
          cur[i] = (line[i] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 255;
        }
      } else throw new Error("This PNG file is damaged (bad filter type).");
      const base = y * w * ch;
      for (let i = 0, k = 0; i < stride; i += 2, k++) out[base + k] = (cur[i] << 8) | cur[i + 1];
      prev.set(cur);
    }
    return { width: w, height: h, channels: ch, colorType: hd.colorType, data: out };
  }

  // ---------------------------------------------------------------- 16 -> 8 bit mappings
  // numpy.percentile(a, q) with the default "linear" method, taken from integer samples via a
  // histogram (exact order statistics), then mapped through f(v) = v * slope + intercept.
  function orderStats(vals) {
    let vmin = Infinity, vmax = -Infinity;
    for (let i = 0; i < vals.length; i++) { const v = vals[i]; if (v < vmin) vmin = v; if (v > vmax) vmax = v; }
    const range = vmax - vmin + 1;
    if (range <= (1 << 24)) {
      const cum = new Float64Array(range);
      for (let i = 0; i < vals.length; i++) cum[vals[i] - vmin]++;
      for (let i = 1; i < range; i++) cum[i] += cum[i - 1];
      const kth = (k) => {                       // smallest v with (#values <= v) > k
        let lo = 0, hi = range - 1;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (cum[mid] > k) hi = mid; else lo = mid + 1; }
        return vmin + lo;
      };
      return { vmin, vmax, kth };
    }
    const s = Float64Array.from(vals).sort();
    return { vmin, vmax, kth: (k) => s[k] };
  }

  function lerp(a, b, t) {                       // numpy _lerp (keeps the exact float rounding)
    const d = b - a;
    return t >= 0.5 ? b - d * (1 - t) : a + d * t;
  }

  function percentile(st, n, pct, f, reversed) {
    const q = pct / 100;
    const vi = (n - 1) * q;
    const at = (k) => f(st.kth(reversed ? n - 1 - k : k));
    if (vi >= n - 1) return at(n - 1);
    const prev = Math.floor(vi);
    return lerp(at(prev), at(prev + 1), vi - prev);
  }

  /**
   * dentdeid.imaging.raster.display8 / dentdeid.dicom.pixels.to_display8 for integer samples:
   * values v are first mapped to f = v * slope + intercept (float64), then
   * lo/hi = 0.5/99.9 percentiles; top = max; if top - hi < 0.5 (hi - lo): hi = max(hi, top);
   * if hi <= lo: hi = lo + 1; out = uint8(clip((f - lo) * 255 / (hi - lo) + 0.5, 0, 255)).
   */
  function display8(vals, slope, intercept) {
    slope = slope == null ? 1 : slope;
    intercept = intercept == null ? 0 : intercept;
    const n = vals.length;
    const out = new Uint8Array(n);
    if (!n) return out;
    const f = (v) => v * slope + intercept;
    const st = orderStats(vals);
    const reversed = slope < 0;
    const lo = percentile(st, n, 0.5, f, reversed);
    let hi = percentile(st, n, 99.9, f, reversed);
    const top = Math.max(f(st.vmin), f(st.vmax));
    if (top - hi < 0.5 * (hi - lo)) hi = Math.max(hi, top);
    if (hi <= lo) hi = lo + 1;
    const map = (v) => {
      let x = ((f(v) - lo) * 255) / (hi - lo) + 0.5;
      x = x < 0 ? 0 : x > 255 ? 255 : x;
      return Math.floor(x);
    };
    const range = st.vmax - st.vmin + 1;
    if (range <= (1 << 20)) {
      const lut = new Uint8Array(range);
      for (let v = 0; v < range; v++) lut[v] = map(st.vmin + v);
      for (let i = 0; i < n; i++) out[i] = lut[vals[i] - st.vmin];
    } else {
      for (let i = 0; i < n; i++) out[i] = map(vals[i]);
    }
    return out;
  }

  /** raster.linear8: (v - min) * 255 // (max - min), or null when the range fits in 8 bits. */
  function linear8(vals) {
    let vmin = Infinity, vmax = -Infinity;
    for (let i = 0; i < vals.length; i++) { const v = vals[i]; if (v < vmin) vmin = v; if (v > vmax) vmax = v; }
    const top = vmax - vmin;
    if (!(top > 255)) return null;
    const out = new Uint8Array(vals.length);
    for (let i = 0; i < vals.length; i++) out[i] = Math.floor(((vals[i] - vmin) * 255) / top);
    return out;
  }

  /**
   * A decoded 16-bit PNG -> what the app needs: RGBA pixels to show (display8), and the
   * detection views ([display view, linear view]) as {width, height, channels, data}.
   * Grey (+alpha) images become one grey channel (alpha dropped, like raster.load's
   * convert("L")); RGB(A) is mapped over its colour samples, alpha composited on black.
   */
  function toImage(dec) {
    const { width: w, height: h, channels: ch } = dec;
    const n = w * h;
    const grey = ch <= 2;
    const cc = grey ? 1 : 3;
    const colour = new Uint16Array(n * cc);
    for (let i = 0; i < n; i++) for (let c = 0; c < cc; c++) colour[i * cc + c] = dec.data[i * ch + c];
    const disp = display8(colour);
    const lin = linear8(colour);
    const rgba = new Uint8ClampedArray(n * 4);
    const hasAlpha = ch === 4;
    for (let i = 0; i < n; i++) {
      const a = hasAlpha ? dec.data[i * 4 + 3] : 65535;
      for (let c = 0; c < 3; c++) {
        const v = disp[i * cc + (grey ? 0 : c)];
        rgba[i * 4 + c] = hasAlpha ? Math.round((v * a) / 65535) : v;
      }
      rgba[i * 4 + 3] = 255;
    }
    const views = [hasAlpha ? { width: w, height: h, channels: 4, data: rgba } : { width: w, height: h, channels: cc, data: disp }];
    if (lin) views.push({ width: w, height: h, channels: cc, data: lin });
    return { width: w, height: h, rgba, views };
  }

  return { readHeader, decode, display8, linear8, toImage, isPng };
});
