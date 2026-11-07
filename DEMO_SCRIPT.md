# Recall Vault demo script, under 90 seconds

Goal: show that the agent remembers its money rules and cannot break them.
All amounts are NEAR, dry-run only, no money moves. Record the Hermes
terminal, or a phone screen driving Hermes, with captions naming each rule.

## 0-15 seconds: show the policy

Show `/recall-vault policy` or `recall_vault_policy`.

Say: "Recall Vault remembers my money rules. One NEAR per payment,
two NEAR a day, ask me first above half a NEAR, and only pay my freelancer."

On screen: per-transaction cap 1 NEAR, daily cap 2 NEAR, confirm above
0.5 NEAR, allowlist recallwallet-freelancer.testnet, spent today 0.

## 15-40 seconds: a payment that passes

Ask Hermes: "Pay my freelancer 0.4 NEAR."

Hermes calls `recall_vault_check` with amount 0.4 and to
recallwallet-freelancer.testnet. Result on screen: ALLOWED, passes all
spending rules, spent today 0.4.

Say: "Small, allowlisted, under the cap. It goes through, and it is logged."

## 40-65 seconds: a payment that is blocked

Ask Hermes: "Pay 5 NEAR to some-random.testnet."

Hermes calls `recall_vault_check` with amount 5 and to some-random.testnet.
Result on screen: BLOCKED, rule perTxCap, 5 NEAR exceeds the
per-transaction cap of 1 NEAR. The allowlist would also have stopped it.

Say: "Five NEAR to a stranger. Blocked, and it quotes the rule back."

If time allows, also show 0.1 NEAR to the same stranger: BLOCKED, rule
allowlist, address not on the allowlist.

## 65-85 seconds: a payment that pauses for a human

Ask Hermes: "Pay my freelancer 0.8 NEAR."

Result on screen: NEEDS_CONFIRMATION, rule confirmThreshold, 0.8 NEAR is
above the 0.5 NEAR confirmation threshold. Hermes escalates to the human
approval gate instead of paying.

Say: "Above my confirm threshold, so it pauses and asks me first. It does
not pay on its own."

## 85-90 seconds: show the ledger

Show `/recall-vault ledger` or `recall_vault_ledger`.

On screen: ALLOWED 0.4 NEAR, BLOCKED 5 NEAR perTxCap, NEEDS_CONFIRMATION
0.8 NEAR confirmThreshold. Every decision, with its rule and reason.

Say: "Recall Vault, your agent remembers its rules."

## Recording notes

- Reset spent today first if you recorded a practice run:
  `recall_vault_set_policy` with reset_spent true, or delete the ledger
  and policy files under `<HERMES_HOME>/plugin-data/recall-vault/`.
- Keep the policy on screen for the first shot, so the caps are visible
  before any payment is checked.
- Do not show any key, token, or wallet seed. This demo needs none.
- The demo is a screen recording, not generated video. Capture the
  terminal session and convert it to video with ffmpeg, or record the
  phone screen while driving Hermes over a messaging gateway.
