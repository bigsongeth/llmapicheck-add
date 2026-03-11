#!/usr/bin/env python3
"""Add or update an OpenClaw model provider in openclaw.json.

Supports two modes:
1) explicit: provide --provider + --base-url + --api-key (+ optional model list)
2) parse-message: parse free text containing url/key/model(s)

Model names support fuzzy resolution against remote /v1/models (OpenAI-compatible).
This avoids failures when users provide approximate model names (e.g. codex5.3).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


URL_RE = re.compile(r"https?://[^\s,]+", re.IGNORECASE)
KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")
MODEL_RE = re.compile(r"\b([A-Za-z0-9._:/\-]{3,})\b")

STOPWORDS = {
    "this",
    "that",
    "with",
    "from",
    "then",
    "please",
    "model",
    "models",
    "api",
    "key",
    "url",
    "and",
    "the",
    "you",
    "for",
    "openclaw",
    "group",
    "still",
    "same",
    "as",
    "above",
}


def infer_provider_id(base_url: str) -> str:
    host = re.sub(r"^https?://", "", base_url).split("/")[0].lower()
    host = host.replace(":", "-").replace(".", "-")
    return host[:48] if host else "custom-provider"


def parse_message(text: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    url = None
    key = None
    models: List[str] = []

    u = URL_RE.search(text)
    if u:
        url = u.group(0)

    k = KEY_RE.search(text)
    if k:
        key = k.group(0)

    candidates = []
    for m in MODEL_RE.finditer(text):
        token = m.group(1)
        low = token.lower()
        if low.startswith("http"):
            continue
        if low.startswith("sk-"):
            continue
        if low in STOPWORDS:
            continue
        if re.search(r"[A-Za-z].*[0-9]|[0-9].*[A-Za-z]|-|/|\.", token):
            candidates.append(token)

    # preserve order, unique
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            models.append(c)

    return url, key, models


def _normalize_model(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _join_models_endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def fetch_openai_models(
    base_url: str,
    api_key: str,
    timeout: float,
    user_agent: Optional[str],
) -> Tuple[List[str], str]:
    """Return remote model ids and optional warning message."""
    endpoint = _join_models_endpoint(base_url)
    req = urllib.request.Request(endpoint, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    if user_agent:
        req.add_header("User-Agent", user_agent)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            ids: List[str] = []
            for item in payload.get("data", []) or []:
                mid = item.get("id")
                if isinstance(mid, str) and mid.strip():
                    ids.append(mid.strip())
            # dedupe preserve order
            seen = set()
            out = []
            for mid in ids:
                if mid not in seen:
                    seen.add(mid)
                    out.append(mid)
            return out, ""
    except urllib.error.HTTPError as e:
        return [], f"model-discovery http {e.code}"
    except Exception as e:  # noqa: BLE001
        return [], f"model-discovery error: {e}"


def _digit_chunks(s: str) -> List[str]:
    return re.findall(r"\d+", s)


def _resolve_one_model(requested: str, available: List[str]) -> Tuple[str, Optional[str], float]:
    """Return (final_model, matched_from, confidence).

    matched_from is remote model id when fuzzy match happened; None when unchanged.
    """
    if not available:
        return requested, None, 0.0

    # exact
    if requested in available:
        return requested, requested, 1.0

    nreq = _normalize_model(requested)
    if not nreq:
        return requested, None, 0.0

    # normalized exact
    norm_map: Dict[str, List[str]] = {}
    for mid in available:
        norm_map.setdefault(_normalize_model(mid), []).append(mid)
    if nreq in norm_map:
        candidates = sorted(norm_map[nreq], key=len)
        return candidates[0], candidates[0], 0.98

    req_digits = set(_digit_chunks(nreq))
    req_has_codex = "codex" in nreq
    req_has_gpt = "gpt" in nreq

    # score all by similarity + containment bonus + semantic/version bonus
    scored: List[Tuple[float, str]] = []
    for mid in available:
        nmid = _normalize_model(mid)
        if not nmid:
            continue
        ratio = difflib.SequenceMatcher(None, nreq, nmid).ratio()
        bonus = 0.0

        if nreq in nmid or nmid in nreq:
            bonus += 0.25

        mid_has_codex = "codex" in nmid
        mid_has_gpt = "gpt" in nmid
        if req_has_codex == mid_has_codex:
            bonus += 0.06
        if req_has_gpt == mid_has_gpt:
            bonus += 0.03

        mid_digits = set(_digit_chunks(nmid))
        if req_digits and mid_digits:
            common = req_digits & mid_digits
            if common:
                # strong signal for version-like inputs (5.3 / 4.1 / 2025 etc.)
                bonus += min(0.28, 0.14 * len(common))

        score = ratio + bonus
        scored.append((score, mid))

    if not scored:
        return requested, None, 0.0

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_mid = scored[0]

    # safety threshold to avoid bad substitutions
    if best_score >= 0.74:
        return best_mid, best_mid, round(best_score, 3)

    return requested, None, round(best_score, 3)


def resolve_models_fuzzy(requested_models: List[str], available_models: List[str]) -> Tuple[List[str], List[Dict[str, Any]]]:
    resolved: List[str] = []
    mapping: List[Dict[str, Any]] = []

    for req in requested_models:
        final_model, matched, confidence = _resolve_one_model(req, available_models)
        resolved.append(final_model)
        mapping.append(
            {
                "input": req,
                "resolved": final_model,
                "fuzzy": req != final_model,
                "confidence": confidence,
                "matched": matched,
            }
        )

    # dedupe preserve order
    seen = set()
    dedup = []
    for m in resolved:
        if m not in seen:
            seen.add(m)
            dedup.append(m)

    return dedup, mapping


def build_provider_block(
    base_url: str,
    api_key: str,
    models: List[str],
    api_mode: str,
    ua: Optional[str],
) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "baseUrl": base_url,
        "apiKey": api_key,
        "auth": "api-key",
        "api": api_mode,
        "models": [],
    }
    if ua:
        block["headers"] = {"User-Agent": ua}

    for m in models:
        block["models"].append(
            {
                "id": m,
                "name": m,
                "api": api_mode,
                "reasoning": False,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 200000,
                "maxTokens": 8192,
            }
        )

    return block


def upsert_provider(
    config_path: str,
    provider_id: str,
    block: Dict[str, Any],
    set_primary: bool,
) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    providers = cfg.setdefault("models", {}).setdefault("providers", {})
    providers[provider_id] = block

    defaults = cfg.setdefault("agents", {}).setdefault("defaults", {})
    defaults_models = defaults.setdefault("models", {})
    for m in block.get("models", []):
        mid = m.get("id")
        if mid:
            defaults_models.setdefault(f"{provider_id}/{mid}", {})

    if set_primary and block.get("models"):
        first = block["models"][0]["id"]
        model_cfg = defaults.setdefault("model", {})
        prev_primary = model_cfg.get("primary")
        new_primary = f"{provider_id}/{first}"
        model_cfg["primary"] = new_primary
        fb = list(model_cfg.get("fallbacks", []) or [])
        if prev_primary and prev_primary != new_primary and prev_primary not in fb:
            fb.insert(0, prev_primary)
        model_cfg["fallbacks"] = fb

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return {
        "provider": provider_id,
        "modelCount": len(block.get("models", [])),
        "baseUrl": block.get("baseUrl"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Add/update provider in openclaw.json")
    p.add_argument("--config", default="/root/.openclaw/openclaw.json")
    p.add_argument("--provider", default="")
    p.add_argument("--base-url", default="")
    p.add_argument("--api-key", default="")
    p.add_argument("--models", default="", help="comma-separated model ids")
    p.add_argument("--api-mode", default="openai-completions")
    p.add_argument("--user-agent", default="")
    p.add_argument("--set-primary", action="store_true")
    p.add_argument("--from-message", default="")
    p.add_argument("--no-fuzzy", action="store_true", help="disable remote model fuzzy resolution")
    p.add_argument("--timeout", type=float, default=15.0)
    args = p.parse_args()

    base_url = args.base_url.strip()
    api_key = args.api_key.strip()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.from_message:
        m_url, m_key, m_models = parse_message(args.from_message)
        base_url = base_url or (m_url or "")
        api_key = api_key or (m_key or "")
        if not models and m_models:
            models = m_models

    if not base_url or not api_key:
        raise SystemExit("Need base-url and api-key (directly or via --from-message)")

    provider = args.provider.strip() or infer_provider_id(base_url)

    discovery_note = ""
    fuzzy_mapping: List[Dict[str, Any]] = []

    # fuzzy resolve model names via /v1/models (OpenAI-compatible)
    if models and not args.no_fuzzy and args.api_mode == "openai-completions":
        available, discovery_note = fetch_openai_models(
            base_url=base_url,
            api_key=api_key,
            timeout=args.timeout,
            user_agent=args.user_agent or None,
        )
        if available:
            models, fuzzy_mapping = resolve_models_fuzzy(models, available)

    # allow empty model list (user may add later)
    block = build_provider_block(base_url, api_key, models, args.api_mode, args.user_agent or None)
    res = upsert_provider(args.config, provider, block, args.set_primary)
    res["models"] = models
    if fuzzy_mapping:
        res["fuzzyMapping"] = fuzzy_mapping
    if discovery_note:
        res["discoveryNote"] = discovery_note

    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
