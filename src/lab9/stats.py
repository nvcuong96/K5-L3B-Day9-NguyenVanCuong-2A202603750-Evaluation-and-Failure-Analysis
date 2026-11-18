"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

from collections.abc import Sequence
import math

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    if len(human) != len(judge):
        raise ValueError("human and judge labels must have the same length")

    tp = fp = fn = tn = 0
    for h, j in zip(human, judge):
        human_label = str(h).strip().upper()
        judge_label = str(j).strip().upper()
        if human_label == POS and judge_label == POS:
            tp += 1
        elif human_label != POS and judge_label == POS:
            fp += 1
        elif human_label == POS and judge_label != POS:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    cm = confusion(human, judge)
    precision = cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else 0.0
    recall = cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else 0.0
    return precision, recall


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    if len(human) != len(judge):
        raise ValueError("human and judge labels must have the same length")
    if not human:
        raise ValueError("cannot calculate kappa for an empty sample")

    human_labels = [str(x).strip().upper() for x in human]
    judge_labels = [str(x).strip().upper() for x in judge]
    labels = sorted(set(human_labels) | set(judge_labels))
    n = len(human_labels)

    p_o = sum(h == j for h, j in zip(human_labels, judge_labels)) / n
    p_e = 0.0
    for label in labels:
        human_rate = human_labels.count(label) / n
        judge_rate = judge_labels.count(label) / n
        p_e += human_rate * judge_rate

    if p_e == 1.0:
        raise ValueError("kappa is undefined when p_e == 1")
    return (p_o - p_e) / (1.0 - p_e)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    denominator = sensitivity + specificity - 1.0
    if denominator == 0:
        return 0.0
    corrected = (observed_rate + specificity - 1.0) / denominator
    return min(1.0, max(0.0, corrected))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    if len(a) != len(b):
        raise ValueError("paired samples must have the same length")
    if not a:
        return (0.0, 0.0, 0.0)
    if all(x == y for x, y in zip(a, b)):
        return (0.0, 0.0, 0.0)

    import numpy as np
    from scipy.stats import bootstrap

    a_array = np.asarray(a, dtype=float)
    b_array = np.asarray(b, dtype=float)

    def difference(x: np.ndarray, y: np.ndarray) -> float:
        return float(np.mean(x) - np.mean(y))

    result = bootstrap(
        (a_array, b_array),
        difference,
        paired=True,
        n_resamples=n_resamples,
        method="percentile",
        confidence_level=confidence,
        rng=np.random.default_rng(rng),
    )
    mean_difference = float(np.mean(a_array) - np.mean(b_array))
    lo = float(result.confidence_interval.low)
    hi = float(result.confidence_interval.high)
    return mean_difference, lo, hi


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    if n < 0 or c < 0 or k < 1:
        raise ValueError("n, c, and k must be valid non-negative values")
    if c > n:
        raise ValueError("c cannot exceed n")
    if k > n:
        raise ValueError("k cannot exceed n")
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    if n < 0 or c < 0 or k < 1:
        raise ValueError("n, c, and k must be valid non-negative values")
    if c > n:
        raise ValueError("c cannot exceed n")
    if k > n:
        raise ValueError("k cannot exceed n")
    return math.comb(c, k) / math.comb(n, k)
