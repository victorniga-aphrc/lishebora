#!/usr/bin/env python3
"""
CLI test for foodclasses BiLSTM (class/subclass/nova) without OpenAI.

Usage:
  export FOODCLASSES_BILSTM_ENABLED=true
  python scripts/predict_foodclasses_cli.py "White Bread" --brand Xtra
"""

from __future__ import annotations

import argparse
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

if "FOODCLASSES_BILSTM_ENABLED" not in os.environ:
    os.environ["FOODCLASSES_BILSTM_ENABLED"] = "true"


def main() -> int:
    parser = argparse.ArgumentParser(description="Test foodclasses BiLSTM on product text")
    parser.add_argument("name", help="Product name")
    parser.add_argument("--brand", default=None, help="Optional brand")
    args = parser.parse_args()

    from app.services.foodclasses_bilstm_inference import (
        predict_foodclasses_from_product_text,
    )

    pred = predict_foodclasses_from_product_text(args.name, args.brand)
    if pred is None:
        print(
            "No prediction (disabled or missing model/tokenizer/encoders).",
            file=sys.stderr,
        )
        return 1
    print(pred.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

