"""Execute the documented quaternion correction against numeric counterexamples."""

import ast
import math
from pathlib import Path
import re

import pytest


@pytest.fixture(scope='module')
def normalize():
    text = (Path(__file__).resolve().parents[1] / 'references/message-types.md').read_text()
    block = next(code for code in re.findall(r'```python\n(.*?)```', text, re.S)
                 if 'def normalize_xyzw(' in code)
    namespace = {}
    exec(compile(ast.parse(block), 'documented-normalization-example', 'exec'), namespace)
    return namespace['normalize_xyzw']


@pytest.mark.parametrize('values, expected', [
    ((0, 0, 0, 2), (0, 0, 0, 1)),
    ((1, 2, 2, 4), (0.2, 0.4, 0.4, 0.8)),
    ((-1, -2, -2, -4), (-0.2, -0.4, -0.4, -0.8)),
    ((1e-300,) * 4, (0.5,) * 4), ((1e300,) * 4, (0.5,) * 4),
])
def test_normalization_preserves_direction_and_produces_unit_norm(normalize, values, expected):
    result = normalize(values)
    assert math.hypot(*result) == pytest.approx(1.0)
    assert all(math.isfinite(value) for value in result)
    assert result == pytest.approx(expected)
    assert normalize(result) == pytest.approx(result)


@pytest.mark.parametrize('values', [
    (0, 0, 0, 0), (math.nan, 0, 0, 1), (0, math.inf, 0, 1),
    (0, 0, -math.inf, 1), (0, 0, 0), (0, 0, 0, 1, 2),
])
def test_undefined_orientation_is_rejected(normalize, values):
    with pytest.raises(ValueError):
        normalize(values)
