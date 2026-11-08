# Recall Vault

A policy vault and memory for agent money, as a Hermes Agent plugin.

Your agent remembers its own spending rules and cannot break them. You write
plain rules once, per-transaction cap, daily cap, confirm threshold, and
allowlist, and Recall Vault enforces them on every payment-like tool call
before it runs.

## Changes in 0.1.1

After catalog review, four fixes:

- Model calls to `recall_vault_set_policy` and `recall_vault_remember`
  are sent to the human approval gate first, which shows the exact
  change or rule text. The user changes policy directly with the
  user-only `/recall-vault set ...` command, which is not a model
  tool call. A prompt-injected turn can no longer loosen the guard
  and then pay.
- An empty allowlist means nobody is allowed, not allow all.
- Payments sent to approval are recorded as pending, keyed by
  tool_call_id, so an approved payment counts toward the daily cap.
  `post_tool_call` books only the single pending entry for that call.
- The model-set `confirmed` flag on `recall_vault_check` is gone. A
  payment above the confirm threshold always returns
  NEEDS_CONFIRMATION, and confirmation is only the human approval
  prompt in Hermes.

## What it does

- Under the cap and allowlisted: the payment proceeds.
- Over the per-transaction cap: blocked, with the rule quoted back.
- Over the daily cap: blocked, with the rule quoted back.
- Unknown recipient: blocked, new addresses need your approval first.
- Above the confirm threshold but below the cap: escalated to the human
  approval gate, so you confirm before it runs.
- Every decision is written to a local audit ledger you can read with
  `recall_vault_ledger` or `/recall-vault ledger`.
- Every turn, the agent is reminded of the current money rules, so it
  stops forgetting what it was told.

Default policy, matching the verified Recall Wallet NEAR testnet vault:

- Per-transaction cap: 1 NEAR
- Daily cap: 2 NEAR
- Confirm above: 0.5 NEAR
- Allowlist: `recallwallet-freelancer.testnet`
- Currency: NEAR only

## Dry-run only

This version moves no money and executes no payment. `recall_vault_check`
is a dry check that logs its decision. The `pre_tool_call` hook blocks or
escalates payment-like tool calls, it does not send a transaction itself.
No transaction hashes are produced or faked. Do not use this plugin as
proof that a payment settled, it is proof that a payment was checked.

## Install

Requires Hermes Agent 0.23.0 or later.

```bash
hermes plugins install tydavid5/recall-vault
hermes plugins enable recall-vault
```

Or, from a local copy:

```bash
cp -r recall-vault ~/.hermes/plugins/
hermes plugins enable recall-vault
```

Check it loaded:

```bash
hermes plugins list
hermes plugins doctor recall-vault
```

## Tools

| Tool | What it does |
| --- | --- |
| `recall_vault_check` | Check a payment: amount in NEAR, recipient. No confirmed flag. Returns ALLOWED, BLOCKED with the rule, or NEEDS_CONFIRMATION. Allowed checks are logged and count toward today. |
| `recall_vault_policy` | Show the current policy, spent today, and remembered rules. An empty allowlist is noted as meaning nobody is allowed. |
| `recall_vault_set_policy` | Update caps, threshold, or allowlist. Give only the fields to change. Model calls are sent to the human approval gate, which shows the exact change. An empty allowlist means nobody is allowed. |
| `recall_vault_ledger` | Show the audit ledger, most recent first. |
| `recall_vault_remember` | Remember a plain-language money rule. Model calls are sent to the human approval gate, which shows the rule text. |

## Slash command

```
/recall-vault policy
/recall-vault ledger
/recall-vault check 0.4 recallwallet-freelancer.testnet
/recall-vault remember Never pay a brand-new address without asking first.
/recall-vault set per_tx_cap=1 daily_cap=2 confirm_above=0.5 allowlist=a,b reset_spent=true
```

The `set` command is user-only: it runs from your own typed command,
not from a model tool call, so it is not sent to the approval gate.
Give key=value pairs, allowlist as a comma-separated list. An empty
allowlist (`allowlist=`) means nobody is allowed.

## Demo cases

These are the same cases the Recall Wallet vault passed on NEAR testnet.

