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

Changes in 0.1.1, after catalog review: model calls to
recall_vault_set_policy and recall_vault_remember are sent to the human
approval gate, with the exact change shown, instead of running free.
The user changes policy directly with the user-only /recall-vault set
command. An empty allowlist means nobody is allowed. Payments sent to
approval are recorded as pending, keyed by tool_call_id, so approved
payments count toward the daily cap. The model-set confirmed flag on
recall_vault_check is gone; confirmation is only the human approval
prompt in Hermes.

Ported from Recall Wallet, whose NEAR testnet vault was verified with
per-transaction cap 1 NEAR, daily cap 2 NEAR, confirm threshold 0.5 NEAR,
and allowlist [recallwallet-freelancer.testnet].
"""

import json
import logging
import threading

try:
    from . import schemas, tools
    from .policy import check_payment, default_policy
    from . import store
except ImportError:
    import schemas
    import tools
    from policy import check_payment, default_policy
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

# Payments the pre_tool_call hook allowed or sent to approval, waiting
# for post_tool_call to confirm the tool actually ran. Keyed by
# tool_call_id when Hermes passes one, else by task_id, so two calls in
# one task never clobber each other.
_PENDING = {}
_PENDING_LOCK = threading.Lock()


def _pending_key(tool_call_id, task_id):
    """Key for one pending payment: the tool call id, else the task id."""
    if tool_call_id:
        return "call:%s" % str(tool_call_id)
    return "task:%s" % str(task_id or "")


def _set_policy_diff(args):
    """Plain-language diff of a set_policy call, old value to new value."""
    try:
        state = store.load_state()
        policy = state["policy"]
        spent_today = state["spent_today"]
    except Exception:
        policy = default_policy()
        spent_today = 0.0
    parts = []
    if not isinstance(args, dict) or not args:
        parts.append("no field given")
    else:
        for key in ("per_tx_cap", "daily_cap", "confirm_above"):
            if key in args and args.get(key) is not None:
                parts.append("%s: %s -> %s" % (key, policy.get(key), args.get(key)))
        if "allowlist" in args and args.get("allowlist") is not None:
            old = list(policy.get("allowlist") or [])
            new = args.get("allowlist")
            new = list(new) if isinstance(new, list) else new
            parts.append("allowlist: %s -> %s" % (old, new))
        if args.get("reset_spent"):
            parts.append(
                "reset_spent: false -> true (spent today %s -> 0.0)" % spent_today
            )
    if not parts:
        parts.append("no recognised field given")
    return (
        "Recall Vault policy change needs your approval: %s. "
        "Approve only if you, the user, asked for this change."
        % "; ".join(parts)
    )


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


def _pre_tool_call(tool_name, args, task_id, tool_call_id=None, **kwargs):
    """Hook: before any tool executes. Blocks or escalates payments only."""
    try:
        name = str(tool_name or "")
        # Loosening the guard needs a human. A model call to set_policy
        # or remember goes to the approval gate with the exact change
        # shown, so a prompt-injected turn cannot loosen the guard and
        # then pay. The user-only /recall-vault set command is not a
        # model tool call, so it is not gated here.
        if name == "recall_vault_set_policy":
            return {"action": "approve", "message": _set_policy_diff(args)}
        if name == "recall_vault_remember":
            rule = ""
            if isinstance(args, dict):
                rule = str(args.get("rule") or "").strip()
            return {
                "action": "approve",
                "message": (
                    "Recall Vault rule to remember needs your approval: "
                    '"%s". Remembered rules are shown to the model every turn.'
                    % rule
                ),
            }
        if name.startswith("recall_vault_"):
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
            # Record it as pending too, so an approved payment counts
            # toward the daily cap once the tool runs clean.
            with _PENDING_LOCK:
                _PENDING[_pending_key(tool_call_id, task_id)] = {
                    "tool": tool_name,
                    "amount": amount,
                    "to": to_address,
                }
            return {"action": "approve", "message": result["reason"]}
        with _PENDING_LOCK:
            _PENDING[_pending_key(tool_call_id, task_id)] = {
                "tool": tool_name,
                "amount": amount,
                "to": to_address,
            }
        return None
    except Exception as exc:
        logger.warning("Recall Vault pre_tool_call failed: %s", exc)
        return None


def _post_tool_call(tool_name, args, result, task_id, duration_ms=None, tool_call_id=None, **kwargs):
    """Hook: after any tool returns. Logs payments that ran, by call id."""
    try:
        if str(tool_name or "").startswith("recall_vault_"):
            return None
        key = _pending_key(tool_call_id, task_id)
        with _PENDING_LOCK:
            entry = _PENDING.pop(key, None)
        if entry is None:
            return None
        if entry["tool"] != tool_name:
            with _PENDING_LOCK:
                _PENDING[key] = entry
            return None
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
        allowlist = policy.get("allowlist")
        if allowlist:
            allowlist_text = ", ".join(allowlist)
        elif isinstance(allowlist, list):
            allowlist_text = "empty, nobody is allowed"
        else:
            allowlist_text = "none set"
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
                allowlist_text,
                rules,
            )
        )
        return {"context": text}
    except Exception as exc:
        logger.warning("Recall Vault pre_llm_call failed: %s", exc)
        return None


def _handle_recall_vault(raw_args, **kwargs):
    """Slash command: /recall-vault [policy|ledger|check <amount> <to>|remember <rule>|set <key=value> ...]"""
    try:
        text = str(raw_args or "").strip()
        if not text or text == "help":
            return (
                "Recall Vault: /recall-vault policy | /recall-vault ledger | "
                "/recall-vault check <amount> <to> | /recall-vault remember <rule> | "
                "/recall-vault set per_tx_cap=1 daily_cap=2 confirm_above=0.5 allowlist=a,b reset_spent=true"
            )
        parts = text.split(None, 1)
        command = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if command == "policy":
            payload = json.loads(tools.recall_vault_policy({}))
            if not payload.get("ok"):
                return "Recall Vault: %s" % payload.get("error", "policy failed.")
            policy = payload
            allowlist = ", ".join(policy.get("allowlist") or [])
            if isinstance(policy.get("allowlist"), list) and len(policy["allowlist"]) == 0:
                allowlist = "empty, nobody is allowed"
            elif not allowlist:
                allowlist = "none set"
            return (
                "Recall Vault policy (NEAR, dry-run): per-transaction cap %s, "
                "daily cap %s, confirm above %s, spent today %s, "
                "allowlist: %s. Rules: %s"
                % (
                    policy.get("per_tx_cap"),
                    policy.get("daily_cap"),
                    policy.get("confirm_above"),
                    policy.get("spent_today"),
                    allowlist,
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
        if command == "set":
            if not rest.strip():
                return (
                    "Usage: /recall-vault set per_tx_cap=1 daily_cap=2 "
                    "confirm_above=0.5 allowlist=a,b reset_spent=true. "
                    "An empty allowlist (allowlist=) means nobody is allowed."
                )
            parsed = {}
            for token in rest.split():
                if "=" not in token:
                    return "Bad field %r. Use key=value, for example per_tx_cap=1." % token
                key, value = token.split("=", 1)
                key = key.strip().lower()
                value = value.strip()
                if key in ("per_tx_cap", "daily_cap", "confirm_above"):
                    try:
                        parsed[key] = float(value)
                    except ValueError:
                        return "Bad value for %s: %r must be a number." % (key, value)
                elif key == "allowlist":
                    parsed["allowlist"] = (
                        [a.strip() for a in value.split(",") if a.strip()]
                        if value
                        else []
                    )
                elif key == "reset_spent":
                    parsed["reset_spent"] = value.lower() in ("true", "1", "yes", "y")
                else:
                    return (
                        "Unknown policy field %r. Use per_tx_cap, daily_cap, "
                        "confirm_above, allowlist, or reset_spent." % key
                    )
            if not parsed:
                return (
                    "Usage: /recall-vault set per_tx_cap=1 daily_cap=2 "
                    "confirm_above=0.5 allowlist=a,b reset_spent=true"
                )
            payload = json.loads(tools.recall_vault_set_policy(parsed))
            if not payload.get("ok"):
                return "Recall Vault: %s" % payload.get("error", "set policy failed.")
            policy = payload.get("policy") or {}
            return (
                "Policy updated: changed %s. Per-transaction cap %s, "
                "daily cap %s, confirm above %s, spent today %s, "
                "allowlist: %s"
                % (
                    ", ".join(payload.get("changed") or []),
                    policy.get("per_tx_cap"),
                    policy.get("daily_cap"),
                    policy.get("confirm_above"),
                    payload.get("spent_today"),
                    ", ".join(policy.get("allowlist") or []) or "empty, nobody is allowed",
                )
            )
        return (
            "Unknown Recall Vault command. Try: /recall-vault policy | "
            "/recall-vault ledger | /recall-vault check <amount> <to> | "
            "/recall-vault remember <rule> | /recall-vault set per_tx_cap=1 daily_cap=2 confirm_above=0.5 allowlist=a,b reset_spent=true"
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
        args_hint="[policy|ledger|check <amount> <to>|remember <rule>|set <key=value> ...]",
    )
