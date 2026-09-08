"""Synthetic event validation only; live fleet execution is a separate ROS gate."""

import pytest

from tests.check_generated_fleet import validate_child_exits


def events():
    return [{'event': 'start', 'pid': 11}, {'event': 'start', 'pid': 12},
            {'event': 'exit', 'pid': 12, 'returncode': 0},
            {'event': 'exit', 'pid': 11, 'returncode': 0}]


def test_complete_child_exits_are_required():
    assert validate_child_exits(events()) == [
        {'pid': 11, 'returncode': 0}, {'pid': 12, 'returncode': 0}]


@pytest.mark.parametrize('code', [-2, -9, 1, None, False, '0'])
def test_zero_supervisor_exit_cannot_hide_bad_children(code):
    rows = events()
    rows[-1]['returncode'] = code
    with pytest.raises(RuntimeError, match='did not exit cleanly'):
        validate_child_exits(rows)


@pytest.mark.parametrize('rows', [[], events()[:-1], events() + [events()[-1]],
                                  [events()[-1]], [{'event': 'start', 'pid': True}],
                                  [{'event': 'other', 'pid': 11}], [None]])
def test_incomplete_or_ambiguous_evidence_fails(rows):
    with pytest.raises(RuntimeError):
        validate_child_exits(rows)
