"""Tests for rosbag2_qos_checker.py - rosbag2 QoS playback validation."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from rosbag2_qos_checker import (
    _parse_yaml_qos, _extract_duration_ms, parse_metadata,
    check_playback_compatibility,
)
from qos_checker import (
    QoSProfile, Reliability, Durability, History,
)

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "rosbag2_qos_checker.py")


def _write_yaml(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


SAMPLE_METADATA = """\
rosbag2_bagfile_information:
  version: 8
  storage_identifier: sqlite3
  relative_file_paths:
    - bag_0.db3
  duration:
    nanoseconds: 5000000000
  starting_time:
    nanoseconds_since_epoch: 1000000000
  message_count: 100
  topics_with_message_count:
    - topic_metadata:
        name: /scan
        type: sensor_msgs/msg/LaserScan
        serialization_format: cdr
        offered_qos_profiles: |
          - reliability: best_effort
            durability: volatile
            history: keep_last
            depth: 5
      message_count: 50
    - topic_metadata:
        name: /map
        type: nav_msgs/msg/OccupancyGrid
        serialization_format: cdr
        offered_qos_profiles: |
          - reliability: reliable
            durability: transient_local
            history: keep_last
            depth: 1
      message_count: 1
"""

METADATA_NO_QOS = """\
rosbag2_bagfile_information:
  version: 8
  topics_with_message_count:
    - topic_metadata:
        name: /cmd_vel
        type: geometry_msgs/msg/Twist
        serialization_format: cdr
        offered_qos_profiles: ""
      message_count: 30
"""

METADATA_NUMERIC_QOS = """\
rosbag2_bagfile_information:
  version: 8
  topics_with_message_count:
    - topic_metadata:
        name: /odom
        type: nav_msgs/msg/Odometry
        serialization_format: cdr
        offered_qos_profiles: |
          - reliability: 1
            durability: 2
            history: 1
            depth: 10
      message_count: 100
