"""
Class/subclass/NOVA prediction from product name using
``models/foodclasses_model.pkl`` (multi-head BiLSTM).

Requires:
  - tokenizer pickle with ``texts_to_sequences``
  - label_encoders pickle (sklearn LabelEncoder objects)
"""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import FoodclassesBiLstmPrediction, ProductClassification
from app.utils.nova_display import normalize_nova_for_api

logger = logging.getLogger(__name__)

SEQ_LEN = 12


class _State:
    __slots__ = ("ready", "model", "tokenizer", "encoders", "warned_once")

    def __init__(self) -> None:
        self.ready = False
        self.model: Any = None
        self.tokenizer: Any = None
        self.encoders: dict[str, Any] = {}
        self.warned_once = False


_state = _State()


def _configure_tf_env() -> None:
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")


def _pad_sequences(sequences: list[list[int]], maxlen: int) -> Any:
    import numpy as np

    out = np.zeros((len(sequences), maxlen), dtype=np.int32)
    for i, seq in enumerate(sequences):
        s = seq[:maxlen] if seq else []
        for j, token in enumerate(s):
            if j < maxlen:
                out[i, j] = int(token)
    return out


def _encoder_by_len(encoders: dict[str, Any], length: int, preferred_keys: tuple[str, ...]) -> Any | None:
    for key in preferred_keys:
        enc = encoders.get(key)
        classes = getattr(enc, "classes_", None)
        if classes is not None and len(classes) == length:
            return enc
    for enc in encoders.values():
        classes = getattr(enc, "classes_", None)
        if classes is not None and len(classes) == length:
            return enc
    return None


def _ensure_loaded() -> bool:
    if _state.ready:
        return _state.model is not None and _state.tokenizer is not None

    _state.ready = True
    if not settings.foodclasses_bilstm_enabled:
        return False

    m_path = settings.foodclasses_bilstm_model_pkl
    t_path = settings.foodclasses_bilstm_tokenizer_pkl
    e_path = settings.foodclasses_bilstm_label_encoders_pkl

    if not m_path.is_file() or not t_path.is_file() or not e_path.is_file():
        if not _state.warned_once:
            logger.warning(
                "Foodclasses BiLSTM artifacts missing (model=%s tokenizer=%s encoders=%s).",
                m_path,
                t_path,
                e_path,
            )
            _state.warned_once = True
        return False

    try:
        _configure_tf_env()
        import tensorflow as tf  # noqa: F401

        with m_path.open("rb") as f:
            _state.model = pickle.load(f)
        if isinstance(_state.model, dict):
            for k in ("model", "keras_model", "classifier"):
                if k in _state.model:
                    _state.model = _state.model[k]
                    break
        with t_path.open("rb") as f:
            _state.tokenizer = pickle.load(f)
        with e_path.open("rb") as f:
            enc = pickle.load(f)
        _state.encoders = enc if isinstance(enc, dict) else {}
    except ModuleNotFoundError as exc:
        if exc.name == "tensorflow":
            if not _state.warned_once:
                logger.warning(
                    "Foodclasses BiLSTM disabled because TensorFlow is not installed."
                )
                _state.warned_once = True
            _state.model = None
            _state.tokenizer = None
            _state.encoders = {}
            return False
        logger.exception("Failed loading foodclasses BiLSTM artifacts")
        _state.model = None
        _state.tokenizer = None
        _state.encoders = {}
        return False
    except Exception:
        logger.exception("Failed loading foodclasses BiLSTM artifacts")
        _state.model = None
        _state.tokenizer = None
        _state.encoders = {}
        return False

    return _state.model is not None and _state.tokenizer is not None


def _texts_to_sequences(tokenizer: Any, text: str) -> list[list[int]]:
    if hasattr(tokenizer, "texts_to_sequences"):
        return tokenizer.texts_to_sequences([text])
    raise TypeError("Tokenizer must support texts_to_sequences([str])")


