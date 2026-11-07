"""Tool schemas, what the model sees when it decides to call Recall Vault."""

CHECK = {
    "name": "recall_vault_check",
    "description": (
        "Check a NEAR payment against the user's remembered money rules "
        "before paying. Returns ALLOWED, BLOCKED with the rule that stopped it, "
        "or NEEDS_CONFIRMATION when the amount is above the confirm threshold. "
        "An allowed check is logged in the audit ledger and counts toward "
        "today's spending. This is a dry check, it moves no money."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "Payment amount in NEAR, for example 0.4.",
            },
            "to": {
                "type": "string",
                "description": "Recipient address, for example recallwallet-freelancer.testnet.",
            },
            "currency": {
                "type": "string",
                "description": "Currency. Only NEAR is supported in this version.",
            },
            "confirmed": {
                "type": "boolean",
                "description": "True when the user already confirmed this payment.",
            },
        },
        "required": ["amount", "to"],
    },
}

POLICY = {
    "name": "recall_vault_policy",
    "description": (
        "Show the current Recall Vault money policy: per-transaction cap, "
        "daily cap, confirm threshold, allowlist, amount spent today, and "
        "the plain-language rules the user remembered."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

SET_POLICY = {
    "name": "recall_vault_set_policy",
    "description": (
        "Update the Recall Vault money policy. Give only the fields to change. "
        "Caps and threshold are in NEAR. Allowlist replaces the whole list. "
        "Set reset_spent to true to reset today's spent total to zero."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "per_tx_cap": {
                "type": "number",
                "description": "Maximum NEAR allowed in one payment.",
            },
            "daily_cap": {
                "type": "number",
                "description": "Maximum NEAR allowed in one day.",
            },
            "confirm_above": {
                "type": "number",
                "description": "Payments above this NEAR amount need human confirmation.",
            },
            "allowlist": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full replacement allowlist of recipient addresses.",
            },
            "reset_spent": {
                "type": "boolean",
                "description": "True to reset today's spent total to zero.",
            },
        },
        "required": [],
    },
}

LEDGER = {
    "name": "recall_vault_ledger",
    "description": (
        "Show the Recall Vault audit ledger: every allowed, blocked, and "
        "awaiting-confirmation payment decision, most recent first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "number",
                "description": "How many recent entries to show, default 20, maximum 100.",
            },
        },
        "required": [],
    },
}

REMEMBER = {
    "name": "recall_vault_remember",
    "description": (
        "Remember a plain-language money rule for the agent, for example "
        "Never pay a brand-new address without asking first. Remembered rules "
        "are shown to the agent every turn. Enforcement uses the numeric "
        "policy caps, threshold, and allowlist."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule": {
                "type": "string",
                "description": "The plain-language rule to remember.",
            },
        },
        "required": ["rule"],
    },
}

ALL_SCHEMAS = [CHECK, POLICY, SET_POLICY, LEDGER, REMEMBER]
