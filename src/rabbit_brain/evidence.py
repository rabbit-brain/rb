"""Evidence rendering: what a flagged case looks like, as PNGs a human can open.

For a case, `render_case` re-runs both checkpoints on that case (the bundle stores numbers, not fields) and writes under
`rb-runs/<run>/evidence/<case>/`:

  inputs.png       the input image(s), when the adapter can read them
  flow.png         current | candidate | ground truth (when present), the standard flow colour wheel, one shared scale
  error.png        per-pixel error of current | candidate against ground truth (when present), one shared scale
  filmstrip.png    the candidate's per-iteration update magnitude, one tile per refinement iteration, then the current model's
  trajectory.png   both trajectories as a line plot, the flagged limits drawn in
  case.png         everything above stacked, with a caption line (the receipt's finding for the case)

Only numpy and pillow are needed (they come with the `raft` extra). Nothing here changes the numbers: the error and
trajectory values written next to the images are recomputed the same way the run computed them, and compared.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

from .errors import RBError
from .models import Bundle, Limits
from .recorder import TrajectoryRecorder


def _deps():
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RBError("E_EVIDENCE_DEPS", message=f"Evidence rendering needs numpy and pillow ({exc}).")
    return np, Image, ImageDraw


# ---------------------------------------------------------------- colour

def flow_to_rgb(flow, max_mag: Optional[float] = None):
    """Middlebury-style colour wheel: hue = direction, saturation = magnitude / max_mag. flow: (H, W, 2) float array."""
    np, _, _ = _deps()
    u, v = flow[..., 0].astype(np.float64), flow[..., 1].astype(np.float64)
    mag = np.sqrt(u * u + v * v)
    if max_mag is None or max_mag <= 0:
        max_mag = float(np.percentile(mag, 99)) if mag.size else 1.0
        max_mag = max(max_mag, 1e-6)
    sat = np.clip(mag / max_mag, 0.0, 1.0)
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


def heat_to_rgb(values, valid=None, vmax: Optional[float] = None):
    """Grey-to-red heat map for a non-negative field; invalid pixels black. values: (H, W)."""
    np, _, _ = _deps()
    x = np.asarray(values, dtype=np.float64)
    if vmax is None or vmax <= 0:
        pool = x[valid] if valid is not None and np.any(valid) else x
        vmax = max(float(np.percentile(pool, 99)) if pool.size else 1.0, 1e-6)
    s = np.clip(x / vmax, 0, 1)
    # anchors: dark blue-grey -> yellow -> red
    r = np.clip(np.where(s < 0.5, 0.25 + 1.5 * s, 1.0), 0, 1)
    g = np.clip(np.where(s < 0.5, 0.25 + 1.5 * s, 1.0 - 2.0 * (s - 0.5)), 0, 1)
    b = np.clip(0.35 - 0.7 * s, 0, 1)
    rgb = np.stack([r, g, b], axis=-1)
    if valid is not None:
        rgb = rgb * np.asarray(valid, dtype=bool)[..., None]
    return (rgb * 255).astype(np.uint8), float(vmax)


# ---------------------------------------------------------------- drawing helpers

def _ascii(text: str) -> str:
    """PIL's built-in font has no arrows or middle dots; keep captions in ASCII."""
    return text.replace("→", "->").replace("·", "|").replace("≤", "<=").replace("−", "-").encode("ascii", "replace").decode("ascii")


def _label(img, text: str):
    _, Image, ImageDraw = _deps()
    d = ImageDraw.Draw(img)
    w = d.textlength(text) if hasattr(d, "textlength") else 7 * len(text)
    d.rectangle([2, 2, 6 + w, 16], fill=(0, 0, 0))
    d.text((4, 3), text, fill=(255, 255, 255))
    return img


def _row(images, gap: int = 6, bg=(24, 24, 24)):
    _, Image, _ = _deps()
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


def _column(images, gap: int = 8, bg=(24, 24, 24)):
    _, Image, _ = _deps()
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


def _fit(img, width: int):
    if img.width <= width:
        return img
    _, Image, _ = _deps()
    return img.resize((width, max(1, round(img.height * width / img.width))), Image.BILINEAR)


