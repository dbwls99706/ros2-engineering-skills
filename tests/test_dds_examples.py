"""The parts of tests/check_dds_examples.py that need no middleware."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import check_dds_examples as check  # noqa: E402


def test_every_configuration_block_of_the_references_is_found():
    found = check.examples()
    kinds = [kind for _source, kind, _text in found]
    assert kinds.count('cyclonedds') >= 9 and kinds.count('fastdds') >= 3
    sources = {source.split(':')[0] for source, _kind, _text in found}
    assert {'communication.md', 'deployment.md', 'multi-robot.md', 'realtime.md', 'security.md'} <= sources
    # SROS2 policies and DDS-Security documents are not middleware profiles
    assert not any(check.NOT_PROFILES.search(text) for _source, kind, text in found if kind == 'fastdds')


def test_blocks_are_read_from_markdown(tmp_path):
    (tmp_path / 'one.md').write_text(
        "text\n\n```xml\n<CycloneDDS><Domain/></CycloneDDS>\n```\n\n"
        "```bash\nexport CYCLONEDDS_URI='<CycloneDDS><Domain><General/></Domain></CycloneDDS>'\n```\n\n"
        "```xml\n<profiles><participant profile_name=\"p\"/></profiles>\n```\n\n"
        "```xml\n<policy version=\"0.2.0\"><enclaves><enclave path=\"/x\"><profiles/></enclave></enclaves>"
        "</policy>\n```\n"
        "```python\nprint('<CycloneDDS>')\n```\n")
    assert [(source, kind) for source, kind, _text in check.examples(tmp_path)] == [
        ('one.md:3', 'cyclonedds'), ('one.md:7', 'cyclonedds'), ('one.md:11', 'fastdds')]


def test_only_what_depends_on_the_machine_is_replaced(tmp_path):
    text = ('<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="eth0"/>'
            '<NetworkInterface name="wg0" /></Interfaces></General>'
            '<SharedMemory><Enable>true</Enable><LogLevel>warn</LogLevel></SharedMemory>'
            '<Tracing><OutputFile>/tmp/cdds.log</OutputFile><Verbosity>fine</Verbosity></Tracing>'
            '</Domain></CycloneDDS>')
    local = check.localised('cyclonedds', text, str(tmp_path))
    assert local.count('name="lo"') == 2 and 'eth0' not in local and 'wg0' not in local
    assert '<Enable>false</Enable><LogLevel>warn</LogLevel>' in local
    assert '<OutputFile>%s/trace.log</OutputFile><Verbosity>fine</Verbosity>' % tmp_path in local
    assert check.localised('fastdds', text, str(tmp_path)) == text


@pytest.mark.parametrize('kind,started,output,verdict', [
    ('cyclonedds', True, 'NODE_OK\n', 'loaded'),
    ('cyclonedds', False, "1.2 [0] python3: config: //CycloneDDS/Domain: Scheduling: unknown element (line 15)\n",
     'rejected'),
    ('cyclonedds', False, '1.2 [0] python3: failed to increase socket receive buffer size to at least 10485760 '
     'bytes, current is 425984 bytes\n', 'precondition'),
    ('cyclonedds', False, 'wg0: does not match an available interface.\n', 'failed'),
    ('cyclonedds', False, "[ERROR] [1.0] [rcl]: Error getting RMW implementation identifier / RMW implementation "
     "not installed, with error message 'failed to load shared library 'librmw_cyclonedds_cpp.so' due to dlopen "
     "error: libddsc.so.11: cannot open shared object file'\n", 'unavailable'),
    ('fastdds', True, "\x1b[37;1m2026-01-01 00:00:00.000 \x1b[31;1m[\x1b[37;1mXMLPARSER\x1b[31;1m Error] "
     "\x1b[37mNode 'kind' with bad content\x1b[34;1m -> Function \x1b[36mgetXMLReliabilityQos\x1b[m\nNODE_OK\n",
     'rejected'),
    ('fastdds', True, '[WARN] FASTRTPS_DEFAULT_PROFILES_FILE ... is deprecated\nNODE_OK\n', 'loaded'),
    ('fastdds', False, 'Traceback\nRCLError: failed\n', 'failed'),
])
def test_what_the_middleware_printed_decides_the_verdict(kind, started, output, verdict):
    got, lines = check.classify(kind, started, output)
    assert got == verdict
    assert bool(lines) is (verdict != 'loaded')


def test_what_the_middleware_said_is_kept_ahead_of_the_traceback():
    output = ('1795410530.339112 [83]    python3: add_peer_address: robot_a: unknown address\n'
              '[ERROR] [1795410530.4] [rmw_cyclonedds_cpp]: rmw_create_node: failed to create domain, error Error\n'
              'Traceback (most recent call last):\n  File "<string>", line 4, in <module>\n'
              'rclpy._rclpy_pybind11.RCLError: error creating node\n')
    verdict, lines = check.classify('cyclonedds', False, output)
    assert verdict == 'failed' and lines[0].endswith('add_peer_address: robot_a: unknown address')
    assert not any('Traceback' in line for line in lines)


def test_a_peer_given_by_host_name_is_replaced_and_an_address_is_kept(tmp_path):
    text = ('<CycloneDDS><Domain><Discovery><Peers><Peer address="robot_a"/><Peer address="10.0.0.2" />'
            '<Peer address="[::1]:7400"/></Peers></Discovery></Domain></CycloneDDS>')
    local = check.localised('cyclonedds', text, str(tmp_path))
    assert '<Peer address="127.0.0.1"/>' in local and 'robot_a' not in local
    assert '<Peer address="10.0.0.2" />' in local and '<Peer address="[::1]:7400"/>' in local


@pytest.mark.parametrize('kind,text,have,expected', [
    ('fastdds', '<!-- Fast DDS 2.x: Humble, Jazzy -->', '3.x', 'Fast DDS 2.x'),
    ('fastdds', '<!-- Fast DDS 2.x: Humble, Jazzy -->', '2.x', None),
    ('fastdds', '<!-- Fast DDS 3.x: Kilted and later -->', '2.x', 'Fast DDS 3.x'),
    ('fastdds', '<profiles/>', '2.x', None),
    ('cyclonedds', '<!-- legacy generation (CycloneDDS\n     0.10.x era) ONLY -->', '0.10', None),
    ('cyclonedds', '<!-- legacy generation (CycloneDDS\n     0.10.x era) ONLY -->', '11.0', 'CycloneDDS 0.10'),
    ('cyclonedds', '<CycloneDDS/>', '11.0', None),
])
def test_an_example_for_another_generation_is_skipped(kind, text, have, expected):
    assert check.other_generation(kind, text, have) == expected


def test_versions_are_read_from_the_installation(tmp_path, monkeypatch):
    prefix = tmp_path / 'opt' / 'ros' / 'demo'
    for name, text in (('share/fastrtps/cmake/fastrtps-config-version.cmake', 'set(PACKAGE_VERSION "2.14.7")'),
                       ('lib/x86_64-linux-gnu/cmake/CycloneDDS/CycloneDDSConfigVersion.cmake',
                        'set(PACKAGE_VERSION "0.10.5")')):
        (prefix / name).parent.mkdir(parents=True)
        (prefix / name).write_text(text)
    real = check.Path
    monkeypatch.setattr(check, 'Path', lambda *parts: real(str(tmp_path / 'opt' / 'ros'), *parts[1:])
                        if parts and parts[0] == '/opt/ros' else real(*parts))
    assert check.installed('demo') == {'fastdds': None, 'cyclonedds': None}  # no rmw layer for either
    (prefix / 'share' / 'rmw_fastrtps_cpp').mkdir()
    (prefix / 'share' / 'rmw_cyclonedds_cpp').mkdir()
    assert check.installed('demo') == {'fastdds': '2.x', 'cyclonedds': '0.10'}
    assert check.installed('missing') == {'fastdds': None, 'cyclonedds': None}


def test_listing_needs_no_middleware(capsys):
    assert check.main(['--list']) == 0
    assert 'communication.md' in capsys.readouterr().out
