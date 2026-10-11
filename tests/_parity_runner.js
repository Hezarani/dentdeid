// Reads a JSON job list from argv[2] and runs the web app's JavaScript on each job, printing one
// JSON result per job (used by test_parity_cli_bench.py and test_web_parity.py).
//
//   {file, width, height, channels}                    -> detectText regions (raw uint8 pixel dump)
//   {..., forceColor: true}                            -> detectText with the colour pass forced
//   {..., op: "class"}                                 -> imageClass
//   {..., op: "both"}                                  -> {imageClass, regions, regionsForced}
//   {op: "display8", file, signed, slope, intercept, out} -> writes png16.display8 bytes of a raw
//                                                         little-endian (u)int16 dump to `out`
//   {op: "png16", png, out}                            -> decodes a 16-bit PNG like the app; writes the
//                                                         display view to `out` (+ linear view to out.lin)
//                                                         and returns {width, height, channels, imageClass, regions}
//   {op: "meta"}                                       -> {ALGORITHM_VERSION, DEFAULTS}
const fs = require("fs");
const path = require("path");
const docs = path.join(__dirname, "..", "docs");
const det = require(path.join(docs, "detector.js"));
const png16 = require(path.join(docs, "png16.js"));

function image(j) {
  const data = new Uint8Array(fs.readFileSync(j.file));
  return { width: j.width, height: j.height, channels: j.channels, data };
}
const save = (file, arr) => fs.writeFileSync(file, Buffer.from(arr.buffer, arr.byteOffset, arr.byteLength));

async function run(j) {
  const op = j.op || "detect";
  if (op === "detect") return det.detectText(image(j), j.params || null, !!j.forceColor);
  if (op === "class") return det.imageClass(image(j), j.params || null);
  if (op === "both") {
    const img = image(j);
    return { imageClass: det.imageClass(img), regions: det.detectText(img, null, false),
             regionsForced: det.detectText(img, null, true) };
  }
  if (op === "display8") {
    const b = fs.readFileSync(j.file);
    const ab = b.buffer.slice(b.byteOffset, b.byteOffset + b.length);
    const vals = j.signed ? new Int16Array(ab) : new Uint16Array(ab);
    save(j.out, png16.display8(vals, j.slope, j.intercept));
    return { n: vals.length };
  }
  if (op === "png16") {
    const dec = await png16.decode(new Uint8Array(fs.readFileSync(j.png)));
    const im = png16.toImage(dec);
    save(j.out, im.views[0].data);
    if (im.views[1]) save(j.out + ".lin", im.views[1].data);
    const res = det.scanViews(im.views);
    return { width: dec.width, height: dec.height, channels: dec.channels, viewChannels: im.views[0].channels,
             views: im.views.length, imageClass: res.imageClass, regions: res.regions };
  }
  if (op === "meta") return { ALGORITHM_VERSION: det.ALGORITHM_VERSION, DEFAULTS: det.DEFAULTS };
  throw new Error("unknown op " + op);
}

(async () => {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const out = [];
  for (const j of jobs) out.push(await run(j));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
