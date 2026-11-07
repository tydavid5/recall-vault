"""Recall Vault, a Hermes plugin.

Gives every Hermes agent a policy vault and a memory of its own money
rules. The user writes plain rules once, and the plugin enforces them
on every payment-like tool call through the pre_tool_call hook:
under the cap it proceeds, over the cap it blocks with a plain-language
reason, over the confirm threshold it escalates to the human approval
gate, and unknown recipients are refused.

Dry-run only in this version: no real money moves, no transaction
hashes are faked, and the NEAR payment itself is not executed by this
plugin. recall_vault_check is a dry check that logs its decision.

Ported from Recall Wallet, whose NEAR testnet vault was verified with
per-transaction cap 1 NEAR, daily cap 2 NEAR, confirm threshold 0.5 NEAR,
and allowlist [recallwallet-freelancer.testnet].
"""

import json
import logging
import threading

try:
    from . import schemas, tools
    from .policy import check_payment
    from . import store
except ImportError:
    import schemas
    import tools
    from policy import check_payment
    import store

logger = logging.getLogger(__name__)

_TOOLSET = "recall_vault"

# Tool names that can move money, and the argument keys that carry an
# amount and a recipient. A tool is only intercepted when its name looks
# like a payment AND its args carry both an amount and a recipient, so
# send_message, send_email, and other non-payment tools are never blocked.
_PAYMENT_NAME_WORDS = (
    "pay",
    "payment",
    "transfer",
    "withdraw",
    "payout",
    "settle",
)
_AMOUNT_KEYS = (
    "amount",
    "amount_near",
    "near_amount",
    "payment_amount",
    "value",
    "total",
    "sum",
)
_RECIPIENT_KEYS = (
    "to",
    "recipient",
    "to_address",
    "destination",
    "payee",
    "address",
    "account",
)

# Payments the pre_tool_call hook allowed, waiting for post_tool_call
# to confirm the tool actually ran. Keyed by task_id.
_PENDING = {}
_PENDING_LOCK = threading.Lock()


def _extract_payment(tool_name, args):
    """Return (amount, to) for a payment-like tool call, else None."""
    if not isinstance(args, dict):
        return None
    name = str(tool_name or "").lower()
    looks_like_payment = any(word in name for word in _PAYMENT_NAME_WORDS)
    if "send" in name and ("money" in name or "payment" in name or "near" in name):
        looks_like_payment = True
    if not looks_like_payment:
        return None
    amount = None
    for key in _AMOUNT_KEYS:
        if key in args and args.get(key) is not None:
            try:
                amount = float(args.get(key))
                break
            except (TypeError, ValueError):
                return None
    if amount is None:
        return None
    to_address = None
    for key in _RECIPIENT_KEYS:
        if key in args and args.get(key):
            to_address = str(args.get(key)).strip()
            if to_address:
                break
    if not to_address:
        return None
    return amount, to_address


def _pre_tool_call(tool_name, args, task_id, **kwargs):
    """Hook: before any tool executes. Blocks or escalates payments only."""
    try:
        if str(tool_name or "").startswith("recall_vault_"):
            return None
        payment = _extract_payment(tool_name, args)
        if payment is None:
            return None
        amount, to_address = payment
        try:
            state = store.load_state()
        except Exception as exc:
            logger.warning("Recall Vault could not load its policy: %s", exc)
            return {
                "action": "block",
                "message": "Recall Vault could not load its money policy, so this payment is blocked until the policy loads.",
            }
        result = check_payment(
            state["policy"],
            amount,
            to_address,
            spent_today=state["spent_today"],
            confirmed=False,
        )
        decision = result["decision"]
        if decision == "BLOCKED":
            store.record_entry(
                {
                    "decision": "BLOCKED",
                    "rule": result.get("rule"),
                    "reason": result["reason"],
                    "amount": amount,
                    "to": to_address,
                    "tool": tool_name,
                }
            )
            return {"action": "block", "message": result["reason"]}
        if decision == "NEEDS_CONFIRMATION":
            store.record_entry(
                {
                    "decision": "NEEDS_CONFIRMATION",
                    "rule": result.get("rule"),
                    "reason": result["reason"],
                    "amount": amount,
                    "to": to_address,
                    "tool": tool_name,
                }
            )
            return {"action": "approve", "message": result["reason"]}
        with _PENDING_LOCK:
            _PENDING.setdefault(str(task_id or ""), []).append(
                {"tool": tool_name, "amount": amount, "to": to_address}
            )
        return None
    except Exception as exc:
        logger.warning("Recall Vault pre_tool_call failed: %s", exc)
        return None


def _post_tool_call(tool_name, args, result, task_id, duration_ms=None, **kwargs):
    """Hook: after any tool returns. Logs allowed payments that ran."""
    try:
        if str(tool_name or "").startswith("recall_vault_"):
            return None
        with _PENDING_LOCK:
            pending = _PENDING.pop(str(task_id or ""), [])
        if not pending:
            return None
        entry = None
        for item in pending:
            if item["tool"] == tool_name:
                entry = item
                break
        if entry is None:
            entry = pending[0]
        text = result if isinstance(result, str) else json.dumps(result or "")
        lowered = text.lower()
        if '"error"' in lowered or '"ok": false' in lowered or "error:" in lowered:
            return None
        store.record_allowed(entry["amount"], entry["to"])
        return None
    except Exception as exc:
        logger.warning("Recall Vault post_tool_call failed: %s", exc)
        return None


