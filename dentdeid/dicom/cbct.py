"""CBCT / CT volumes: facial re-identification risk and **experimental** soft-tissue removal.

A cone-beam CT that includes the lower face can be surface-rendered into a recognisable 3D
face. For head MRI, face-recognition software matched 70 of 84 volunteers (83%) from
reconstructions (Schwarz et al., NEJM 2019); for head CT, re-identification stayed at
30–45% even after defacing (Ding et al., IJCARS 2025). PS3.15 E.3.2 itself cites dental
images as a case where hiding recognisable features conflicts with clinical usefulness.

DentDeID therefore:

* **always** assesses and reports the risk (field of view, presence of a soft-tissue surface);
* offers two **experimental, unvalidated** mitigations, off by default:

  - ``strip-soft-tissue``: every voxel below an automatically found bone threshold is set to
    air, leaving only bone and teeth. No skin surface remains to render, so Recognizable
    Visual Features is set to NO. This destroys soft-tissue and airway information.
  - ``shell``: soft tissue within *N* mm of the outer skin surface is set to air, keeping
    deeper soft tissue and the airway. The face is reduced, **not** removed; Recognizable
    Visual Features stays YES.

Neither mitigation has been tested against face-recognition attacks. Use them to reduce risk,
never as a guarantee.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from pydicom.dataset import Dataset

CBCT_MODES = ("flag", "strip-soft-tissue", "shell")


@dataclass
class FaceRisk:
    level: str                       # high | moderate | low | unknown
    fov_mm: tuple[float, float, float] | None
    soft_tissue_fraction: float
    reason: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["fov_mm"] = list(self.fov_mm) if self.fov_mm else None
        return d


def otsu(values: np.ndarray, bins: int = 256) -> float:
    v = values[np.isfinite(values)].astype(np.float64)
    if v.size == 0:
        return 0.0
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return lo
    hist, edges = np.histogram(v, bins=bins, range=(lo, hi))
    p = hist.astype(np.float64) / hist.sum()
    w0 = np.cumsum(p)
    mids = (edges[:-1] + edges[1:]) / 2
    mu0 = np.cumsum(p * mids)
    mu_t = mu0[-1]
    w1 = 1.0 - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu_t * w0 - mu0) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = -1
    return float(edges[int(np.argmax(between)) + 1])


def thresholds(vol: np.ndarray) -> tuple[float, float]:
    """(air|tissue, soft-tissue|bone) thresholds by two-level Otsu on stored values."""
    sample = vol[:: max(1, vol.shape[0] // 64), ::2, ::2].ravel()
    t_air = otsu(sample)
    above = sample[sample > t_air]
    t_bone = otsu(above) if above.size > 100 else float(sample.max())
    return t_air, t_bone


def _pixel_measures(ds: Dataset):
    """Pixel Spacing / slice spacing, also from an enhanced multi-frame object's functional groups."""
    src = ds
    if "PixelSpacing" not in ds:
        try:
            src = ds.SharedFunctionalGroupsSequence[0].PixelMeasuresSequence[0]
        except (AttributeError, IndexError, KeyError):
            try:
                src = ds.PerFrameFunctionalGroupsSequence[0].PixelMeasuresSequence[0]
            except (AttributeError, IndexError, KeyError):
                return None, None
    ps = [float(x) for x in src.PixelSpacing]
    thick = float(src.get("SpacingBetweenSlices", 0) or ds.get("SpacingBetweenSlices", 0)
                  or src.get("SliceThickness", 0) or ds.get("SliceThickness", 0) or ps[0])
    return ps, thick


def volume_fov_mm(dss: list[Dataset], n_slices: int) -> tuple[float, float, float] | None:
    ds = dss[0]
    try:
        ps, thick = _pixel_measures(ds)
        rows, cols = int(ds.Rows), int(ds.Columns)
    except Exception:
        return None
    if ps is None:
        return None
    return (round(cols * ps[1], 1), round(rows * ps[0], 1), round(n_slices * thick, 1))


def pixel_spacing_mm(ds: Dataset) -> float:
    try:
        ps, _ = _pixel_measures(ds)
        return float(ps[0]) if ps else 0.4
    except Exception:
        return 0.4


def assess_face_risk(vol: np.ndarray, dss: list[Dataset]) -> FaceRisk:
    fov = volume_fov_mm(dss, vol.shape[0])
    t_air, t_bone = thresholds(vol)
    soft = (vol > t_air) & (vol < t_bone)
    frac = float(soft.mean())
    if fov is None:
        return FaceRisk("unknown", None, round(frac, 4), "pixel spacing missing; cannot size the field of view")
    fx, fy, fz = fov
    if frac < 0.01:
        return FaceRisk("low", fov, round(frac, 4), "no appreciable soft-tissue volume")
    if min(fx, fy) >= 130 and fz >= 80:
        return FaceRisk("high", fov, round(frac, 4),
                        "large field of view with a soft-tissue surface: a 3D face can be rendered")
    if min(fx, fy) >= 80:
        return FaceRisk("moderate", fov, round(frac, 4),
                        "medium field of view: lips/nose/chin surface may be renderable")
    return FaceRisk("low", fov, round(frac, 4), "small (dento-alveolar) field of view")


def _erode6(mask: np.ndarray) -> np.ndarray:
    m = mask.copy()
    m[1:, :, :] &= mask[:-1, :, :]
    m[:-1, :, :] &= mask[1:, :, :]
    m[:, 1:, :] &= mask[:, :-1, :]
    m[:, :-1, :] &= mask[:, 1:, :]
    m[:, :, 1:] &= mask[:, :, :-1]
    m[:, :, :-1] &= mask[:, :, 1:]
    return m


def strip_soft_tissue(vol: np.ndarray) -> tuple[np.ndarray, dict]:
    t_air, t_bone = thresholds(vol)
    air = vol.min()
    out = vol.copy()
    sel = vol < t_bone
    out[sel] = air
    return out, {"mode": "strip-soft-tissue", "voxels_set_to_air": int(sel.sum() - (vol <= t_air).sum()),
                 "bone_threshold_stored": float(t_bone)}


def shell_soft_tissue(vol: np.ndarray, depth_vox: int) -> tuple[np.ndarray, dict]:
    t_air, t_bone = thresholds(vol)
    air = vol.min()
    head = vol > t_air
    inner = head
    for _ in range(max(1, depth_vox)):
        inner = _erode6(inner)
    layer = head & ~inner & (vol < t_bone)
    out = vol.copy()
    out[layer] = air
    return out, {"mode": "shell", "depth_voxels": int(depth_vox), "voxels_set_to_air": int(layer.sum())}
