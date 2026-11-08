"""Recall Vault policy tests.

Runs the policy engine through the same cases as the Recall Wallet
verification, adapted to NEAR amounts with caps per-tx 1, daily 2,
confirm 0.5, allowlist [recallwallet-freelancer.testnet].

Sequence, with spent tracked only on ALLOWED payments:
  0.2 to freelancer, allowed, spent 0.2
  1.2 to freelancer, blocked perTxCap, spent stays 0.2
  0.7 to freelancer, needs confirmation, spent stays 0.2
  0.7 to freelancer again, still needs confirmation, never allowed
    from the check alone, spent stays 0.2
  0.4 to freelancer, allowed, spent 0.6
  0.5 to freelancer, allowed, spent 1.1
  0.5 to freelancer, allowed, spent 1.6
  0.5 to freelancer, blocked dailyCap (1.6 + 0.5 = 2.1 > 2), spent stays 1.6
  0.1 to some-random.testnet, blocked allowlist, spent stays 1.6

There is no confirmed flag: a payment above the confirm threshold
always returns NEEDS_CONFIRMATION, and confirmation is only the human
approval prompt in Hermes, never a flag the model sets.

Run: python3 -m pytest tests/test_policy.py -v
  or: python3 tests/test_policy.py
No Hermes install is needed. No network, no money, no keys.
"""

import importlib.util
import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from policy import check_payment, default_policy  # noqa: E402
from store import load_state, record_allowed, record_entry, load_ledger  # noqa: E402

FREELANCER = "recallwallet-freelancer.testnet"
RANDOM = "some-random.testnet"


def _check(policy, amount, to, spent):
    return check_payment(policy, amount, to, spent_today=spent)