def _pre_llm_call(session_id=None, user_message=None, conversation_history=None, is_first_turn=False, model=None, platform=None, **kwargs):
    """Hook: once per turn, remind the model of the money rules."""
    try:
        state = store.load_state()
        policy = state["policy"]
        allowlist = ", ".join(policy.get("allowlist") or []) or "none set"
        rules = "; ".join(state["rules"][:4]) if state["rules"] else "none"
        text = (
            "Recall Vault money rules (NEAR, dry-run, no money moves): "
            "per-transaction cap %s NEAR, daily cap %s NEAR, "
            "confirm above %s NEAR, spent today %s NEAR, "
            "allowlist: %s. Remembered rules: %s. "
            "Check any payment with recall_vault_check before paying, "
            "and never pay an address that is not allowlisted without asking the user first."
            % (
                policy.get("per_tx_cap"),
                policy.get("daily_cap"),
                policy.get("confirm_above"),
                state["spent_today"],
                allowlist,
                rules,
            )
        )
        return {"context": text}
    except Exception as exc:
        logger.warning("Recall Vault pre_llm_call failed: %s", exc)
        return None


def _handle_recall_vault(raw_args, **kwargs):
    """Slash command: /recall-vault [policy|ledger|check <amount> <to>|remember <rule>]"""
    try:
        text = str(raw_args or "").strip()
        if not text or text == "help":
            return (
                "Recall Vault: /recall-vault policy | /recall-vault ledger | "
                "/recall-vault check <amount> <to> | /recall-vault remember <rule>"
            )
        parts = text.split(None, 1)
        command = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if command == "policy":
            payload = json.loads(tools.recall_vault_policy({}))
            if not payload.get("ok"):
                return "Recall Vault: %s" % payload.get("error", "policy failed.")
            policy = payload
            return (
                "Recall Vault policy (NEAR, dry-run): per-transaction cap %s, "
                "daily cap %s, confirm above %s, spent today %s, "
                "allowlist: %s. Rules: %s"
                % (
                    policy.get("per_tx_cap"),
                    policy.get("daily_cap"),
                    policy.get("confirm_above"),
                    policy.get("spent_today"),
                    ", ".join(policy.get("allowlist") or []) or "none set",
                    "; ".join(policy.get("rules") or []) or "none",
                )
            )
        if command == "ledger":
            payload = json.loads(tools.recall_vault_ledger({"limit": 10}))
            if not payload.get("ok"):
                return "Recall Vault: %s" % payload.get("error", "ledger failed.")
            lines = ["Recall Vault ledger (%s entries):" % payload.get("count", 0)]
            for entry in payload.get("entries") or []:
                lines.append(
                    "- %s %s NEAR to %s: %s (%s)"
                    % (
                        entry.get("decision"),
                        entry.get("amount"),
                        entry.get("to"),
                        entry.get("reason"),
                        entry.get("at", ""),
                    )
                )
            return "\n".join(lines)
        if command == "check":
            fields = rest.split()
            if len(fields) < 2:
                return "Usage: /recall-vault check <amount> <to>"
            try:
                amount = float(fields[0])
            except ValueError:
                return "Usage: /recall-vault check <amount> <to>"
            payload = json.loads(
                tools.recall_vault_check({"amount": amount, "to": fields[1]})
            )
            if not payload.get("ok"):
                return "Recall Vault: %s" % payload.get("error", "check failed.")
            return "%s: %s" % (payload.get("decision"), payload.get("reason"))
        if command == "remember":
            if not rest.strip():
                return "Usage: /recall-vault remember <rule>"
            payload = json.loads(tools.recall_vault_remember({"rule": rest.strip()}))
            if not payload.get("ok"):
                return "Recall Vault: %s" % payload.get("error", "remember failed.")
            return "Remembered: %s" % rest.strip()
        return (
            "Unknown Recall Vault command. Try: /recall-vault policy | "
            "/recall-vault ledger | /recall-vault check <amount> <to> | "
            "/recall-vault remember <rule>"
        )
    except Exception as exc:
        return "Recall Vault command failed: %s" % (exc,)


def register(ctx):
    """Wire schemas to handlers and register hooks and the slash command."""
    ctx.register_tool(
        name="recall_vault_check",
        toolset=_TOOLSET,
        schema=schemas.CHECK,
        handler=tools.recall_vault_check,
    )
    ctx.register_tool(
        name="recall_vault_policy",
        toolset=_TOOLSET,
        schema=schemas.POLICY,
        handler=tools.recall_vault_policy,
    )
    ctx.register_tool(
        name="recall_vault_set_policy",
        toolset=_TOOLSET,
        schema=schemas.SET_POLICY,
        handler=tools.recall_vault_set_policy,
    )
    ctx.register_tool(
        name="recall_vault_ledger",
        toolset=_TOOLSET,
        schema=schemas.LEDGER,
        handler=tools.recall_vault_ledger,
    )
    ctx.register_tool(
        name="recall_vault_remember",
        toolset=_TOOLSET,
        schema=schemas.REMEMBER,
        handler=tools.recall_vault_remember,
    )
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_command(
        "recall-vault",
        handler=_handle_recall_vault,
        description="Show Recall Vault policy, ledger, or check a payment",
        args_hint="[policy|ledger|check <amount> <to>|remember <rule>]",
    )
