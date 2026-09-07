"""Control-flow regressions; real ament/libxml validation remains a container gate."""

from pathlib import Path
import subprocess
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from tests import check_offline_xml as schema

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('rejection_code', [1, 3])
def test_positive_and_negative_controls_both_required(monkeypatch, tmp_path, rejection_code):
    call = Mock(side_effect=[subprocess.CompletedProcess([], code, '', '') for code in (0, rejection_code)])
    monkeypatch.setattr(schema.subprocess, 'run', call)
    schema.check(['validator'], tmp_path / 'valid.xml', tmp_path / 'invalid.xml', rejection_code)
    assert call.call_count == 2
    assert all(item.kwargs['timeout'] == 20 for item in call.call_args_list)


@pytest.mark.parametrize('codes', [(1, 1), (3, 3), (0, 0), (0, -9), (0, 5), (0, 124)])
def test_missing_schema_accepted_invalid_and_process_failures_do_not_pass(monkeypatch, tmp_path, codes):
    monkeypatch.setattr(schema.subprocess, 'run', Mock(side_effect=[
        subprocess.CompletedProcess([], code, '', '') for code in codes]))
    with pytest.raises(RuntimeError):
        schema.check(['validator'], tmp_path / 'valid.xml', tmp_path / 'invalid.xml', 3)


@pytest.mark.parametrize('failure', [OSError('missing tool'), subprocess.TimeoutExpired('validator', 20)])
def test_launch_or_timeout_error_propagates(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(schema.subprocess, 'run', Mock(side_effect=failure))
    with pytest.raises(type(failure)):
        schema.check(['validator'], tmp_path / 'valid.xml', tmp_path / 'invalid.xml', 3)


def test_control_is_well_formed_but_missing_required_license():
    assert ET.fromstring(schema.VALID).find('license') is not None
    assert ET.fromstring(schema.INVALID).find('license') is None
    assert schema.SCHEMA_URI in schema.VALID and schema.SCHEMA_URI in schema.INVALID


def test_main_runs_both_real_client_entry_points(monkeypatch, capsys):
    checked = []

    def check(command, valid, invalid, rejection_code):
        assert valid.read_text() == schema.VALID
        assert invalid.read_text() == schema.INVALID
        checked.append((command, rejection_code))
    monkeypatch.setattr(schema, 'check', check)
    schema.main()
    assert checked == [(['xmllint', '--nonet', '--noout', '--schema', schema.SCHEMA_URI], 3),
                       (['ament_xmllint'], 1)]
    assert 'controls passed' in capsys.readouterr().out


def test_catalog_resolves_original_schema_identifiers():
    namespace = '{urn:oasis:names:tc:entity:xmlns:xml:catalog}'
    catalog = ET.parse(ROOT / 'tests/schema/catalog.xml').getroot()
    for protocol in ('http', 'https'):
        url = f'{protocol}://download.ros.org/schema/package_format3.xsd'
        for tag, attribute in (('uri', 'name'), ('system', 'systemId')):
            assert any(row.get(attribute) == url and row.get('uri') == 'package_format3.xsd'
                       for row in catalog.findall(namespace + tag))


def test_image_prepares_pinned_schema_and_transitive_include_before_offline_runtime():
    dockerfile = (ROOT / 'tests/Dockerfile.ros2-test').read_text()
    assert '11ca24a41f31480dfb9562ba99f2a5b93d3ebda5' in dockerfile
    assert 'package_format3.xsd package_common.xsd' in dockerfile
    assert 'sha256sum --check SHA256SUMS' in dockerfile
    assert 'XML_CATALOG_FILES=/opt/ros2-test-schemas/catalog.xml' in dockerfile
    runtime = (ROOT / 'tests/run_ros2_container_tests.sh').read_text()
    assert runtime.index('check_offline_xml.py') < runtime.index('python3 -m pytest tests/')
    assert 'colcon test-result --verbose' in runtime
    runner = (ROOT / 'tests/run_ros2_tests.sh').read_text()
    assert '--network none' in runner
