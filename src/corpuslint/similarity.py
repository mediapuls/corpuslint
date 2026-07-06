from __future__ import annotations

import numpy as np


def cosine_matrix(vectors: list[list[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = m / norms
    return unit @ unit.T
