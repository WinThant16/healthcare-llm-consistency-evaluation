"""Percentile confidence intervals with a local RNG and whole-question resampling."""

import hashlib
import json
import math
import random


def stable_seed(seed, *parts):
    content = json.dumps([seed, *parts], sort_keys=True, ensure_ascii=False).encode()
    return int.from_bytes(hashlib.sha256(content).digest()[:8], "big")


def percentile(values, probability):
    if not 0 <= probability <= 1:
        raise ValueError("Percentile probability must be in [0,1]")
    values = sorted(values)
    if not values:
        return None
    position = probability * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def group_bootstrap(groups, statistic, samples=2000, seed=566):
    """Each item is a whole question (or a matched pair of model results for that question)."""
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 100:
        raise ValueError("Use at least 100 bootstrap replicates")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("Bootstrap seed must be an integer")
    groups = list(groups)
    if not groups:
        return {"estimate": None, "ci95": [None, None], "n_groups": 0,
                "valid_replicates": 0, "requested_replicates": samples, "note": "Empty denominator"}
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        estimate = statistic([groups[rng.randrange(len(groups))] for _ in groups])
        if estimate is not None:
            if not math.isfinite(estimate):
                raise ValueError("Statistic returned a non-finite estimate")
            draws.append(estimate)
    point = statistic(groups)
    return {"estimate": point, "ci95": [percentile(draws, .025), percentile(draws, .975)],
            "n_groups": len(groups), "valid_replicates": len(draws), "requested_replicates": samples,
            "note": "Single-group sample: CI is degenerate" if len(groups) == 1 else
                    "Some bootstrap draws have undefined denominators" if len(draws) != samples else None}
