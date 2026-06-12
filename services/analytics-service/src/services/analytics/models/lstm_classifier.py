"""LSTM sequence classifier for failure prediction."""

import pickle

import numpy as np

from src.services.analytics.models.tf_runtime import configure_tensorflow_runtime

SEQUENCE_LENGTH = 30
EPOCHS = 50
BATCH_SIZE = 32
MIN_SEQUENCES = 50
MIN_POS_LABELS = 5
MAX_TRAIN_SEQUENCES = 12000


class LSTMFailureClassifier:
    """Temporal model that predicts failure probability from recent sequences."""

    def __init__(self):
        self.model = None
        self.is_trained = False

    def _build(self, n_features: int):
        configure_tensorflow_runtime()
        import tensorflow as tf

        inp = tf.keras.Input(shape=(SEQUENCE_LENGTH, n_features))
        x = tf.keras.layers.LSTM(64, return_sequences=True, dropout=0.2)(inp)
        x = tf.keras.layers.LSTM(32, dropout=0.2)(x)
        x = tf.keras.layers.Dense(16, activation="relu")(x)
        x = tf.keras.layers.Dropout(0.3)(x)
        out = tf.keras.layers.Dense(1, activation="sigmoid")(x)

        self.model = tf.keras.Model(inp, out)
        self.model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

    def train(self, sequences: np.ndarray, labels: np.ndarray) -> bool:
        if len(sequences) < MIN_SEQUENCES or int(labels.sum()) < MIN_POS_LABELS:
            self.is_trained = False
            return False

        try:
            import tensorflow as tf
        except Exception:
            self.is_trained = False
            return False

        configure_tensorflow_runtime()
        self._build(sequences.shape[2])
        train_seq = sequences[-MAX_TRAIN_SEQUENCES:] if len(sequences) > MAX_TRAIN_SEQUENCES else sequences
        train_labels = labels[-len(train_seq):] if len(labels) > len(train_seq) else labels

        pos = max(float(train_labels.sum()), 1.0)
        neg = max(float((train_labels == 0).sum()), 1.0)
        class_weight = {0: 1.0, 1: neg / pos}
        batch_size = min(BATCH_SIZE, max(8, len(train_seq) // 20))

        self.model.fit(
            train_seq,
            train_labels,
            epochs=EPOCHS,
            batch_size=batch_size,
            validation_split=0.15,
            class_weight=class_weight,
            verbose=0,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(
                    patience=7,
                    restore_best_weights=True,
                )
            ],
        )
        self.is_trained = True
        return True

    def predict_proba(self, sequences: np.ndarray) -> np.ndarray:
        if not self.is_trained or self.model is None or len(sequences) == 0:
            return np.zeros(len(sequences))
        batch_size = min(BATCH_SIZE, max(8, len(sequences) // 20))
        return np.clip(self.model.predict(sequences, verbose=0, batch_size=batch_size).flatten(), 0, 1)

    def to_bytes(self) -> bytes:
        if not self.is_trained or self.model is None:
            return b""
        payload = {
            "model_json": self.model.to_json(),
            "weights": self.model.get_weights(),
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
            self.model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
            self.is_trained = True
            return True
        except Exception:
            self.model = None
            self.is_trained = False
            return False