1. Pay 0.2 NEAR to `recallwallet-freelancer.testnet`: ALLOWED, spent today 0.2.
2. Pay 1.2 NEAR to the freelancer: BLOCKED, rule `perTxCap`, 1.2 exceeds the 1 NEAR cap.
3. Pay 0.7 NEAR to the freelancer: NEEDS_CONFIRMATION, rule `confirmThreshold`, 0.7 is above 0.5. It stays NEEDS_CONFIRMATION until the human confirms it in Hermes. There is no model flag that makes it ALLOWED.
4. Pay 0.4 NEAR to the freelancer: ALLOWED, spent today 0.6.
5. Pay 0.5 NEAR to the freelancer: ALLOWED, spent today 1.1.
6. Pay 0.5 NEAR to the freelancer: ALLOWED, spent today 1.6.
7. Pay 0.5 NEAR to the freelancer: BLOCKED, rule `dailyCap`, 1.6 plus 0.5 is 2.1, over the 2 NEAR daily cap.
8. Pay 0.1 NEAR to `some-random.testnet`: BLOCKED, rule `allowlist`.

Run the same cases locally, no Hermes needed:

```bash
python3 tests/test_policy.py
```

## How it differs from other guards

- `airlock` guards against prompt injection, tainted instructions reaching the
  model. Recall Vault guards money, it does not inspect prompts for injection.
- `memory-shield` guards writes to MEMORY.md and USER.md. Recall Vault guards
  payments and transfers, it does not guard memory files.
- AgentGuard caps LLM call spend, tokens and model cost. Recall Vault caps
  payments to other people, per transaction and per day, with an allowlist
  and a confirm threshold. Different money, different rules.
- A prompt or a skill can tell an agent to be careful with money. Recall
  Vault does more than a prompt or a skill can do: its `pre_tool_call`
  hook blocks the tool call before it executes, and its `pre_llm_call`
  hook reminds the model of the rules every turn.

## How the hook decides what is a payment

A tool is only intercepted when its name looks like a payment (pay,
payment, transfer, withdraw, payout, settle, send money, send payment)
AND its arguments carry both an amount and a recipient. Tools like
`read_file`, `send_message`, and `send_email` are never blocked, because
they carry no amount, or no recipient, or neither. The plugin's own
`recall_vault_*` tools are not all skipped: `recall_vault_set_policy`
and `recall_vault_remember` are sent to the human approval gate, with
the exact change or rule text shown, because they can loosen the guard.
`recall_vault_check`, `recall_vault_policy`, and `recall_vault_ledger`
are not intercepted.

## Security and privacy

- Local only. Policy, rules, spent today, and the ledger live in two JSON
  files under the Hermes plugin data directory
  (`<HERMES_HOME>/plugin-data/recall-vault/`). No network calls, no
  subprocesses, no telemetry.
- No credentials. The plugin reads no `.env` files, no `auth.json`, no
  token files, and no other app's login. It needs no API key.
- No self-updating code. The plugin never fetches or replaces its own
  files. Updates come only through a catalog SHA bump plus
  `hermes plugins update recall-vault`.
- No overrides of Hermes core. It extends Hermes only through the public
  surfaces: `register_tool`, `register_hook`, and `register_command`.
- No compiled binaries, no `.pyc` files in the repo.
- The ledger is capped at 200 entries. Spent today resets when the date
  changes. Remembered rules are plain text you wrote, they are shown to
  the model every turn, so do not store secrets in a rule.

## Files

- `plugin.yaml`, manifest, tools, hooks, and the Hermes version floor.
- `__init__.py`, registration, hooks, and the `/recall-vault` command.
- `policy.py`, pure policy engine, no Hermes imports.
- `store.py`, local policy, rules, and ledger storage.
- `schemas.py`, tool schemas the model sees.
- `tools.py`, tool handlers, always returning a JSON string.
- `tests/test_policy.py`, policy and hook tests, run with Python only.
- `DEMO_SCRIPT.md`, the under-90-second demo script.
- `catalog-entry.yaml`, draft catalog entry for the submission PR, SHA to be filled in after the repo is pushed.

## Origin

Ported from Recall Wallet, an agent wallet with a memory and a conscience
built for the NEAR Hackathon. Its Gated Payment Vault was deployed to
`recallwallet.testnet` and verified on chain with the same policy used
here as the default. Recall Vault is the Hermes plugin form of that
vault: same rules, same ledger, new home inside Hermes.

## License

MIT. See `LICENSE`.
