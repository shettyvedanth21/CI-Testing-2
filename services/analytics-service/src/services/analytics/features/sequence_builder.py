"""Sequence builder for LSTM-compatible tensors."""

from typing import List, Tuple

import numpy as np
import pandas as pd


class SequenceBuilder:
    """Builds fixed-length sequences from timestamp-indexed dataframes."""

    def build_sequences(
        self,
        df: pd.DataFrame,
        sequence_length: int,
        numeric_cols: List[str],
    ) -> Tuple[np.ndarray, np.ndarray]:
        data = df[numeric_cols].values.astype(np.float32)
        timestamps = df.index.values
        x_list, ts_list = [], []

        for i in range(len(data) - sequence_length):
            x_list.append(data[i : i + sequence_length])
            ts_list.append(timestamps[i + sequence_length - 1])

        if not x_list:
            return (
                np.empty((0, sequence_length, len(numeric_cols)), dtype=np.float32),
                np.array([]),
            )

        return np.array(x_list, dtype=np.float32), np.array(ts_list)
