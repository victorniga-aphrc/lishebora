#!/usr/bin/env python3
"""
CLI test for NOVA BiLSTM (no OpenAI). Requires:
  NOVA_BILSTM_ENABLED=true
  models/novaclasses_model.pkl, models/tokenizer.pkl, and either
  models/nova_labels.json or models/label_encoders.pkl

Usage:
  export NOVA_BILSTM_ENABLED=true
  python scripts/predict_nova_cli.py "Xtra White Bread"
  python scripts/predict_nova_cli.py "White Bread" --brand Xtra
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure repo root on path
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

if "NOVA_BILSTM_ENABLED" not in os.environ:
    os.environ["NOVA_BILSTM_ENABLED"] = "true"


def main() -> int:
    parser = argparse.ArgumentParser(description="Test NOVA BiLSTM on product text")
    parser.add_argument("name", help="Product name")
    parser.add_argument("--brand", default=None, help="Optional brand")
    args = parser.parse_args()

    from app.services.nova_bilstm_inference import predict_nova_from_product_text

    pred = predict_nova_from_product_text(args.name, args.brand)
    if pred is None:
        print(
            "No prediction (disabled, missing tokenizer/labels/model, or empty text). "
            "Set NOVA_BILSTM_ENABLED=true and ensure models/tokenizer.pkl exists.",
            file=sys.stderr,
        )
        return 1
    print(pred.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
