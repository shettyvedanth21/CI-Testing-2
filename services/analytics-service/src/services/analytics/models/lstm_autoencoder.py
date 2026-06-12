"""LSTM autoencoder for anomaly sequence reconstruction."""

import pickle
import time

import numpy as np

from src.config.settings import get_settings
from src.services.analytics.models.tf_runtime import configure_tensorflow_runtime

SEQUENCE_LENGTH = 30
LATENT_DIM = 16
EPOCHS = 50
BATCH_SIZE = 32
THRESHOLD_PCTILE = 95
MIN_SEQUENCES = 50
MAX_TRAIN_SEQUENCES = 12000


class LSTMAnomalyAutoencoder:
    """Sequence model that flags anomalies via reconstruction error."""

    def __init__(self):
        self.model = None
        self.threshold = None
        self.is_trained = False
        self.training_timed_out = False
        self.training_stop_reason = None
        self.trained_epochs = 0

    def _build(self, n_features: int):
        configure_tensorflow_runtime()
        import tensorflow as tf

        inp = tf.keras.Input(shape=(SEQUENCE_LENGTH, n_features))
        x = tf.keras.layers.LSTM(32, return_sequences=True)(inp)
        x = tf.keras.layers.LSTM(LATENT_DIM)(x)
        x = tf.keras.layers.RepeatVector(SEQUENCE_LENGTH)(x)
        x = tf.keras.layers.LSTM(LATENT_DIM, return_sequences=True)(x)
        x = tf.keras.layers.LSTM(32, return_sequences=True)(x)
        out = tf.keras.layers.TimeDistributed(tf.keras.layers.Dense(n_features))(x)

        self.model = tf.keras.Model(inp, out)
        self.model.compile(optimizer="adam", loss="mse")

    def train(self, sequences: np.ndarray) -> bool:
        self.training_timed_out = False
        self.training_stop_reason = None
        self.trained_epochs = 0
        if len(sequences) < MIN_SEQUENCES:
            self.is_trained = False
            self.training_stop_reason = "insufficient_sequences"
            return False

        try:
            import tensorflow as tf
        except Exception:
            self.is_trained = False
            self.training_stop_reason = "tensorflow_unavailable"
            return False

        configure_tensorflow_runtime()
        self._build(sequences.shape[2])
        train_seq = sequences[-MAX_TRAIN_SEQUENCES:] if len(sequences) > MAX_TRAIN_SEQUENCES else sequences
        batch_size = min(BATCH_SIZE, max(8, len(train_seq) // 20))
        settings = get_settings()

        class _TrainingTimeBudgetCallback(tf.keras.callbacks.Callback):
            def __init__(self, max_seconds: float, min_epochs: int):
                super().__init__()
                self.max_seconds = max(1.0, float(max_seconds))
                self.min_epochs = max(1, int(min_epochs))
                self.started_at = 0.0
                self.completed_epochs = 0
                self.timed_out = False

            def on_train_begin(self, logs=None):
                self.started_at = time.monotonic()

            def _within_budget(self) -> bool:
                if self.started_at <= 0:
                    return True
                if self.completed_epochs < self.min_epochs:
                    return True
                return (time.monotonic() - self.started_at) < self.max_seconds

            def on_train_batch_end(self, batch, logs=None):
                if not self._within_budget():
                    self.timed_out = True
                    self.model.stop_training = True

            def on_epoch_end(self, epoch, logs=None):
                self.completed_epochs = max(self.completed_epochs, int(epoch) + 1)
                if not self._within_budget():
                    self.timed_out = True
                    self.model.stop_training = True

        time_budget_callback = _TrainingTimeBudgetCallback(
            settings.ml_temporal_autoencoder_max_train_seconds,
            settings.ml_temporal_autoencoder_min_epochs_before_cap,
        )

        history = self.model.fit(
            train_seq,
            train_seq,
            epochs=EPOCHS,
            batch_size=batch_size,
            validation_split=0.1,
            verbose=0,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(
                    patience=5,
                    restore_best_weights=True,
                ),
                time_budget_callback,
            ],
        )
        self.trained_epochs = max(
            int(time_budget_callback.completed_epochs),
            len((history.history or {}).get("loss", [])),
        )
        self.training_timed_out = bool(time_budget_callback.timed_out)
        self.training_stop_reason = (
            "time_budget_exceeded" if self.training_timed_out else "completed"
        )

        preds = self.model.predict(train_seq, verbose=0, batch_size=batch_size)
        errors = np.mean(np.abs(train_seq - preds), axis=(1, 2))
        self.threshold = float(np.percentile(errors, THRESHOLD_PCTILE))
        self.is_trained = True
        return True

    def predict(self, sequences: np.ndarray) -> dict:
        n = len(sequences)
        if not self.is_trained or self.model is None or n == 0:
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n),
                "reconstruction_error": np.zeros(n),
                "threshold": 0.0,
                "is_trained": False,
            }

        batch_size = min(BATCH_SIZE, max(8, len(sequences) // 20))
        preds = self.model.predict(sequences, verbose=0, batch_size=batch_size)
        errors = np.mean(np.abs(sequences - preds), axis=(1, 2))
        scores = np.clip(errors / (self.threshold + 1e-9) / 3, 0, 1)

        return {
            "is_anomaly": errors > self.threshold,
            "anomaly_score": scores,
            "reconstruction_error": errors,
            "threshold": self.threshold,
            "is_trained": True,
        }

    def to_bytes(self) -> bytes:
        if not self.is_trained or self.model is None:
            return b""
        payload = {
            "model_json": self.model.to_json(),
            "weights": self.model.get_weights(),
            "threshold": self.threshold,
        }
        return pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)

    def load_bytes(self, payload: bytes) -> bool:
        if not payload:
            self.is_trained = False
            return False
        try:
            import tensorflow as tf
        except Exception:
            self.is_trained = False
            return False

        try:
            data = pickle.loads(payload)
            configure_tensorflow_runtime()
            self.model = tf.keras.models.model_from_json(data["model_json"])
            self.model.set_weights(data["weights"])
            self.model.compile(optimizer="adam", loss="mse")
            self.threshold = float(data.get("threshold") or 0.0)
            self.is_trained = True
            return True
        except Exception:
            self.model = None
            self.threshold = None
            self.is_trained = False
            self.training_timed_out = False
            self.training_stop_reason = "artifact_load_failed"
            self.trained_epochs = 0
            return False
