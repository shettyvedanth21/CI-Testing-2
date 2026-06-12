"""XGBoost classifier wrapper for failure probability."""

import hashlib
import pickle
from typing import List, Tuple

import numpy as np
from sklearn.model_selection import train_test_split

MIN_POINTS = 100
MIN_POS_LABELS = 3


class XGBoostFailureClassifier:
    """Gradient-boosted classifier with SHAP-backed explainability."""

    def __init__(self):
        self.model = None
        self.feature_names: List[str] = []
        self.is_trained = False

    def train(self, x: np.ndarray, y: np.ndarray, feature_names: List[str]) -> bool:
        if len(x) < MIN_POINTS or int(y.sum()) < MIN_POS_LABELS:
            self.is_trained = False
            return False

        try:
            import xgboost as xgb
        except Exception:
            self.is_trained = False
            return False

        self.feature_names = feature_names
        pos = max(float(y.sum()), 1.0)
        neg = max(float((y == 0).sum()), 1.0)
        scale = neg / pos

        self.model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=scale,
            random_state=42,
            tree_method="hist",
            eval_metric="aucpr",
            verbosity=0,
        )

        if len(x) > 200:
            stratify = y if int(y.sum()) >= 2 else None
            x_tr, x_val, y_tr, y_val = train_test_split(
                x,
                y,
                test_size=0.15,
                stratify=stratify,
                random_state=42,
            )
            self.model.fit(
                x_tr,
                y_tr,
                eval_set=[(x_val, y_val)],
                verbose=False,
            )
        else:
            self.model.fit(x, y, verbose=False)

        self.is_trained = True
        return True

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if not self.is_trained:
            return np.zeros(len(x))
        return self.model.predict_proba(x)[:, 1]

    def feature_schema_hash(self) -> str:
        joined = "|".join(self.feature_names)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def to_bytes(self) -> bytes:
        if not self.is_trained or self.model is None:
            return b""
        payload = {
            "model": self.model,
            "feature_names": self.feature_names,
        }
        return pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)

    def load_bytes(self, payload: bytes) -> bool:
        if not payload:
            self.is_trained = False
            return False
        try:
            data = pickle.loads(payload)
            self.model = data.get("model")
            self.feature_names = list(data.get("feature_names") or [])
            self.is_trained = self.model is not None
            return self.is_trained
        except Exception:
            self.is_trained = False
            return False

    def get_top_features(self, x_recent: np.ndarray, top_n: int = 5) -> List[Tuple[str, float]]:
        if not self.is_trained or len(x_recent) == 0:
            return []

        try:
            import shap

            explainer = shap.TreeExplainer(self.model)
            sample = x_recent[-min(100, len(x_recent)) :]
            shap_vals = explainer.shap_values(sample)
            importance = np.abs(shap_vals).mean(axis=0)
            pairs = sorted(
                zip(self.feature_names, importance),
                key=lambda x: x[1],
                reverse=True,
            )
            return pairs[:top_n]
        except Exception:
            pass

        try:
            imp = self.model.feature_importances_
            pairs = sorted(zip(self.feature_names, imp), key=lambda x: x[1], reverse=True)
            return pairs[:top_n]
        except Exception:
            return []
