"""TensorFlow runtime safeguards for memory stability."""

from __future__ import annotations

import os

_CONFIGURED = False


def configure_tensorflow_runtime() -> None:
    """Best-effort runtime configuration to reduce OOM/allocator churn."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    try:
        import tensorflow as tf

        # Prevent TensorFlow from over-consuming threads in containerized workers.
        tf.config.threading.set_inter_op_parallelism_threads(1)
        tf.config.threading.set_intra_op_parallelism_threads(1)

        # If GPU exists, avoid pre-allocating all memory.
        for gpu in tf.config.list_physical_devices("GPU"):
            try:
                tf.config.experimental.set_memory_growth(gpu, True)
            except Exception:
                pass
    except Exception:
        pass
    _CONFIGURED = True