def trajectory_plot(baseline: Optional[list[float]], candidate: Optional[list[float]], unit: str, limits: Limits, width: int = 720, height: int = 220):
    """Both trajectories on a log-free linear axis, the late window shaded, the quarter windows the paired rule reads marked."""
    np, Image, ImageDraw = _deps()
    img = Image.new("RGB", (width, height), (24, 24, 24))
    d = ImageDraw.Draw(img)
    series = [(s, name, col) for s, name, col in ((baseline, "current", (150, 150, 150)), (candidate, "candidate", (255, 170, 60))) if s]
    if not series:
        d.text((8, 8), "no trajectories", fill=(200, 200, 200))
        return img
    n = max(len(s) for s, _, _ in series)
    vmax = max(max(s) for s, _, _ in series) or 1.0
    left, right, top, bottom = 48, width - 12, 40, height - 28
    late_from = (n * 2) // 3
    quarter = max(1, n // 4)
    d.rectangle([left + (right - left) * late_from / max(n - 1, 1), top, right, bottom], fill=(38, 38, 38))
    d.rectangle([left + (right - left) * (n - quarter) / max(n - 1, 1), top, right, bottom], fill=(50, 44, 30))
    d.line([left, bottom, right, bottom], fill=(120, 120, 120))
    d.line([left, top, left, bottom], fill=(120, 120, 120))
    for s, name, col in series:
        pts = [(left + (right - left) * k / max(n - 1, 1), bottom - (bottom - top) * v / vmax) for k, v in enumerate(s)]
        d.line(pts, fill=col, width=2)
        for x, y in pts:
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=col)
    d.text((8, 6), _ascii(f"update per iteration ({unit})"), fill=(220, 220, 220))
    d.text((left, bottom + 6), "iteration 1", fill=(160, 160, 160))
    d.text((right - 70, bottom + 6), f"iteration {n}", fill=(160, 160, 160))
    d.text((8, top), f"{vmax:.2f}", fill=(160, 160, 160))
    d.text((8, bottom - 12), "0", fill=(160, 160, 160))
    x = 250
    for s, name, col in series:
        d.rectangle([x, 8, x + 10, 18], fill=col)
        d.text((x + 14, 6), _ascii(f"{name}: last quarter {sum(s[-quarter:]) / quarter:.3f} {unit}"), fill=(220, 220, 220))
        x += 230
    return img


def filmstrip(fields, scale_note: str, tile_width: int = 160, per_row: int = 6):
    """One tile per iteration: the update magnitude field (any 2-channel or scalar array), shared colour scale."""
    np, Image, _ = _deps()
    if not fields:
        return None
    mags = []
    for f in fields:
        a = np.asarray(f.detach().cpu().numpy() if hasattr(f, "detach") else f, dtype=np.float64)
        a = a.reshape(-1, *a.shape[-3:])[0] if a.ndim >= 4 else a
        if a.ndim == 3 and a.shape[0] == 2:
            a = np.sqrt((a ** 2).sum(axis=0))
        elif a.ndim == 3:
            a = np.abs(a).mean(axis=0)
        mags.append(a)
    vmax = max(float(np.percentile(m, 99)) for m in mags) or 1.0
    tiles = []
    for k, m in enumerate(mags):
        rgb, _ = heat_to_rgb(m, vmax=vmax)
        im = Image.fromarray(rgb)
        im = im.resize((tile_width, max(1, round(im.height * tile_width / im.width))), Image.NEAREST)
        tiles.append(_label(im, f"{k + 1}: {float(m.mean()):.3f}"))
    rows = [_row(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)]
    strip = _column(rows, gap=4)
    if strip is None:
        return None
    title = Image.new("RGB", (strip.width, 18), (24, 24, 24))
    _label(title, _ascii(f"{scale_note} (tile label: iteration: mean {float(np.mean([m.mean() for m in mags])):.3f} average)"))
    return _column([title, strip], gap=2)


# ---------------------------------------------------------------- the case

