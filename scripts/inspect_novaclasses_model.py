#!/usr/bin/env python3
"""
Inspect models/novaclasses_model.pkl without integrating into the API yet.

Prints:
  - Whether Keras/TensorFlow can be imported (required to unpickle)
  - Loaded object type (Model vs dict wrapper)
  - model.summary(), input/output specs
  - Safe dummy predict if shapes allow

Usage:
  pip install -r requirements.txt   # includes TensorFlow; use Python 3.10–3.12 if TF fails
  python scripts/inspect_novaclasses_model.py
  python scripts/inspect_novaclasses_model.py --quick    # load + I/O + dummy predict only (no full summary)
  python scripts/inspect_novaclasses_model.py --no-load   # file stats + byte hints only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = _REPO_ROOT / "models" / "novaclasses_model.pkl"


def _sequence_length_from_input(inp) -> int | None:
    """Fixed sequence length (e.g. 12) from a Keras input tensor; None if unknown."""
    shp = inp.shape
    try:
        if hasattr(shp, "as_list"):
            dims = shp.as_list()
        else:
            dims = list(shp)
    except (TypeError, ValueError):
        return None
    if len(dims) != 2 or dims[1] is None:
        return None
    return int(dims[1])


def _print_prediction_samples(prediction) -> None:
    """Print shape + first values for ndarray, list/tuple of arrays, or dict of arrays."""
    import numpy as np

    def _to_numpy(x):
        if hasattr(x, "numpy"):
            return x.numpy()
        return x

    def _one(label: str, head) -> None:
        a = _to_numpy(head)
        if isinstance(a, np.ndarray):
            flat = np.ravel(a)
            n = min(5, flat.size)
            print(f"  {label} shape={a.shape} sample={flat[:n]}")
        else:
            print(f"  {label} type={type(head)!r} (not ndarray after conversion)")

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
        b"class_name",
        b"nova_output",
        b"forward_lstm",
        b"backward_lstm",
        b"subclass",
        b"Functional",
    ):
        print(f"  contains {needle!r}: {needle in data}")
    # Rough token scan for *_output layers
    outs = set(re.findall(rb"[a-z][a-z0-9_]{0,24}_output", data.lower()))
    print(f"  *_output-like tokens: {sorted(outs)[:20]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect novaclasses_model.pkl")
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="Do not unpickle; only print path and byte-level hints.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="After load: print input/output specs and dummy predict only (skip model.summary()).",
    )
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

        # Quieter CPU-only runs (XLA still logs cuFFT/cuDNN registration as ERROR).
        # 3 = suppress INFO, WARNING, ERROR from TensorFlow C++. Must run before import.
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        # Optional: avoid probing CUDA at all on CPU-only machines
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

        import tensorflow as tf  # noqa: F401 — registers keras backend
        import keras
    except ImportError as e:
        print(
            "\nCould not import TensorFlow/Keras (needed to unpickle this file):\n"
            f"  {e}\n\n"
            "Fix:\n"
            "  - Use Python 3.10–3.12\n"
            "  - pip install -r requirements.txt\n",
            file=sys.stderr,
        )
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
        print(
            "Loaded object does not look like a Keras Model. "
            "If training saved a dict, add handling for your keys in this script.",
            file=sys.stderr,
        )
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
    for i, out_tensor in enumerate(model.outputs):
        print(
            f"  [{i}] name={getattr(out_tensor, 'name', '?')} "
            f"shape={out_tensor.shape} dtype={out_tensor.dtype}"
        )

    # Dummy predict: single input (batch, seq) int32 — Keras 3 shapes may be tuple-like (no .rank)
    try:
        if len(model.inputs) == 1:
            seq = _sequence_length_from_input(model.inputs[0])
            if seq is not None:
                batch = 1
                import numpy as np

                dummy = np.zeros((batch, seq), dtype=np.int32)
                print(f"\n=== Dummy predict (zeros int32, shape ({batch}, {seq})) ===")
                prediction = model.predict(dummy, verbose=0)
                _print_prediction_samples(prediction)
                print(
                    "  (zeros are valid token id 0 if padding uses 0; "
                    "logits are still a sanity check)"
                )
    except Exception as e:
        print(f"\nDummy predict skipped: {e}")

    print("\nDone. Next: add tokenizer + label JSON from training to map text → ids → labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
