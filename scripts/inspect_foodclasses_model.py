#!/usr/bin/env python3
"""
Inspect models/foodclasses_model.pkl before integrating into the API.

Usage:
  python scripts/inspect_foodclasses_model.py
  python scripts/inspect_foodclasses_model.py --quick
  python scripts/inspect_foodclasses_model.py --no-load
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = _REPO_ROOT / "models" / "foodclasses_model.pkl"


def _sequence_length_from_input(inp) -> int | None:
    shp = inp.shape
    try:
        dims = shp.as_list() if hasattr(shp, "as_list") else list(shp)
    except (TypeError, ValueError):
        return None
    if len(dims) != 2 or dims[1] is None:
        return None
    return int(dims[1])


def _print_prediction_samples(prediction) -> None:
    import numpy as np

    def _to_numpy(x):
        return x.numpy() if hasattr(x, "numpy") else x

    def _one(label: str, head) -> None:
        a = _to_numpy(head)
        if isinstance(a, np.ndarray):
            flat = np.ravel(a)
            n = min(5, flat.size)
            print(f"  {label} shape={a.shape} sample={flat[:n]}")
        else:
            print(f"  {label} type={type(head)!r}")

    if isinstance(prediction, list):
        for idx, head in enumerate(prediction):
            _one(f"output[{idx}]", head)
    elif isinstance(prediction, tuple):
        for idx, head in enumerate(prediction):
            _one(f"output[{idx}]", head)
    elif isinstance(prediction, dict):
        for key, head in prediction.items():
            _one(f"head[{key!r}]", head)
    else:
        _one("output", prediction)


def _byte_hints() -> None:
    data = MODEL_PATH.read_bytes()
    print(f"File size: {len(data):,} bytes")
    for needle in (
        b"class_name_output",
        b"subclass_name_output",
        b"nova_output",
        b"forward_lstm",
        b"backward_lstm",
        b"Functional",
    ):
        print(f"  contains {needle!r}: {needle in data}")
    outs = set(re.findall(rb"[a-z][a-z0-9_]{0,30}_output", data.lower()))
    print(f"  *_output-like tokens: {sorted(outs)[:30]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect foodclasses_model.pkl")
    parser.add_argument("--no-load", action="store_true")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    if args.no_load and args.quick:
        print("Use only one of --no-load or --quick.", file=sys.stderr)
        return 1
    if not MODEL_PATH.is_file():
        print(f"Missing model file: {MODEL_PATH}", file=sys.stderr)
        return 1

    print(f"Model path: {MODEL_PATH}")
    if not args.quick:
        _byte_hints()
    if args.no_load:
        print("\n--no-load: skipping pickle (Keras not required).")
        return 0

    try:
        import os

        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
        import tensorflow as tf  # noqa: F401
    except ImportError as e:
        print(f"\nMissing TensorFlow/Keras import: {e}", file=sys.stderr)
        return 2

    import pickle

    print("\nUnpickling (this may take a few seconds)...")
    with MODEL_PATH.open("rb") as f:
        obj = pickle.load(f)
    print(f"Loaded type: {type(obj)}")

    model = obj
    if isinstance(obj, dict):
        print(f"Dict keys: {list(obj.keys())}")
        for k in ("model", "keras_model", "classifier"):
            if k in obj:
                model = obj[k]
                print(f"Using dict['{k}'] as model.")
                break
    if not hasattr(model, "inputs"):
        print("Loaded object does not look like a Keras model.", file=sys.stderr)
        return 3

    if args.quick:
        print("\n=== Quick mode (no full summary) ===")
    else:
        print("\n=== Summary ===")
        model.summary()

    print("\n=== Inputs ===")
    for i, inp in enumerate(model.inputs):
        print(f"  [{i}] name={getattr(inp, 'name', '?')} shape={inp.shape} dtype={inp.dtype}")

    print("\n=== Outputs ===")
    for i, out in enumerate(model.outputs):
        print(f"  [{i}] name={getattr(out, 'name', '?')} shape={out.shape} dtype={out.dtype}")

    try:
        if len(model.inputs) == 1:
            seq = _sequence_length_from_input(model.inputs[0])
            if seq is not None:
                import numpy as np

                dummy = np.zeros((1, seq), dtype=np.int32)
                print(f"\n=== Dummy predict (zeros int32, shape (1, {seq})) ===")
                prediction = model.predict(dummy, verbose=0)
                _print_prediction_samples(prediction)
    except Exception as e:
        print(f"\nDummy predict skipped: {e}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

