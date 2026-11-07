"""Recall Vault policy engine.

Pure logic, no chain, no funds, no network, no Hermes imports.
Ported from Recall Wallet (~/workspace/recall-wallet/app/policy.js),
with amounts in NEAR to match the verified NEAR testnet vault.

Default policy matches the verified vault:
  per-transaction cap 1 NEAR, daily cap 2 NEAR,
  confirm threshold 0.5 NEAR, allowlist with recallwallet-freelancer.testnet.

A payment check returns one of:
  ALLOWED, BLOCKED (with the exact rule that stopped it),
  NEEDS_CONFIRMATION (above the confirm threshold, below the cap).

Check order matters, and matches the Recall Wallet verification:
  1. invalid amount
  2. per-transaction cap
  3. daily cap
  4. allowlist
  5. confirm threshold
  6. allowed
"""

DEFAULT_PER_TX_CAP = 1.0
DEFAULT_DAILY_CAP = 2.0
DEFAULT_CONFIRM_ABOVE = 0.5
DEFAULT_ALLOWLIST = ["recallwallet-freelancer.testnet"]
CURRENCY = "NEAR"


def default_policy():
    """Return a fresh copy of the default policy."""
    return {
        "currency": CURRENCY,
        "per_tx_cap": DEFAULT_PER_TX_CAP,
        "daily_cap": DEFAULT_DAILY_CAP,
        "confirm_above": DEFAULT_CONFIRM_ABOVE,
        "allowlist": list(DEFAULT_ALLOWLIST),
    }


def check_payment(policy, amount, to_address, spent_today=0.0, confirmed=False):
    """Check one payment against a policy. Never mutates state.

    policy: dict with per_tx_cap, daily_cap, confirm_above, allowlist.
    amount: number, in NEAR.
    to_address: string, recipient address.
    spent_today: number, NEAR already spent today.
    confirmed: bool, the user already confirmed this payment.
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {
            "decision": "BLOCKED",
            "rule": "invalidAmount",
            "reason": "Amount is not a number.",
        }
    if amount != amount or amount in (float("inf"), float("-inf")):
        return {
            "decision": "BLOCKED",
            "rule": "invalidAmount",
            "reason": "Amount is not a finite number.",
        }
    if amount <= 0:
        return {
            "decision": "BLOCKED",
            "rule": "invalidAmount",
            "reason": "Amount must be greater than zero.",
        }

    per_tx_cap = float(policy.get("per_tx_cap", DEFAULT_PER_TX_CAP))
    daily_cap = float(policy.get("daily_cap", DEFAULT_DAILY_CAP))
    confirm_above = float(policy.get("confirm_above", DEFAULT_CONFIRM_ABOVE))
    allowlist = policy.get("allowlist") or []
    try:
        spent_today = float(spent_today or 0.0)
    except (TypeError, ValueError):
        spent_today = 0.0

    if amount > per_tx_cap:
        return {
            "decision": "BLOCKED",
            "rule": "perTxCap",
            "reason": "Amount %s NEAR exceeds the per-transaction cap of %s NEAR." % (_fmt(amount), _fmt(per_tx_cap)),
        }
    if spent_today + amount > daily_cap:
        return {
            "decision": "BLOCKED",
            "rule": "dailyCap",
            "reason": "This would take today spending to %s NEAR, over the daily cap of %s NEAR." % (_fmt(spent_today + amount), _fmt(daily_cap)),
        }
    if allowlist and to_address not in allowlist:
        return {
            "decision": "BLOCKED",
            "rule": "allowlist",
            "reason": "Address %s is not on your allowlist, and new addresses need your approval first." % (to_address,),
        }
    if amount > confirm_above and not confirmed:
        return {
            "decision": "NEEDS_CONFIRMATION",
            "rule": "confirmThreshold",
            "reason": "Amount %s NEAR is above your %s NEAR confirmation threshold. Confirm to proceed." % (_fmt(amount), _fmt(confirm_above)),
        }
    return {
        "decision": "ALLOWED",
        "rule": None,
        "reason": "Passes all spending rules.",
    }


def _fmt(value):
    """Format a NEAR amount without trailing noise."""
    text = ("%.4f" % float(value)).rstrip("0").rstrip(".")
    return text if text else "0"
