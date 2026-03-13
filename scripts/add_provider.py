#!/usr/bin/env python3
"""Add or update an OpenClaw model provider in openclaw.json.

Supports two modes:
1) explicit: provide --provider + --base-url + --api-key (+ optional model list)
2) parse-message: parse free text containing url/key/model(s)

Model names support fuzzy resolution against remote /v1/models (OpenAI-compatible).
This avoids failures when users provide approximate model names (e.g. codex5.3).

New in the browserless-friendly flow:
- uses provider /models for exact ids
- can enrich static metadata from models.dev/api.json
- falls back safely when metadata is unavailable
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
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

DEFAULT_MODELS_DEV = "https://models.dev/api.json"


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
    if not available:
        return requested, None, 0.0

    if requested in available:
        return requested, requested, 1.0

    nreq = _normalize_model(requested)
    if not nreq:
        return requested, None, 0.0

    norm_map: Dict[str, List[str]] = {}
    for mid in available:
        norm_map.setdefault(_normalize_model(mid), []).append(mid)
    if nreq in norm_map:
        candidates = sorted(norm_map[nreq], key=len)
        return candidates[0], candidates[0], 0.98

    req_digits = set(_digit_chunks(nreq))
    req_has_codex = "codex" in nreq
    req_has_gpt = "gpt" in nreq

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
                bonus += min(0.28, 0.14 * len(common))

        score = ratio + bonus
        scored.append((score, mid))

    if not scored:
        return requested, None, 0.0

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_mid = scored[0]

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

    seen = set()
    dedup = []
    for m in resolved:
        if m not in seen:
            seen.add(m)
            dedup.append(m)

    return dedup, mapping


def _get_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "openclaw-model-ops/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_models_dev_metadata(api_url: str, timeout: float) -> Tuple[Dict[str, Dict[str, Any]], str]:
    try:
        payload = _get_json(api_url, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {}, f"models.dev fetch error: {e}"

    # Higher score = more authoritative default source when duplicate model ids exist.
    precedence = {
        "openai": 100,
        "opencode": 95,
        "opencode-go": 95,
        "azure": 90,
        "azure-cognitive-services": 90,
        "abacus": 70,
        "alibaba-cn": 60,
        "aihubmix": 50,
        "jiekou": 40,
    }

    by_id: Dict[str, Dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return by_id, "models.dev payload unexpected"

    for provider_id, provider in payload.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for mid, meta in models.items():
            if isinstance(meta, dict) and isinstance(mid, str):
                clone = dict(meta)
                clone.setdefault("provider", provider_id)
                prev = by_id.get(mid)
                if not prev:
                    by_id[mid] = clone
                    continue
                if precedence.get(provider_id, 0) > precedence.get(str(prev.get("provider", "")), 0):
                    by_id[mid] = clone
    return by_id, ""


def provider_candidates_for_model(model_id: str) -> List[str]:
    lid = model_id.lower()
    out = []
    if lid.startswith("gpt-") or "codex" in lid:
        out += ["openai", "opencode", "abacus", "jiekou", "aihubmix"]
    if lid.startswith("glm-"):
        out += ["opencode-go", "abacus", "alibaba-cn", "aihubmix"]
    if "minimax" in lid:
        out += ["opencode-go", "alibaba-cn", "aihubmix"]
    return out


def pick_metadata_for_model(model_id: str, models_dev: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = provider_candidates_for_model(model_id)

    if model_id in models_dev:
        meta = models_dev[model_id]
        if not candidates or meta.get("provider") in candidates:
            return meta

    want = _normalize_model(model_id)
    scored = []
    for mid, meta in models_dev.items():
        nm = _normalize_model(mid)
        score = difflib.SequenceMatcher(None, want, nm).ratio()
        if meta.get("provider") in candidates:
            score += 0.03
        if score >= 0.92:
            scored.append((score, mid, meta))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][2]
    return None


def build_model_entry(model_id: str, meta: Optional[Dict[str, Any]], api_mode: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "api": api_mode,
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 200000,
        "maxTokens": 8192,
    }
    if not meta:
        return entry

    entry["name"] = meta.get("name") or model_id
    entry["reasoning"] = bool(meta.get("reasoning", False))

    modalities = meta.get("modalities") or {}
    in_mod = modalities.get("input") if isinstance(modalities, dict) else None
    if isinstance(in_mod, list) and in_mod:
        # OpenClaw currently expects lower-case strings like text/image/pdf
        entry["input"] = [str(x).lower() for x in in_mod]

    limit = meta.get("limit") or {}
    if isinstance(limit, dict):
        if isinstance(limit.get("context"), int):
            entry["contextWindow"] = int(limit["context"])
        if isinstance(limit.get("output"), int):
            entry["maxTokens"] = int(limit["output"])

    cost = meta.get("cost") or {}
    if isinstance(cost, dict):
        entry["cost"] = {
            "input": cost.get("input", 0),
            "output": cost.get("output", 0),
            "cacheRead": cost.get("cache_read", cost.get("cacheRead", 0)),
            "cacheWrite": cost.get("cache_write", cost.get("cacheWrite", 0)),
        }

    return entry


def build_provider_block(
    base_url: str,
    api_key: str,
    models: List[str],
    api_mode: str,
    ua: Optional[str],
    models_dev_meta: Optional[Dict[str, Dict[str, Any]]] = None,
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
        meta = pick_metadata_for_model(m, models_dev_meta or {}) if models_dev_meta else None
        block["models"].append(build_model_entry(m, meta, api_mode))

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
        f.write("\n")

    return {
        "provider": provider_id,
        "modelCount": len(block.get("models", [])),
        "baseUrl": block.get("baseUrl"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Add/update provider in openclaw.json")
    p.add_argument("--config", default="")
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
    p.add_argument("--models-dev-url", default=DEFAULT_MODELS_DEV)
    p.add_argument("--no-models-dev", action="store_true", help="do not enrich metadata from models.dev/api.json")
    args = p.parse_args()

    config = args.config or os.environ.get("OPENCLAW_CONFIG") or os.path.expanduser("~/.openclaw/openclaw.json")
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

    if models and not args.no_fuzzy and args.api_mode == "openai-completions":
        available, discovery_note = fetch_openai_models(
            base_url=base_url,
            api_key=api_key,
            timeout=args.timeout,
            user_agent=args.user_agent or None,
        )
        if available:
            models, fuzzy_mapping = resolve_models_fuzzy(models, available)

    models_dev_meta: Dict[str, Dict[str, Any]] = {}
    models_dev_note = ""
    if models and not args.no_models_dev:
        models_dev_meta, models_dev_note = fetch_models_dev_metadata(args.models_dev_url, args.timeout)

    block = build_provider_block(
        base_url,
        api_key,
        models,
        args.api_mode,
        args.user_agent or None,
        models_dev_meta=models_dev_meta,
    )
    res = upsert_provider(config, provider, block, args.set_primary)
    res["models"] = models
    if fuzzy_mapping:
        res["fuzzyMapping"] = fuzzy_mapping
    if discovery_note:
        res["discoveryNote"] = discovery_note
    if models_dev_note:
        res["modelsDevNote"] = models_dev_note

    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