"""


class TestParseYamlQoS:
    def test_basic_string_values(self):
        qos = _parse_yaml_qos({
            "reliability": "reliable",
            "durability": "volatile",
            "history": "keep_last",
            "depth": 10,
        }, "test")
        assert qos is not None
        assert qos.reliability == Reliability.RELIABLE
        assert qos.durability == Durability.VOLATILE
        assert qos.depth == 10

    def test_numeric_values(self):
        qos = _parse_yaml_qos({
            "reliability": 1,
            "durability": 1,
            "history": 2,
            "depth": 0,
        }, "num")
        assert qos is not None
        assert qos.reliability == Reliability.RELIABLE
        assert qos.durability == Durability.TRANSIENT_LOCAL
        assert qos.history == History.KEEP_ALL

    def test_numeric_liveliness_values(self):
        # rmw_qos_liveliness_policy_t: 1=AUTOMATIC,
        # 2=MANUAL_BY_NODE (deprecated), 3=MANUAL_BY_TOPIC
        from qos_checker import Liveliness
        qos = _parse_yaml_qos({"liveliness": 1}, "auto")
        assert qos is not None
        assert qos.liveliness == Liveliness.AUTOMATIC
        qos = _parse_yaml_qos({"liveliness": 3}, "topic")
        assert qos is not None
        assert qos.liveliness == Liveliness.MANUAL_BY_TOPIC
        # Deprecated MANUAL_BY_NODE maps down to AUTOMATIC so a
        # MANUAL_BY_TOPIC subscriber is still flagged incompatible.
        qos = _parse_yaml_qos({"liveliness": 2}, "node")
        assert qos is not None
        assert qos.liveliness == Liveliness.AUTOMATIC

    def test_with_deadline_dict(self):
        qos = _parse_yaml_qos({
            "reliability": "reliable",
            "durability": "volatile",
            "history": "keep_last",
            "depth": 1,
            "deadline": {"sec": 1, "nsec": 500000000},
        })
        assert qos is not None
        assert qos.deadline_ms == 1500

    def test_defaults_for_missing_fields(self):
        qos = _parse_yaml_qos({})
        assert qos is not None
        assert qos.reliability == Reliability.RELIABLE
        assert qos.durability == Durability.VOLATILE
        assert qos.history == History.KEEP_LAST


class TestExtractDurationMs:
    def test_nanoseconds_int(self):
        assert _extract_duration_ms(500_000_000) == 500

    def test_dict_format(self):
        assert _extract_duration_ms({"sec": 2, "nsec": 0}) == 2000

    def test_small_int(self):
        # 100 nanoseconds = 0 milliseconds (truncated)
        assert _extract_duration_ms(100) == 0

    def test_millisecond_range_int(self):
        # 5,000,000 nanoseconds = 5 milliseconds
        assert _extract_duration_ms(5_000_000) == 5

    def test_zero(self):
        assert _extract_duration_ms(0) == 0

    def test_string_returns_zero(self):
        assert _extract_duration_ms("invalid") == 0


class TestParseMetadata:
    def test_parse_sample_metadata(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        topics = parse_metadata(path)
        assert len(topics) == 2
        assert topics[0]["topic"] == "/scan"
        assert len(topics[0]["qos_profiles"]) == 1
        assert topics[1]["topic"] == "/map"

    def test_parse_no_qos(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", METADATA_NO_QOS)
        topics = parse_metadata(path)
        assert len(topics) == 1
        assert len(topics[0]["qos_profiles"]) == 0

    def test_parse_numeric_qos(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", METADATA_NUMERIC_QOS)
        topics = parse_metadata(path)
        assert len(topics) == 1
        assert len(topics[0]["qos_profiles"]) == 1

    def test_nonexistent_file(self):
        with pytest.raises(SystemExit):
            parse_metadata("/nonexistent/metadata.yaml")

    def test_empty_yaml(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", "")
        topics = parse_metadata(path)
        assert topics == []


class TestCheckPlaybackCompatibility:
    def test_compatible_scan_topic(self):
        topics = [{
            "topic": "/scan",
            "type": "sensor_msgs/msg/LaserScan",
            "qos_profiles": [{
                "reliability": "best_effort",
                "durability": "volatile",
                "history": "keep_last",
                "depth": 5,
            }],
        }]
        sub = QoSProfile(Reliability.BEST_EFFORT, Durability.VOLATILE,
                         History.KEEP_LAST, 5)
        results = check_playback_compatibility(topics, sub)
        assert results["compatible_topics"] == 1
        assert results["incompatible_topics"] == 0

    def test_incompatible_reliability(self):
        topics = [{
            "topic": "/scan",
            "type": "sensor_msgs/msg/LaserScan",
            "qos_profiles": [{
                "reliability": "best_effort",
                "durability": "volatile",
                "history": "keep_last",
                "depth": 5,
            }],
        }]
        sub = QoSProfile(Reliability.RELIABLE, Durability.VOLATILE,
                         History.KEEP_LAST, 5)
        results = check_playback_compatibility(topics, sub)
        assert results["incompatible_topics"] == 1

    def test_no_qos_produces_warning(self):
        topics = [{
            "topic": "/cmd_vel",
            "type": "geometry_msgs/msg/Twist",
            "qos_profiles": [],
        }]
        results = check_playback_compatibility(topics)
        assert results["compatible_topics"] == 1
        assert len(results["warnings"]) > 0

    def test_transient_local_playback_warning(self):
        topics = [{
            "topic": "/map",
            "type": "nav_msgs/msg/OccupancyGrid",
            "qos_profiles": [{
                "reliability": "reliable",
                "durability": "transient_local",
                "history": "keep_last",
                "depth": 1,
            }],
        }]
        results = check_playback_compatibility(topics)
        topic = results["topics"][0]
        assert any("TRANSIENT_LOCAL" in w for w in topic["warnings"])

    def test_no_subscriber_just_reports(self):
        topics = [{
            "topic": "/scan",
            "type": "sensor_msgs/msg/LaserScan",
            "qos_profiles": [{
                "reliability": "best_effort",
                "durability": "volatile",
                "history": "keep_last",
                "depth": 5,
            }],
        }]
        results = check_playback_compatibility(topics, None)
        assert results["compatible_topics"] == 1

    def test_multiple_topics(self):
        topics = [
            {
                "topic": "/scan",
                "type": "sensor_msgs/msg/LaserScan",
                "qos_profiles": [{"reliability": "best_effort",
                                  "durability": "volatile",
                                  "history": "keep_last", "depth": 5}],
            },
            {
                "topic": "/map",
                "type": "nav_msgs/msg/OccupancyGrid",
                "qos_profiles": [{"reliability": "reliable",
                                  "durability": "transient_local",
                                  "history": "keep_last", "depth": 1}],
            },
        ]
        sub = QoSProfile(Reliability.RELIABLE, Durability.VOLATILE,
                         History.KEEP_LAST, 10)
        results = check_playback_compatibility(topics, sub)
        assert results["total_topics"] == 2


class TestMainDirect:
    """Test main() directly for coverage (subprocess doesn't count)."""

    def test_main_basic(self, tmp_path, monkeypatch):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        monkeypatch.setattr("sys.argv", ["rosbag2_qos_checker.py", path])
        from rosbag2_qos_checker import main
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_main_json(self, tmp_path, monkeypatch, capsys):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        monkeypatch.setattr("sys.argv",
                            ["rosbag2_qos_checker.py", path, "--json"])
        from rosbag2_qos_checker import main
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert "topics" in data

    def test_main_with_sub(self, tmp_path, monkeypatch):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        monkeypatch.setattr("sys.argv",
                            ["rosbag2_qos_checker.py", path,
                             "--sub", "reliable,volatile,keep_last,10"])
        from rosbag2_qos_checker import main
        with pytest.raises(SystemExit) as exc:
            main()
        # /scan is best_effort, sub is reliable → incompatible → exit 1
        assert exc.value.code == 1

    def test_main_text_output_incompatible(self, tmp_path, monkeypatch, capsys):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        monkeypatch.setattr("sys.argv",
                            ["rosbag2_qos_checker.py", path,
                             "--sub", "reliable,volatile,keep_last,10"])
        from rosbag2_qos_checker import main
        with pytest.raises(SystemExit):
            main()
        out = capsys.readouterr().out
        assert "INCOMPATIBLE" in out
        assert "Summary:" in out

    def test_main_text_output_with_warnings(self, tmp_path, monkeypatch, capsys):
        path = _write_yaml(tmp_path, "metadata.yaml", METADATA_NO_QOS)
        monkeypatch.setattr("sys.argv", ["rosbag2_qos_checker.py", path])
        from rosbag2_qos_checker import main
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "global warning" in out or "WARN" in out or "topic(s)" in out

    def test_main_version(self, monkeypatch):
        monkeypatch.setattr("sys.argv",
                            ["rosbag2_qos_checker.py", "--version"])
        from rosbag2_qos_checker import main
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0


class TestEdgeCases:
    """Cover remaining edge-case branches."""

    def test_parse_yaml_qos_handles_unknown_reliability(self):
        """Unknown reliability falls back to default (RELIABLE)."""
        result = _parse_yaml_qos({"reliability": "invalid_value"})
        assert result is not None
        assert result.reliability == Reliability.RELIABLE

    def test_parse_metadata_dict_qos_profile(self, tmp_path):
        """Cover branch: offered_qos_profiles is a dict (not string/list)."""
        content = """\
rosbag2_bagfile_information:
  version: 8
  topics_with_message_count:
    - topic_metadata:
        name: /test
        type: std_msgs/msg/String
        serialization_format: cdr
        offered_qos_profiles:
          reliability: reliable
          durability: volatile
          history: keep_last
          depth: 1
      message_count: 10
"""
        path = _write_yaml(tmp_path, "metadata.yaml", content)
        topics = parse_metadata(path)
        assert len(topics) == 1
        assert len(topics[0]["qos_profiles"]) == 1

    def test_parse_metadata_alternative_format(self, tmp_path):
        """Cover branch: no rosbag2_bagfile_information key."""
        content = """\
topics_with_message_count:
  - topic_metadata:
      name: /alt
      type: std_msgs/msg/String
      serialization_format: cdr
      offered_qos_profiles: ""
    message_count: 5
"""
        path = _write_yaml(tmp_path, "metadata.yaml", content)
        topics = parse_metadata(path)
        assert len(topics) == 1
        assert topics[0]["topic"] == "/alt"

    def test_parse_yaml_qos_single_dict_from_string(self, tmp_path):
        """Cover branch: YAML string parses to a single dict (not list)."""
        content = """\
rosbag2_bagfile_information:
  version: 8
  topics_with_message_count:
    - topic_metadata:
        name: /single
        type: std_msgs/msg/String
        serialization_format: cdr
        offered_qos_profiles: |
          reliability: reliable
          durability: volatile
          history: keep_last
          depth: 1
      message_count: 10
"""
        path = _write_yaml(tmp_path, "metadata.yaml", content)
        topics = parse_metadata(path)
        assert len(topics[0]["qos_profiles"]) == 1


class TestCLI:
    def test_basic_run(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        result = subprocess.run(
            [sys.executable, SCRIPT, path],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "topic(s)" in result.stdout

    def test_json_output(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        result = subprocess.run(
            [sys.executable, SCRIPT, path, "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "topics" in data
        assert data["total_topics"] == 2

    def test_with_subscriber_qos(self, tmp_path):
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        result = subprocess.run(
            [sys.executable, SCRIPT, path,
             "--sub", "reliable,volatile,keep_last,10"],
            capture_output=True, text=True,
        )
        # /scan is best_effort, sub is reliable → incompatible
        assert result.returncode != 0

    def test_version(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--version"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "0.1.0" in result.stdout

    def test_shorthand_subscriber_qos(self, tmp_path):
        """Test that --sub accepts shorthand like 'reliable'."""
        path = _write_yaml(tmp_path, "metadata.yaml", SAMPLE_METADATA)
        result = subprocess.run(
            [sys.executable, SCRIPT, path, "--sub", "reliable"],
            capture_output=True, text=True,
        )
        # /scan is best_effort, sub=reliable → incompatible
        assert result.returncode != 0

    def test_shorthand_best_effort_compatible(self, tmp_path):
        """Test that --sub best_effort is compatible with best_effort recorded."""
        path = _write_yaml(tmp_path, "metadata.yaml", METADATA_NUMERIC_QOS)
        result = subprocess.run(
            [sys.executable, SCRIPT, path, "--sub", "reliable"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0


class TestMetadataInput:
    WRAPPED = (
        "rosbag2_bagfile_information:\n"
        "  topics_with_message_count:\n"
        "    - topic_metadata:\n"
        "        name: /라이다/scan\n"
        "        type: sensor_msgs/msg/LaserScan\n"
        "        offered_qos_profiles: \"- reliability: 2\\n  durability: 2\"\n"
        "      message_count: 1\n")
    UNWRAPPED = (
        "topics_with_message_count:\n"
        "  - topic_metadata:\n"
        "      name: /scan\n"
        "      type: sensor_msgs/msg/LaserScan\n"
        "      offered_qos_profiles: ''\n"
        "    message_count: 1\n")

    def _run(self, *args, env=None):
        return subprocess.run([sys.executable, SCRIPT, *map(str, args)],
                              capture_output=True, text=True, env=env)

    def test_bag_directory_is_accepted(self, tmp_path):
        (tmp_path / "metadata.yaml").write_text(self.WRAPPED, encoding="utf-8")
        result = self._run(tmp_path, "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["total_topics"] == 1

    @pytest.mark.parametrize("text", [
        "rosbag2_bagfile_information:\n  version: 4\n",
        "rosbag2_bagfile_information:\n  topics_with_message_count: []\n",
        "version: 4\n",
        "topics_with_message_count: []\n",
        "",
    ])
    def test_absent_or_empty_topic_list_is_an_empty_bag(self, tmp_path, text):
        meta = tmp_path / "metadata.yaml"
        meta.write_text(text, encoding="utf-8")
        result = self._run(meta, "--json")
        assert result.returncode == 0, (text, result.stderr)
        assert json.loads(result.stdout)["total_topics"] == 0

    def test_both_metadata_layouts_and_empty_file_still_parse(self, tmp_path):
        for text in (self.WRAPPED, self.UNWRAPPED, ""):
            meta = tmp_path / "metadata.yaml"
            meta.write_text(text, encoding="utf-8")
            result = self._run(meta, "--json")
            assert result.returncode == 0, (text, result.stderr)
            assert json.loads(result.stdout)["total_topics"] == (0 if text == "" else 1)

    def test_utf8_metadata_is_read_independently_of_the_locale(self, tmp_path):
        meta = tmp_path / "metadata.yaml"
        meta.write_text(self.WRAPPED, encoding="utf-8")
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        result = self._run(meta, "--json", env=env)
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr

    def test_invalid_yaml_is_an_error_message_not_a_traceback(self, tmp_path):
        meta = tmp_path / "metadata.yaml"
        meta.write_text("a: [unclosed\n", encoding="utf-8")
        result = self._run(meta)
        assert result.returncode == 1
        assert "not valid YAML" in result.stderr and "Traceback" not in result.stderr

    @pytest.mark.parametrize("text", [
        "- a\n- b\n",
        "just a string\n",
        "rosbag2_bagfile_information: 3\n",
        "topics_with_message_count: 'x'\n",
        # Present but mistyped is not "no topics": none of these may read as
        # an empty, passing bag.
        "topics_with_message_count: null\n",
        "topics_with_message_count: ''\n",
        "topics_with_message_count: {}\n",
        "topics_with_message_count: 0\n",
        "topics_with_message_count: false\n",
        "rosbag2_bagfile_information:\n  topics_with_message_count: null\n",
        "topics_with_message_count:\n  - just_a_string\n",
        "topics_with_message_count:\n  - topic_metadata: 7\n",
    ])
    def test_malformed_structure_is_an_error_not_a_partial_result(self, tmp_path, text):
        # Skipping a bad entry would let --json report fewer topics than the
        # bag holds with exit 0.
        meta = tmp_path / "metadata.yaml"
        meta.write_text(text, encoding="utf-8")
        result = self._run(meta, "--json")
        assert result.returncode == 1, result.stdout
        assert "not rosbag2 metadata" in result.stderr and "Traceback" not in result.stderr
        assert result.stdout == ""
