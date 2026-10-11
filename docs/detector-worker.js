/* DentDeID — runs the text detector off the main thread so the page stays responsive on big
 * images. Receives {id, views}, answers {id, progress} while working and then {id, result}
 * (= DentDeIDDetector.scanViews: {imageClass, regions}) or {id, error}. MIT licence. */
/* global importScripts, DentDeIDDetector */
importScripts("detector.js");

self.onmessage = (e) => {
  const { id, views } = e.data || {};
  try {
    let last = -1;
    const result = DentDeIDDetector.scanViews(views, null, {
      onProgress(f) {
        if (f - last >= 0.02 || f >= 1) { last = f; self.postMessage({ id, progress: f }); }
      },
    });
    self.postMessage({ id, result });
  } catch (err) {
    self.postMessage({ id, error: String((err && err.message) || err) });
  }
};
