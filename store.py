"""Recall Vault local store.

Policy, plain-language rules, spent-today tracking, and the audit ledger
live in two small JSON files in the plugin data directory. No network,
no subprocesses, no credentials, no reading of .env or token files.

Data dir resolution, in order:
  1. An explicit data_dir passed in (used by tests).
  2. Hermes plugin_data_dir("recall-vault"), when Hermes is importable.
  3. $HERMES_HOME/plugin-data/recall-vault, or ~/.hermes/plugin-data/recall-vault.
  4. A data/ folder next to this file (last resort, for local runs).

File layout:
  policy.json  {"policy": {...}, "rules": [...], "spent_today": 0.0, "spent_day": "YYYY-MM-DD"}
  ledger.json  {"entries": [ {...}, ... ]}  newest last, capped at 200 entries.
"""

import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path

try:
    from .policy import default_policy
except ImportError:
    from policy import default_policy

_LOCK = threading.Lock()
_MAX_LEDGER_ENTRIES = 200


def resolve_data_dir(data_dir=None):
    """Return the directory this plugin should store its state in."""
    if data_dir is not None:
        return Path(data_dir)
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir("recall-vault"))
    except Exception:
        pass
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "plugin-data" / "recall-vault"


def _policy_path(data_dir):
    return resolve_data_dir(data_dir) / "policy.json"


def _ledger_path(data_dir):
    return resolve_data_dir(data_dir) / "ledger.json"


def _today():
    return date.today().isoformat()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_state():
    return {
        "policy": default_policy(),
        "rules": [
            "Never spend more than the daily cap.",
            "Never pay a brand-new address without asking first.",
        ],
        "spent_today": 0.0,
        "spent_day": _today(),
    }


def load_state(data_dir=None):
    """Load policy state, resetting spent_today when the day has changed."""
    path = _policy_path(data_dir)
    state = _default_state()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                policy = loaded.get("policy") or {}
                base = default_policy()
                base.update({k: v for k, v in policy.items() if k in base})
                state["policy"] = base
                if isinstance(loaded.get("rules"), list):
                    state["rules"] = [str(r) for r in loaded["rules"]]
                try:
                    state["spent_today"] = float(loaded.get("spent_today") or 0.0)
                except (TypeError, ValueError):
                    state["spent_today"] = 0.0
                if isinstance(loaded.get("spent_day"), str):
                    state["spent_day"] = loaded["spent_day"]
        except (OSError, ValueError):
            state = _default_state()
    if state["spent_day"] != _today():
        state["spent_today"] = 0.0
        state["spent_day"] = _today()
        save_state(state, data_dir=data_dir)
    return state


def save_state(state, data_dir=None):
    """Save policy state. Creates the data directory if needed."""
    directory = resolve_data_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _policy_path(data_dir).write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    return state


def load_ledger(data_dir=None):
    """Load ledger entries, oldest first."""
    path = _ledger_path(data_dir)
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        entries = loaded.get("entries") if isinstance(loaded, dict) else None
        return entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []


def _save_ledger(entries, data_dir=None):
    directory = resolve_data_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _ledger_path(data_dir).write_text(
        json.dumps({"entries": entries}, indent=2), encoding="utf-8"
    )


def record_entry(entry, data_dir=None):
    """Append one ledger entry, capped at 200 entries. Thread-safe."""
    with _LOCK:
        entries = load_ledger(data_dir=data_dir)
        record = dict(entry)
        record.setdefault("at", _now_iso())
        record.setdefault("currency", "NEAR")
        entries.append(record)
        entries = entries[-_MAX_LEDGER_ENTRIES:]
        _save_ledger(entries, data_dir=data_dir)
        return record


def record_allowed(amount, to_address, data_dir=None):
    """Record an allowed payment and add it to spent today."""
    with _LOCK:
        state = load_state(data_dir=data_dir)
        state["spent_today"] = round(float(state["spent_today"]) + float(amount), 4)
        save_state(state, data_dir=data_dir)
    return record_entry(
        {
            "decision": "ALLOWED",
            "rule": None,
            "reason": "Passes all spending rules.",
            "amount": float(amount),
            "to": to_address,
            "spent_today_after": state["spent_today"],
        },
        data_dir=data_dir,
    )


def remember_rule(text, data_dir=None):
    """Store one plain-language rule, ignoring exact duplicates."""
    text = str(text or "").strip()
    if not text:
        return None
    with _LOCK:
        state = load_state(data_dir=data_dir)
        if any(r.strip().lower() == text.lower() for r in state["rules"]):
            return len(state["rules"])
        state["rules"].append(text)
        save_state(state, data_dir=data_dir)
        return len(state["rules"])
