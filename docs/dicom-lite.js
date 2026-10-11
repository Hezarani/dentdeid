/*
 * dicom-lite — a small, dependency-free DICOM reader for the DentDeID web app.
 *
 * Reads Part-10 files (with or without the 128-byte preamble) and raw datasets without a file
 * meta header, in Explicit/Implicit VR Little Endian; uncompressed 8/16-bit greyscale or RGB,
 * plus baseline JPEG (decoded by the browser). Big-endian, deflated, JPEG 2000, JPEG-LS, RLE
 * and lossless JPEG get a clear "use the Python tool" message instead of a wrong picture.
 * It reports WHICH identifying attributes are present — never their values — and converts the
 * first frame to 8-bit RGBA with the same mapping as dentdeid.dicom.pixels.to_display8
 * (and, for >8-bit greyscale, the plain linear view pixels.linear8 as a second detection view).
 * MIT licence.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DicomLite = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const LONG_VR = new Set(["OB", "OD", "OF", "OL", "OV", "OW", "SQ", "SV", "UC", "UN", "UR", "UT", "UV"]);
  const ALL_VR = new Set(["AE", "AS", "AT", "CS", "DA", "DS", "DT", "FD", "FL", "IS", "LO", "LT", "OB", "OD", "OF", "OL",
    "OV", "OW", "PN", "SH", "SL", "SQ", "SS", "ST", "SV", "TM", "UC", "UI", "UL", "UN", "UR", "US", "UT", "UV"]);
  const IMPLICIT = "1.2.840.10008.1.2";
  const EXPLICIT = "1.2.840.10008.1.2.1";
  const JPEG_BASELINE = "1.2.840.10008.1.2.4.50";
  const JPEG_EXTENDED = "1.2.840.10008.1.2.4.51";
  const UNCOMPRESSED = new Set([IMPLICIT, EXPLICIT]);
  const PYTHON = "Use the Python tool instead (pip install dentdeid).";

  // Transfer syntaxes the browser can't read, with a name a person recognises.
  function unsupportedSyntax(ts) {
    const t = ts || "";
    if (t === "1.2.840.10008.1.2.2") return "the retired big-endian encoding";
    if (t === "1.2.840.10008.1.2.1.99") return "deflate compression of the whole file";
    if (/^1\.2\.840\.10008\.1\.2\.4\.(57|70)$/.test(t)) return "lossless JPEG compression";
    if (/^1\.2\.840\.10008\.1\.2\.4\.8[01]$/.test(t)) return "JPEG-LS compression";
    if (/^1\.2\.840\.10008\.1\.2\.4\.(9[0-3]|20[1-3])$/.test(t)) return "JPEG 2000 compression";
    if (t === "1.2.840.10008.1.2.5") return "RLE compression";
    if (/^1\.2\.840\.10008\.1\.2\.4\.1\d\d$/.test(t)) return "video (MPEG/HEVC) compression";
    if (/^1\.2\.840\.10008\.1\.2\.4\.11[0-2]$/.test(t)) return "JPEG XL compression";
    return null;
  }
  function userError(msg) { const e = new Error(msg); e.userFacing = true; return e; }

  // Attributes shown (by NAME only) in the "hidden data in this file" list.
  const IDENTIFYING = {
    // patient
    "00100010": "Patient's Name", "00100020": "Patient ID", "00100021": "Issuer of Patient ID",
    "00100030": "Patient's Birth Date", "00100032": "Patient's Birth Time", "00100040": "Patient's Sex",
    "00101000": "Other Patient IDs", "00101001": "Other Patient Names", "00101002": "Other Patient IDs Sequence",
    "00101005": "Patient's Birth Name", "00101010": "Patient's Age", "00101020": "Patient's Size",
    "00101030": "Patient's Weight", "00101040": "Patient's Address", "00101060": "Patient's Mother's Birth Name",
    "00101080": "Military Rank", "00101090": "Medical Record Locator", "00102150": "Country of Residence",
    "00102152": "Region of Residence", "00102154": "Patient's Telephone Numbers", "00102160": "Ethnic Group",
    "00102180": "Occupation", "001021B0": "Additional Patient History", "001021F0": "Patient's Religious Preference",
    "00102297": "Responsible Person", "00102299": "Responsible Organization", "00104000": "Patient Comments",
    "00380010": "Admission ID", "00380300": "Current Patient Location", "00380500": "Patient State",
    // people & places
    "00080080": "Institution Name", "00080081": "Institution Address", "00080082": "Institution Code Sequence",
    "00081040": "Institutional Department Name", "00080090": "Referring Physician's Name",
    "00080092": "Referring Physician's Address", "00080094": "Referring Physician's Telephone Numbers",
    "00081048": "Physician(s) of Record", "00081050": "Performing Physician's Name",
    "00081060": "Name of Physician(s) Reading Study", "00081070": "Operators' Name", "00321032": "Requesting Physician",
    "00321033": "Requesting Service", "00081010": "Station Name", "00181000": "Device Serial Number",
    "0018700A": "Detector ID", "00181030": "Protocol Name", "00400006": "Scheduled Performing Physician's Name",
    // dates & times
    "00080012": "Instance Creation Date", "00080013": "Instance Creation Time",
    "00080020": "Study Date", "00080021": "Series Date", "00080022": "Acquisition Date", "00080023": "Content Date",
    "0008002A": "Acquisition DateTime", "00080030": "Study Time", "00080031": "Series Time",
    "00080032": "Acquisition Time", "00080033": "Content Time", "00080201": "Timezone Offset From UTC",
    "00400244": "Performed Procedure Step Start Date", "00400245": "Performed Procedure Step Start Time",
    // study / order identifiers and free text
    "00080050": "Accession Number", "00200010": "Study ID", "00401001": "Requested Procedure ID",
    "00400253": "Performed Procedure Step ID", "00081030": "Study Description", "0008103E": "Series Description",
    "00081080": "Admitting Diagnoses Description", "00204000": "Image Comments", "00400275": "Request Attributes Sequence",
    "0040A730": "Content Sequence", "00081120": "Referenced Patient Sequence", "00880200": "Icon Image Sequence",
    // unique identifiers that link the file to the source record
    "0020000D": "Study Instance UID", "0020000E": "Series Instance UID", "00080018": "SOP Instance UID",
    "00200052": "Frame of Reference UID",
  };
  const IMPLICIT_VR = { "00280010": "US", "00280011": "US", "00280100": "US", "00280101": "US", "00280103": "US",
    "00280002": "US", "00280006": "US", "00280004": "CS", "00280008": "IS", "00281052": "DS", "00281053": "DS",
    "00080060": "CS", "00120062": "CS", "7FE00010": "OW" };

  const hex4 = (n) => n.toString(16).toUpperCase().padStart(4, "0");

  /** Does this look like DICOM even without a .dcm name? (preamble+DICM, or a raw LE dataset) */
  function looksLikeDicom(u8) {
    if (u8.length >= 132 && u8[128] === 68 && u8[129] === 73 && u8[130] === 67 && u8[131] === 77) return true;
    if (u8.length < 16) return false;
    const g = u8[0] | (u8[1] << 8), e = u8[2] | (u8[3] << 8);
    if (g !== 0x0002 && g !== 0x0008) return false;
    const vr = String.fromCharCode(u8[4], u8[5]);
    if (ALL_VR.has(vr)) return true;
    const len = u8[4] | (u8[5] << 8) | (u8[6] << 16) | (u8[7] << 24);
    return e < 0x2000 && len >= 0 && len < 256;          // implicit VR: small first element
  }

  function parse(buffer) {
    const dv = new DataView(buffer);
    const u8 = new Uint8Array(buffer);
    let pos = 0;
    const preamble = u8.length >= 132 && u8[128] === 68 && u8[129] === 73 && u8[130] === 67 && u8[131] === 77;
    if (preamble) pos = 132;
    else if (u8.length >= 4 && u8[0] === 68 && u8[1] === 73 && u8[2] === 67 && u8[3] === 77) pos = 4;   // "DICM" without preamble
    const out = { transferSyntax: IMPLICIT, tags: new Set(), values: {}, privateCount: 0, overlayGroups: new Set(),
                  pixel: null, encapsulated: null, hadPreamble: preamble, hadMeta: false, unsupported: null };
    const txt = (a, b) => { let s = ""; for (let i = a; i < b && i < u8.length; i++) s += String.fromCharCode(u8[i]); return s.replace(/[\0 ]+$/, "").trim(); };
    // no file meta: the dataset starts here — explicit VR if the first element carries a VR
    const firstGroup = pos + 4 <= u8.length ? dv.getUint16(pos, true) : 0;
    let explicitData = null;
    if (firstGroup !== 0x0002 && pos + 6 <= u8.length) {
      const vr = String.fromCharCode(u8[pos + 4], u8[pos + 5]);
      explicitData = ALL_VR.has(vr);
      out.transferSyntax = explicitData ? EXPLICIT : IMPLICIT;
    }

    function readValue(vr, off, len) {
      if (off + len > u8.length) return null;
      if (vr === "US") return len >= 2 ? dv.getUint16(off, true) : null;
      if (vr === "UL") return len >= 4 ? dv.getUint32(off, true) : null;
      if (vr === "DS" || vr === "IS") return parseFloat(txt(off, off + len).split("\\")[0]);
      if (vr === "CS" || vr === "UI" || vr === "SH" || vr === "LO") return txt(off, off + len);
      return null;
    }

    function skipUndefined(p, explicit) {  // returns position after the (FFFE,E0DD) delimiter
      while (p + 8 <= u8.length) {
        const g = dv.getUint16(p, true), e = dv.getUint16(p + 2, true);
        const len = dv.getUint32(p + 4, true);
        p += 8;
        if (g === 0xFFFE && e === 0xE0DD) return p;
        if (g === 0xFFFE && e === 0xE000) {           // item
          if (len === 0xFFFFFFFF) p = readDataset(p, explicit, true);
          else p += len;
          continue;
        }
        return p;                                       // malformed: stop
      }
      return p;
    }

    function readDataset(p, explicit, inItem) {
      while (p + 8 <= u8.length) {
        const group = dv.getUint16(p, true), elem = dv.getUint16(p + 2, true);
        if (group === 0xFFFE && (elem === 0xE00D)) { return p + 8; }          // item delimitation
        if (group === 0xFFFE && elem === 0xE0DD) { return p + 8; }
        const key = hex4(group) + hex4(elem);
        const isMeta = group === 0x0002;
        const expl = isMeta || explicit;
        let vr, len, off;
        if (expl) {
          vr = String.fromCharCode(u8[p + 4], u8[p + 5]);
          if (LONG_VR.has(vr)) { if (p + 12 > u8.length) break; len = dv.getUint32(p + 8, true); off = p + 12; }
          else { len = dv.getUint16(p + 6, true); off = p + 8; }
        } else {
          vr = IMPLICIT_VR[key] || "UN";
          len = dv.getUint32(p + 4, true); off = p + 8;
        }
        if (!inItem) {
          if (isMeta) out.hadMeta = true;
          if (group % 2 === 1) out.privateCount++;
          else out.tags.add(key);
          if (group >= 0x6000 && group <= 0x601E && group % 2 === 0) out.overlayGroups.add(group);
        }
        if (len === 0xFFFFFFFF) {
          if (key === "7FE00010") {                    // encapsulated pixel data
            const frags = [];
            let q = off;
            let first = true;
            while (q + 8 <= u8.length) {
              const g = dv.getUint16(q, true), e = dv.getUint16(q + 2, true), l = dv.getUint32(q + 4, true);
              q += 8;
              if (g === 0xFFFE && e === 0xE0DD) break;
              if (!first) frags.push(u8.subarray(q, Math.min(u8.length, q + l)));
              first = false;
              q += l;
            }
            if (!inItem) out.encapsulated = frags;
            p = q;
          } else {
            p = skipUndefined(off, expl);
          }
          continue;
        }
        if (!inItem) {
          if (key === "00020010") out.transferSyntax = txt(off, off + len);
          else if (key === "7FE00010") out.pixel = u8.subarray(off, Math.min(u8.length, off + len));
          else if (IMPLICIT_VR[key]) out.values[key] = readValue(expl ? vr : IMPLICIT_VR[key], off, len);
        }
        p = off + len;
        if (isMeta && p + 2 <= u8.length && dv.getUint16(p, true) !== 0x0002) {
          // end of the file meta group: the rest uses the transfer syntax it announced
          const why = unsupportedSyntax(out.transferSyntax);
          if (why && (out.transferSyntax === "1.2.840.10008.1.2.2" || out.transferSyntax === "1.2.840.10008.1.2.1.99")) {
            out.unsupported = `This DICOM file uses ${why}, which the browser version can't read. ${PYTHON}`;
            return p;                                   // the dataset can't be parsed as little-endian
          }
          explicit = out.transferSyntax !== IMPLICIT;
        }
      }
      return p;
    }

    readDataset(pos, explicitData === null ? true : explicitData, false);
    const v = out.values;
    out.rows = v["00280010"]; out.columns = v["00280011"];
    out.bitsAllocated = v["00280100"] || 8; out.bitsStored = v["00280101"] || out.bitsAllocated;
    out.pixelRepresentation = v["00280103"] || 0; out.samplesPerPixel = v["00280002"] || 1;
    out.planarConfiguration = v["00280006"] || 0; out.photometric = v["00280004"] || "MONOCHROME2";
    out.frames = v["00280008"] || 1;
    out.slope = v["00281053"] || 1; out.intercept = v["00281052"] || 0;
    if (!isFinite(out.slope)) out.slope = 1;
    if (!isFinite(out.intercept)) out.intercept = 0;
    out.modality = v["00080060"] || "";
    out.identityRemoved = (v["00120062"] || "").toUpperCase() === "YES";
    return out;
  }

  function identifyingPresent(d) {
    const list = [];
    for (const [k, name] of Object.entries(IDENTIFYING)) if (d.tags.has(k)) list.push(name);
    if (d.privateCount) list.push(`${d.privateCount} private (vendor) attribute${d.privateCount > 1 ? "s" : ""}`);
    if (d.overlayGroups.size) list.push(`${d.overlayGroups.size} overlay plane${d.overlayGroups.size > 1 ? "s" : ""}`);
    if (d.hadPreamble || d.hadMeta) list.push("File preamble & meta header");
    return list;
  }

  function mapper() {
    if (typeof DentDeIDPng16 !== "undefined") return DentDeIDPng16;
    if (typeof self !== "undefined" && self.DentDeIDPng16) return self.DentDeIDPng16;
    if (typeof require === "function") return require("./png16.js");
    throw new Error("png16.js is missing");
  }

  // Greyscale windowing identical to dentdeid.dicom.pixels.to_display8 (+ linear8 view)
  function grayViews(vals, w, h, d) {
    const n = w * h;
    const M = mapper();
    const g = M.display8(vals, d.slope, d.intercept);
    const inv = d.photometric === "MONOCHROME1";
    if (inv) for (let i = 0; i < n; i++) g[i] = 255 - g[i];
    const rgba = new Uint8ClampedArray(n * 4);
    for (let i = 0; i < n; i++) {
      const v = g[i];
      rgba[4 * i] = rgba[4 * i + 1] = rgba[4 * i + 2] = v;
      rgba[4 * i + 3] = 255;
    }
    const views = [{ width: w, height: h, channels: 1, data: g }];
    if (!(vals instanceof Uint8Array || vals instanceof Uint8ClampedArray)) {        // pixels.linear8 (stored values)
      const lin = M.linear8(vals);
      if (lin) {
        if (inv) for (let i = 0; i < n; i++) lin[i] = 255 - lin[i];
        views.push({ width: w, height: h, channels: 1, data: lin });
      }
    }
    return { rgba, views };
  }

  function canvas2d(w, h) {
    if (typeof OffscreenCanvas !== "undefined") {
      const c = new OffscreenCanvas(w, h);
      return c.getContext("2d", { willReadFrequently: true });
    }
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    return c.getContext("2d", { willReadFrequently: true });
  }

  /** First frame as {rgba, width, height, views}; throws an Error with a user-facing message. */
  async function toImage(d) {
    if (d.unsupported) throw userError(d.unsupported);
    const w = d.columns, h = d.rows;
    const why = unsupportedSyntax(d.transferSyntax);
    if (why) throw userError(`This DICOM file uses ${why}, which the browser can't decode. ${PYTHON}`);
    if (!w || !h) throw userError("This DICOM file contains no image (it may be a report, a structured document or a damaged file).");
    if (d.encapsulated) {
      const jpeg = d.transferSyntax === JPEG_BASELINE || (d.transferSyntax === JPEG_EXTENDED && d.bitsStored <= 8);
      if (!jpeg || typeof createImageBitmap === "undefined") {
        throw userError(`This DICOM file uses a compression the browser can't decode (${d.transferSyntax}). ${PYTHON}`);
      }
      let total = 0;
      for (const f of d.encapsulated) total += f.length;
      const buf = new Uint8Array(total);
      let o = 0;
      for (const f of d.encapsulated) { buf.set(f, o); o += f.length; }
      let bmp;
      try { bmp = await createImageBitmap(new Blob([buf], { type: "image/jpeg" })); }
      catch { throw userError(`The JPEG image inside this DICOM file could not be decoded. ${PYTHON}`); }
      const bw = bmp.width, bh = bmp.height;
      const ctx = canvas2d(bw, bh);
      ctx.drawImage(bmp, 0, 0);
      if (bmp.close) bmp.close();
      const px = ctx.getImageData(0, 0, bw, bh).data;
      if (d.samplesPerPixel === 3) {
        const rgba = new Uint8ClampedArray(px);
        return { rgba, width: bw, height: bh, views: [{ width: bw, height: bh, channels: 4, data: rgba }] };
      }
      const g = new Uint8Array(bw * bh);                // greyscale JPEG: rescale, window, MONOCHROME1
      for (let i = 0; i < g.length; i++) g[i] = px[4 * i];
      const { rgba, views } = grayViews(g, bw, bh, d);
      return { rgba, width: bw, height: bh, views };
    }
    if (!UNCOMPRESSED.has(d.transferSyntax)) throw userError(`Unsupported DICOM transfer syntax (${d.transferSyntax}). ${PYTHON}`);
    if (!d.pixel) throw userError("This DICOM file contains no pixel data.");
    const n = w * h;
    const px = d.pixel;
    if (d.samplesPerPixel === 3) {
      if (d.bitsAllocated !== 8) throw userError(`Unsupported colour bit depth (${d.bitsAllocated}). ${PYTHON}`);
      if (px.length < 3 * n) throw userError("This DICOM file's pixel data is truncated — the file may not have been copied completely. Export or copy it again.");
      if (/^YBR/.test(d.photometric)) throw userError(`Uncompressed ${d.photometric} colour isn't supported in the browser. ${PYTHON}`);
      const rgba = new Uint8ClampedArray(n * 4);
      for (let i = 0; i < n; i++) {
        if (d.planarConfiguration === 1) {
          rgba[4 * i] = px[i]; rgba[4 * i + 1] = px[n + i]; rgba[4 * i + 2] = px[2 * n + i];
        } else {
          rgba[4 * i] = px[3 * i]; rgba[4 * i + 1] = px[3 * i + 1]; rgba[4 * i + 2] = px[3 * i + 2];
        }
        rgba[4 * i + 3] = 255;
      }
      return { rgba, width: w, height: h, views: [{ width: w, height: h, channels: 4, data: rgba }] };
    }
    let vals;
    if (d.bitsAllocated === 16) {
      if (px.length < 2 * n) throw userError("This DICOM file's pixel data is truncated — the file may not have been copied completely. Export or copy it again.");
      const bytes = px.slice(0, n * 2);
      const u16 = new Uint16Array(bytes.buffer);
      // like pydicom: only the Bits Stored low bits carry the value (high bits may hold overlays)
      const bs = d.bitsStored > 0 && d.bitsStored < 16 ? d.bitsStored : 16;
      if (d.pixelRepresentation === 1) {
        const i16 = new Int16Array(n);
        const sh = 16 - bs;
        for (let i = 0; i < n; i++) i16[i] = ((u16[i] << sh) << 16) >> (16 + sh);   // sign-extend from bit bs-1
        vals = i16;
      } else if (bs < 16) {
        const mask = (1 << bs) - 1;
        for (let i = 0; i < n; i++) u16[i] &= mask;
        vals = u16;
      } else vals = u16;
    } else if (d.bitsAllocated === 8) {
      if (px.length < n) throw userError("This DICOM file's pixel data is truncated — the file may not have been copied completely. Export or copy it again.");
      vals = px.subarray(0, n);
    } else {
      throw userError(`Unsupported DICOM bit depth (${d.bitsAllocated} bits). ${PYTHON}`);
    }
    const { rgba, views } = grayViews(vals, w, h, d);
    return { rgba, width: w, height: h, views };
  }

  return { parse, toImage, identifyingPresent, looksLikeDicom, unsupportedSyntax, IDENTIFYING };
});
