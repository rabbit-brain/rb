"""Talking to a Rabbit Brain workspace.

The free tool never needs this module. It exists so that an engineer, or an agent working on their
behalf, can discover what a workspace costs, hand a person a link to approve, notice when it goes
live, and push a finished review into it, without leaving the terminal the review happened in.

Three deliberate constraints.

**No new dependency.** `urllib` from the standard library, because a tool people `pip install` to
compare two checkpoints should not pull in an HTTP stack to do it.

**The token is read from the environment and never written anywhere.** Not to a config file, not
into a URL, not into a receipt, not into an error message. `RB_WORKSPACE_TOKEN` is the only place it
lives, which is also what a CI job already knows how to provide.

**Payment is a person's decision.** `checkout` returns a link for a human to open. Nothing here can
complete a purchase, and that is the design rather than a gap.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from .errors import RBError

DEFAULT_BASE = "https://rabbitbrain.ai"
TOKEN_ENV = "RB_WORKSPACE_TOKEN"
BASE_ENV = "RB_WORKSPACE_URL"
TIMEOUT = 30


def base_url() -> str:
    return (os.environ.get(BASE_ENV) or DEFAULT_BASE).rstrip("/")


def token() -> str:
    t = os.environ.get(TOKEN_ENV, "").strip()
    if not t:
        raise RBError("E_WORKSPACE_NO_TOKEN")
    return t


def _request(method: str, path: str, body: Optional[dict] = None, authed: bool = True) -> tuple[int, Any]:
    url = f"{base_url()}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"accept": "application/json", "user-agent": "rabbit-brain"}
    if data is not None:
        headers["content-type"] = "application/json"
    if authed:
        headers["authorization"] = f"Bearer {token()}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:            # a 4xx/5xx still carries a JSON body we want
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:400] or f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        raise RBError("E_WORKSPACE_UNREACHABLE", message=f"Could not reach {base_url()}: {exc.reason}")
    except json.JSONDecodeError:
        raise RBError("E_WORKSPACE_REJECTED", message=f"{base_url()} returned a response that was not JSON.")


def plans() -> dict:
    status, body = _request("GET", "/api/v1/plans", authed=False)
    if status != 200:
        raise RBError("E_WORKSPACE_REJECTED", message=_msg(body, status))
    return body


def status_of() -> dict:
    status, body = _request("GET", "/api/v1/subscription")
    if status == 401:
        raise RBError("E_WORKSPACE_AUTH")
    if status != 200:
        raise RBError("E_WORKSPACE_REJECTED", message=_msg(body, status))
    return body


def checkout(plan: str) -> dict:
    status, body = _request("POST", "/api/v1/checkout", {"plan": plan})
    if status == 401:
        raise RBError("E_WORKSPACE_AUTH")
    if status == 409:
        raise RBError("E_WORKSPACE_NOT_SELLABLE", message=_msg(body, status))
    if status == 503:
        raise RBError("E_WORKSPACE_REJECTED", message=_msg(body, status))
    if status not in (200, 201):
        raise RBError("E_WORKSPACE_REJECTED", message=_msg(body, status))
    return body


def push(bundle: dict) -> dict:
    """Post one finished comparison. A 402 is not a failure of the push; it is the workspace saying
    this is the paid feature, and it carries what to do next."""
    status, body = _request("POST", "/api/v1/comparisons", bundle)
    if status == 401:
        raise RBError("E_WORKSPACE_AUTH")
    if status == 402:
        plan = (body.get("required_plan") or {}).get("price", "a subscription")
        raise RBError("E_WORKSPACE_PAYMENT_REQUIRED",
                      message=f"{_msg(body, status)} The plan is {plan}.")
    if status not in (200, 201):
        raise RBError("E_WORKSPACE_REJECTED", message=_msg(body, status))
    return body


def _msg(body: Any, status: int) -> str:
    if isinstance(body, dict):
        parts = [str(body.get("error") or f"HTTP {status}")]
        if body.get("fix"):
            parts.append(str(body["fix"]))
        if body.get("why"):
            parts.append(str(body["why"]))
        return " ".join(parts)
    return f"HTTP {status}"
