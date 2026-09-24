"""Evidence rendering: what a flagged case looks like, as PNGs a human can open.

For a case, `render_case` re-runs both checkpoints on that case (the bundle stores numbers, not fields) and writes under
`rb-runs/<run>/evidence/<case>/`:

  inputs.png       the input image(s), and where the two models disagree (|candidate - current| per pixel, label-free)
  flow.png         current | candidate | ground truth (when present): the standard flow colour wheel, one shared scale
  error.png        per-pixel error of current | candidate against ground truth, and the change (red = worse, blue = better)
  filmstrip.png    one tile per refinement iteration (the update size per pixel), candidate then current, one colour scale
  trajectory.png   both trajectories on a log axis, the last quarter (the window the paired rule reads) shaded
  case.png         everything above stacked, with the finding as caption and the reproduction check

Only numpy and pillow are needed (they come with the `raft` extra). Nothing here changes the numbers: the error and
trajectory values written next to the images are recomputed the same way the run computed them, and compared.

Scales are chosen to show structure, not extremes: flow colour saturates at the 95th percentile of the magnitudes with
square-root saturation (a near object saturates, the rest keeps its colour); error, disagreement and filmstrip maps run to
the 99th percentile capped at four times the mean (what is above that saturates, and saturation is itself the finding); the
filmstrip's scale is shared by both models and comes from their late iterations, so what is still moving at the end is
visible. Every label says the scale it used.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

from .errors import RBError
from .models import Bundle, Limits
from .recorder import TrajectoryRecorder

WIDTH = 1920                       # composite width; three tiles per row
GAP = 12
TILE = (WIDTH - 2 * GAP) // 3      # 632
BG = (22, 22, 22)
ORANGE = (255, 170, 60)
GREY = (150, 150, 150)


def _deps():
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RBError("E_EVIDENCE_DEPS", message=f"Evidence rendering needs numpy and pillow ({exc}).")
    return np, Image, ImageDraw, ImageFont


_FONTS: dict = {}


def _font(size: int):
    if size not in _FONTS:
        _, _, _, ImageFont = _deps()
        try:
            _FONTS[size] = ImageFont.load_default(size=size)   # pillow >= 10.1 ships a scalable default
        except TypeError:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


# ---------------------------------------------------------------- colour

def flow_to_rgb(flow, max_mag: Optional[float] = None):
    """Middlebury-style colour wheel: hue = direction, saturation = sqrt(magnitude / max_mag) (clipped; the square root keeps
    slow regions coloured when a near object sets the scale). flow: (H, W, 2)."""
    np, _, _, _ = _deps()
    u, v = flow[..., 0].astype(np.float64), flow[..., 1].astype(np.float64)
    mag = np.sqrt(u * u + v * v)
    if max_mag is None or max_mag <= 0:
        max_mag = max(float(np.percentile(mag, 95)) if mag.size else 1.0, 1e-6)
    sat = np.sqrt(np.clip(mag / max_mag, 0.0, 1.0))
    hue = (np.arctan2(-v, -u) / np.pi + 1.0) / 2.0  # 0..1
    h6 = hue * 6.0
    i = np.floor(h6).astype(int) % 6
    f = h6 - np.floor(h6)
    p = 1.0 - sat
    q = 1.0 - sat * f
    t = 1.0 - sat * (1.0 - f)
    one = np.ones_like(sat)
    r = np.choose(i, [one, q, p, p, t, one])
    g = np.choose(i, [t, one, one, q, p, p])
    b = np.choose(i, [p, p, t, one, one, q])
    rgb = np.stack([r, g, b], axis=-1)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8), float(max_mag)


def _heat(s):
    """Colour ramp for s in 0..1: dark blue-grey -> yellow -> red. Returns float 0..1 (H, W, 3)."""
    np, _, _, _ = _deps()
    r = np.clip(np.where(s < 0.5, 0.25 + 1.5 * s, 1.0), 0, 1)
    g = np.clip(np.where(s < 0.5, 0.25 + 1.5 * s, 1.0 - 2.0 * (s - 0.5)), 0, 1)
    b = np.clip(0.35 - 0.7 * s, 0, 1)
    return np.stack([r, g, b], axis=-1)


def heat_to_rgb(values, valid=None, vmax: Optional[float] = None, base=None):
    """Heat map of a non-negative field. Invalid pixels show `base` (a dimmed image, float 0..255 (H, W, 3)) or black."""
    np, _, _, _ = _deps()
    x = np.asarray(values, dtype=np.float64)
    if vmax is None or vmax <= 0:
        pool = x[valid] if valid is not None and np.any(valid) else x
        vmax = max(float(np.percentile(pool, 99)) if pool.size else 1.0, 1e-6)
    rgb = _heat(np.clip(x / vmax, 0, 1)) * 255
    if base is None:
        base = np.zeros_like(rgb)
    if valid is not None:
        rgb = np.where(np.asarray(valid, dtype=bool)[..., None], rgb, base)
    return np.clip(rgb, 0, 255).astype(np.uint8), float(vmax)


def heat_overlay(values, vmax: float, valid=None, base=None):
    """A non-negative field over a dimmed image: transparent at zero, yellow then red towards vmax. Invalid pixels show the image."""
    np, _, _, _ = _deps()
    x = np.asarray(values, dtype=np.float64)
    s = np.clip(x / max(vmax, 1e-6), 0, 1)
    a = (s ** 0.6)[..., None]
    yellow, red = np.array([255.0, 225.0, 40.0]), np.array([255.0, 40.0, 30.0])
    col = yellow * (1 - s[..., None]) + red * s[..., None]
    if base is None:
        base = np.zeros(x.shape + (3,))
    rgb = base * (1 - a) + col * a
    if valid is not None:
        rgb = np.where(np.asarray(valid, dtype=bool)[..., None], rgb, base)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def diverging_to_rgb(values, vmax: float, valid=None, base=None):
    """Signed field over a dimmed image: red where positive (worse), blue where negative (better), transparent near zero."""
    np, _, _, _ = _deps()
    x = np.asarray(values, dtype=np.float64)
    s = np.clip(x / max(vmax, 1e-6), -1, 1)
    a = np.abs(s)[..., None]
    red, blue = np.array([255.0, 60.0, 40.0]), np.array([70.0, 130.0, 255.0])
    col = np.where((s > 0)[..., None], red, blue)
    if base is None:
        base = np.zeros(x.shape + (3,))
    rgb = base * (1 - a) + col * a
    if valid is not None:
        rgb = np.where(np.asarray(valid, dtype=bool)[..., None], rgb, base)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _dim(image, factor: float = 0.35):
    """A greyscale, dimmed copy of an RGB uint8 image as float (H, W, 3), the backdrop for overlays."""
    np, _, _, _ = _deps()
    img = np.asarray(image, dtype=np.float64)
    grey = img.mean(axis=-1) if img.ndim == 3 else img
    return np.repeat((grey * factor)[..., None], 3, axis=-1)


def _heat_scale(fields, valid=None) -> float:
    """Shared vmax for error-like maps: the 99th percentile, capped at four times the largest mean, never below the 90th."""
    np, _, _, _ = _deps()
    pools = [np.asarray(f, dtype=np.float64)[valid] if valid is not None else np.asarray(f, dtype=np.float64).ravel() for f in fields]
    pools = [p for p in pools if p.size]
    if not pools:
        return 1.0
    p99 = max(float(np.percentile(p, 99)) for p in pools)
    p90 = max(float(np.percentile(p, 90)) for p in pools)
    mean = max(float(p.mean()) for p in pools)
    return max(min(p99, 4.0 * mean), p90, 1e-6)


def _ramp(kind: str, width: int = 72, height: int = 10):
    np, Image, _, _ = _deps()
    if kind == "diverging":
        x = np.linspace(-1, 1, width)[None, :].repeat(height, 0)
        rgb = diverging_to_rgb(x, 1.0, base=np.full((height, width, 3), 60.0))
    elif kind == "overlay":
        x = np.linspace(0, 1, width)[None, :].repeat(height, 0)
        rgb = heat_overlay(x, 1.0, base=np.full((height, width, 3), 60.0))
    else:
        x = np.linspace(0, 1, width)[None, :].repeat(height, 0)
        rgb, _ = heat_to_rgb(x, vmax=1.0)
    return Image.fromarray(rgb)


# ---------------------------------------------------------------- drawing helpers

def _ascii(text: str) -> str:
    """Keep captions in ASCII; the bundled font has no arrows or middle dots."""
    return text.replace("→", "->").replace("·", "|").replace("≤", "<=").replace("−", "-").encode("ascii", "replace").decode("ascii")


def _label(img, text: str, size: int = 15, ramp: Optional[str] = None):
    """A caption box in the top-left corner of a tile, optionally followed by a colour ramp."""
    _, Image, ImageDraw, _ = _deps()
    d = ImageDraw.Draw(img, "RGBA")
    f = _font(size)
    lines = _wrap(d, _ascii(text), f, img.width - 10 - (84 if ramp else 0))
    w = max(d.textlength(line, font=f) for line in lines)
    line_h = size + 5
    h = line_h * len(lines) + 3
    d.rectangle([0, 0, w + 10 + (84 if ramp else 0), h], fill=(0, 0, 0, 205))
    for i, line in enumerate(lines):
        d.text((5, 3 + i * line_h), line, font=f, fill=(255, 255, 255))
    if ramp:
        img.paste(_ramp(ramp, 72, size - 4), (int(w) + 12, 6))
    return img


def _wrap(d, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if not cur or d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _row(images, gap: int = GAP, bg=BG):
    _, Image, _, _ = _deps()
    images = [im for im in images if im is not None]
    if not images:
        return None
    h = max(im.height for im in images)
    w = sum(im.width for im in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), bg)
    x = 0
    for im in images:
        out.paste(im, (x, 0))
        x += im.width + gap
    return out


def _column(images, gap: int = 10, bg=BG):
    _, Image, _, _ = _deps()
    images = [im for im in images if im is not None]
    if not images:
        return None
    w = max(im.width for im in images)
    h = sum(im.height for im in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), bg)
    y = 0
    for im in images:
        out.paste(im, (0, y))
        y += im.height + gap
    return out


def _fit(img, width: int = TILE, nearest: bool = False):
    """Resize to exactly `width` wide (down or up), keeping the aspect ratio."""
    _, Image, _, _ = _deps()
    if img.width == width:
        return img
    return img.resize((width, max(1, round(img.height * width / img.width))), Image.NEAREST if nearest else Image.BILINEAR)


def _magnitude(field):
    """(H, W) float64 magnitude of a 2-channel field in (2, H, W) or (H, W, 2) layout (or a scalar field), tensor or array."""
    np, _, _, _ = _deps()
    a = np.asarray(field.detach().cpu().numpy() if hasattr(field, "detach") else field, dtype=np.float64)
    while a.ndim > 3:
        a = a[0]
    if a.ndim == 3 and a.shape[0] == 2:
        return np.sqrt((a ** 2).sum(axis=0))
    if a.ndim == 3 and a.shape[-1] == 2:
        return np.sqrt((a ** 2).sum(axis=-1))
    if a.ndim == 3:
        return np.abs(a).mean(axis=0)
    return np.abs(a)


def _to_hw2(out):
    """A prediction output as an (H, W, 2) float32 array, or None when it is not a 2-channel field."""
    np, _, _, _ = _deps()
    if not (hasattr(out, "detach") or hasattr(out, "shape")):
        return None
    arr = np.asarray(out.detach().cpu().numpy() if hasattr(out, "detach") else out, dtype=np.float32)
    while arr.ndim > 3:
        arr = arr[0]
    if arr.ndim == 3 and arr.shape[0] == 2:
        arr = np.transpose(arr, (1, 2, 0))
    return arr if arr.ndim == 3 and arr.shape[-1] == 2 else None


# ---------------------------------------------------------------- plots

def trajectory_plot(baseline: Optional[list[float]], candidate: Optional[list[float]], unit: str, limits: Limits, width: int = WIDTH, height: int = 300):
    """Both trajectories on a log axis, the last quarter (the window `late movement` reads) shaded, the last-update limit drawn when set."""
    np, Image, ImageDraw, _ = _deps()
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    f, fs = _font(15), _font(13)
    series = [(s, name, col) for s, name, col in ((baseline, "current", GREY), (candidate, "candidate", ORANGE)) if s]
    if not series:
        d.text((8, 8), "no trajectories", font=f, fill=(200, 200, 200))
        return img
    n = max(len(s) for s, _, _ in series)
    quarter = max(1, n // 4)
    vals = [v for s, _, _ in series for v in s]
    vmax = max(max(vals), 1e-6)
    positive = [v for v in vals if v > 0]
    vmin = max(min(positive) if positive else vmax * 1e-3, vmax * 1e-4)
    if limits.max_last_update:
        vmin = min(vmin, limits.max_last_update)
    lo, hi = math.log10(vmin) - 0.15, math.log10(vmax) + 0.15
    if hi - lo < 1:
        hi = lo + 1
    left, right, top, bottom = 84, width - 24, 44, height - 34

    def xpos(k: int) -> float:
        return left + (right - left) * k / max(n - 1, 1)

    def ypos(v: float) -> float:
        return bottom - (bottom - top) * (math.log10(max(v, vmin)) - lo) / (hi - lo)

    # the last quarter of the iterations
    d.rectangle([xpos(n - quarter) - (right - left) / max(n - 1, 1) / 2, top, right, bottom], fill=(44, 38, 26))
    d.text((min(xpos(n - quarter), right - 90), top + 4), "last quarter", font=fs, fill=(200, 170, 110))
    # axes and ticks
    for k in range(math.ceil(lo), math.floor(hi) + 1):
        y = ypos(10.0 ** k)
        d.line([left, y, right, y], fill=(48, 48, 48))
        d.text((10, y - 8), _ascii(f"{10.0 ** k:g} {unit}"), font=fs, fill=(160, 160, 160))
    for k in range(n):
        x = xpos(k)
        d.line([x, bottom, x, bottom + 4], fill=(120, 120, 120))
        if n <= 24 or k % max(1, n // 12) == 0 or k == n - 1:
            d.text((x - 4, bottom + 8), "iteration 1" if k == 0 else str(k + 1), font=fs, fill=(160, 160, 160))
    d.line([left, bottom, right, bottom], fill=(120, 120, 120))
    d.line([left, top, left, bottom], fill=(120, 120, 120))
    if limits.max_last_update:
        y = ypos(limits.max_last_update)
        for x in range(left, right, 12):
            d.line([x, y, x + 6, y], fill=(220, 90, 90))
        d.text((left + 8, y - 18), _ascii(f"max_last_update = {limits.max_last_update:g} {unit}"), font=fs, fill=(220, 120, 120))
    # the series
    for s, name, col in series:
        pts = [(xpos(k), ypos(v)) for k, v in enumerate(s)]
        d.line(pts, fill=col, width=2)
        for x, y in pts:
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=col)
    # legend
    x = 10
    d.text((x, 10), _ascii(f"update per iteration ({unit}, log scale)"), font=f, fill=(220, 220, 220))
    x += d.textlength(f"update per iteration ({unit}, log scale)", font=f) + 40
    late = {}
    for s, name, col in series:
        late[name] = sum(s[-quarter:]) / quarter
        d.rectangle([x, 13, x + 12, 25], fill=col)
        text = _ascii(f"{name}: late movement {late[name]:.3f} {unit} per iteration")
        d.text((x + 18, 10), text, font=f, fill=(220, 220, 220))
        x += d.textlength(text, font=f) + 44
    if "current" in late and "candidate" in late:
        diff = late["candidate"] - late["current"]
        text = f"candidate - current: {diff:+.3f} {unit}"
        if limits.max_trajectory_regression is not None:
            text += f" (limit +{limits.max_trajectory_regression:g})"
        d.text((x, 10), _ascii(text), font=f, fill=ORANGE if (limits.max_trajectory_regression is not None and diff > limits.max_trajectory_regression) else (220, 220, 220))
    return img


def filmstrip(fields, title: str, unit: str, vmax: Optional[float] = None, per_row: int = 6, width: int = WIDTH):
    """One tile per iteration: the update size per pixel, one colour scale (given, or from the late iterations of this strip).
    The tiles of the last quarter get an orange frame: that is the window `late movement` averages."""
    np, Image, ImageDraw, _ = _deps()
    if not fields:
        return None
    mags = [_magnitude(f) for f in fields]
    n = len(mags)
    if vmax is None:
        vmax = filmstrip_scale([mags])
    quarter = max(1, n // 4)
    tile_w = (width - GAP * (per_row - 1)) // per_row
    tiles = []
    for k, m in enumerate(mags):
        rgb, _ = heat_to_rgb(m, vmax=vmax)
        im = _fit(Image.fromarray(rgb), tile_w, nearest=True)
        _label(im, f"iteration {k + 1}: {float(m.mean()):.3f} {unit}", size=14)
        if k >= n - quarter:
            ImageDraw.Draw(im).rectangle([0, 0, im.width - 1, im.height - 1], outline=ORANGE, width=2)
        tiles.append(im)
    rows = [_row(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)]
    strip = _column(rows, gap=6)
    head = Image.new("RGB", (strip.width, 26), BG)
    ImageDraw.Draw(head).text((0, 4), _ascii(f"{title}: update size per pixel, iteration 1 to {n}, colour 0 to {vmax:.2f} {unit} (early tiles saturate; orange frame = last quarter)"), font=_font(15), fill=(230, 230, 230))
    return _column([head, strip], gap=4)


def filmstrip_scale(strips: list) -> float:
    """A colour scale from the second half of the iterations of every strip (list of lists of magnitude arrays), so late movement is
    visible: the 99th percentile of those tiles, capped at four times their largest mean (a region the model keeps moving saturates)."""
    np, _, _, _ = _deps()
    late = [m for mags in strips for m in mags[len(mags) // 2:]] or [m for mags in strips for m in mags]
    if not late:
        return 1.0
    return _heat_scale(late)


# ---------------------------------------------------------------- the case

def render_case(cfg, bundle: Bundle, case_id: str, run_dir: Path, device: Optional[str] = None, models: Optional[dict] = None) -> dict:
    """Re-run both checkpoints on one case and write the evidence PNGs. Returns {"dir": ..., "files": [...], "check": {...}}.
    `models` may carry already-loaded models keyed "baseline"/"candidate" to avoid reloading during `rb review run`."""
    np, Image, ImageDraw, _ = _deps()
    from .adapters import load_adapter
    from .fmt import to_fixed

    case_v2 = next((c for c in bundle.cases if c.id == case_id), None)
    if case_v2 is None:
        raise RBError("E_CASE_NOT_FOUND", message=f"Case '{case_id}' is not in run {bundle.run_id}.")
    adapter = load_adapter(cfg)
    device = device or cfg.adapter.device
    case = next((c for c in adapter.cases() if c.id == case_id), None)
    if case is None:
        raise RBError("E_CASE_NOT_FOUND", message=f"Case '{case_id}' is not in the adapter's case set any more (dataset changed?).")
    scale = float(getattr(adapter, "trajectory_scale", 1.0) or 1.0)
    out_dir = run_dir / "evidence" / case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    unit = bundle.metric.unit
    limits = bundle.limits

    preds, recs, errors = {}, {}, {}
    for role, ref in (("baseline", bundle.baseline), ("candidate", bundle.candidate)):
        model = (models or {}).get(role)
        own = model is None
        if own:
            if not ref.checkpoint:
                raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"The run's record has no {role} checkpoint path; evidence needs the checkpoints.")
            model = adapter.load(Path(ref.checkpoint), device)
        rec = TrajectoryRecorder(scale=scale)
        pred = adapter.infer(model, case, rec)
        preds[role], recs[role] = pred, rec
        errors[role] = adapter.metric_value(pred, case)
        if own:
            del model

    files: list[str] = []
    panels = []
    fields = {role: _to_hw2(preds[role].output) for role in ("baseline", "candidate")}
    both = fields["baseline"] is not None and fields["candidate"] is not None and fields["baseline"].shape == fields["candidate"].shape
    images = adapter.read_images(case) if hasattr(adapter, "read_images") else None
    images = [np.asarray(im).astype(np.uint8) for im in images] if images else []
    backdrop = None
    if images and both and images[0].shape[:2] == fields["candidate"].shape[:2]:
        backdrop = _dim(images[0])

    # inputs, and where the two models disagree (needs no ground truth)
    tiles = [_label(_fit(Image.fromarray(im)), f"input {i + 1}") for i, im in enumerate(images)]
    if both:
        disagreement = np.sqrt(((fields["candidate"] - fields["baseline"]) ** 2).sum(-1))
        vmax = _heat_scale([disagreement])
        rgb = heat_overlay(disagreement, vmax, base=backdrop)
        tiles.append(_label(_fit(Image.fromarray(rgb)), f"where the models disagree: mean {float(disagreement.mean()):.2f} {unit}, colour to {vmax:.1f}", ramp="overlay"))
    if tiles:
        row = _row(tiles); row.save(out_dir / "inputs.png"); files.append("inputs.png"); panels.append(row)

    # flow fields on one colour scale, ground truth when present
    gt = adapter.read_gt(case) if hasattr(adapter, "read_gt") and case.gt is not None else None
    if gt is not None and (fields["candidate"] is None or gt[0].shape != fields["candidate"].shape):
        gt = None
    if fields["candidate"] is not None:
        pool = [np.sqrt((f ** 2).sum(-1)).ravel() for f in (fields["baseline"], fields["candidate"]) if f is not None]
        if gt is not None and np.any(gt[1]):
            pool.append(np.sqrt((gt[0] ** 2).sum(-1))[gt[1]])
        max_mag = max(float(np.percentile(np.concatenate(pool), 95)), 1e-6)
        tiles = []
        for name, f in (("current", fields["baseline"]), ("candidate", fields["candidate"])):
            if f is not None:
                rgb, _ = flow_to_rgb(f, max_mag)
                tiles.append(_label(_fit(Image.fromarray(rgb)), f"{name} flow: hue = direction, full colour at {max_mag:.1f} {unit} (square-root scale)"))
        if gt is not None:
            gflow, valid = gt
            rgb, _ = flow_to_rgb(gflow, max_mag)
            rgb = np.where(valid[..., None], rgb, np.uint8(40))
            tiles.append(_label(_fit(Image.fromarray(rgb)), f"ground truth flow, same scale, {100 * valid.mean():.0f}% of pixels labeled"))
        row = _row(tiles); row.save(out_dir / "flow.png"); files.append("flow.png"); panels.append(row)

        # error maps and the change in error
        if gt is not None and both and np.any(gt[1]):
            gflow, valid = gt
            errs = {r: np.sqrt(((fields[r] - gflow) ** 2).sum(-1)) for r in ("baseline", "candidate")}
            vmax = _heat_scale(list(errs.values()), valid)
            tiles = []
            for name, r in (("current", "baseline"), ("candidate", "candidate")):
                rgb = heat_overlay(errs[r], vmax, valid, base=backdrop)
                tiles.append(_label(_fit(Image.fromarray(rgb)), f"{name} error: mean {float(errs[r][valid].mean()):.2f} {unit}, colour to {vmax:.1f}", ramp="overlay"))
            change = errs["candidate"] - errs["baseline"]
            cmax = max(float(np.percentile(np.abs(change[valid]), 95)) if np.any(valid) else 1.0, 1e-6)
            rgb = diverging_to_rgb(change, cmax, valid, base=backdrop if backdrop is not None else np.full(change.shape + (3,), 60.0))
            mean_change = float(change[valid].mean()) if np.any(valid) else 0.0
            tiles.append(_label(_fit(Image.fromarray(rgb)), f"error change: mean {mean_change:+.2f} {unit}, red = candidate worse, blue = better, colour to {cmax:.1f}", ramp="diverging"))
            row = _row(tiles); row.save(out_dir / "error.png"); files.append("error.png"); panels.append(row)

    # filmstrips of the update fields, one colour scale for both models
    strips_mags = [[_magnitude(f) for f in recs[r]._fields] for r in ("candidate", "baseline") if recs[r]._fields]
    if strips_mags:
        vmax = filmstrip_scale(strips_mags)
        strips = [filmstrip(recs[role]._fields, name, unit, vmax) for name, role in (("candidate", "candidate"), ("current", "baseline"))]
        col = _column(strips, gap=14)
        if col is not None:
            col.save(out_dir / "filmstrip.png"); files.append("filmstrip.png"); panels.append(col)

    # trajectories as recorded now (and compared with the run below)
    plot = trajectory_plot(recs["baseline"].values, recs["candidate"].values, unit, limits)
    plot.save(out_dir / "trajectory.png"); files.append("trajectory.png"); panels.append(plot)

    # reproduction check: the numbers rendered now against the numbers the run recorded
    def close(a, b, tol):
        return a is None or b is None or abs(a - b) <= tol
    check = {
        "baseline_error": {"run": case_v2.baseline_error, "now": errors["baseline"]},
        "candidate_error": {"run": case_v2.candidate_error, "now": errors["candidate"]},
        "candidate_trajectory_matches": (case_v2.candidate_trajectory is None) or (len(recs["candidate"].values) == len(case_v2.candidate_trajectory)
                                         and all(abs(a - b) <= 1e-3 * max(1.0, abs(b)) for a, b in zip(recs["candidate"].values, case_v2.candidate_trajectory))),
    }
    check["errors_match"] = close(errors["baseline"], case_v2.baseline_error, 1e-3 * max(1.0, abs(case_v2.baseline_error or 0))) and \
        close(errors["candidate"], case_v2.candidate_error, 1e-3 * max(1.0, abs(case_v2.candidate_error or 0)))

    # composite with the caption
    from .prose import why
    ident = case.id if case.name in (None, "", case.id) else f"{case.id} ({case.name})"
    caption = _ascii(f"{bundle.run_id} | {ident} | {', '.join(case_v2.flags) or 'no flags'}")
    err_line = (f"error {to_fixed(case_v2.baseline_error)} -> {to_fixed(case_v2.candidate_error)} {unit}" if case_v2.candidate_error is not None else "error not measured")
    reason = why(case_v2, limits, unit)
    if check["errors_match"] and check["candidate_trajectory_matches"]:
        repro = "Re-run now: reproduced the run's numbers."
    else:
        repro = _ascii(f"Re-run now: DIFFERS from the run (current {errors['baseline']} vs {case_v2.baseline_error}, candidate {errors['candidate']} vs {case_v2.candidate_error}).")
    width = max(p.width for p in panels)
    f_title, f_body = _font(19), _font(15)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    body = _wrap(probe, _ascii(f"{err_line}. {reason}"), f_body, width - 24)
    text_img = Image.new("RGB", (width, 12 + 26 + 21 * (len(body) + 1) + 8), BG)
    d = ImageDraw.Draw(text_img)
    d.text((12, 8), caption, font=f_title, fill=(255, 255, 255))
    y = 38
    for line in body:
        d.text((12, y), line, font=f_body, fill=(215, 215, 215)); y += 21
    d.text((12, y), repro, font=f_body, fill=(140, 220, 140) if repro.startswith("Re-run now: reproduced") else (255, 120, 120))
    composite = _column([text_img, *panels])
    composite.save(out_dir / "case.png"); files.append("case.png")
    (out_dir / "README.md").write_text(
        f"# Evidence for {case.id}\n\n{caption}\n\n{err_line}. {reason}\n\n{repro}\n\n"
        f"Scales: flow colour saturates at the 95th percentile of the flow magnitudes, with square-root saturation so slow regions keep their colour; "
        f"error, disagreement and filmstrip maps run to the 99th percentile of their values, capped at four times the mean (regions above that saturate); "
        f"the filmstrip's scale is shared by both models and comes from their late iterations; the trajectory axis is logarithmic.\n\nFiles: {', '.join(files)}.\n", encoding="utf-8")
    return {"dir": str(out_dir), "files": files, "check": check}
