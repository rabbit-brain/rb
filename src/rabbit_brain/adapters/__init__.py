"""Adapter registry: built-ins by id, or any `package.module:Class` from rb.toml."""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any

from ..config import Config
from ..errors import RBError
from ..models import Metric
from .base import Adapter, Case, Prediction, read_case_selector, task_metric

BUILTIN = {
    "raft": "rabbit_brain.adapters.raft:RaftAdapter",
    "synthetic": "rabbit_brain.adapters.synthetic:SyntheticAdapter",
}


def adapter_spec(cfg: Config) -> str:
    if cfg.adapter.module:
        return cfg.adapter.module
    if cfg.adapter.id:
        if cfg.adapter.id not in BUILTIN:
            raise RBError("E_CONFIG_INVALID", message=f"Unknown adapter id '{cfg.adapter.id}'. Built-ins: {', '.join(BUILTIN)}; or set [adapter] module = \"package.module:Class\".")
        return BUILTIN[cfg.adapter.id]
    raise RBError("E_CONFIG_INVALID", message="rb.toml needs [adapter] id (raft | synthetic) or module.")


def load_adapter(cfg: Config) -> Any:
    spec = adapter_spec(cfg)
    module_name, _, class_name = spec.partition(":")
    if not class_name:
        raise RBError("E_CONFIG_INVALID", message=f"Adapter '{spec}' must be written as package.module:Class.")
    # a custom adapter usually sits next to rb.toml; the project directory is importable without PYTHONPATH
    for extra in (os.getcwd(), cfg.adapter.model_code):
        if extra and os.path.isdir(extra) and os.path.abspath(extra) not in [os.path.abspath(p) for p in sys.path if p]:
            sys.path.append(os.path.abspath(extra))
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and module_name.split(".")[0] == exc.name.split(".")[0] and cfg.adapter.module:
            raise RBError("E_ADAPTER_IMPORT", message=f"Adapter module '{module_name}' was not found ({exc}).",
                          fix=f"Put {module_name.split('.')[0]}.py (or the package) next to rb.toml, under [adapter] model_code, or pip install it; rb adds the project directory and model_code to sys.path. Check the spelling in rb.toml ([adapter] module = \"{spec}\").")
        raise RBError("E_ADAPTER_IMPORT", message=f"Could not import adapter module '{module_name}': {exc}",
                      fix=f"A dependency of the adapter is missing in this environment: pip install it ({exc.name}).")
    except ImportError as exc:
        raise RBError("E_ADAPTER_IMPORT", message=f"Could not import adapter module '{module_name}': {exc}")
    cls = getattr(module, class_name, None)
    if cls is None:
        raise RBError("E_ADAPTER_IMPORT", message=f"'{module_name}' has no class '{class_name}'.")
    try:
        adapter = cls(cfg)
    except RBError:
        raise
    except Exception as exc:
        raise RBError("E_ADAPTER_IMPORT", message=f"Adapter {spec} could not be constructed with the config: {type(exc).__name__}: {exc}")
    for method in ("describe", "load", "cases", "infer", "metric_value", "expected_iterations"):
        if not callable(getattr(adapter, method, None)):
            raise RBError("E_ADAPTER_IMPORT", message=f"Adapter {spec} lacks the method '{method}' (see rb docs, custom adapters).")
    if not getattr(adapter, "metric", None):
        adapter.metric = task_metric(cfg)
    if not hasattr(adapter, "synthetic"):
        adapter.synthetic = False
    return adapter


__all__ = ["Adapter", "Case", "Prediction", "Metric", "RBError", "read_case_selector", "BUILTIN", "load_adapter", "adapter_spec", "task_metric"]
