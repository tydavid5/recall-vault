"""Recall Vault policy tests.

Runs the policy engine through the same cases as the Recall Wallet
verification, adapted to NEAR amounts with caps per-tx 1, daily 2,
confirm 0.5, allowlist [recallwallet-freelancer.testnet].

Sequence, with spent tracked only on ALLOWED payments:
  0.2 to freelancer, allowed, spent 0.2
  1.2 to freelancer, blocked perTxCap, spent stays 0.2
  0.7 to freelancer, needs confirmation, spent stays 0.2
  0.7 to freelancer with confirmed, allowed, spent 0.9
  0.4 to freelancer, allowed, spent 1.3
  0.8 to freelancer, blocked dailyCap (1.3 + 0.8 = 2.1 > 2), spent stays 1.3
  0.1 to some-random.testnet, blocked allowlist, spent stays 1.3

Run: python3 -m pytest tests/test_policy.py -v
  or: python3 tests/test_policy.py
No Hermes install is needed. No network, no money, no keys.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from policy import check_payment, default_policy  # noqa: E402
from store import load_state, record_allowed, record_entry, load_ledger  # noqa: E402

FREELANCER = "recallwallet-freelancer.testnet"
RANDOM = "some-random.testnet"


def _check(policy, amount, to, spent, confirmed=False):
    return check_payment(policy, amount, to, spent_today=spent, confirmed=confirmed)


def test_sequence():
    policy = default_policy()
    assert policy["per_tx_cap"] == 1.0
    assert policy["daily_cap"] == 2.0
    assert policy["confirm_above"] == 0.5
    assert policy["allowlist"] == [FREELANCER]

    spent = 0.0

    result = _check(policy, 0.2, FREELANCER, spent)
    assert result["decision"] == "ALLOWED", result
    spent = round(spent + 0.2, 4)
    assert spent == 0.2

    result = _check(policy, 1.2, FREELANCER, spent)
    assert result["decision"] == "BLOCKED", result
    assert result["rule"] == "perTxCap", result

    result = _check(policy, 0.7, FREELANCER, spent)
    assert result["decision"] == "NEEDS_CONFIRMATION", result
    assert result["rule"] == "confirmThreshold", result

    result = _check(policy, 0.7, FREELANCER, spent, confirmed=True)
    assert result["decision"] == "ALLOWED", result
    spent = round(spent + 0.7, 4)
    assert spent == 0.9

    result = _check(policy, 0.4, FREELANCER, spent)
    assert result["decision"] == "ALLOWED", result
    spent = round(spent + 0.4, 4)
    assert spent == 1.3

    result = _check(policy, 0.8, FREELANCER, spent)
    assert result["decision"] == "BLOCKED", result
    assert result["rule"] == "dailyCap", result

    result = _check(policy, 0.1, RANDOM, spent)
    assert result["decision"] == "BLOCKED", result
    assert result["rule"] == "allowlist", result

    # Invalid amounts are blocked, never allowed.
    assert _check(policy, 0, FREELANCER, spent)["decision"] == "BLOCKED"
    assert _check(policy, -1, FREELANCER, spent)["decision"] == "BLOCKED"
    assert _check(policy, "not-a-number", FREELANCER, spent)["decision"] == "BLOCKED"


def test_store_and_ledger(tmp_path=None):
    """Allowed payments update spent today and land in the ledger."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        state = load_state(data_dir=data_dir)
        assert state["spent_today"] == 0.0
        assert state["policy"]["per_tx_cap"] == 1.0
        record_allowed(0.2, FREELANCER, data_dir=data_dir)
        record_allowed(0.4, FREELANCER, data_dir=data_dir)
        state = load_state(data_dir=data_dir)
        assert abs(state["spent_today"] - 0.6) < 1e-9
        entries = load_ledger(data_dir=data_dir)
        assert len(entries) == 2
        assert entries[0]["decision"] == "ALLOWED"
        assert entries[1]["amount"] == 0.4
        record_entry(
            {"decision": "BLOCKED", "rule": "perTxCap", "reason": "test", "amount": 1.2, "to": FREELANCER},
            data_dir=data_dir,
        )
        assert len(load_ledger(data_dir=data_dir)) == 3


def test_hooks_do_not_block_unrelated_tools():
    """The pre_tool_call hook only intercepts payment-like tools."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "recall_vault_init", Path(__file__).resolve().parent.parent / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._extract_payment("read_file", {"path": "README.md"}) is None
    assert module._extract_payment("send_message", {"text": "hello"}) is None
    assert module._extract_payment("send_email", {"to": "a@b.com", "subject": "hi"}) is None
    assert module._extract_payment("pay_freelancer", {"amount": 0.4, "to": FREELANCER}) == (0.4, FREELANCER)
    assert module._extract_payment("transfer", {"amount": 5, "to": RANDOM}) == (5.0, RANDOM)
    # A payment name without an amount or recipient is not intercepted.
    assert module._extract_payment("pay_freelancer", {"note": "hi"}) is None


if __name__ == "__main__":
    test_sequence()
    test_store_and_ledger()
    test_hooks_do_not_block_unrelated_tools()
    print("All Recall Vault policy tests passed.")
