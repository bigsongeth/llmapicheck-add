#!/usr/bin/env python3
"""OpenClaw model availability matrix.

Reads openclaw.json providers/models, probes each model endpoint, and prints
an easy-to-read markdown table + agent primary/fallback summary.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


def detect_default_config() -> str:
    candidates = [
        os.environ.get("OPENCLAW_CONFIG", ""),
        os.path.expanduser("~/.openclaw/openclaw.json"),
        os.path.expanduser("~/.config/openclaw/openclaw.json"),
        "/etc/openclaw/openclaw.json",
        "/root/.openclaw/openclaw.json",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return os.path.expanduser("~/.openclaw/openclaw.json")


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], value)
    return value


def _join_endpoint(base_url: str, mode: str) -> str:
    base = (base_url or "").rstrip("/")
    if mode == "openai-completions":
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"
    if mode == "anthropic-messages":
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"
    return base


def _http_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float,
) -> Tuple[int, str, float]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), body, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return int(e.code), body, (time.perf_counter() - started) * 1000
    except Exception as e:  # noqa: BLE001
        return 0, str(e), (time.perf_counter() - started) * 1000


def _classify_probe_result(ok: bool, code: int, body: str, api_mode: str) -> Tuple[str, str]:
    note = (body or "").strip().replace("\n", " ")[:180]
    low = note.lower()

    if ok:
        try:
            payload_json = json.loads(body)
        except Exception as e:  # noqa: BLE001
            return "non_json_response", f"non-json response: {str(e)[:60]} | {note}"

        if isinstance(payload_json, dict) and payload_json.get("error") is not None:
            return "runtime_error_payload", note

        if api_mode == "openai-completions":
            choices = payload_json.get("choices") if isinstance(payload_json, dict) else None
            if not isinstance(choices, list) or not choices:
                # common symptom: model is listed in /models but upstream payload shape is incompatible
                if "status_code" in low or "status_msg" in low or "login fail" in low:
                    return "advertised_but_unusable", f"advertised in /models but runtime payload incompatible | {note}"
                return "incompatible_runtime_json", f"unexpected json (no choices) | {note}"
        elif api_mode == "anthropic-messages":
            content = payload_json.get("content") if isinstance(payload_json, dict) else None
            if not isinstance(content, list) or not content:
                return "incompatible_runtime_json", f"unexpected json (no content) | {note}"

        return "ok", note

    if code in (401, 403):
        return "auth_error", note
    if code == 429:
        return "rate_limited", note
    if code == 404:
        return "endpoint_not_found", note
    if code == 0:
        return "network_error", note
    if any(x in low for x in ["timed out", "timeout", "temporarily unavailable", "connection reset", "name or service not known"]):
        return "network_error", note
    return "http_error", note


def _probe_openai(
    base_url: str,
    api_key: Optional[str],
    model_id: str,
    provider_headers: Dict[str, Any],
    timeout: float,
) -> Tuple[bool, int, float, str, str]:
    endpoint = _join_endpoint(base_url, "openai-completions")
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "openclaw-model-ops/0.3",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for k, v in (provider_headers or {}).items():
        headers[str(k)] = str(_resolve_env(v))

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "healthcheck ping"}],
        "max_tokens": 16,
        "stream": False,
    }
    code, body, ms = _http_json(endpoint, payload, headers, timeout)
    ok = 200 <= code < 300
    status, note = _classify_probe_result(ok, code, body, "openai-completions")
    return status == "ok", code, ms, note, status


def _probe_anthropic(
    base_url: str,
    api_key: Optional[str],
    model_id: str,
    provider_headers: Dict[str, Any],
    timeout: float,
) -> Tuple[bool, int, float, str, str]:
    endpoint = _join_endpoint(base_url, "anthropic-messages")
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "User-Agent": "openclaw-model-ops/0.3",
    }
    if api_key:
        headers["x-api-key"] = api_key

    for k, v in (provider_headers or {}).items():
        headers[str(k)] = str(_resolve_env(v))

    payload = {
        "model": model_id,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "healthcheck ping"}],
    }
    code, body, ms = _http_json(endpoint, payload, headers, timeout)
    ok = 200 <= code < 300
    status, note = _classify_probe_result(ok, code, body, "anthropic-messages")
    return status == "ok", code, ms, note, status


def _status_icon(kind: str, ok: bool, code: int) -> str:
    if ok:
        return "✅"
    if kind == "auth_error" or code in (401, 403):
        return "🔒"
    if kind == "network_error" or code == 0:
        return "🌐"
    if kind == "rate_limited":
        return "⏳"
    if kind == "advertised_but_unusable":
        return "⚠️"
    return "❌"


def _build_agent_summary(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    defaults = cfg.get("agents", {}).get("defaults", {}).get("model", {})
    default_primary = defaults.get("primary", "-")
    default_fallbacks = defaults.get("fallbacks", []) or []

    rows: List[Dict[str, str]] = []
    for agent in cfg.get("agents", {}).get("list", []):
        aid = agent.get("id", "unknown")
        model_cfg = agent.get("model")

        if isinstance(model_cfg, str):
            primary = model_cfg
            fallbacks: List[str] = []
            source = "agent.model"
        elif isinstance(model_cfg, dict):
            primary = model_cfg.get("primary", default_primary)
            fallbacks = model_cfg.get("fallbacks", default_fallbacks)
            source = "agent.model(+defaults)"
        else:
            primary = default_primary
            fallbacks = default_fallbacks
            source = "defaults"

        rows.append(
            {
                "agent": aid,
                "primary": primary or "-",
                "fallbacks": ", ".join(fallbacks) if fallbacks else "-",
                "source": source,
            }
        )
    return rows


def run() -> int:
    p = argparse.ArgumentParser(description="OpenClaw model availability matrix")
    p.add_argument("--config", default=detect_default_config())
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--providers", default="", help="Comma-separated provider ids")
    p.add_argument("--output", default="", help="Write markdown report to file")
    p.add_argument("--json", dest="json_output", default="", help="Write raw JSON to file")
    args = p.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    providers: Dict[str, Dict[str, Any]] = cfg.get("models", {}).get("providers", {})
    allow = {x.strip() for x in args.providers.split(",") if x.strip()}

    rows: List[Dict[str, Any]] = []
    for pid, pcfg in providers.items():
        if allow and pid not in allow:
            continue

        api_mode = pcfg.get("api", "openai-completions")
        base_url = _resolve_env(pcfg.get("baseUrl", ""))
        api_key = _resolve_env(pcfg.get("apiKey"))
        models = pcfg.get("models", []) or []
        headers = pcfg.get("headers", {}) or {}

        if not models:
            rows.append(
                {
                    "provider": pid,
                    "model": "(none)",
                    "api": api_mode,
                    "ok": False,
                    "kind": "no_models",
                    "code": 0,
                    "latency_ms": 0.0,
                    "note": "no models configured",
                }
            )
            continue

        for m in models:
            mid = m.get("id", "")
            if not mid:
                continue

            if api_mode == "openai-completions":
                ok, code, ms, note, kind = _probe_openai(base_url, api_key, mid, headers, args.timeout)
            elif api_mode == "anthropic-messages":
                ok, code, ms, note, kind = _probe_anthropic(base_url, api_key, mid, headers, args.timeout)
            else:
                ok, code, ms, note, kind = False, 0, 0.0, f"unsupported api mode: {api_mode}", "unsupported_api_mode"

            rows.append(
                {
                    "provider": pid,
                    "model": mid,
                    "api": api_mode,
                    "ok": ok,
                    "kind": kind,
                    "code": code,
                    "latency_ms": round(ms, 1),
                    "note": note,
                }
            )

    ok_count = sum(1 for r in rows if r.get("ok"))
    total = len(rows)
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    md_lines: List[str] = []
    md_lines.append("# 📊 OpenClaw 模型可用性矩阵")
    md_lines.append("")
    md_lines.append(f"- 检测时间: `{now}`")
    md_lines.append(f"- 总计: `{ok_count}/{total}` 可用")
    md_lines.append("")
    md_lines.append("| Provider | Model | API | 状态 | 分类 | HTTP | RTT(ms) | 备注 |")
    md_lines.append("|---|---|---|---:|---|---:|---:|---|")
    for r in rows:
        icon = _status_icon(str(r.get("kind", "")), bool(r["ok"]), int(r["code"]))
        note = str(r["note"]).replace("|", "\\|")
        md_lines.append(
            f"| {r['provider']} | {r['model']} | {r['api']} | {icon} | {r.get('kind','')} | {r['code']} | {r['latency_ms']} | {note} |"
        )

    md_lines.append("")
    md_lines.append("## 🤖 Agent 首选 / Fallback 模型")
    md_lines.append("")
    md_lines.append("| Agent | Primary | Fallbacks | 来源 |")
    md_lines.append("|---|---|---|---|")
    for a in _build_agent_summary(cfg):
        md_lines.append(
            f"| {a['agent']} | {a['primary']} | {a['fallbacks']} | {a['source']} |"
        )

    report = "\n".join(md_lines)
    print(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report + "\n")

    if args.json_output:
        payload = {
            "checkedAt": now,
            "summary": {"ok": ok_count, "total": total},
            "rows": rows,
            "agents": _build_agent_summary(cfg),
        }
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
