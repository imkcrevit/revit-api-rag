"""
API connectivity checker
Usage:
  python check_api.py              # reads .env + config.yaml automatically
  python check_api.py --no-proxy   # skip proxy even if config says enabled
"""
import argparse
import json
import os
import socket
import sys
import urllib.parse
from pathlib import Path

# ── load .env ────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ── load config.yaml ─────────────────────────────────────────────────────
try:
    import yaml
    config_path = Path(__file__).parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"[WARN] Cannot load config.yaml: {e}")
    config = {}

try:
    import httpx
except ImportError:
    print("httpx not installed. Run: pip install httpx")
    sys.exit(1)

# ── CLI args ──────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--no-proxy", action="store_true", help="Ignore proxy settings")
args = parser.parse_args()

SEP = "─" * 60
OK  = "✓"
ERR = "✗"
WARN = "!"


def _client(proxy_url: str | None) -> httpx.Client:
    if proxy_url:
        return httpx.Client(proxy=proxy_url, timeout=10, follow_redirects=True)
    return httpx.Client(timeout=10, follow_redirects=True)


def check_proxy(proxy_cfg: dict) -> tuple[bool, str | None]:
    """Returns (reachable, proxy_url)."""
    if args.no_proxy or not proxy_cfg.get("enabled"):
        return False, None
    proxy_url = proxy_cfg.get("https") or proxy_cfg.get("http", "")
    parsed = urllib.parse.urlparse(proxy_url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 10808
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return True, proxy_url
    except Exception as e:
        return False, proxy_url


def check_openrouter_key(client: httpx.Client, api_key: str) -> bool:
    """Checks if the API key is valid by calling /auth/key."""
    try:
        r = client.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            limit    = data.get("limit")
            usage    = data.get("usage", 0)
            label    = data.get("label", "")
            remaining = (limit - usage) if limit else "unlimited"
            print(f"  Key label   : {label or '(no label)'}")
            print(f"  Usage       : ${usage:.4f}")
            print(f"  Remaining   : ${remaining}" if isinstance(remaining, float) else f"  Remaining   : {remaining}")
            return True
        else:
            print(f"  Key check   : HTTP {r.status_code} — {r.text[:120]}")
            return False
    except Exception as e:
        print(f"  Key check   : ERROR — {e}")
        return False


def check_model(client: httpx.Client, api_key: str, model: str) -> bool:
    """Sends a minimal 1-token chat request to verify model access."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
        "max_tokens": 5,
    }
    try:
        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "revit-api-rag-check",
            },
            json=payload,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            print(f"  {OK} {model:<55} → {content.strip()!r}")
            return True
        else:
            err_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            err_msg  = err_body.get("error", {}).get("message", str(err_body)) if isinstance(err_body, dict) else str(err_body)
            print(f"  {ERR} {model:<55} HTTP {r.status_code}: {err_msg[:100]}")
            return False
    except Exception as e:
        print(f"  {ERR} {model:<55} ERROR: {e}")
        return False


# ═══════════════════════════════════════════════════════════
print(SEP)
print("  API Connectivity Checker")
print(SEP)

# ── 1. Proxy ─────────────────────────────────────────────────
proxy_cfg = config.get("proxy", {})
proxy_ok, proxy_url = check_proxy(proxy_cfg)

print(f"\n[1] Proxy")
if args.no_proxy:
    print(f"  {WARN} Skipped (--no-proxy)")
elif not proxy_cfg.get("enabled"):
    print(f"  {WARN} Disabled in config.yaml")
elif proxy_ok:
    print(f"  {OK} {proxy_url}  (port reachable)")
else:
    print(f"  {ERR} {proxy_url}  (Connection refused — is Clash/v2ray running?)")
    print(f"\n  Tip: run with --no-proxy to test direct connection")

# ── 2. API Key ────────────────────────────────────────────────
llm_cfg   = config.get("llm", {})
key_env   = (llm_cfg.get("models", {}).get("claude", {}).get("api_key_env")
             or config.get("openrouter", {}).get("api_key_env", "OPENROUTER_API_KEY"))
api_key   = os.getenv(key_env, "")

print(f"\n[2] API Key  ({key_env})")
if not api_key:
    print(f"  {ERR} Not set — add {key_env}=sk-... to .env or environment variables")
    sys.exit(1)
print(f"  Key prefix  : {api_key[:8]}...{api_key[-4:]}")

client = _client(proxy_url if proxy_ok else None)
key_valid = check_openrouter_key(client, api_key)
if not key_valid:
    print(f"\n  {ERR} API key appears invalid or has no credits")

# ── 3. Model Tests ────────────────────────────────────────────
models_cfg = llm_cfg.get("models", {})
models_to_test = {
    name: cfg.get("model", "")
    for name, cfg in models_cfg.items()
    if cfg.get("model")
}

print(f"\n[3] Model Access Tests")
results = {}
for provider, model in models_to_test.items():
    ok = check_model(client, api_key, model)
    results[provider] = ok

# ── Summary ───────────────────────────────────────────────────
print(f"\n{SEP}")
print("  Summary")
print(SEP)
for provider, ok in results.items():
    status = f"{OK} OK" if ok else f"{ERR} FAIL"
    print(f"  {provider:<12} {status}")

failed = [p for p, ok in results.items() if not ok]
if failed:
    print(f"\n  Failed providers: {', '.join(failed)}")
    print("  Possible causes:")
    print("    1. Model name changed on OpenRouter (check openrouter.ai/models)")
    print("    2. API key doesn't have credit/permission for this model")
    print("    3. Model requires specific tier on OpenRouter")
    print()
    print("  Suggested fix: replace the failing model in config.yaml with a working one.")
    print("  E.g. for gemini, try:  google/gemini-flash-1.5  or  google/gemini-2.0-flash-001")
else:
    print(f"\n  All models OK. Ready to run Quality Agent.")
print(SEP)
