/* DentDeID web app — everything runs locally in this tab. MIT licence. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "checked") n.checked = !!v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids) if (k != null) n.append(k.nodeType ? k : document.createTextNode(k));
    return n;
  };
  const SVGNS = "http://www.w3.org/2000/svg";
  const PYTHON = "use the Python tool (pip install dentdeid)";
  const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const scrollBehavior = () => (reduceMotion() ? "auto" : "smooth");
  const userError = (msg) => { const e = new Error(msg); e.userFacing = true; return e; };

  // S holds the one image being worked on. A new file is decoded into a separate object and
  // only committed here when it opened successfully (a failed load never touches S).
  const S = {
    doc: 0, name: "", source: "", kind: null, w: 0, h: 0, base: null, views: null,
    regions: [], meta: [], metaNotes: [], history: [], future: [], view: "review", mode: null, nextId: 1,
    scanned: false, scanning: false,
  };
  const isBoxType = (r) => r.type === "manual" || r.type === "face" || r.type === "keep";
  const isRedaction = (r) => r.type === "text" || r.type === "manual";

  // ---------------------------------------------------------------- toast / helpers
  let toastTimer, toastAction = null;
  function toast(msg, opts = {}) {
    const t = $("#toast"), act = $("#toast-act");
    $("#toast-msg").textContent = msg;
    t.classList.toggle("error", !!opts.error);
    toastAction = opts.onAction || null;
    act.hidden = !toastAction;
    act.textContent = toastAction ? opts.action || "Undo" : "";
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.classList.remove("show"); toastAction = null; },
      opts.error ? 7000 : toastAction ? 5000 : 2600);
  }
  $("#toast-act").addEventListener("click", () => {
    const f = toastAction;
    toastAction = null;
    $("#toast").classList.remove("show");
    if (f) f();
  });
  const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  function where(r) {
    const cx = (r.x0 + r.x1) / 2 / S.w, cy = (r.y0 + r.y1) / 2 / S.h;
    const v = cy < 0.33 ? "top" : cy > 0.67 ? "bottom" : "middle";
    const h = cx < 0.33 ? "left" : cx > 0.67 ? "right" : "centre";
    return v === "middle" && h === "centre" ? "centre" : `${v}-${h}`.replace("middle-", "");
  }

  // ---------------------------------------------------------------- metadata inspection (names only)
  const EXIF_NAMES = {
    0x010e: ["Image description", true], 0x010f: ["Camera make", false], 0x0110: ["Camera model", false],
    0x0131: ["Software", false], 0x0132: ["Date & time", true], 0x013b: ["Artist / author", true],
    0x8298: ["Copyright", true], 0x9003: ["Date taken", true], 0x9004: ["Date digitised", true],
    0x927c: ["Maker note", true], 0x9286: ["User comment", true], 0xa431: ["Camera serial number", true],
    0xa435: ["Lens serial number", true], 0xa430: ["Camera owner", true], 0xa420: ["Image unique ID", true],
    0x9c9b: ["Windows title", true], 0x9c9c: ["Windows comment", true], 0x9c9d: ["Windows author", true],
    0x9c9f: ["Windows subject", true], 0x0112: ["Orientation (applied, then dropped)", false],
    0x010d: ["Document name", true], 0x013c: ["Host computer", true], 0x02bc: ["XMP metadata packet", true],
    0x83bb: ["IPTC data", true], 0x8773: ["Colour profile (ICC)", false],
  };

  function parseTiff(u8, off, items) {
    if (off + 8 > u8.length) return;
    const le = u8[off] === 0x49;
    const dv = new DataView(u8.buffer, u8.byteOffset);
    const u16 = (p) => dv.getUint16(off + p, le), u32 = (p) => dv.getUint32(off + p, le);
    const seen = new Set();
    const ifd = (p) => {
      if (!p || p + 2 > u8.length - off || seen.has(p)) return;
      seen.add(p);
      const n = u16(p);
      for (let i = 0; i < n && p + 2 + i * 12 + 12 <= u8.length - off; i++) {
        const e = p + 2 + i * 12;
        const tag = u16(e);
        if (tag === 0x8769) ifd(u32(e + 8));
        else if (tag === 0x8825) { if (u32(e + 8)) items.push({ label: "GPS location", warn: true, gps: true }); }
        else if (EXIF_NAMES[tag]) items.push({ label: `EXIF · ${EXIF_NAMES[tag][0]}`, warn: EXIF_NAMES[tag][1] });
      }
    };
    ifd(u32(4));
  }

  function inspectImageBytes(u8, fmt) {
    const items = [];
    const str = (a, n) => String.fromCharCode(...u8.subarray(a, a + n));
    try {
      if (fmt === "jpeg") {
        let p = 2;
        while (p + 4 <= u8.length && u8[p] === 0xff) {
          const m = u8[p + 1];
          if (m === 0xd9 || m === 0xda) break;
          const len = (u8[p + 2] << 8) | u8[p + 3];
          const seg = p + 4;
          if (m === 0xe1 && str(seg, 6) === "Exif\0\0") parseTiff(u8, seg + 6, items);
          else if (m === 0xe1 && str(seg, 28).startsWith("http://ns.adobe.com/xap")) items.push({ label: "XMP metadata packet", warn: true });
          else if (m === 0xe2 && str(seg, 11) === "ICC_PROFILE") items.push({ label: "Colour profile (ICC)", warn: false });
          else if (m === 0xed) items.push({ label: "IPTC / Photoshop data", warn: true });
          else if (m === 0xfe) items.push({ label: "JPEG comment", warn: true });
          p += 2 + len;
        }
      } else if (fmt === "png") {
        let p = 8;
        const dv = new DataView(u8.buffer, u8.byteOffset);
        while (p + 8 <= u8.length) {
          const len = dv.getUint32(p), type = str(p + 4, 4);
          const d = p + 8;
          if (type === "tEXt" || type === "zTXt" || type === "iTXt") {
            let k = d; while (k < d + len && u8[k] !== 0) k++;
            items.push({ label: `PNG text · ${str(d, Math.min(40, k - d))}`, warn: true });
          } else if (type === "eXIf") parseTiff(u8, d, items);
          else if (type === "iCCP") items.push({ label: "Colour profile (ICC)", warn: false });
          else if (type === "tIME") items.push({ label: "PNG timestamp", warn: true });
          if (type === "IEND") break;
          p = d + len + 4;
        }
      } else if (fmt === "webp") {
        let p = 12;
        const dv = new DataView(u8.buffer, u8.byteOffset);
        while (p + 8 <= u8.length) {
          const type = str(p, 4), len = dv.getUint32(p + 4, true);
          if (type === "EXIF") parseTiff(u8, str(p + 8, 6) === "Exif\0\0" ? p + 14 : p + 8, items);
          else if (type === "XMP ") items.push({ label: "XMP metadata packet", warn: true });
          else if (type === "ICCP") items.push({ label: "Colour profile (ICC)", warn: false });
          p += 8 + len + (len & 1);
        }
      } else if (fmt === "gif") {                                           // walk the GIF blocks
        let p = 13;
        if (u8[10] & 0x80) p += 3 << ((u8[10] & 7) + 1);                  // global colour table
        const subBlocks = (q) => { while (q < u8.length && u8[q]) q += u8[q] + 1; return q + 1; };
        let comment = false, xmp = false;
        while (p < u8.length) {
          const b = u8[p];
          if (b === 0x21) {                                                 // extension
            const label = u8[p + 1];
            if (label === 0xfe) comment = true;
            if (label === 0xff && str(p + 3, 11) === "XMP DataXMP") xmp = true;
            p = subBlocks(p + 2);
          } else if (b === 0x2c) {                                          // image: descriptor, colour table, LZW data
            let q = p + 10;
            if (u8[p + 9] & 0x80) q += 3 << ((u8[p + 9] & 7) + 1);
            p = subBlocks(q + 1);
          } else break;                                                     // 0x3B trailer (or damage)
        }
        if (comment) items.push({ label: "GIF comment", warn: true });
        if (xmp) items.push({ label: "XMP metadata packet", warn: true });
      } else if (fmt === "tiff") {
        parseTiff(u8, 0, items);
      }
    } catch { /* a malformed metadata block is reported as nothing found; it is never copied anyway */ }
    return items;
  }

  function filenameLooksIdentifying(name) {
    const stem = name.replace(/\.[^.]+$/, "");
    return /\d{4}[-_. ]\d{1,2}[-_. ]\d{1,2}|\d{1,2}[-_. ]\d{1,2}[-_. ]\d{4}|(?:19|20)\d{6}|\d{6,}/.test(stem) ||
      /[A-ZÀ-ÞÇĞİÖŞÜ][a-zß-ÿçğıöşü]{2,}[ _-]+[A-ZÀ-ÞÇĞİÖŞÜ][a-zß-ÿçğıöşü]{2,}/.test(stem);
  }

  // ---------------------------------------------------------------- format sniffing & messages
  function sniff(u8, name) {
    const s = (a, n) => String.fromCharCode(...u8.subarray(a, a + n));
    if (u8.length >= 3 && u8[0] === 0xff && u8[1] === 0xd8 && u8[2] === 0xff) return "jpeg";
    if (DentDeIDPng16.isPng(u8)) return "png";
    if (s(0, 4) === "GIF8") return "gif";
    if (s(0, 4) === "RIFF" && s(8, 4) === "WEBP") return "webp";
    if (s(0, 2) === "BM") return "bmp";
    if (s(0, 4) === "II*\0" || s(0, 4) === "MM\0*" || s(0, 4) === "II+\0" || s(0, 4) === "MM\0+") return "tiff";
    if (s(4, 4) === "ftyp") {
      const brands = s(8, 4) + " " + s(16, Math.min(32, Math.max(0, u8.length - 16)));
      if (/avi[fs]/.test(brands)) return "avif";
      if (/hei[cxms]|hev[cxms]|mif1|msf1|heif/.test(brands)) return "heic";
    }
    if (u8.length >= 4 && u8[0] === 0 && u8[1] === 0 && u8[2] === 1 && u8[3] === 0) return "ico";
    if (DicomLite.looksLikeDicom(u8) || /\.(dcm|dicom)$/i.test(name)) return "dicom";
    if (s(0, 5) === "%PDF-") return "pdf";
    return "unknown";
  }
  const FORMAT_NAME = { jpeg: "JPEG", png: "PNG", gif: "GIF", webp: "WebP", bmp: "BMP", tiff: "TIFF", heic: "HEIC/HEIF",
                        avif: "AVIF", ico: "ICO", dicom: "DICOM", pdf: "PDF" };
  const EXT_FORMAT = { jpg: "jpeg", jpeg: "jpeg", jfif: "jpeg", png: "png", gif: "gif", webp: "webp", bmp: "bmp", tif: "tiff",
                       tiff: "tiff", heic: "heic", heif: "heic", avif: "avif", dcm: "dicom", dicom: "dicom" };
  function formatError(fmt, name) {
    const f = FORMAT_NAME[fmt];
    const ext = ((name || "").match(/\.([a-z0-9]+)$/i) || [])[1];
    const claimed = ext && EXT_FORMAT[ext.toLowerCase()];
    if (fmt === "unknown" && claimed) {
      return userError(`This file is named .${ext.toLowerCase()} but doesn't contain a readable ${FORMAT_NAME[claimed]} image — it may be damaged or incomplete. Export it again as PNG or JPEG, or ${PYTHON}.`);
    }
    if (fmt === "tiff") return userError(`TIFF images can't be opened in this browser. Export the image as PNG or JPEG, or ${PYTHON}, which reads TIFF.`);
    if (fmt === "heic") return userError("HEIC/HEIF photos can't be opened in this browser. Export the photo as JPEG or PNG and open that file (on iPhone: Settings › Camera › Formats › Most Compatible).");
    if (fmt === "avif") return userError("This browser can't decode AVIF images. Export the image as PNG or JPEG and open that file.");
    if (fmt === "pdf") return userError("This is a PDF document, not an image. Export the page as PNG or JPEG first.");
    if (f) return userError(`This ${f} file is damaged or incomplete and can't be decoded. Export it again as PNG or JPEG, or ${PYTHON}.`);
    return userError(`This file isn't an image the browser can open (JPEG, PNG, WebP, BMP, GIF or DICOM). Export it as PNG or JPEG, or ${PYTHON}.`);
  }

  // ---------------------------------------------------------------- decoding (never touches S)
  function newCanvas(w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    return c;
  }

  async function decodeFile(blob, name) {
    const buf = await blob.arrayBuffer();
    const u8 = new Uint8Array(buf);
    if (!u8.length) throw userError("This file is empty (0 bytes). Export the image again as PNG or JPEG.");
    const fmt = sniff(u8, name);
    const doc = { name, source: FORMAT_NAME[fmt] || "Image", w: 0, h: 0, rgba: null, views: null, meta: [], metaNotes: [] };
    if (fmt === "dicom") {
      let d;
      try { d = DicomLite.parse(buf); } catch { throw userError(`This DICOM file is damaged and can't be read. ${PYTHON[0].toUpperCase() + PYTHON.slice(1)}.`); }
      let img;
      try { img = await DicomLite.toImage(d); }
      catch (e) { if (e && e.userFacing) throw e; throw userError(`This DICOM file is damaged or incomplete and can't be displayed. ${PYTHON[0].toUpperCase() + PYTHON.slice(1)}.`); }
      doc.source = `DICOM${d.modality ? " " + d.modality : ""}`;
      doc.w = img.width; doc.h = img.height; doc.rgba = img.rgba; doc.views = img.views;
      doc.meta = DicomLite.identifyingPresent(d).map((label) => ({ label: `DICOM · ${label}`, warn: true }));
      doc.metaNotes.push("The download is an image file. The DICOM header, private tags and overlays do not travel with it.");
      if (d.frames > 1) doc.metaNotes.push(`Only the first of ${d.frames} frames is shown and exported. Use the Python tool for volumes.`);
      if (d.identityRemoved) doc.metaNotes.push("This file is already marked as de-identified (Patient Identity Removed = YES).");
    } else {
      const hd = fmt === "png" ? DentDeIDPng16.readHeader(u8) : null;
      if (hd && hd.bitDepth === 16) {
        // browsers drop the low byte of 16-bit PNGs: a 12-bit export would look black and its text be missed
        let dec;
        try { dec = await DentDeIDPng16.decode(u8); }
        catch (e) {
          if (e && e.userFacing) throw e;
          if (e && e.message === "no-decompression-stream") throw userError(`This 16-bit PNG needs a newer browser to be read at full depth. Update the browser, or ${PYTHON}.`);
          throw formatError("png", name);
        }
        const im = DentDeIDPng16.toImage(dec);
        doc.w = im.width; doc.h = im.height; doc.rgba = im.rgba; doc.views = im.views;
        doc.source = "PNG 16-bit";
        doc.metaNotes.push("16-bit image: shown and exported as 8-bit, contrast-stretched like the Python tool.");
      } else {
        let bmp;
        try { bmp = await createImageBitmap(blob, { imageOrientation: "from-image" }); }
        catch {
          try { bmp = await createImageBitmap(blob); } catch { throw formatError(fmt, name); }
        }
        const w = bmp.width, h = bmp.height;
        if (!w || !h) throw formatError(fmt, name);
        let data;
        try {
          const ctx = newCanvas(w, h).getContext("2d", { willReadFrequently: true });
          ctx.drawImage(bmp, 0, 0);
          data = ctx.getImageData(0, 0, w, h).data;
        } catch {
          throw userError(`This image (${w}×${h}) is too large for the browser to process. Scale it down, or ${PYTHON}.`);
        } finally { if (bmp.close) bmp.close(); }
        doc.w = w; doc.h = h; doc.rgba = data;
        doc.views = [{ width: w, height: h, channels: 4, data }];
      }
      doc.meta = inspectImageBytes(u8, fmt);
      if (fmt === "heic" || fmt === "avif") doc.metaNotes.push(`This page can't list the metadata inside ${FORMAT_NAME[fmt]} files; none of it is copied into the download.`);
      if (fmt === "tiff") doc.metaNotes.push("Only the first page of a TIFF is shown and exported.");
    }
    if (filenameLooksIdentifying(name)) doc.meta.unshift({ label: "File name looks like it contains a name, date or ID", warn: true, fname: true });
    return doc;
  }

  // ---------------------------------------------------------------- loading
  let loadSeq = 0;
  async function loadBlob(blob, name) {
    const seq = ++loadSeq;
    let doc;
    try {
      doc = await decodeFile(blob, name || "image");
    } catch (err) {
      if (seq !== loadSeq) return;
      toast(err && err.userFacing ? err.message
        : `This file could not be opened. Export it as PNG or JPEG, or ${PYTHON}.`, { error: true });
      if (S.base) {            // keep the image that is open, exactly as it was — and ask for a fresh check
        resetConfirm();
        renderAll();
      }
      return;
    }
    if (seq !== loadSeq) return;           // a newer file was chosen meanwhile
    commit(doc);
  }

  function commit(doc) {
    cancelScan();
    S.doc++;
    S.name = doc.name; S.source = doc.source; S.w = doc.w; S.h = doc.h;
    S.base = new ImageData(new Uint8ClampedArray(doc.rgba.buffer, doc.rgba.byteOffset, doc.w * doc.h * 4), doc.w, doc.h);
    S.views = doc.views;
    S.regions = []; S.history = []; S.future = []; S.meta = doc.meta; S.metaNotes = doc.metaNotes;
    S.nextId = 1; S.mode = null; S.scanned = false; S.kind = null;
    $("#f-auto").hidden = true;
    showWork();
    scan();
  }

  // ---------------------------------------------------------------- text detection (Web Worker)
  let worker = null, workerFailed = false, jobSeq = 0, scanSeq = 0;
  const jobs = new Map();
  function getWorker() {
    if (worker || workerFailed || typeof Worker === "undefined") return worker;
    try {
      worker = new Worker("detector-worker.js");
      worker.onmessage = (e) => {
        const m = e.data || {}, job = jobs.get(m.id);
        if (!job) return;
        if (m.progress != null) { job.onProgress(m.progress); return; }
        jobs.delete(m.id);
        if (m.error) job.reject(new Error(m.error)); else job.resolve(m.result);
      };
      worker.onerror = (e) => {           // e.g. opened from file:// — fall back to this thread
        if (e && e.preventDefault) e.preventDefault();
        workerFailed = true;
        const pending = [...jobs.values()];
        jobs.clear();
        try { worker.terminate(); } catch { /* ignore */ }
        worker = null;
        for (const job of pending) runSync(job.views, job.onProgress).then(job.resolve, job.reject);
      };
    } catch { workerFailed = true; worker = null; }
    return worker;
  }
  async function runSync(views, onProgress) {
    await nextFrame();
    return DentDeIDDetector.scanViews(views, null, { onProgress });
  }
  function detect(views, onProgress) {
    const w = getWorker();
    if (!w) return runSync(views, onProgress);
    return new Promise((resolve, reject) => {
      const id = ++jobSeq;
      jobs.set(id, { resolve, reject, onProgress, views });
      const copies = views.map((v) => ({ width: v.width, height: v.height, channels: v.channels, data: new Uint8Array(v.data) }));
      w.postMessage({ id, views: copies }, copies.map((v) => v.data.buffer));
    });
  }
  function cancelScan() {
    scanSeq++;
    if (S.scanning && worker) {            // a new image replaces this one: stop the old scan now
      worker.terminate();
      worker = null;
      jobs.clear();
    }
    S.scanning = false;
    setBusy(false);
  }
  function setBusy(on, frac) {
    $("#scanning").classList.toggle("show", on);
    $("#work").setAttribute("aria-busy", on ? "true" : "false");
    const pct = Math.round((frac || 0) * 100);
    $("#scan-msg").textContent = on ? `Looking for burned-in text… ${pct}%` : "";
    $("#scan-bar").style.width = `${pct}%`;
    for (const id of ["#t-add", "#f-hide", "#f-keep", "#checked"]) $(id).disabled = on;
    if (on) { $("#undo").disabled = true; $("#redo").disabled = true; $("#t-scan").hidden = true; }
  }

  async function scan() {
    const seq = ++scanSeq, doc = S.doc;
    S.scanning = true;
    resetConfirm();
    setBusy(true, 0);
    renderLists();
    const t0 = performance.now();
    let res;
    try {
      res = await detect(S.views, (f) => { if (seq === scanSeq) setBusy(true, f); });
    } catch {
      if (seq !== scanSeq || doc !== S.doc) return;
      S.scanning = false; setBusy(false);
      S.kind = S.kind || "radiograph";
      updateFileChip(); renderAll();
      toast("Automatic text detection failed — please check the image yourself and add boxes", { error: true });
      return;
    }
    if (seq !== scanSeq || doc !== S.doc) return;          // cancelled, or another image is open now
    S.scanning = false;
    setBusy(false);
    S.kind = res.imageClass;
    const photo = S.kind === "photo";
    S.regions = S.regions.filter((r) => r.type !== "text");
    for (const r of res.regions) {
      S.regions.push({ id: S.nextId++, type: "text", on: !photo, suggest: photo, x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1,
                       conf: r.confidence, source: r.source, kind: r.kind });
    }
    S.scanned = true;
    updateFileChip();
    renderAll();
    if (photo && "FaceDetector" in window) $("#f-auto").hidden = false;
    const ms = Math.round(performance.now() - t0), n = res.regions.length;
    if (photo) toast(n ? `Colour photo — ${n} possible text area${n > 1 ? "s" : ""} suggested, not blacked out` : "Colour photo — nothing text-like found");
    else toast(n ? `Found ${n} text area${n > 1 ? "s" : ""} in ${ms} ms` : "No burned-in text found — please still check by eye");
  }

  function kindLabel() {
    return S.kind === "photo" ? "Colour photo" : S.kind === "screenshot" ? "Screenshot" : S.kind === "radiograph" ? "Radiograph" : "Checking…";
  }
  function updateFileChip() {
    const fc = $("#filechip");
    fc.textContent = "";
    fc.append(el("b", {}, kindLabel()), ` · ${S.source} · ${S.w}×${S.h}`);
    fc.title = `${kindLabel()} · ${S.source} · ${S.w}×${S.h}`;
    $("#frame").setAttribute("aria-label", `Image: ${kindLabel().toLowerCase()}, ${S.w} by ${S.h} pixels`);
  }

  // ---------------------------------------------------------------- layout
  function layout() {
    const root = document.documentElement;
    const barH = $(".bar").getBoundingClientRect().height;
    root.style.setProperty("--bar-h", `${barH}px`);
    const tbH = $(".toolbar").getBoundingClientRect().height;
    const stageTop = Math.round(barH + 3 + tbH + 14);
    root.style.setProperty("--stage-top", `${stageTop}px`);
    // the sticky bar (and, in the workspace, the sticky toolbar) must never cover a focused element
    root.style.scrollPaddingTop = `${S.base ? stageTop + 4 : barH + 12}px`;
  }
  function fitFrame(w = S.w, h = S.h) {
    layout();
    if (!w || !h) return;
    const wide = matchMedia("(min-width: 961px)").matches;
    const stageTop = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--stage-top")) || 124;
    const gridTop = $(".grid").getBoundingClientRect().top + window.scrollY;     // where it starts, unscrolled
    // desktop: the stage is sticky, so the whole image must fit below the toolbar (also before scrolling)
    const maxH = wide ? Math.max(220, innerHeight - Math.max(stageTop, S.base ? gridTop : 0) - 28 - 16) : Math.max(260, innerHeight * 0.72);
    $("#frame").style.maxWidth = `${Math.round((maxH * w) / h)}px`;
  }
  window.addEventListener("resize", () => fitFrame(...(S.view === "result" ? outputSize() : [S.w, S.h])));

  function showWork() {
    $("#start").hidden = true;
    $("#how").hidden = true;
    const w = $("#work");
    w.classList.remove("show"); void w.offsetWidth; w.classList.add("show");
    S.view = "review"; S.mode = null;
    syncViewButtons(); syncModeButtons();
    resetConfirm();
    $("#frame").style.aspectRatio = `${S.w} / ${S.h}`;
    updateFileChip();
    fitFrame();
    renderAll();
    window.scrollTo({ top: 0, behavior: "auto" });
    $("#frame").focus({ preventScroll: true });           // keyboard & screen-reader users land in the workspace
  }

  // ---------------------------------------------------------------- state changes
  const snapState = () => JSON.stringify(S.regions);
  function snapshot() {
    S.history.push(snapState());
    if (S.history.length > 80) S.history.shift();
    S.future = [];
  }
  function resetConfirm() {
    $("#checked").checked = false;
    $("#dl").disabled = true;
    $("#done").classList.remove("show");
  }
  // every change to what will be removed asks for a fresh look before downloading
  function changed() {
    resetConfirm();
    renderAll();
  }
  function undo() {
    if (!S.history.length || S.scanning) return;
    S.future.push(snapState());
    S.regions = JSON.parse(S.history.pop());
    changed();
    toast("Undone");
  }
  function redo() {
    if (!S.future.length || S.scanning) return;
    S.history.push(snapState());
    S.regions = JSON.parse(S.future.pop());
    changed();
    toast("Redone");
  }
  const findRegion = (id) => S.regions.find((x) => x.id === Number(id));
  function toggleRegion(id) {
    const r = findRegion(id);
    if (!r || !isRedaction(r) || S.scanning) return;
    snapshot();
    r.on = !r.on;
    changed();
    toast(r.on ? "Area will be blacked out" : "Area will be kept", { action: "Undo", onAction: undo });
  }
  function removeRegion(id) {
    if (S.scanning) return;
    snapshot();
    S.regions = S.regions.filter((x) => x.id !== Number(id));
    changed();
    toast("Box removed", { action: "Undo", onAction: undo });
  }
  function blackOutSuggestions() {
    const sug = S.regions.filter((r) => r.suggest && !r.on);
    if (!sug.length) return;
    snapshot();
    for (const r of sug) r.on = true;
    changed();
    toast(`${sug.length} suggested area${sug.length > 1 ? "s" : ""} will be blacked out`, { action: "Undo", onAction: undo });
  }

  // ---------------------------------------------------------------- rendering
  function boxLabel(r, n) {
    if (r.type === "manual") return `Your box ${n}, ${where(r)}, ${r.on ? "will be blacked out" : "kept"}`;
    if (r.type === "face") return `Face blur box, ${where(r)}`;
    if (r.type === "keep") return "Keep-only area (crop)";
    return "";
  }
  function renderOverlay() {
    const ov = $("#ov");
    const active = document.activeElement;
    const focusId = active && ov.contains(active) && active.dataset ? active.dataset.id : null;
    ov.setAttribute("viewBox", `0 0 ${S.w} ${S.h}`);
    ov.textContent = "";
    if (S.view !== "review") { if (focusId) $("#frame").focus({ preventScroll: true }); return; }
    const keep = S.regions.filter((r) => r.type === "keep").pop();
    if (keep) {
      const p = document.createElementNS(SVGNS, "path");
      p.setAttribute("class", "dim");
      p.setAttribute("fill-rule", "evenodd");
      p.setAttribute("d", `M0 0H${S.w}V${S.h}H0Z M${keep.x0} ${keep.y0}V${keep.y1}H${keep.x1}V${keep.y0}Z`);
      ov.append(p);
    }
    let nManual = 0;
    for (const r of S.regions) {
      const rect = document.createElementNS(SVGNS, "rect");
      rect.setAttribute("x", r.x0); rect.setAttribute("y", r.y0);
      rect.setAttribute("width", Math.max(1, r.x1 - r.x0)); rect.setAttribute("height", Math.max(1, r.y1 - r.y0));
      rect.setAttribute("rx", 3);
      rect.setAttribute("class", `r ${r.type}${r.suggest ? " suggest" : ""} ${r.on ? "on" : "off"}`);
      rect.dataset.id = r.id;
      rect.style.cursor = isRedaction(r) ? "pointer" : "default";
      if (isBoxType(r)) {                  // boxes you placed can be moved/resized/removed from the keyboard
        if (r.type === "manual") nManual++;
        rect.setAttribute("tabindex", "0");
        rect.setAttribute("role", "button");
        rect.setAttribute("aria-label", `${boxLabel(r, nManual)}. Arrow keys move, Shift with arrow keys resizes, Delete removes.`);
      }
      rect.addEventListener("pointerenter", () => hot(r.id, true));
      rect.addEventListener("pointerleave", () => hot(r.id, false));
      ov.append(rect);
    }
    if (focusId) {
      const n = ov.querySelector(`[data-id="${focusId}"]`);
      (n || $("#frame")).focus({ preventScroll: true });
    }
  }
  function hot(id, on) {
    document.querySelectorAll(`[data-id="${id}"]`).forEach((n) => n.classList.toggle("hot", on));
  }

  function renderLists() {
    const text = S.regions.filter(isRedaction);
    const lt = $("#l-text");
    lt.textContent = "";
    let n = 0;
    text.forEach((r) => {
      n++;
      const what = r.type === "manual" ? "Your box" : r.suggest ? "Suggestion" : r.kind === "scale" ? "Scale / ruler"
        : r.source === "color" ? "Colour label" : "Text";
      const label = `${n}. ${what} · ${where(r)}`;
      const li = el("li", { "data-id": r.id, class: r.suggest && !r.on ? "suggest" : "",
                            onpointerenter: () => hot(r.id, true), onpointerleave: () => hot(r.id, false) },
        el("label", { class: "row" },
          el("input", { type: "checkbox", checked: r.on, disabled: S.scanning, "aria-label": `Black out ${label}`,
                        onchange: () => toggleRegion(r.id) }),
          el("span", {}, label)),
        r.type === "manual" ? el("button", { class: "x", type: "button", title: "Delete box", "aria-label": `Delete box ${n}`, onclick: () => removeRegion(r.id) }, "×")
          : el("span", { class: "sub" }, r.suggest && !r.on ? "not blacked out" : r.conf === "low" ? "low confidence" : r.conf));
      lt.append(li);
    });
    $("#n-text").textContent = String(n);
    const on = text.filter((r) => r.on).length;
    const sugg = text.filter((r) => r.suggest).length;
    let status;
    if (S.scanning) status = "Looking for burned-in text…";
    else if (S.kind === "photo") {
      status = sugg
        ? `Colour photo: ${sugg} text-like area${sugg > 1 ? "s were" : " was"} found and ${sugg > 1 ? "are" : "is"} only suggested (blue, dashed) — photos are not blacked out automatically, because natural texture like teeth, gums or fabric often looks like text. Tick the real ones, black out all, or add boxes yourself.`
        : "Colour photo: nothing text-like was found. Photos are not blacked out automatically (natural texture often looks like text) — check the image and add boxes yourself if needed.";
      if (on) status += ` ${on} of ${n} area${n > 1 ? "s" : ""} will be blacked out.`;
    } else if (!text.length) status = "No burned-in text was found automatically. Faint text over teeth or bone can be missed — check the image yourself.";
    else status = `${on} of ${n} area${n > 1 ? "s" : ""} will be blacked out in the download.`;
    $("#text-status").textContent = status;
    const pending = text.filter((r) => r.suggest && !r.on).length;
    $("#t-scan").hidden = S.scanning || !pending;
    $("#t-scan").textContent = pending > 1 ? `Black out all ${pending} suggestions` : "Black out the suggestion";

    const lm = $("#l-meta");
    lm.textContent = "";
    for (const m of S.meta) lm.append(el("li", { class: m.warn ? "warn" : "" }, el("span", {}, m.label)));
    $("#n-meta").textContent = String(S.meta.length);
    const metaCount = S.meta.filter((m) => !m.fname).length;
    $("#meta-status").textContent = (metaCount
      ? `${metaCount} item${metaCount > 1 ? "s" : ""} found. None of it is copied into the download: it is a new file with the pixels only.`
      : "No embedded metadata found. The download is a new file with the pixels only.") +
      (S.metaNotes.length ? " " + S.metaNotes.join(" ") : "") +
      (S.meta.some((m) => m.fname) ? " The download gets a neutral file name." : "");

    const lf = $("#l-face");
    lf.textContent = "";
    for (const r of S.regions.filter((x) => x.type === "face" || x.type === "keep")) {
      lf.append(el("li", { "data-id": r.id, onpointerenter: () => hot(r.id, true), onpointerleave: () => hot(r.id, false) },
        el("span", {}, r.type === "face" ? `Face hidden · ${where(r)}` : "Keeping only this area (crop)"),
        el("button", { class: "x", type: "button", title: "Remove", "aria-label": r.type === "face" ? "Remove face blur" : "Remove crop", onclick: () => removeRegion(r.id) }, "×")));
    }
    $("#face-status").textContent = !S.kind ? "—" : S.kind === "photo"
      ? "Colour photo. If a face (or part of one) is visible, hide it — or keep only the smile."
      : S.kind === "screenshot"
        ? "Screenshot. If it shows a 3D face rendering or a photo of the patient, hide it — or keep only the part you need."
        : "Radiograph — no face expected. For CBCT volumes, use the Python tool, which checks facial soft tissue.";
    $("#undo").disabled = S.scanning || !S.history.length;
    $("#redo").disabled = S.scanning || !S.future.length;
  }

  function outputSize() {
    const keep = S.regions.filter((r) => r.type === "keep").pop();
    return keep ? [Math.max(1, keep.x1 - keep.x0), Math.max(1, keep.y1 - keep.y0)] : [S.w, S.h];
  }
  function renderStage() {
    const cv = $("#cv"), ctx = cv.getContext("2d");
    if (S.view === "result") {
      const out = renderOutput();
      cv.width = out.width; cv.height = out.height;
      ctx.drawImage(out, 0, 0);
      $("#frame").style.aspectRatio = `${out.width} / ${out.height}`;
      fitFrame(out.width, out.height);
      cv.setAttribute("aria-label", "Result: what the download will look like");
    } else {
      cv.width = S.w; cv.height = S.h;
      ctx.putImageData(S.base, 0, 0);
      $("#frame").style.aspectRatio = `${S.w} / ${S.h}`;
      fitFrame();
      cv.setAttribute("aria-label", "The image being de-identified, with the areas found marked");
    }
  }

  // signature of what will be removed; stamped on the page whenever it is drawn
  function stateSig() {
    return `${S.doc}|` + S.regions.map((r) => `${r.id}:${r.type}:${r.on ? 1 : 0}:${r.x0},${r.y0},${r.x1},${r.y1}`).join(";");
  }
  function renderAll() {
    if (!S.base) return;
    renderStage(); renderOverlay(); renderLists();
    $("#stage").dataset.sig = stateSig();
    $("#stage").dataset.view = S.view;
  }

  function syncViewButtons() {
    $("#v-review").setAttribute("aria-pressed", String(S.view === "review"));
    $("#v-result").setAttribute("aria-pressed", String(S.view === "result"));
  }
  function setView(v) {
    S.view = v;
    syncViewButtons();
    setMode(null);
    if (S.base) renderAll();
  }

  // ---------------------------------------------------------------- output
  function pixelateBlur(img, x0, y0, x1, y1) {
    const W = img.width, d = img.data;
    x0 = Math.max(0, x0 | 0); y0 = Math.max(0, y0 | 0); x1 = Math.min(W, x1 | 0); y1 = Math.min(img.height, y1 | 0);
    const bw = x1 - x0, bh = y1 - y0;
    if (bw <= 0 || bh <= 0) return;
    const b = Math.max(6, Math.floor(Math.min(bw, bh) / 4));
    const roi = new Float32Array(bw * bh * 3);
    for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) {
      const s = ((y0 + y) * W + x0 + x) * 4, t = (y * bw + x) * 3;
      roi[t] = d[s]; roi[t + 1] = d[s + 1]; roi[t + 2] = d[s + 2];
    }
    for (let yy = 0; yy < bh; yy += b) for (let xx = 0; xx < bw; xx += b) {       // pixelate
      const m = [0, 0, 0]; let c = 0;
      for (let y = yy; y < Math.min(bh, yy + b); y++) for (let x = xx; x < Math.min(bw, xx + b); x++) {
        const t = (y * bw + x) * 3; m[0] += roi[t]; m[1] += roi[t + 1]; m[2] += roi[t + 2]; c++;
      }
      for (let y = yy; y < Math.min(bh, yy + b); y++) for (let x = xx; x < Math.min(bw, xx + b); x++) {
        const t = (y * bw + x) * 3; roi[t] = m[0] / c; roi[t + 1] = m[1] / c; roi[t + 2] = m[2] / c;
      }
    }
    const k = Math.max(3, b | 1), pad = k >> 1;                                  // separable box blur
    const tmp = new Float32Array(roi.length);
    for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) for (let ch = 0; ch < 3; ch++) {
      let s = 0; for (let i = -pad; i <= pad; i++) s += roi[(y * bw + Math.min(bw - 1, Math.max(0, x + i))) * 3 + ch];
      tmp[(y * bw + x) * 3 + ch] = s / k;
    }
    const mean = [0, 0, 0];
    for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) for (let ch = 0; ch < 3; ch++) {
      let s = 0; for (let i = -pad; i <= pad; i++) s += tmp[(Math.min(bh - 1, Math.max(0, y + i)) * bw + x) * 3 + ch];
      roi[(y * bw + x) * 3 + ch] = s / k; mean[ch] += s / k;
    }
    for (let ch = 0; ch < 3; ch++) mean[ch] /= bw * bh;
    for (let y = 0; y < bh; y++) for (let x = 0; x < bw; x++) {                   // flatten contrast
      const s = ((y0 + y) * W + x0 + x) * 4, t = (y * bw + x) * 3;
      for (let ch = 0; ch < 3; ch++) d[s + ch] = Math.round(mean[ch] + (roi[t + ch] - mean[ch]) * 0.35);
    }
  }

  // Built from S every time — the download never reuses what happens to be on screen.
  function renderOutput() {
    const c = newCanvas(S.w, S.h);
    const ctx = c.getContext("2d", { willReadFrequently: true });
    const img = new ImageData(new Uint8ClampedArray(S.base.data), S.w, S.h);
    for (const r of S.regions) if (r.type === "face") pixelateBlur(img, r.x0, r.y0, r.x1, r.y1);
    ctx.putImageData(img, 0, 0);
    ctx.fillStyle = "#000";
    for (const r of S.regions) if (isRedaction(r) && r.on) ctx.fillRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
    const keep = S.regions.filter((r) => r.type === "keep").pop();
    if (!keep) return c;
    const k = newCanvas(Math.max(1, keep.x1 - keep.x0), Math.max(1, keep.y1 - keep.y0));
    k.getContext("2d", { willReadFrequently: true }).drawImage(c, keep.x0, keep.y0, k.width, k.height, 0, 0, k.width, k.height);
    return k;
  }

  // Every area listed as "blacked out" must be solid black in the file that is about to be written.
  function verifyOutput(out) {
    const keep = S.regions.filter((r) => r.type === "keep").pop();
    const ox = keep ? keep.x0 : 0, oy = keep ? keep.y0 : 0;
    const ctx = out.getContext("2d", { willReadFrequently: true });
    for (const r of S.regions) {
      if (!isRedaction(r) || !r.on) continue;
      const x0 = Math.max(0, Math.round(r.x0) - ox), y0 = Math.max(0, Math.round(r.y0) - oy);
      const x1 = Math.min(out.width, Math.round(r.x1) - ox), y1 = Math.min(out.height, Math.round(r.y1) - oy);
      if (x1 <= x0 || y1 <= y0) continue;               // outside the kept crop
      const d = ctx.getImageData(x0, y0, x1 - x0, y1 - y0).data;
      for (let i = 0; i < d.length; i += 4) if (d[i] | d[i + 1] | d[i + 2] || d[i + 3] !== 255) return false;
    }
    return true;
  }

  // What the page currently shows (stamp + list) must be exactly what S says will be removed.
  function shownMatchesState() {
    if ($("#stage").dataset.sig !== stateSig()) return false;
    const listed = [...document.querySelectorAll("#l-text li")].map((li) => `${li.dataset.id}:${li.querySelector("input").checked ? 1 : 0}`).join(";");
    const held = S.regions.filter(isRedaction).map((r) => `${r.id}:${r.on ? 1 : 0}`).join(";");
    if (listed !== held) return false;
    const faces = [...document.querySelectorAll("#l-face li")].map((li) => li.dataset.id).join(";");
    return faces === S.regions.filter((r) => r.type === "face" || r.type === "keep").map((r) => String(r.id)).join(";");
  }

  // Keep only the image data: PNG → IHDR/PLTE/tRNS/IDAT/IEND; JPEG → drop APP1–APP15 and comments.
  function stripMetadata(u8, fmt) {
    const parts = [];
    if (fmt === "image/png") {
      if (!DentDeIDPng16.isPng(u8)) return u8;
      const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
      parts.push(u8.subarray(0, 8));
      let p = 8;
      while (p + 12 <= u8.length) {
        const len = dv.getUint32(p);
        const type = String.fromCharCode(u8[p + 4], u8[p + 5], u8[p + 6], u8[p + 7]);
        if (["IHDR", "PLTE", "tRNS", "IDAT", "IEND"].includes(type)) parts.push(u8.subarray(p, p + 12 + len));
        p += 12 + len;
        if (type === "IEND") break;
      }
    } else {
      if (!(u8[0] === 0xff && u8[1] === 0xd8)) return u8;
      parts.push(u8.subarray(0, 2));
      let p = 2;
      while (p + 4 <= u8.length && u8[p] === 0xff) {
        const m = u8[p + 1];
        if (m === 0xda) { parts.push(u8.subarray(p)); p = u8.length; break; }       // scan data to the end
        const len = (u8[p + 2] << 8) | u8[p + 3];
        const drop = (m >= 0xe1 && m <= 0xef) || m === 0xfe;
        if (!drop) parts.push(u8.subarray(p, p + 2 + len));
        p += 2 + len;
      }
      if (p < u8.length) parts.push(u8.subarray(p));
    }
    let total = 0;
    for (const x of parts) total += x.length;
    const out = new Uint8Array(total);
    let o = 0;
    for (const x of parts) { out.set(x, o); o += x.length; }
    return out;
  }

  function download() {
    if (!S.base || S.scanning) return;
    if (!$("#checked").checked) { $("#dl").disabled = true; return; }
    if (!shownMatchesState()) {
      renderAll(); resetConfirm();
      toast("The view was out of date and has been refreshed — please look it over again before downloading", { error: true });
      return;
    }
    const fmt = $("#fmt").value;
    const out = renderOutput();
    if (!verifyOutput(out)) {
      resetConfirm();
      toast("The blacked-out areas could not be verified, so nothing was saved. Please reload the page and try again.", { error: true });
      return;
    }
    const doc = S.doc, sig = stateSig();
    const rand = Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, "0")).join("");
    const name = `dentdeid-${rand}.${fmt === "image/png" ? "png" : "jpg"}`;
    out.toBlob(async (blob) => {
      if (!blob) { toast("Could not create the file.", { error: true }); return; }
      if (doc !== S.doc || sig !== stateSig()) { toast("The image changed while saving — nothing was saved.", { error: true }); return; }
      const clean = stripMetadata(new Uint8Array(await blob.arrayBuffer()), fmt);
      const a = el("a", { href: URL.createObjectURL(new Blob([clean], { type: fmt })), download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      const blacked = S.regions.filter((r) => isRedaction(r) && r.on).length;
      const faces = S.regions.filter((r) => r.type === "face").length;
      const crop = S.regions.some((r) => r.type === "keep");
      const parts = [`${blacked} area${blacked === 1 ? "" : "s"} blacked out`,
        `${S.meta.filter((m) => !m.fname).length} metadata item(s) dropped`];
      if (faces) parts.push(`${faces} face${faces > 1 ? "s" : ""} hidden`);
      if (crop) parts.push("cropped to the smile");
      const done = $("#done");
      done.textContent = `Saved ${name} — ${parts.join(", ")}.`;
      done.classList.remove("show"); void done.offsetWidth; done.classList.add("show");
    }, fmt, 0.95);
  }

  // ---------------------------------------------------------------- drawing boxes (1:1 pointer tracking)
  function syncModeButtons() {
    for (const [id, mm] of [["#t-add", "manual"], ["#f-hide", "face"], ["#f-keep", "keep"]]) {
      $(id).classList.toggle("on", S.mode === mm);
      $(id).setAttribute("aria-pressed", String(S.mode === mm));
    }
    $("#frame").classList.toggle("drawing", !!S.mode);
  }
  function setMode(m, viaKeyboard) {
    if (S.scanning) m = null;
    S.mode = m;
    if (!m && drag) { drag.r.remove(); drag = null; }
    syncModeButtons();
    if (m && S.view !== "review") { S.view = "review"; syncViewButtons(); renderAll(); }
    if (m && viaKeyboard) {
      $("#frame").focus({ preventScroll: false });
      toast("Press Enter to place a box in the middle, then move it with the arrow keys");
    }
  }
  function toImage(e) {
    const b = $("#ov").getBoundingClientRect();
    return [Math.max(0, Math.min(S.w, ((e.clientX - b.left) * S.w) / b.width)),
            Math.max(0, Math.min(S.h, ((e.clientY - b.top) * S.h) / b.height))];
  }
  let drag = null, tap = null;
  const TAP_SLOP = 6;                       // px: more movement than this is a swipe/scroll, not a tap
  $("#ov").addEventListener("pointerdown", (e) => {
    if (S.view !== "review" || S.scanning || !S.base) return;
    if (!S.mode) {
      // never act on pointerdown: a swipe that starts on a box must scroll, not toggle
      const id = e.target && e.target.dataset && e.target.dataset.id;
      tap = id && e.isPrimary ? { id, x: e.clientX, y: e.clientY, pid: e.pointerId } : null;
      return;
    }
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    $("#ov").setPointerCapture(e.pointerId);
    const [x, y] = toImage(e);
    const r = document.createElementNS(SVGNS, "rect");
    r.setAttribute("class", "draft");
    $("#ov").append(r);
    drag = { x, y, r, id: e.pointerId };
    moveDraft(e);
  });
  function moveDraft(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const [x, y] = toImage(e);
    const x0 = Math.min(drag.x, x), y0 = Math.min(drag.y, y);
    drag.r.setAttribute("x", x0); drag.r.setAttribute("y", y0);
    drag.r.setAttribute("width", Math.abs(x - drag.x)); drag.r.setAttribute("height", Math.abs(y - drag.y));
    drag.cur = [x, y];
  }
  $("#ov").addEventListener("pointermove", (e) => {
    if (tap && e.pointerId === tap.pid && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) > TAP_SLOP) tap = null;
    moveDraft(e);
  });
  function addBox(type, x0, y0, x1, y1, focus) {
    snapshot();
    const box = { id: S.nextId++, type, on: true, x0, y0, x1, y1 };
    if (type === "keep") S.regions = S.regions.filter((r) => r.type !== "keep");
    S.regions.push(box);
    S.mode = null; syncModeButtons();
    changed();
    if (focus) {
      const n = $(`#ov [data-id="${box.id}"]`);
      if (n) n.focus();
    }
    const tail = focus ? " — arrow keys move it, Shift + arrows resize, Delete removes" : "";
    toast((type === "face" ? "Face will be blurred" : type === "keep" ? "Only this area will be kept" : "Box added — it will be blacked out") + tail,
      focus ? {} : { action: "Undo", onAction: undo });
    return box;
  }
  function endDraft(e, cancel) {
    if (!drag || (e && e.pointerId !== drag.id)) return;
    const d = drag; drag = null;
    d.r.remove();
    if (cancel || !d.cur || !S.mode) return;
    const [x, y] = d.cur;
    const b = $("#ov").getBoundingClientRect();
    const minPx = (6 * S.w) / b.width;
    if (Math.abs(x - d.x) < minPx || Math.abs(y - d.y) < minPx) return;
    addBox(S.mode, Math.round(Math.min(d.x, x)), Math.round(Math.min(d.y, y)), Math.round(Math.max(d.x, x)), Math.round(Math.max(d.y, y)), false);
  }
  $("#ov").addEventListener("pointerup", (e) => {
    if (tap && e.pointerId === tap.pid) {
      const t = tap; tap = null;
      const still = Math.hypot(e.clientX - t.x, e.clientY - t.y) <= TAP_SLOP;
      const onSame = e.target && e.target.dataset && e.target.dataset.id === t.id;
      if (still && onSame && !S.mode) toggleRegion(t.id);
    }
    endDraft(e, false);
  });
  $("#ov").addEventListener("pointercancel", (e) => { tap = null; endDraft(e, true); });   // the browser took over (scroll)

  // ---------------------------------------------------------------- keyboard: place, move, resize, remove
  let kbEdit = null;
  function dropDefaultBox() {
    const m = S.mode;
    const [fw, fh] = m === "face" ? [0.4, 0.5] : m === "keep" ? [0.4, 0.3] : [0.3, 0.08];
    const bw = Math.max(8, Math.min(S.w, Math.round(S.w * fw))), bh = Math.max(8, Math.min(S.h, Math.round(S.h * fh)));
    const x0 = Math.round((S.w - bw) / 2), y0 = Math.round((S.h - bh) / 2);
    addBox(m, x0, y0, x0 + bw, y0 + bh, true);
  }
  $("#frame").addEventListener("keydown", (e) => {
    if (!S.base || S.scanning || e.metaKey || e.ctrlKey) return;
    const t = e.target;
    if (t === $("#frame")) {
      if ((e.key === "Enter" || e.key === " ") && S.mode) { e.preventDefault(); dropDefaultBox(); }
      else if (e.key === "Enter") toast("Choose Add a box, Hide a face or Keep only the smile first, then press Enter here");
      return;
    }
    const r = t && t.dataset && t.dataset.id ? findRegion(t.dataset.id) : null;
    if (!r) return;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[e.key]) {
      e.preventDefault();
      const [dx, dy] = arrows[e.key];
      const step = e.altKey ? 1 : Math.max(2, Math.round(Math.min(S.w, S.h) / 100));
      const now = performance.now();
      if (!kbEdit || kbEdit.id !== r.id || now - kbEdit.t > 1000) snapshot();     // one undo step per burst
      kbEdit = { id: r.id, t: now };
      if (e.shiftKey) {                                   // resize from the right / bottom edge
        r.x1 = Math.max(r.x0 + 4, Math.min(S.w, r.x1 + dx * step));
        r.y1 = Math.max(r.y0 + 4, Math.min(S.h, r.y1 + dy * step));
      } else {
        const bw = r.x1 - r.x0, bh = r.y1 - r.y0;
        r.x0 = Math.max(0, Math.min(S.w - bw, r.x0 + dx * step)); r.x1 = r.x0 + bw;
        r.y0 = Math.max(0, Math.min(S.h - bh, r.y0 + dy * step)); r.y1 = r.y0 + bh;
      }
      changed();
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeRegion(r.id);
      $("#frame").focus();
    } else if ((e.key === "Enter" || e.key === " ") && isRedaction(r)) {
      e.preventDefault();
      toggleRegion(r.id);
    } else if (e.key === "Escape") {
      $("#frame").focus();
    }
  });

  // ---------------------------------------------------------------- face auto-detect (only where the browser offers it)
  async function autoFaces() {
    try {
      const fd = new window.FaceDetector({ fastMode: false, maxDetectedFaces: 8 });
      const faces = await fd.detect($("#cv"));
      if (!faces.length) { toast("No face found automatically — draw a box if one is visible"); return; }
      snapshot();
      for (const f of faces) {
        const b = f.boundingBox;
        S.regions.push({ id: S.nextId++, type: "face", on: true, x0: Math.max(0, Math.round(b.x - 0.2 * b.width)),
          y0: Math.max(0, Math.round(b.y - 0.35 * b.height)), x1: Math.min(S.w, Math.round(b.x + 1.2 * b.width)),
          y1: Math.min(S.h, Math.round(b.y + 1.25 * b.height)) });
      }
      changed();
      toast(`${faces.length} face${faces.length > 1 ? "s" : ""} found and hidden — check the result`);
    } catch { toast("Automatic face finding isn't available in this browser — draw a box instead"); }
  }

  // ---------------------------------------------------------------- wiring
  const drop = $("#drop"), file = $("#file");
  drop.addEventListener("click", () => file.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } });
  file.addEventListener("change", () => { if (file.files[0]) loadBlob(file.files[0], file.files[0].name); file.value = ""; });
  // a file dropped anywhere on the page opens (the drop zone just highlights)
  const hasFiles = (e) => e.dataTransfer && [...(e.dataTransfer.types || [])].includes("Files");
  document.addEventListener("dragover", (e) => {
    e.preventDefault();
    if (hasFiles(e)) { e.dataTransfer.dropEffect = "copy"; if (!S.base) drop.classList.add("over"); }
  });
  document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) drop.classList.remove("over"); });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) loadBlob(f, f.name);
  });
  // paste an image (e.g. a screenshot) with Ctrl/⌘+V
  document.addEventListener("paste", (e) => {
    const cd = e.clipboardData;
    if (!cd) return;
    let f = [...(cd.files || [])].find((x) => /^image\//.test(x.type) || /\.(dcm|dicom)$/i.test(x.name));
    if (!f) for (const it of cd.items || []) if (it.kind === "file") { f = it.getAsFile(); if (f) break; }
    if (!f) return;
    e.preventDefault();
    loadBlob(f, f.name || "pasted-image.png");
  });
  document.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const url = b.dataset.sample;
      const res = await fetch(url);
      if (!res.ok) throw new Error();
      loadBlob(await res.blob(), url.split("/").pop());
    } catch { toast("Samples need the site to be served over http(s) — open the GitHub Pages link", { error: true }); }
  }));
  $("#back").addEventListener("click", () => {
    loadSeq++;                              // a load still decoding is dropped
    cancelScan();
    S.doc++; S.base = null; S.views = null; S.regions = []; S.history = []; S.future = []; S.mode = null;
    const cv = $("#cv"); cv.width = 1; cv.height = 1;
    $("#ov").textContent = "";
    $("#work").classList.remove("show");
    $("#start").hidden = false; $("#how").hidden = false;
    layout();
    window.scrollTo({ top: 0, behavior: "auto" });
    $("#drop").focus();
  });
  // header links also work while an image is open: show the section, then go there
  document.querySelectorAll('.bar nav a[href^="#"]').forEach((a) => a.addEventListener("click", (e) => {
    const target = document.querySelector(a.getAttribute("href"));
    if (!target) return;
    e.preventDefault();
    $("#how").hidden = false;
    target.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    target.focus({ preventScroll: true });
    if (history.replaceState) history.replaceState(null, "", a.getAttribute("href"));
  }));
  $("#v-review").addEventListener("click", () => setView("review"));
  $("#v-result").addEventListener("click", () => setView("result"));
  $("#undo").addEventListener("click", undo);
  $("#redo").addEventListener("click", redo);
  for (const [id, m] of [["#t-add", "manual"], ["#f-hide", "face"], ["#f-keep", "keep"]]) {
    $(id).addEventListener("click", (e) => setMode(S.mode === m ? null : m, e.detail === 0));
  }
  $("#f-auto").addEventListener("click", autoFaces);
  $("#t-scan").addEventListener("click", blackOutSuggestions);
  $("#checked").addEventListener("change", (e) => {
    $("#dl").disabled = !e.target.checked || S.scanning;
    if (e.target.checked && S.view !== "result") {
      setView("result");
      toast("Showing the result — look it over before downloading");
      const r = $("#stage").getBoundingClientRect();
      if (r.bottom < 80 || r.top > innerHeight - 80) $("#stage").scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    }
  });
  $("#dl").addEventListener("click", download);
  document.addEventListener("keydown", (e) => {
    if (!S.base) return;
    const tag = e.target && e.target.tagName;
    if (tag === "INPUT" && e.target.type !== "checkbox" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === "Escape" && S.mode) { setMode(null); return; }
    const mod = e.metaKey || e.ctrlKey, k = e.key.toLowerCase();
    if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (mod && ((k === "z" && e.shiftKey) || (k === "y" && e.ctrlKey && !e.metaKey))) { e.preventDefault(); redo(); }
  });
  layout();
  window.addEventListener("load", layout);
})();