def render_case(cfg, bundle: Bundle, case_id: str, run_dir: Path, device: Optional[str] = None, models: Optional[dict] = None) -> dict:
    """Re-run both checkpoints on one case and write the evidence PNGs. Returns {"dir": ..., "files": [...], "check": {...}}.
    `models` may carry already-loaded models keyed "baseline"/"candidate" to avoid reloading during `rb run`."""
    np, Image, ImageDraw = _deps()
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

    # inputs
    images = adapter.read_images(case) if hasattr(adapter, "read_images") else None
    if images:
        ims = [Image.fromarray(np.asarray(im).astype(np.uint8)) for im in images]
        row = _row([_label(_fit(im, 640), f"input {i + 1}") for i, im in enumerate(ims)])
        row.save(out_dir / "inputs.png"); files.append("inputs.png"); panels.append(row)

    # flow fields, shared scale, ground truth when present
    gt = adapter.read_gt(case) if hasattr(adapter, "read_gt") and case.gt is not None else None
    fields = {}
    for role in ("baseline", "candidate"):
        out = preds[role].output
        fields[role] = None
        if hasattr(out, "detach") or hasattr(out, "shape"):
            arr = np.asarray(out.detach().cpu().numpy() if hasattr(out, "detach") else out, dtype=np.float32)
            if arr.ndim == 3 and arr.shape[0] == 2:
                arr = np.transpose(arr, (1, 2, 0))
            if arr.ndim == 3 and arr.shape[-1] == 2:
                fields[role] = arr
    if fields["candidate"] is not None:
        pool = [fields[r] for r in ("baseline", "candidate") if fields[r] is not None] + ([gt[0]] if gt else [])
        max_mag = max(float(np.percentile(np.sqrt((f ** 2).sum(-1)), 99)) for f in pool) or 1.0
        tiles = []
        for name, f in (("current", fields["baseline"]), ("candidate", fields["candidate"]), ("ground truth", gt[0] if gt else None)):
            if f is None:
                continue
            rgb, _ = flow_to_rgb(f, max_mag)
            tiles.append(_label(_fit(Image.fromarray(rgb), 640), f"{name} flow (scale {max_mag:.1f} {unit})"))
        row = _row(tiles); row.save(out_dir / "flow.png"); files.append("flow.png"); panels.append(row)
        if gt is not None:
            gflow, valid = gt
            errs = {r: np.sqrt(((fields[r] - gflow) ** 2).sum(-1)) for r in ("baseline", "candidate") if fields[r] is not None}
            vmax = max(float(np.percentile(e[valid], 99)) for e in errs.values()) or 1.0
            tiles = []
            for name, r in (("current", "baseline"), ("candidate", "candidate")):
                rgb, _ = heat_to_rgb(errs[r], valid, vmax)
                mean_err = float(errs[r][valid].mean())
                tiles.append(_label(_fit(Image.fromarray(rgb), 640), f"{name} error, mean {mean_err:.2f} {unit} (scale {vmax:.1f})"))
            row = _row(tiles); row.save(out_dir / "error.png"); files.append("error.png"); panels.append(row)

    # filmstrips of the update fields
    strips = []
    for name, role in (("candidate", "candidate"), ("current", "baseline")):
        strip = filmstrip(recs[role]._fields, f"{name}: update magnitude per iteration ({unit}), shared scale")
        if strip is not None:
            strips.append(strip)
    if strips:
        col = _column(strips); col.save(out_dir / "filmstrip.png"); files.append("filmstrip.png"); panels.append(col)

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
    caption = _ascii(f"{bundle.run_id} | {case.name} ({case.id}) | {', '.join(case_v2.flags) or 'no flags'}")
    err_line = (f"error {to_fixed(case_v2.baseline_error)} -> {to_fixed(case_v2.candidate_error)} {unit}" if case_v2.candidate_error is not None else "error not measured")
    repro = "reproduced" if (check["errors_match"] and check["candidate_trajectory_matches"]) else "DIFFERS from the run"
    text_img = Image.new("RGB", (max(p.width for p in panels), 44), (24, 24, 24))
    d = ImageDraw.Draw(text_img)
    d.text((8, 6), caption, fill=(255, 255, 255))
    d.text((8, 24), _ascii(f"{err_line} | {why(case_v2, limits, unit)[:150]} | re-run now: {repro}"), fill=(200, 200, 200))
    composite = _column([text_img, *panels])
    composite.save(out_dir / "case.png"); files.append("case.png")
    (out_dir / "README.md").write_text(
        f"# Evidence for {case.id}\n\n{caption}\n\n{err_line}. {why(case_v2, limits, unit)}\n\n"
        f"Re-run now: errors {'match' if check['errors_match'] else 'differ'} the run (current {errors['baseline']}, candidate {errors['candidate']}), "
        f"candidate trajectory {'matches' if check['candidate_trajectory_matches'] else 'differs'}.\n\nFiles: {', '.join(files)}.\n", encoding="utf-8")
    return {"dir": str(out_dir), "files": files, "check": check}