def _load_init_module():
    spec = importlib.util.spec_from_file_location(
        "recall_vault_init", Path(__file__).resolve().parent.parent / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    # A payment above the confirm threshold never becomes ALLOWED from
    # the check alone. There is no confirmed flag to set.
    result = _check(policy, 0.7, FREELANCER, spent)
    assert result["decision"] == "NEEDS_CONFIRMATION", result
    assert spent == 0.2

    result = _check(policy, 0.4, FREELANCER, spent)
    assert result["decision"] == "ALLOWED", result
    spent = round(spent + 0.4, 4)
    assert spent == 0.6

    result = _check(policy, 0.5, FREELANCER, spent)
    assert result["decision"] == "ALLOWED", result
    spent = round(spent + 0.5, 4)
    assert spent == 1.1

    result = _check(policy, 0.5, FREELANCER, spent)
    assert result["decision"] == "ALLOWED", result
    spent = round(spent + 0.5, 4)
    assert spent == 1.6

    result = _check(policy, 0.5, FREELANCER, spent)
    assert result["decision"] == "BLOCKED", result
    assert result["rule"] == "dailyCap", result

    result = _check(policy, 0.1, RANDOM, spent)
    assert result["decision"] == "BLOCKED", result
    assert result["rule"] == "allowlist", result

    # Invalid amounts are blocked, never allowed.
    assert _check(policy, 0, FREELANCER, spent)["decision"] == "BLOCKED"
    assert _check(policy, -1, FREELANCER, spent)["decision"] == "BLOCKED"
    assert _check(policy, "not-a-number", FREELANCER, spent)["decision"] == "BLOCKED"


def test_empty_allowlist_blocks_everyone():
    """An empty allowlist means nobody is allowed, not allow all."""
    policy = default_policy()
    policy["allowlist"] = []
    for to in (FREELANCER, RANDOM):
        result = _check(policy, 0.1, to, 0.0)
        assert result["decision"] == "BLOCKED", result
        assert result["rule"] == "allowlist", result
        assert "nobody" in result["reason"].lower(), result
    # A missing allowlist falls back to the default allowlist.
    policy = default_policy()
    del policy["allowlist"]
    assert _check(policy, 0.2, FREELANCER, 0.0)["decision"] == "ALLOWED"
    assert _check(policy, 0.1, RANDOM, 0.0)["decision"] == "BLOCKED"


def test_no_confirmed_flag():
    """check_payment has no confirmed parameter, and the CHECK schema
    has no confirmed property. A payment above the confirm threshold
    never returns ALLOWED from the check alone."""
    assert "confirmed" not in inspect.signature(check_payment).parameters
    import schemas

    assert "confirmed" not in schemas.CHECK["parameters"]["properties"]
    policy = default_policy()
    result = _check(policy, 0.7, FREELANCER, 0.0)
    assert result["decision"] == "NEEDS_CONFIRMATION", result
    assert result["rule"] == "confirmThreshold", result
    # Even a model-passed confirmed argument is ignored by the handler.
    import tools

    with tempfile.TemporaryDirectory() as tmp:
        payload = tools.recall_vault_check(
            {"amount": 0.7, "to": FREELANCER, "confirmed": True}, data_dir=Path(tmp)
        )
        import json

        body = json.loads(payload)
        assert body["decision"] == "NEEDS_CONFIRMATION", body
        assert load_state(data_dir=Path(tmp))["spent_today"] == 0.0


def test_set_policy_and_remember_are_approval_gated():
    """pre_tool_call sends set_policy and remember to the human approval
    gate, with the exact change shown. Other own tools are not gated."""
    module = _load_init_module()
    with tempfile.TemporaryDirectory() as tmp:
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            result = module._pre_tool_call(
                "recall_vault_set_policy",
                {"per_tx_cap": 5.0, "allowlist": []},
                task_id="t1",
            )
            assert result is not None and result["action"] == "approve", result
            message = result["message"]
            assert "per_tx_cap" in message, message
            assert "1.0" in message and "5.0" in message, message
            assert "allowlist" in message, message
            assert FREELANCER in message, message
            assert "[]" in message, message

            result = module._pre_tool_call(
                "recall_vault_set_policy",
                {"reset_spent": True},
                task_id="t1",
            )
            assert result["action"] == "approve", result
            assert "reset_spent" in result["message"], result

            rule = "Never pay on weekends."
            result = module._pre_tool_call(
                "recall_vault_remember", {"rule": rule}, task_id="t1"
            )
            assert result is not None and result["action"] == "approve", result
            assert rule in result["message"], result

            # Other own tools are not intercepted.
            assert module._pre_tool_call("recall_vault_check", {"amount": 0.4, "to": FREELANCER}, task_id="t1") is None
            assert module._pre_tool_call("recall_vault_policy", {}, task_id="t1") is None
            assert module._pre_tool_call("recall_vault_ledger", {}, task_id="t1") is None
        finally:
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home


def test_pending_keyed_by_tool_call_id():
    """Two call ids for the same task do not clobber each other, and
    post_tool_call for one id books only that payment."""
    module = _load_init_module()
    with tempfile.TemporaryDirectory() as tmp:
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            module._PENDING.clear()
            # Two allowed payments in the same task, different call ids.
            assert module._pre_tool_call(
                "pay_freelancer", {"amount": 0.4, "to": FREELANCER},
                task_id="t1", tool_call_id="call-A",
            ) is None
            assert module._pre_tool_call(
                "pay_freelancer", {"amount": 0.3, "to": FREELANCER},
                task_id="t1", tool_call_id="call-B",
            ) is None
            assert len(module._PENDING) == 2
            # Booking call A books only 0.4, call B stays pending.
            module._post_tool_call(
                "pay_freelancer", {"amount": 0.4, "to": FREELANCER},
                {"ok": True}, task_id="t1", tool_call_id="call-A",
            )
            assert load_state()["spent_today"] == 0.4
            assert len(module._PENDING) == 1
            module._post_tool_call(
                "pay_freelancer", {"amount": 0.3, "to": FREELANCER},
                {"ok": True}, task_id="t1", tool_call_id="call-B",
            )
            assert load_state()["spent_today"] == 0.7
            assert len(module._PENDING) == 0
            # A payment sent to approval is pending too, and an approved
            # run counts toward the daily cap.
            result = module._pre_tool_call(
                "pay_freelancer", {"amount": 0.7, "to": FREELANCER},
                task_id="t1", tool_call_id="call-C",
            )
            assert result is not None and result["action"] == "approve", result
            assert len(module._PENDING) == 1
            module._post_tool_call(
                "pay_freelancer", {"amount": 0.7, "to": FREELANCER},
                {"ok": True}, task_id="t1", tool_call_id="call-C",
            )
            assert load_state()["spent_today"] == 1.4
            assert len(module._PENDING) == 0
        finally:
            module._PENDING.clear()
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home


def test_slash_set_command():
    """The user-only /recall-vault set command applies key=value pairs."""
    module = _load_init_module()
    with tempfile.TemporaryDirectory() as tmp:
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            # The slash handler uses tools with the default data dir,
            # which resolves through HERMES_HOME.
            out = module._handle_recall_vault("set per_tx_cap=2 daily_cap=5")
            assert "Policy updated" in out, out
            assert load_state()["policy"]["per_tx_cap"] == 2.0
            assert load_state()["policy"]["daily_cap"] == 5.0
            out = module._handle_recall_vault("set allowlist=a.testnet,b.testnet")
            assert load_state()["policy"]["allowlist"] == ["a.testnet", "b.testnet"], out
            out = module._handle_recall_vault("set allowlist=")
            assert load_state()["policy"]["allowlist"] == [], out
            assert "unknown" in module._handle_recall_vault("set bogus=1").lower()
            assert "usage" in module._handle_recall_vault("set").lower()
        finally:
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home


def test_store_and_ledger(tmp_path=None):
    """Allowed payments update spent today and land in the ledger."""
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
    module = _load_init_module()
    assert module._extract_payment("read_file", {"path": "README.md"}) is None
    assert module._extract_payment("send_message", {"text": "hello"}) is None
    assert module._extract_payment("send_email", {"to": "a@b.com", "subject": "hi"}) is None
    assert module._extract_payment("pay_freelancer", {"amount": 0.4, "to": FREELANCER}) == (0.4, FREELANCER)
    assert module._extract_payment("transfer", {"amount": 5, "to": RANDOM}) == (5.0, RANDOM)
    # A payment name without an amount or recipient is not intercepted.
    assert module._extract_payment("pay_freelancer", {"note": "hi"}) is None


if __name__ == "__main__":
    test_sequence()
    test_empty_allowlist_blocks_everyone()
    test_no_confirmed_flag()
    test_set_policy_and_remember_are_approval_gated()
    test_pending_keyed_by_tool_call_id()
    test_slash_set_command()
    test_store_and_ledger()
    test_hooks_do_not_block_unrelated_tools()
    print("All Recall Vault policy tests passed.")
