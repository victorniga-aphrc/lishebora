"""
NOVA prediction from product name using ``models/novaclasses_model.pkl`` (BiLSTM).

Requires:
  - ``models/tokenizer.pkl`` — ``keras.preprocessing.text.Tokenizer`` (or compatible
    object with ``texts_to_sequences``), saved from training.
  - ``models/nova_labels.json`` — map ``"0"``..``"3"`` to display strings (order must match training).

TensorFlow is imported only on first prediction when the feature is enabled.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import NovaBiLstmPrediction

logger = logging.getLogger(__name__)

NOVA_SEQ_LEN = 12


class _NovaState:
    __slots__ = ("ready", "model", "tokenizer", "labels", "warned_once")

    def __init__(self) -> None:
        self.ready = False
        self.model: Any = None
        self.tokenizer: Any = None
        self.labels: dict[int, str] = {}
        self.warned_once = False


_state = _NovaState()


def _configure_tf_env() -> None:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")


def _load_labels(path: Path) -> dict[int, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, str] = {}
    for k, v in raw.items():
        out[int(k)] = str(v).strip()
    return out


def _load_labels_from_encoders(path: Path) -> dict[int, str]:
    """
    Try deriving NOVA labels from ``label_encoders.pkl`` (sklearn LabelEncoder).
    Supports common keys: ``nova``, ``nova_output``, ``nova_class``.
    """
    with path.open("rb") as f:
        obj = pickle.load(f)
    if not isinstance(obj, dict):
        raise TypeError("label_encoders.pkl is not a dict")
    for key in ("nova", "nova_output", "nova_class"):
        enc = obj.get(key)
        if enc is None:
            continue
        classes = getattr(enc, "classes_", None)
        if classes is None:
            continue
        return {int(i): str(v) for i, v in enumerate(classes)}
    raise KeyError("No NOVA encoder key found in label_encoders.pkl")


def _pad_sequences(sequences: list[list[int]], maxlen: int) -> Any:
    """Pad/truncate to (len(sequences), maxlen) int32."""
    import numpy as np

    batch = len(sequences)
    out = np.zeros((batch, maxlen), dtype=np.int32)
    for i, seq in enumerate(sequences):
        s = seq[:maxlen] if seq else []
        for j, t in enumerate(s):
            if j < maxlen:
                out[i, j] = int(t)
    return out


def _ensure_loaded() -> bool:
    if _state.ready:
        return _state.model is not None and _state.tokenizer is not None

    _state.ready = True
    if not settings.nova_bilstm_enabled:
        return False

    model_path = settings.nova_bilstm_model_pkl
    tok_path = settings.nova_bilstm_tokenizer_pkl
    labels_path = settings.nova_bilstm_labels_json
    enc_path = settings.nova_bilstm_label_encoders_pkl

    if not model_path.is_file():
        if not _state.warned_once:
            logger.warning("NOVA BiLSTM model not found at %s — disabled.", model_path)
            _state.warned_once = True
        return False
    if not tok_path.is_file():
        if not _state.warned_once:
            logger.warning(
                "NOVA tokenizer not found at %s — add from training (see models/README.md).",
                tok_path,
            )
            _state.warned_once = True
        return False
    try:
        _configure_tf_env()
        import tensorflow as tf  # noqa: F401

        with model_path.open("rb") as f:
            _state.model = pickle.load(f)
        if isinstance(_state.model, dict):
            for k in ("model", "keras_model", "classifier"):
                if k in _state.model:
                    _state.model = _state.model[k]
                    break

        with tok_path.open("rb") as f:
            _state.tokenizer = pickle.load(f)

        if labels_path.is_file():
            _state.labels = _load_labels(labels_path)
        elif enc_path.is_file():
            _state.labels = _load_labels_from_encoders(enc_path)
            logger.info(
                "NOVA labels loaded from label encoders at %s (JSON missing).",
                enc_path,
            )
        else:
            logger.warning(
                "NOVA label map missing: neither %s nor %s exists. "
                "Will return fallback labels like 'NOVA class {idx}'.",
                labels_path,
                enc_path,
            )
            _state.labels = {}
    except Exception:
        logger.exception("Failed to load NOVA BiLSTM artifacts")
        _state.model = None
        _state.tokenizer = None
        _state.labels = {}
        return False

    return _state.model is not None and _state.tokenizer is not None


def _texts_to_sequences(tokenizer: Any, text: str) -> list[list[int]]:
    if hasattr(tokenizer, "texts_to_sequences"):
        return tokenizer.texts_to_sequences([text])
    raise TypeError("Tokenizer must have texts_to_sequences([str])")


def predict_nova_from_product_text(
    product_name: str | None,
    brand: str | None = None,
) -> NovaBiLstmPrediction | None:
    """
    Run BiLSTM on ``brand + name`` (trimmed). Returns None if disabled or load failed.
    """
    if not settings.nova_bilstm_enabled:
        return None
    if not _ensure_loaded():
        return None

    parts = []
    if brand and str(brand).strip():
        parts.append(str(brand).strip())
    if product_name and str(product_name).strip():
        parts.append(str(product_name).strip())
    line = " ".join(parts).strip()
    if len(line) < 1:
        return None

    try:
        seqs = _texts_to_sequences(_state.tokenizer, line)
        x = _pad_sequences(seqs, NOVA_SEQ_LEN)
        pred = _state.model.predict(x, verbose=0)
        if isinstance(pred, (list, tuple)):
            pred = pred[0]
        if hasattr(pred, "numpy"):
            pred = pred.numpy()
        import numpy as np

        probs = np.asarray(pred, dtype=float).reshape(-1)
        idx = int(np.argmax(probs))
        label = _state.labels.get(idx, f"NOVA class {idx}")
        conf = float(probs[idx]) if probs.size > idx else 0.0
        return NovaBiLstmPrediction(
            nova_label=label,
            nova_index=idx,
            probabilities=[float(p) for p in probs.tolist()],
            confidence=conf,
            input_text=line,
        )
    except Exception:
        logger.exception("NOVA BiLSTM predict failed for %r", line[:80])
        return None


def maybe_fill_supermarket_nova(
    supermarket_classification: Any,
    nova_pred: NovaBiLstmPrediction | None,
) -> Any:
    """If configured and POS ``nova`` is empty, copy BiLSTM label onto classification."""
    if not settings.nova_bilstm_fill_pos_nova or nova_pred is None:
        return supermarket_classification
    if supermarket_classification is None:
        return None
    existing = getattr(supermarket_classification, "nova", None)
    if existing and str(existing).strip():
        return supermarket_classification
    return supermarket_classification.model_copy(update={"nova": nova_pred.nova_label})
