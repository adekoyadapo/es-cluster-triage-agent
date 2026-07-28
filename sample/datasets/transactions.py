"""
transactions.py — genericized revamp of the customer transaction mapping.
Same structural shape (text+keyword multifields, nested monetary objects, date fields)
with all sensitive/PII field names replaced with generic equivalents.
Uses a datastream backed by ILM.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator

from ..synth import SynthGen

NAME = "transactions"
DATASTREAM = "sample-transactions"
COMPONENT_TEMPLATE = "sample-transactions-mappings"
INDEX_TEMPLATE_NAME = "sample-transactions"
ILM_POLICY_NAME = "sample-transactions-ilm"

# ── ILM policy ─────────────────────────────────────────────────────────────────
ILM_POLICY = {
    "policy": {
        "phases": {
            "hot": {
                "min_age": "0ms",
                "actions": {
                    "rollover": {
                        "max_primary_shard_size": "10gb",
                        "max_age": "1d",
                        "max_docs": 50_000_000,
                    },
                    "set_priority": {"priority": 100},
                },
            },
            "warm": {
                "min_age": "2d",
                "actions": {
                    "forcemerge": {"max_num_segments": 1},
                    "set_priority": {"priority": 50},
                },
            },
            "delete": {
                "min_age": "7d",
                "actions": {"delete": {}},
            },
        }
    }
}

# ── Mapping (genericized from customer mapping.json) ──────────────────────────
#
# Sensitive names replaced:
#   account_id         → account_ref       (eager_global_ordinals kept)
#   card_number        → instrument_token  (eager_global_ordinals kept)
#   customer_id        → party_ref
#   parent_customer_id → parent_party_ref
#   merchant_id        → merchant_ref
#   se_name            → merchant_name
#   loyalty_display_id → loyalty_ref
#   legacy_program_code→ legacy_code
#   market_offer_id    → offer_ref
#   offering_terms_id  → terms_ref
#   profile_group_id   → group_ref
#   fulfillment_id     → fulfillment_ref
#   fulfillment_product_id → product_ref
#   fulfillment_system → fulfillment_channel
#   extra_credit_*     → bonus_*
#   incremental_feature_id → feature_ref
#   incremental_product_id → product_line_ref
#   originator_reference_id → originator_ref
#   roc_reference_id   → ref_id
#   rewards_account_id → rewards_ref

def _text_kw(eager=False) -> dict:
    kw: dict = {"type": "keyword", "ignore_above": 256}
    if eager:
        kw["eager_global_ordinals"] = True
    return {"type": "text", "fields": {"keyword": kw}}


MAPPING_PROPERTIES: dict = {
    "@timestamp": {"type": "date"},
    "event": {
        "properties": {
            "execution_id": _text_kw(),
            "id":           _text_kw(),
            "processed":    {"type": "date"},
            "source":       _text_kw(),
            "type":         _text_kw(),
        }
    },
    "transaction": {
        "properties": {
            # identity refs
            "account_ref":           _text_kw(eager=True),
            "instrument_token":      _text_kw(eager=True),
            "party_ref":             _text_kw(),
            "parent_party_ref":      _text_kw(),
            "merchant_ref":          _text_kw(),
            "merchant_name":         _text_kw(),
            # monetary objects
            "base_amount": {
                "properties": {
                    "amount":        {"type": "float"},
                    "currency_code": _text_kw(),
                    "multiplier":    {"type": "float"},
                }
            },
            "reward_info": {
                "properties": {
                    "amount":        {"type": "float"},
                    "currency_code": _text_kw(),
                    "multiplier":    {"type": "float"},
                }
            },
            "spend_info": {
                "properties": {
                    "amount":        {"type": "float"},
                    "currency_code": _text_kw(),
                    "multiplier":    {"type": "float"},
                }
            },
            "incremental_amount": {
                "properties": {
                    "amount":        {"type": "float"},
                    "currency_code": _text_kw(),
                    "multiplier":    {"type": "float"},
                }
            },
            # dates
            "transaction_time":   {"type": "date"},
            "posted_time":        {"type": "date"},
            "decision_time":      {"type": "date"},
            "status_update_time": {"type": "date"},
            "qualified_time":     {"type": "date"},
            "bucket_start_date":  {"type": "date"},
            "bucket_end_date":    {"type": "date"},
            # status / type fields
            "status":           _text_kw(),
            "transaction_type": _text_kw(),
            "sub_type":         _text_kw(),
            "reason":           _text_kw(),
            "category":         _text_kw(),
            "category_id":      _text_kw(),
            "description":      _text_kw(),
            "earn_qualifiers":  _text_kw(),
            # numeric
            "bonus_cycle_id":   {"type": "integer"},
            # misc refs
            "program_ref":      _text_kw(),
            "loyalty_ref":      _text_kw(),
            "legacy_code":      _text_kw(),
            "offer_ref":        _text_kw(),
            "terms_ref":        _text_kw(),
            "group_ref":        _text_kw(),
            "fulfillment_ref":  _text_kw(),
            "product_ref":      _text_kw(),
            "fulfillment_channel": _text_kw(),
            "bonus_period_type": _text_kw(),
            "feature_ref":       _text_kw(),
            "product_line_ref":  _text_kw(),
            "instrument_id":     _text_kw(),
            "originator_ref":    _text_kw(),
            "ref_id":            _text_kw(),
            "rewards_ref":       _text_kw(),
            "qualified_amount":  _text_kw(),
            "qualified_id":      _text_kw(),
            "qualification_reason": _text_kw(),
            "bucket_id":         _text_kw(),
            "international_transaction": _text_kw(),
            "token_requestor_id": _text_kw(),
            "external_triggers": {
                "properties": {
                    "id":   _text_kw(),
                    "type": _text_kw(),
                }
            },
        }
    },
}

COMPONENT_TEMPLATE_BODY = {
    "template": {
        "mappings": {
            "dynamic": "strict",
            "_source": {"enabled": True},
            "properties": MAPPING_PROPERTIES,
        },
        "settings": {
            "index.lifecycle.name": ILM_POLICY_NAME,
        },
    }
}

INDEX_TEMPLATE_BODY = {
    "index_patterns": [f"{DATASTREAM}*"],
    "data_stream": {},
    "composed_of": [COMPONENT_TEMPLATE, "sample-common-slowlog"],
    "priority": 500,
}

# ── Document generator ─────────────────────────────────────────────────────────

class TransactionsDataset:
    name = NAME
    datastream = DATASTREAM
    component_template = COMPONENT_TEMPLATE
    component_template_body = COMPONENT_TEMPLATE_BODY
    index_template = INDEX_TEMPLATE_NAME
    index_template_body = INDEX_TEMPLATE_BODY
    ilm_policy = ILM_POLICY_NAME
    ilm_policy_body = ILM_POLICY

    @staticmethod
    def generate_doc(synth: SynthGen, inject_dynamic_fields: bool = False) -> dict:
        now = datetime.now(timezone.utc)
        ts = synth.ts_jitter_iso(now, max_jitter_s=30)

        doc: dict = {
            "@timestamp": ts,
            "event": {
                "id": synth.short_id("EVT-"),
                "execution_id": synth.short_id("EXEC-"),
                "processed": synth.ts_jitter_iso(now, max_jitter_s=5),
                "source": synth.event_source(),
                "type": synth.event_type(),
            },
            "transaction": {
                "account_ref": synth.account_ref(),
                "instrument_token": synth.instrument_token(),
                "party_ref": synth.party_ref(),
                "parent_party_ref": synth.party_ref(),
                "merchant_ref": synth.merchant_ref(),
                "merchant_name": synth.merchant_name(),
                "base_amount": synth.monetary(),
                "reward_info": synth.reward_monetary(),
                "spend_info": synth.monetary(),
                "incremental_amount": {"amount": 0.0, "currency_code": "USD", "multiplier": 0.0},
                "transaction_time": ts,
                "posted_time": synth.ts_jitter_iso(now, max_jitter_s=60),
                "decision_time": synth.ts_jitter_iso(now, max_jitter_s=5),
                "status_update_time": synth.ts_jitter_iso(now, max_jitter_s=10),
                "qualified_time": synth.ts_jitter_iso(now, max_jitter_s=10),
                "status": synth.tx_status(),
                "transaction_type": synth.tx_type(),
                "sub_type": synth.tx_subtype(),
                "reason": "qualified",
                "category": synth.category(),
                "category_id": f"CAT-{synth._int(1, 20):03d}",
                "description": f"Purchase at {synth.merchant_name()}",
                "earn_qualifiers": "standard-earn",
                "bonus_cycle_id": synth._int(0, 12),
                "program_ref": f"PRG-{synth.short_id()}",
                "loyalty_ref": f"LYL-{synth._int(100000, 999999)}",
                "legacy_code": f"LEG-{synth._int(1, 50):03d}",
                "offer_ref": f"OFR-{synth.short_id()}",
                "terms_ref": f"TRM-{synth._int(2020, 2026)}",
                "group_ref": f"GRP-{synth.short_id()}",
                "fulfillment_ref": f"FUL-{synth.short_id()}",
                "product_ref": f"PRD-{synth.short_id()}",
                "fulfillment_channel": synth._choice(["digital", "physical", "api"]),
                "bonus_period_type": synth._choice(["monthly", "quarterly", "annual"]),
                "feature_ref": f"FTR-{synth.short_id()}",
                "product_line_ref": f"PLR-{synth.short_id()}",
                "instrument_id": f"INSTR-{synth.short_id()}",
                "originator_ref": f"ORIG-{synth.short_id()}",
                "ref_id": f"REF-{synth.short_id()}",
                "rewards_ref": f"RWD-{synth._int(100000, 999999)}",
                "qualified_amount": str(round(synth.amount(), 2)),
                "qualified_id": f"QUAL-{synth.short_id()}",
                "qualification_reason": synth._choice(["standard", "bonus", "tier1", "promo"]),
                "bucket_id": f"BKT-{synth.short_id()}",
                "international_transaction": synth._choice(["true", "false", "false", "false"]),
                "token_requestor_id": f"TRI-{synth._int(100, 999)}",
                "external_triggers": {"id": "", "type": ""},
            },
        }

        # mapping_explosion: inject randomly-named fields each call
        if inject_dynamic_fields:
            for _ in range(synth._int(3, 8)):
                doc["transaction"][synth.random_field_name()] = synth.lorem_words(3)

        return doc

    @classmethod
    def generate_ndjson_lines(
        cls, synth: SynthGen, n: int, inject_dynamic_fields: bool = False
    ) -> Iterator[str]:
        for _ in range(n):
            yield json.dumps(cls.generate_doc(synth, inject_dynamic_fields))
