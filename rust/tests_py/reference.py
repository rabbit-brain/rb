"""Reference implementations the differential tests hold the binary to (diff_stats.py, diff_pyyaml.py).

Each is the Python the binary's own code was written from, kept here as a second, independent implementation: the
statistics behind a finding (the t distribution, the interval of a mean and of a Welch difference), and how a
project's YAML config is read (PyYAML's safe loader with rb's two changes, and which line holds which key). Nothing in
rb runs these; they change only when the rule they state changes, together with the binary."""
from __future__ import annotations

import math
import re
import statistics
from typing import Any, Optional

# ---------------------------------------------------------------- the statistics behind a finding

MIN_UNITS_FOR_INTERVAL = 3


# ---------------------------------------------------------------- Student t, without scipy


def _betacf(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        step = d * c
        h *= step
        if abs(step - 1.0) < 1e-15:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    tail = 0.5 * _betai(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    """The quantile of Student's t: the t with P(T <= t) = p, for p above 0.5."""
    if not 0.5 < p < 1.0:
        raise ValueError("t_ppf takes p in (0.5, 1)")
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p and hi < 1e12:
        lo, hi = hi, hi * 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13 * max(1.0, hi):
            break
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- intervals


def mean_interval(values: list[float], level: float = 0.95) -> tuple[float, Optional[float], Optional[float], str]:
    """(mean, lo, hi, why there is no interval): a t interval over independent units, from 3 of them."""
    n = len(values)
    est = statistics.fmean(values)
    if n < MIN_UNITS_FOR_INTERVAL:
        return est, None, None, f"{n} unit{'' if n == 1 else 's'}: rb gives an interval from {MIN_UNITS_FOR_INTERVAL}"
    sd = statistics.stdev(values)
    if sd == 0.0:
        return est, est, est, ""
    half = t_ppf(1.0 - (1.0 - level) / 2.0, n - 1) * sd / math.sqrt(n)
    return est, est - half, est + half, ""


def welch_interval(xs: list[float], ys: list[float], level: float = 0.95) -> tuple[float, Optional[float], Optional[float], str]:
    """(mean(xs) - mean(ys), lo, hi, why there is no interval): Welch's interval for two arms never measured on the
    same unit, from 3 units in each."""
    est = statistics.fmean(xs) - statistics.fmean(ys)
    if len(xs) < MIN_UNITS_FOR_INTERVAL or len(ys) < MIN_UNITS_FOR_INTERVAL:
        return est, None, None, (f"{len(xs)} and {len(ys)} units in the two arms, never paired: rb gives an unpaired interval "
                                 f"from {MIN_UNITS_FOR_INTERVAL} in each")
    vx, vy = statistics.variance(xs) / len(xs), statistics.variance(ys) / len(ys)
    se = math.sqrt(vx + vy)
    if se == 0.0:
        return est, est, est, ""
    df = (vx + vy) ** 2 / ((vx ** 2) / (len(xs) - 1) + (vy ** 2) / (len(ys) - 1))
    half = t_ppf(1.0 - (1.0 - level) / 2.0, df) * se
    return est, est - half, est + half, ""


# ---------------------------------------------------------------- the result: what the reading shows


# ---------------------------------------------------------------- reading a project's YAML config

_LOADER: Any = None


def _yaml_loader() -> Any:
    """PyYAML's safe loader, reading numbers the way YAML 1.2 and every training config mean them: `1e-4` is a float
    (YAML 1.1 wants a dot), and a date stays the text it is."""
    global _LOADER
    if _LOADER is None:
        import yaml

        class Loader(yaml.SafeLoader):
            pass

        Loader.yaml_implicit_resolvers = {ch: [(tag, rx) for tag, rx in rs if tag != "tag:yaml.org,2002:timestamp"]
                                          for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()}
        Loader.add_implicit_resolver("tag:yaml.org,2002:float", re.compile(r"^[-+]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)[eE][-+]?[0-9]+$"),
                                     list("-+0123456789."))
        _LOADER = Loader
    return _LOADER


def key_steps(key: str) -> list[str]:
    """`optim.lr` or `/optim/lr` (a JSON pointer, for keys that themselves contain dots)."""
    if key.startswith("/"):
        return [s.replace("~1", "/").replace("~0", "~") for s in key[1:].split("/")]
    return key.split(".")


def yaml_line_of(text: str, key: str) -> Optional[int]:
    """The line holding the value at `key` in a YAML file, from the parser's own positions."""
    try:
        import yaml
        node = yaml.compose(text)
    except Exception:  # noqa: BLE001
        return None
    for step in key_steps(key):
        if isinstance(node, yaml.MappingNode):
            node = next((v for k, v in node.value if getattr(k, "value", None) == step), None)
        elif isinstance(node, yaml.SequenceNode) and step.isdigit() and int(step) < len(node.value):
            node = node.value[int(step)]
        else:
            return None
        if node is None:
            return None
    return node.start_mark.line + 1


def yaml_key_at(text: str, line: int) -> Optional[str]:
    """The key path whose scalar value sits on `line` of a YAML file, or None."""
    try:
        import yaml
        root = yaml.compose(text)
    except Exception:  # noqa: BLE001
        return None
    found: list[str] = []

    def walk(node: Any, path: list[str]) -> None:
        if found:
            return
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, [*path, str(getattr(k, "value", ""))])
        elif isinstance(node, yaml.SequenceNode):
            if node.start_mark.line + 1 == line and path and all(isinstance(x, yaml.ScalarNode) for x in node.value):
                found.append(".".join(path))
                return
            for i, v in enumerate(node.value):
                walk(v, [*path, str(i)])
        elif isinstance(node, yaml.ScalarNode) and node.start_mark.line + 1 == line and path:
            found.append(".".join(path))
    if root is not None:
        walk(root, [])
    return found[0] if found else None