def _decode_head(probs: Any, encoder: Any, fallback_prefix: str) -> tuple[str | None, float | None]:
    import numpy as np

    arr = np.asarray(probs, dtype=float).reshape(-1)
    if arr.size == 0:
        return None, None
    idx = int(np.argmax(arr))
    conf = float(arr[idx])
    classes = getattr(encoder, "classes_", None) if encoder is not None else None
    if classes is not None and len(classes) > idx:
        return str(classes[idx]), conf
    return f"{fallback_prefix} {idx}", conf


def predict_foodclasses_from_product_text(
    product_name: str | None,
    brand: str | None = None,
) -> FoodclassesBiLstmPrediction | None:
    if not settings.foodclasses_bilstm_enabled:
        return None
    if not _ensure_loaded():
        return None

    parts: list[str] = []
    if brand and str(brand).strip():
        parts.append(str(brand).strip())
    if product_name and str(product_name).strip():
        parts.append(str(product_name).strip())
    line = " ".join(parts).strip()
    if not line:
        return None

    try:
        seqs = _texts_to_sequences(_state.tokenizer, line)
        x = _pad_sequences(seqs, SEQ_LEN)
        pred = _state.model.predict(x, verbose=0)
        if not isinstance(pred, (list, tuple)) or len(pred) < 3:
            return None

        class_probs, subclass_probs, nova_probs = pred[0], pred[1], pred[2]

        class_enc = _encoder_by_len(_state.encoders, int(class_probs.shape[-1]), ("class_name", "class", "class_name_output"))
        subclass_enc = _encoder_by_len(_state.encoders, int(subclass_probs.shape[-1]), ("subclass_name", "subclass", "subclass_name_output"))
        nova_enc = _encoder_by_len(_state.encoders, int(nova_probs.shape[-1]), ("nova", "nova_output", "nova_class"))

        class_label, class_conf = _decode_head(class_probs, class_enc, "class")
        subclass_label, subclass_conf = _decode_head(subclass_probs, subclass_enc, "subclass")
        nova_label, nova_conf = _decode_head(nova_probs, nova_enc, "NOVA")

        return FoodclassesBiLstmPrediction(
            class_name=class_label,
            subclass_name=subclass_label,
            nova_label=nova_label,
            class_confidence=class_conf,
            subclass_confidence=subclass_conf,
            nova_confidence=nova_conf,
            input_text=line,
        )
    except Exception:
        logger.exception("Foodclasses BiLSTM predict failed for %r", line[:80])
        return None


def is_strong_catalog_classification(classification: ProductClassification | None) -> bool:
    """
    True when the reference-catalog row match is strong enough to skip BiLSTM:
    exact product name, or fuzzy score at/above the configured weak threshold.
    """
    if classification is None:
        return False
    method = (classification.match_method or "").lower()
    score = classification.match_score
    if method == "db_exact_name" or method.startswith("exact_"):
        return True
    if method == "db_fuzzy_name" and score is not None:
        return float(score) >= float(settings.foodclasses_bilstm_reference_weak_max_score)
    return False


def merge_foodclasses_with_classification(
    classification: ProductClassification | None,
    pred: FoodclassesBiLstmPrediction | None,
) -> ProductClassification | None:
    """
    Strong DB catalog match → keep DB class/subclass/nova.

    Otherwise, when ``pred`` is present, use the model labels as-is. Confidences stay on
    ``FoodclassesBiLstmPrediction`` for clients / future policy; they do not gate adoption here.
    """
    if pred is None:
        return classification
    if classification is not None and is_strong_catalog_classification(classification):
        return classification
    return ProductClassification(
        class_name=pred.class_name,
        subclass_name=pred.subclass_name,
        nova=normalize_nova_for_api(pred.nova_label),
        matched_description=classification.matched_description if classification is not None else None,
        match_method=(
            "bilstm_product_name_weak_fallback"
            if classification is not None
            else "bilstm_product_name_no_classification"
        ),
        match_score=None,
    )

