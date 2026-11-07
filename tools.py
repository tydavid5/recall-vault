"""Tool handlers, the code that runs when the model calls Recall Vault tools.

Rules for handlers, from the Hermes docs:
  1. Signature: def handler(args: dict, **kwargs) -> str
  2. Return: always a JSON string, success and errors alike.
  3. Never raise: catch all exceptions, return error JSON instead.
  4. Accept **kwargs for forward compatibility.

All handlers take an optional data_dir through kwargs for testing.
In normal Hermes use, data_dir is None and the store resolves the
plugin data directory itself.
"""

import json

try:
    from .policy import check_payment
    from . import store
except ImportError:
    from policy import check_payment
    import store


def _data_dir(kwargs):
    return kwargs.get("data_dir")


def _ok(payload):
    return json.dumps(payload)


def _error(message):
    return json.dumps({"ok": False, "error": message})


def recall_vault_check(args, **kwargs):
    """Check a payment. Allowed checks are logged and count toward today."""
    try:
        amount = args.get("amount")
        to_address = str(args.get("to") or "").strip()
        currency = str(args.get("currency") or "NEAR").strip().upper()
        confirmed = bool(args.get("confirmed", False))
        if amount is None or not to_address:
            return _error("Need amount and to.")
        if currency != "NEAR":
            return _error("Only NEAR is supported in this version.")
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return _error("Amount is not a number.")
        data_dir = _data_dir(kwargs)
        state = store.load_state(data_dir=data_dir)
        result = check_payment(
            state["policy"],
            amount,
            to_address,
            spent_today=state["spent_today"],
            confirmed=confirmed,
        )
        decision = result["decision"]
        if decision == "ALLOWED":
            entry = store.record_allowed(amount, to_address, data_dir=data_dir)
            return _ok(
                {
                    "ok": True,
                    "decision": "ALLOWED",
                    "rule": None,
                    "reason": result["reason"],
                    "amount": amount,
                    "to": to_address,
                    "currency": "NEAR",
                    "dry_run": True,
                    "note": "Dry check only, no money moved.",
                    "spent_today_after": entry.get("spent_today_after"),
                    "spent_today": entry.get("spent_today_after"),
                }
            )
        store.record_entry(
            {
                "decision": decision,
                "rule": result.get("rule"),
                "reason": result["reason"],
                "amount": amount,
                "to": to_address,
            },
            data_dir=data_dir,
        )
        return _ok(
            {
                "ok": True,
                "decision": decision,
                "rule": result.get("rule"),
                "reason": result["reason"],
                "amount": amount,
                "to": to_address,
                "currency": "NEAR",
                "dry_run": True,
                "spent_today": state["spent_today"],
            }
        )
    except Exception as exc:
        return _error("Recall Vault check failed: %s" % (exc,))


def recall_vault_policy(args, **kwargs):
    """Show the current policy, spent today, and remembered rules."""
    try:
        data_dir = _data_dir(kwargs)
        state = store.load_state(data_dir=data_dir)
        policy = state["policy"]
        return _ok(
            {
                "ok": True,
                "currency": policy.get("currency", "NEAR"),
                "per_tx_cap": policy.get("per_tx_cap"),
                "daily_cap": policy.get("daily_cap"),
                "confirm_above": policy.get("confirm_above"),
                "allowlist": policy.get("allowlist"),
                "spent_today": state["spent_today"],
                "spent_day": state["spent_day"],
                "rules": state["rules"],
                "dry_run": True,
                "note": "Dry-run only, no money moves.",
            }
        )
    except Exception as exc:
        return _error("Recall Vault policy failed: %s" % (exc,))


def recall_vault_set_policy(args, **kwargs):
    """Update the policy. Only the fields given are changed."""
    try:
        data_dir = _data_dir(kwargs)
        state = store.load_state(data_dir=data_dir)
        policy = state["policy"]
        changed = []
        for key in ("per_tx_cap", "daily_cap", "confirm_above"):
            if key in args and args.get(key) is not None:
                try:
                    value = float(args.get(key))
                except (TypeError, ValueError):
                    return _error("%s must be a number." % key)
                if value <= 0:
                    return _error("%s must be greater than zero." % key)
                policy[key] = value
                changed.append(key)
        if "allowlist" in args and args.get("allowlist") is not None:
            allowlist = args.get("allowlist")
            if not isinstance(allowlist, list):
                return _error("Allowlist must be a list of addresses.")
            cleaned = [str(a).strip() for a in allowlist if str(a).strip()]
            policy["allowlist"] = cleaned
            changed.append("allowlist")
        if args.get("reset_spent"):
            state["spent_today"] = 0.0
            changed.append("reset_spent")
        if not changed:
            return _error("Nothing to change. Give per_tx_cap, daily_cap, confirm_above, allowlist, or reset_spent.")
        store.save_state(state, data_dir=data_dir)
        return _ok(
            {
                "ok": True,
                "changed": changed,
                "policy": policy,
                "spent_today": state["spent_today"],
            }
        )
    except Exception as exc:
        return _error("Recall Vault set policy failed: %s" % (exc,))


def recall_vault_ledger(args, **kwargs):
    """Show recent ledger entries, most recent first."""
    try:
        data_dir = _data_dir(kwargs)
        try:
            limit = int(args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 100))
        entries = store.load_ledger(data_dir=data_dir)
        recent = list(reversed(entries))[:limit]
        return _ok(
            {
                "ok": True,
                "count": len(entries),
                "shown": len(recent),
                "entries": recent,
            }
        )
    except Exception as exc:
        return _error("Recall Vault ledger failed: %s" % (exc,))


def recall_vault_remember(args, **kwargs):
    """Remember one plain-language rule."""
    try:
        rule = str(args.get("rule") or "").strip()
        if not rule:
            return _error("Need a rule to remember.")
        data_dir = _data_dir(kwargs)
        count = store.remember_rule(rule, data_dir=data_dir)
        state = store.load_state(data_dir=data_dir)
        return _ok(
            {
                "ok": True,
                "remembered": rule,
                "rule_count": count,
                "rules": state["rules"],
            }
        )
    except Exception as exc:
        return _error("Recall Vault remember failed: %s" % (exc,))


HANDLERS = {
    "recall_vault_check": recall_vault_check,
    "recall_vault_policy": recall_vault_policy,
    "recall_vault_set_policy": recall_vault_set_policy,
    "recall_vault_ledger": recall_vault_ledger,
    "recall_vault_remember": recall_vault_remember,
}
