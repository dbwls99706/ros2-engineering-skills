"""Documentation factuality regression tests.

These pin previously discovered factual errors in the reference documents so
they cannot silently reappear. They are string-level regression checks, not
proof of correctness — see README "Current status" for the scope statement.

Background (found during a real Humble/Nav2 field diagnosis):
- navigation.md presented ``recoveries_server`` / ``nav2_recoveries/`` as the
  CURRENT Humble naming; the rename to ``behavior_server`` /
  ``nav2_behaviors/`` actually happened in the Galactic -> Humble migration,
  so the documented config did not exist on the installed Humble.
- The pre-Galactic ``default_bt_xml_filename`` parameter appeared in a config
  example as if current; Galactic+ uses ``default_nav_to_pose_bt_xml`` and
  the old name is silently ignored.
- Spin/BackUp motion recoveries were presented as a mandatory default; an
  unvalidated +1.57 rad Spin caused an unexpected in-place rotation on a
  quadruped in the field.
"""

import os
import re

ROOT = os.path.join(os.path.dirname(__file__), '..')
NAVIGATION_MD = os.path.join(ROOT, 'references', 'navigation.md')
TESTING_MD = os.path.join(ROOT, 'references', 'testing.md')
# Detailed tables moved intact out of the selected body; retain all regressions.
PRINCIPLES_MD = os.path.join(ROOT, 'references', 'engineering-principles.md')
NAV2_EXPECTED = os.path.join(ROOT, 'evals', 'expected',
                             'nav2-configuration.md')

# A line that names a legacy identifier must, on the same line, make clear
# that the identifier is legacy (migration notes, rename explanations).
_LEGACY_CONTEXT = re.compile(
    r'pre-Humble|pre-Galactic|Galactic|legacy|renamed', re.IGNORECASE)


def _read(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def _lines(path):
    return _read(path).splitlines()


def _md_section(path, heading):
    """Extract heading-to-next-heading from a markdown file, ignoring '#'
    lines inside fenced code blocks (bash comments are not headings)."""
    lines = _lines(path)
    start = next(i for i, line in enumerate(lines)
                 if re.match(r'#{1,6} ', line) and heading in line)
    in_fence = False
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith('```'):
            in_fence = not in_fence
        elif not in_fence and re.match(r'#{1,6} ', lines[i]):
            end = i
            break
    return '\n'.join(lines[start:end])


class TestNav2RecoveryNaming:
    """recoveries_server / nav2_recoveries must never be presented as
    current naming — only inside migration/legacy context."""

    def _assert_legacy_context_only(self, path, needles):
        for lineno, line in enumerate(_lines(path), start=1):
            if any(n in line for n in needles):
                assert _LEGACY_CONTEXT.search(line), (
                    f'{os.path.basename(path)}:{lineno} mentions legacy Nav2 '
                    f'recovery naming outside a migration/legacy context: '
                    f'{line.strip()!r}'
                )

    def test_navigation_md(self):
        self._assert_legacy_context_only(
            NAVIGATION_MD, ('recoveries_server', 'nav2_recoveries'))

    def test_detailed_principles(self):
        self._assert_legacy_context_only(
            PRINCIPLES_MD, ('recoveries_server', 'nav2_recoveries'))

    def test_nav2_expected_has_no_legacy_recovery_naming(self):
        content = _read(NAV2_EXPECTED)
        assert 'recoveries_server' not in content
        assert 'nav2_recoveries' not in content

    def test_navigation_md_documents_current_humble_naming(self):
        content = _read(NAVIGATION_MD)
        assert 'behavior_server:' in content, (
            'navigation.md must show a behavior_server config example')
        assert 'nav2_behaviors/Spin' in content, (
            'navigation.md must document the Humble/Iron plugin syntax')


class TestPluginTypeSeparator:
    """Nav2 plugin type strings use `/` on Humble/Iron and `::` on Jazzy+
    (some types like dwb_core::DWBLocalPlanner already used `::` earlier).
    Both syntaxes must stay documented, and each occurrence must sit in the
    right distro context — string presence alone would pass even if the
    syntaxes were attributed to the wrong distros."""

    _WINDOW = 3

    def _occurrence_windows(self, path, needle):
        lines = _lines(path)
        for idx, line in enumerate(lines):
            if needle in line:
                lo = max(0, idx - self._WINDOW)
                hi = min(len(lines), idx + self._WINDOW + 1)
                yield idx + 1, '\n'.join(lines[lo:hi])

    def test_humble_slash_syntax_in_humble_context(self):
        found = False
        for lineno, window in self._occurrence_windows(
                NAVIGATION_MD, 'nav2_behaviors/Spin'):
            found = True
            assert re.search(r'Humble|Iron|older', window, re.IGNORECASE), (
                f'navigation.md:{lineno} shows the / plugin syntax without '
                f'Humble/Iron context'
            )
        assert found, 'navigation.md must document nav2_behaviors/Spin'

    def test_jazzy_double_colon_syntax_in_jazzy_context(self):
        found = False
        for lineno, window in self._occurrence_windows(
                NAVIGATION_MD, 'nav2_behaviors::Spin'):
            found = True
            assert re.search(r'Jazzy|newer', window, re.IGNORECASE), (
                f'navigation.md:{lineno} shows the :: plugin syntax without '
                f'Jazzy+ context'
            )
        assert found, 'navigation.md must document nav2_behaviors::Spin'

    def test_jazzy_expected_uses_modern_plugin_separator(self):
        """The nav2 eval prompt targets Jazzy — its expected answer must use
        the :: types and carry no /-style behavior plugin strings."""
        content = _read(NAV2_EXPECTED)
        assert 'nav2_behaviors::Spin' in content
        assert 'nav2_behaviors/Spin' not in content


class TestBtNavigatorParameter:
    """default_bt_xml_filename is pre-Galactic (a DIFFERENT boundary from
    the recovery rename) and must only appear flagged as such."""

    def test_old_param_only_in_pre_galactic_context(self):
        marker = re.compile(r'pre-Galactic|Foxy', re.IGNORECASE)
        for path in (NAVIGATION_MD, PRINCIPLES_MD, NAV2_EXPECTED):
            for lineno, line in enumerate(_lines(path), start=1):
                if 'default_bt_xml_filename' in line:
                    assert marker.search(line), (
                        f'{os.path.basename(path)}:{lineno} presents the '
                        f'pre-Galactic default_bt_xml_filename without '
                        f'flagging it: {line.strip()!r}'
                    )

    def test_current_param_documented(self):
        for path in (NAVIGATION_MD, NAV2_EXPECTED):
            assert 'default_nav_to_pose_bt_xml' in _read(path), (
                f'{os.path.basename(path)} must document the Galactic+ '
                f'default_nav_to_pose_bt_xml parameter'
            )

    def test_old_default_bt_filename_gone(self):
        """The pre-Galactic default BT filename must not be presented; the
        Humble-era name is navigate_to_pose_w_replanning_and_recovery.xml
        (which does not contain the old token as a substring)."""
        for path in (NAVIGATION_MD, NAV2_EXPECTED):
            assert 'navigate_w_replanning_and_recovery' not in _read(path), (
                f'{os.path.basename(path)} still names the pre-Galactic '
                f'default BT file'
            )


class TestMotionRecoveryGating:
    """Motion recoveries (Spin/BackUp) must not be presented as a mandatory
    default anywhere in the docs or eval fixtures."""

    def test_expected_does_not_mandate_spin_backup(self):
        content = _read(NAV2_EXPECTED)
        assert 'Must configure recovery behaviors: spin' not in content
        assert re.search(r'gate|actuation-free', content), (
            'nav2 expected answer must require safety gating for motion '
            'recoveries'
        )

    def test_navigation_md_keeps_escalation_ladder(self):
        content = _read(NAVIGATION_MD)
        assert 'actuation-free' in content
        assert 'escalation ladder' in content

    def test_costmap_clear_caveat_present(self):
        """Costmap clearing is actuation-free but not automatically safe —
        the re-observation caveat must stay documented."""
        content = _read(NAVIGATION_MD)
        assert re.search(r'can erase \*?real\*? obstacles', content), (
            'navigation.md must keep the costmap-clearing re-observation '
            'caveat'
        )


class TestUseSimTimeExamples:
    """use_sim_time: True examples must always pair with a /clock source
    (ros2 bag play --clock or a simulator); a clockless example hangs
    timer-driven nodes."""

    _WINDOW = 15

    def test_testing_md_sim_time_examples_have_clock_source(self):
        lines = _lines(TESTING_MD)
        for idx, line in enumerate(lines):
            if "'use_sim_time': True" in line:
                lo = max(0, idx - self._WINDOW)
                hi = min(len(lines), idx + self._WINDOW + 1)
                window = '\n'.join(lines[lo:hi])
                assert '--clock' in window, (
                    f'testing.md:{idx + 1} sets use_sim_time without a '
                    f'/clock source nearby'
                )


HARDWARE_MD = os.path.join(ROOT, 'references', 'hardware-interface.md')


class TestDistroMatrix:
    """Row-scoped checks on the engineering-principles.md distro table.

    Deliberately anchored to specific table cells (not global string
    absence) so that changelog prose mentioning an old value never trips
    the check. Pinned errors: Kilted EOL was recorded as Nov 2025 (official
    EOL is Dec 2026), Kilted ros2_control was 4.x while
    hardware-interface.md said 5.x, and the pre-Lyrical EventsExecutor was
    labeled stable although it ships in rclcpp::experimental.
    """

    def _distro_table(self):
        lines = _lines(PRINCIPLES_MD)
        header_idx = next(
            i for i, line in enumerate(lines)
            if line.startswith('| Feature') and 'Humble' in line)
        headers = [c.strip() for c in
                   lines[header_idx].strip().strip('|').split('|')]
        rows = {}
        for line in lines[header_idx + 2:]:
            if not line.startswith('|'):
                break
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            rows[cells[0]] = dict(zip(headers[1:], cells[1:]))
        return rows

    def test_table_has_lyrical_column(self):
        rows = self._distro_table()
        assert 'Lyrical (LTS)' in rows['EOL'], (
            'engineering-principles.md distro table must carry a Lyrical (LTS) column')

    def test_kilted_eol_cell_is_dec_2026(self):
        assert self._distro_table()['EOL']['Kilted (non-LTS)'] == 'Dec 2026'

    def test_lyrical_eol_cell_is_may_2031(self):
        assert self._distro_table()['EOL']['Lyrical (LTS)'] == 'May 2031'

    def test_lyrical_ubuntu_cell_is_26_04(self):
        assert self._distro_table()['Ubuntu']['Lyrical (LTS)'] == '26.04'

    def test_ros2_control_versions_per_distro(self):
        row = self._distro_table()['ros2_control interface']
        assert row['Kilted (non-LTS)'] == '5.x'
        assert '6.x' in row['Lyrical (LTS)']

    def test_events_executor_cells(self):
        row = self._distro_table()['EventsExecutor']
        assert 'Experimental' in row['Kilted (non-LTS)']
        assert 'EventsCBGExecutor' in row['Lyrical (LTS)']

    def test_hardware_interface_agrees_on_kilted_5x(self):
        content = _read(HARDWARE_MD)
        assert re.search(r'Kilted \(5\.x\)', content), (
            'hardware-interface.md distro comparison must keep Kilted at '
            '5.x, matching the engineering-principles.md table')


class TestCallbackGroupRule:
    """Service-from-callback semantics: synchronous waiting deadlocks a
    MutuallyExclusiveCallbackGroup; registering a response callback and
    returning is safe even in the same group. Pinned error: an anti-pattern
    row claimed the pattern 'Deadlocks even with async', prescribing group
    surgery where none was needed."""

    def _skill_table_row(self, needle):
        for line in _lines(PRINCIPLES_MD):
            if line.startswith('|') and needle in line:
                return line
        raise AssertionError(
            f'no engineering-principles.md table row containing {needle!r}')

    def test_service_future_row_states_async_is_safe(self):
        row = self._skill_table_row('service future')
        assert 'safe even in the same group' in row
        assert 'Deadlocks even with async' not in row

    def test_principle_bullet_distinguishes_sync_wait(self):
        lines = _lines(PRINCIPLES_MD)
        start = next(i for i, line in enumerate(lines)
                     if 'Calling a service from a callback' in line)
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith('- ') or not lines[i].strip()),
                   len(lines))
        block = '\n'.join(lines[start:end])
        assert 'returns without waiting' in block
        assert 'add_done_callback' in block
        assert 'must** be in a separate' not in block


COMMUNICATION_MD = os.path.join(ROOT, 'references', 'communication.md')


class TestDefaultRmwVendor:
    """Fast DDS (rmw_fastrtps_cpp) is the default RMW on every current
    release; only Galactic defaulted to CycloneDDS. Pinned errors: a
    section heading called CycloneDDS the 'default vendor' and the vendor
    table credited CycloneDDS as default for Humble+."""

    def test_cyclonedds_heading_not_called_default(self):
        for lineno, line in enumerate(_lines(COMMUNICATION_MD), start=1):
            if line.startswith('#') and 'CycloneDDS tuning' in line:
                assert 'default vendor' not in line, (
                    f'communication.md:{lineno} still titles CycloneDDS as '
                    f'the default vendor'
                )
                return
        raise AssertionError('CycloneDDS tuning section heading not found')

    def test_vendor_table_default_row(self):
        row = next((line for line in _lines(COMMUNICATION_MD)
                    if line.startswith('|') and 'Default RMW' in line),
                   None)
        assert row is not None, 'vendor table must keep a Default RMW row'
        cells = [c.strip() for c in row.strip().strip('|').split('|')]
        # Column order: Aspect | CycloneDDS | FastDDS | Connext | Zenoh
        assert 'Galactic' in cells[1], (
            f'CycloneDDS default cell must say Galactic only: {cells[1]!r}')
        assert 'Default' in cells[2], (
            f'FastDDS cell must carry the Default marker: {cells[2]!r}')

    def test_vendor_table_separates_support_tier(self):
        """Default-RMW status and ROS support tier are different facts and
        must live in separate rows (the old combined row put Connext's
        Tier 2 in the default column)."""
        assert any(line.startswith('|') and 'ROS support tier' in line
                   for line in _lines(COMMUNICATION_MD))

    def test_default_rmw_stated_in_dds_section(self):
        content = _read(COMMUNICATION_MD)
        assert 'rmw_fastrtps_cpp' in content


class TestZeroCopyClaims:
    """Copy avoidance is conditional, split across three mechanisms
    (rclcpp intra-process, loaned messages/SHM, separate-process DDS).
    Pinned error: engineering-principles.md called separate-process intra-host DDS
    'zero-overhead' and presented intra-process transfer as unconditionally
    zero-copy."""

    def test_detailed_principles_only_negates_zero_overhead(self):
        """'zero-overhead' may appear only in its negation."""
        for lineno, line in enumerate(_lines(PRINCIPLES_MD), start=1):
            plain = line.replace('**', '')
            idx = 0
            while True:
                idx = plain.find('zero-overhead', idx)
                if idx == -1:
                    break
                assert plain[max(0, idx - 4):idx] == 'not ', (
                    f'engineering-principles.md:{lineno} asserts zero-overhead positively: '
                    f'{line.strip()!r}'
                )
                idx += 1

    def test_detailed_principles_states_dds_is_not_zero_overhead(self):
        assert 'not zero-overhead' in _read(PRINCIPLES_MD).replace('**', '')

    def test_nodes_executors_lists_three_mechanisms(self):
        content = _read(os.path.join(ROOT, 'references',
                                     'nodes-executors.md'))
        assert 'Loaned messages' in content
        assert 'standard DDS transport' in content
        assert 'subscriber count' in content.lower()

    def test_interprocess_copy_avoidance_not_absolutized(self):
        """Vendor SHM/PSMX and loaned messages CAN avoid copies across
        processes when their preconditions hold — the docs must not claim
        inter-process is 'never' zero-copy (pinned overcorrection)."""
        for path in (PRINCIPLES_MD,
                     os.path.join(ROOT, 'references', 'nodes-executors.md')):
            content = _read(path)
            assert 'never zero-overhead' not in content, path
            assert 'never zero-copy' not in content, path


class TestCrashSafetyFraming:
    """Destructors are best-effort cleanup, not crash safety: they never
    run on SIGKILL/power loss and are not guaranteed on segfaults. Crash
    safety lives downstream (command timeout, heartbeat/watchdog, hardware
    e-stop). Pinned error: pitfall 8 and the shutdown anti-pattern row
    presented on_deactivate + destructor as the fix for crashes, and
    hardware-interface.md called the destructor a crash safety net."""

    def _skill_row(self, needle):
        for line in _lines(PRINCIPLES_MD):
            if line.startswith('|') and needle in line:
                return line
        raise AssertionError(f'no engineering-principles.md table row containing {needle!r}')

    def test_pitfall_8_requires_downstream_failsafes(self):
        row = self._skill_row('Treating process-side cleanup')
        assert 'best-effort' in row
        assert re.search(r'watchdog|timeout', row)
        assert 'when the node crashes | Send zero-command' not in row

    def test_shutdown_antipattern_row_points_downstream(self):
        row = self._skill_row('No safe command on shutdown')
        assert 'best-effort' in row
        assert re.search(r'watchdog|timeout', row)
        assert 'safety-estop' in row

    def test_hardware_interface_destructor_not_a_safety_net(self):
        content = _read(HARDWARE_MD)
        assert 'NOT a crash safety net' in content
        assert 'destructor does not run' in content
        assert 'not guaranteed' in content
        assert 'safety-estop' in content


class TestBlockingWaitSemantics:
    """Blocking-wait examples must be split by client library. Pinned
    error: rclpy's Future.result() was listed as a synchronous wait — it
    does not block; it immediately returns whatever result is stored."""

    def _callback_bullet(self):
        lines = _lines(PRINCIPLES_MD)
        start = next(i for i, line in enumerate(lines)
                     if 'Calling a service from a callback' in line)
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith('- ') or not lines[i].strip()),
                   len(lines))
        # Normalize hard-wrapped prose so phrase assertions are not broken
        # by line-wrap positions.
        return ' '.join('\n'.join(lines[start:end]).split())

    def test_rclpy_future_result_not_listed_as_blocking(self):
        block = self._callback_bullet()
        assert 'does not block' in block, (
            'the bullet must state that rclpy future.result() does not '
            'block')
        assert 'future.done()' in block

    def test_rclcpp_get_qualified_to_incomplete_futures(self):
        """future.get() is only a deadlock when called on a not-yet-complete
        future from the initiating callback; inside the response callback
        the future is complete and get() is safe (the examples use it)."""
        block = self._callback_bullet()
        assert 'not-yet-complete' in block
        assert '`get()`' in block
        assert 'response callback' in block


class TestKillTestSafetyConditions:
    """The kill -9 failsafe verification moves a real robot if the
    failsafe is broken; the instruction must carry its safety
    preconditions inline, not in another file."""

    def test_kill_test_paragraph_carries_safety_conditions(self):
        lines = _lines(HARDWARE_MD)
        idx = next(i for i, line in enumerate(lines) if 'kill -9' in line)
        window = '\n'.join(lines[max(0, idx - 3):idx + 10])
        for needle in ('never an automated', 'simulation', 'e-stop',
                       'torque limits'):
            assert needle in window, (
                f'kill-test instructions missing inline safety condition: '
                f'{needle!r}'
            )


SAFETY_ESTOP_MD = os.path.join(ROOT, 'references', 'safety-estop.md')


class TestFaultInjectionSafetyConsistency:
    """Fault injection and spoof tests on a real robot are operator-approved
    tests on a physically restrained platform, never unattended CI or an
    agent action. Pinned errors: the stop-path checklist said to automate
    items 1-6 'in CI-on-robot', and the SROS2 isolation check said 'Test
    this as part of CI-on-robot' — both contradicting hardware-interface.md.

    Scoped to the two sections that instruct hardware tests (heading to
    next heading), not a global string ban: conditional mentions of
    CI-on-robot elsewhere (e.g. 'do NOT run this in CI-on-robot') must stay
    legal."""

    def _section(self, heading):
        return _md_section(SAFETY_ESTOP_MD, heading)

    def test_isolation_check_requires_safety_conditions(self):
        section = _flat(self._section('Verify the isolation'))
        assert 'Test this as part of CI-on-robot' not in section
        # HIL can actuate real hardware; it is not automatically an isolated
        # simulation. Pin the actuation boundary as well as operator safeguards.
        for needle in ('actuation disconnected', 'HIL as physical',
                       'operator-approved', 'restrained',
                       'unattended CI', 'AI agent'):
            assert needle in section, (
                f'isolation-check section missing safety condition: '
                f'{needle!r}'
            )

    def test_stop_path_checklist_requires_safety_conditions(self):
        section = self._section('Stop-path checklist')
        assert 'Test this as part of CI-on-robot' not in section
        for needle in ('operator approval', 'physically restrained',
                       'unattended CI', 'AI agent'):
            assert needle in section, (
                f'stop-path checklist section missing safety condition: '
                f'{needle!r}'
            )


RUNTIME_PROVENANCE_MD = os.path.join(ROOT, 'references',
                                     'runtime-provenance.md')


def _flat(text):
    """Collapse whitespace so prose assertions survive hard wrapping.

    Reference files wrap at ~76 columns, so any asserted phrase longer than
    a few words straddles a newline. Without this, a test fails when a
    sentence is re-wrapped rather than when its meaning changes.
    """
    return ' '.join(text.split())


def _skill_row(needle):
    """The single engineering-principles.md table row containing `needle`.

    Row-scoped rather than whole-file: a global ban on a corrected phrase
    would also outlaw the migration note or test docstring that explains
    why the phrase was wrong.
    """
    rows = [line for line in _lines(PRINCIPLES_MD)
            if line.startswith('|') and needle in line]
    assert rows, f'no engineering-principles.md table row containing {needle!r}'
    assert len(rows) == 1, (
        f'{needle!r} matches {len(rows)} engineering-principles.md rows; tighten the needle')
    return rows[0]


class TestExecutorAntiPatternWording:
    """`spin(node)` creates an executor for that node only; other locally
    created nodes not added to another spinning executor get no
    executor-driven callbacks. Pinned errors: the row called this 'Starves
    other nodes' (a scheduling problem, sending readers to
    MultiThreadedExecutor as if concurrency were missing), and the first
    correction overshot to 'never spin at all', which is false for a node
    already added to another executor or spun on its own thread."""

    NEEDLE = 'in `main()` for a multi-node process'

    def test_row_does_not_claim_starvation(self):
        assert 'Starves other nodes' not in _skill_row(self.NEEDLE)

    def test_row_scopes_the_claim_to_unadded_nodes(self):
        row = _skill_row(self.NEEDLE)
        assert 'that node only' in row
        assert 'not added to another spinning executor' in _flat(row), (
            'the row must not claim other nodes never spin unconditionally')
        assert 'never spin at all' not in row

    def test_row_prescribes_one_executor(self):
        assert 'one executor' in _skill_row(self.NEEDLE)


class TestConstructorPublishQualified:
    """Publishing in a constructor is correct for TRANSIENT_LOCAL state
    intentionally retained for late joiners. Pinned overcorrection: a
    blanket 'Publishing in constructor' anti-pattern that forbade the
    pattern instead of the assumption behind it. The retained-sample
    precondition (live publisher, compatible subscriber QoS) must stay
    stated, so the exception is not read as a guarantee."""

    NEEDLE = 'one-shot VOLATILE message in the constructor'

    def test_row_targets_the_delivery_assumption(self):
        row = _skill_row(self.NEEDLE)
        assert 'VOLATILE' in row
        assert 'TRANSIENT_LOCAL' in row

    def test_row_keeps_the_retained_sample_precondition(self):
        row = _skill_row(self.NEEDLE)
        assert 'live publisher' in row
        assert 'compatible' in row


class TestCiCacheInvalidation:
    """build/ and install/ caches can serve artifacts whose sources were
    deleted, so CI links and tests code that no longer exists. Pinned
    errors: both engineering-principles.md Principle 10 and testing.md's CI section
    recommended caching build/ and install/ with no invalidation
    conditions; a first correction then called ccache and apt layers safe
    'unconditionally', which they are not.

    Scoped to the Principle 10 block and the named CI section — a global
    search would pass on an unrelated mention elsewhere in the file."""

    def _principle_10_block(self):
        lines = _lines(PRINCIPLES_MD)
        start = next(i for i, line in enumerate(lines)
                     if line.startswith('### ') and 'Build and CI hygiene'
                     in line)
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith('### ')), len(lines))
        return '\n'.join(lines[start:end])

    def test_principle_10_conditions_the_build_install_cache(self):
        block = self._principle_10_block()
        assert 'Do not cache `build/`/`install/` by default' in _flat(block)
        assert 'restore-keys' in block, (
            'Principle 10 must warn against partial cache restores')
        for needle in ('toolchain', 'dependency resolution', 'source tree'):
            assert needle in block, (
                f'Principle 10 cache key missing input: {needle!r}')

    def test_principle_10_does_not_call_any_cache_unconditionally_safe(self):
        block = self._principle_10_block()
        assert 'freely' not in block and 'unconditionally' not in block, (
            'no cache layer is safe unconditionally — apt/rosdep layers go '
            'stale with distro and package-index state')

    def test_testing_md_conditions_the_build_install_cache(self):
        section = _md_section(TESTING_MD, 'Key CI practices')
        assert 'restore-keys' in section
        assert re.search(r'ccache', section)
        assert 'no longer in the repository' in _flat(section), (
            'testing.md must state the stale-artifact failure mode')

    def test_testing_md_does_not_call_any_cache_unconditionally_safe(self):
        """Same rule as Principle 10, pinned on the reference file too: an
        apt/rosdep or base-image layer also goes stale with the distro,
        package index, dependency declarations, and moving tags."""
        section = _flat(_md_section(TESTING_MD, 'Key CI practices'))
        for absolute in ('freely', 'cannot serve', 'unconditionally'):
            assert absolute not in section, (
                f'testing.md cache guidance is absolute again: '
                f'{absolute!r} — no cache layer is safe without explicit '
                f'invalidation inputs')
        assert 'not automatically fresh' in section, (
            'testing.md must say dependency layers are not automatically '
            'fresh')


class TestVerificationLevels:
    """"Tests pass" and "safe to drive" are different levels of evidence.
    The canonical ladder lives in testing.md; other entries route to it.
    Order matters: a shuffled or duplicated ladder is a structural defect."""

    def _skill_ladder_rows(self):
        section = _md_section(TESTING_MD, 'Verification levels')
        return re.findall(r'^\| \*\*(L\d)\*\*', section, re.M)

    def test_canonical_ladder_is_ordered_and_unique(self):
        rows = self._skill_ladder_rows()
        expected = [f'L{i}' for i in range(7)]
        assert rows == expected, (
            f'testing.md verification ladder must be L0..L6 in order, got '
            f'{rows}')

    def test_detailed_principles_routes_to_canonical_ladder(self):
        section = _md_section(PRINCIPLES_MD, 'Verification levels')
        assert '(testing.md#11-verification-levels)' in section

    def test_testing_md_expands_every_level_in_order(self):
        section = _md_section(TESTING_MD, 'Verification levels')
        levels = re.findall(r'\*\*(L\d)\*\*', section)
        assert levels[:7] == [f'L{i}' for i in range(7)], (
            f'testing.md must expand L0..L6 in order, got {levels[:7]}')
        assert 'Does NOT prove' in section, (
            'each level must state what it does not prove')


class TestEndToEndStopVerification:
    """A zero command on a topic is not a stopped robot: the driver may
    discard it, the vendor call may fail, or a competing publisher may
    overwrite it. All four links stay documented together, and the hardware
    link carries measurable criteria — "the feedback got smaller" passes an
    unquantified check while the robot is still moving."""

    def _section(self):
        """Prose section plus its criteria subsection.

        `_md_section` stops at the next heading of any level, so the
        'Acceptance criteria' subsection would otherwise fall outside the
        block that documents the links it quantifies.
        """
        return '\n'.join((
            _md_section(SAFETY_ESTOP_MD,
                        'Zero on a topic is not a stopped robot'),
            self._acceptance_criteria(),
        ))

    def test_all_four_links_documented(self):
        section = _flat(self._section())
        for needle in ('Command ownership matches the declared architecture',
                       'single-arbiter invariant',
                       'explicit stop', 'return code', 'encoder'):
            assert needle in section, (
                f'stop-verification section missing link evidence: '
                f'{needle!r}')

    def test_publisher_count_is_tied_to_declared_architecture(self):
        """Pinned overreach: "publisher count must be exactly 1" stated as a
        universal law. Redundant and hot-standby designs exist; the rule is
        that observation matches the *declared* ownership architecture, with
        the single-arbiter invariant called out as this guide's default."""
        section = _flat(self._section())
        assert 'match the command-ownership architecture you declared' in (
            section)
        assert ('more than one active driver-facing command publisher is a '
                'failure' in section)

    def _acceptance_criteria(self):
        """The criteria live in their own subsection, so read that — and
        confirm the heading is unique first, since `_md_section` matches on
        a substring and would silently pick the wrong block if a second
        'Acceptance criteria' heading were ever added."""
        headings = [line for line in _lines(SAFETY_ESTOP_MD)
                    if re.match(r'^#+ Acceptance criteria\s*$', line)]
        assert len(headings) == 1, (
            f'expected exactly one "Acceptance criteria" heading in '
            f'safety-estop.md, found {len(headings)}')
        return _md_section(SAFETY_ESTOP_MD, 'Acceptance criteria')

    def test_hardware_link_has_measurable_criteria(self):
        section = self._acceptance_criteria()
        for symbol in ('t0', 'T_send', 'T_ack', 'epsilon_stop', 'T_stop',
                       'T_hold'):
            assert symbol in section, (
                f'acceptance criteria missing {symbol!r} — without times and '
                f'tolerances "verified" is not reproducible')
        assert 'measured per' in _flat(section).lower(), (
            'the thresholds must be measured and recorded per platform')

    def test_local_return_is_not_remote_acceptance(self):
        """A successful SDK return commonly means "enqueued locally" or
        "socket write succeeded", not that the device received or applied
        the command. Pinned overclaim: submission and acceptance evidence
        were listed as interchangeable proof of transport success."""
        section = _flat(self._acceptance_criteria())
        assert 'submission evidence only' in section, (
            'a successful local return must be labelled submission evidence')
        for needle in ('acknowledgement', 'echoed sequence number'):
            assert needle in section, (
                f'remote-acceptance evidence missing: {needle!r}')
        assert 'no acknowledgement' in section, (
            'protocols without an acknowledgement must be handled — state '
            'that remote acceptance was not directly verified')
        assert 'must not be relabeled' in section, (
            'downstream hardware response must not be relabeled as a '
            'protocol acknowledgement')

    def test_sros2_claim_separates_observe_constrain_verify(self):
        section = self._section()
        for needle in ('observe', 'constrain', 'verify that'):
            assert needle in section.lower(), (
                f'stop section must distinguish {needle!r} — an unenforced '
                f'policy looks identical to an enforced one')
        assert 'point-in-time' in section, (
            'publisher-count observation must be marked point-in-time')

    def test_detailed_principles_pitfall_covers_the_topic_level_illusion(self):
        row = _skill_row('the robot stopped')
        assert 'safety-estop' in row

    def test_detailed_principles_summary_matches_the_detailed_chain(self):
        """The always-loaded summary must not re-merge what the reference
        file separates. Pinned error: Principle 12 and pitfall 15 said the
        vendor "call returns success" / "call succeeded", equating a local
        return with remote acceptance in exactly the sentence most readers
        will see."""
        content = _flat(_read(PRINCIPLES_MD))
        for merged in ('that call returns success', 'call succeeded'):
            assert merged not in content, (
                f'engineering-principles.md still equates a local return with acceptance: '
                f'{merged!r}')
        assert 'local submission plus any available remote-acceptance' in (
            content), 'Principle 12 must carry the split summary'
        assert 'submission/acceptance evidence' in _skill_row(
            'the robot stopped')


class TestTfAuthorityLimitation:
    """ROS 2 has no per-transform publisher identity: TransformListener
    stores the literal 'Authority undetectable', so view_frames/tf2_monitor
    cannot name a broadcaster. The replacement must not overshoot either:
    `ros2 topic info /tf -v` lists publisher endpoints, which is not the
    same as attributing an individual parent-child edge to one of them."""

    def _tf_section(self):
        return _md_section(RUNTIME_PROVENANCE_MD, 'TF provenance')

    def test_provenance_states_the_limitation(self):
        section = self._tf_section()
        assert 'Authority undetectable' in section
        assert 'ros2 topic info /tf -v' in section, (
            'runtime-provenance.md must give the working alternative')

    def test_topic_info_is_not_sold_as_edge_attribution(self):
        section = self._tf_section()
        assert 'does not by itself attribute' in _flat(section), (
            'the /tf endpoint listing must not be presented as per-edge '
            'broadcaster attribution')

    def test_repeated_data_is_a_clue_not_proof(self):
        section = self._tf_section()
        assert 'TF_REPEATED_DATA' in section
        assert 'clue, not proof' in _flat(section), (
            'TF_REPEATED_DATA has other causes (identical-stamp resends, bag '
            'replay) and must not be presented as proof of duplicates')

    def test_freshness_is_checked_separately(self):
        assert 'tf2_monitor' in self._tf_section()


class TestProvenanceProofBoundaries:
    """`ros2 pkg prefix` and a fresh `python3 -c` import describe the shell
    that runs them, not an already-running node. Pinned error: both were
    filed under "which files did this process load", which is the exact
    overclaim this file exists to prevent."""

    def test_shell_and_process_sections_are_separate(self):
        content = _read(RUNTIME_PROVENANCE_MD)
        for heading in ('## 1. What the current shell resolves',
                        '## 2. What the running process actually inherited',
                        '## 3. Comparing the two'):
            assert heading in content, f'missing section: {heading!r}'

    def test_shell_section_states_what_it_cannot_prove(self):
        section = _md_section(RUNTIME_PROVENANCE_MD,
                              'What the current shell resolves')
        assert '**Does not prove:**' in section
        assert 'already-running process' in _flat(section)

    def test_process_section_flags_interpreter_and_namespace_limits(self):
        section = _md_section(RUNTIME_PROVENANCE_MD,
                              'What the running process actually inherited')
        assert 'interpreter for Python nodes' in _flat(section), (
            '/proc/<pid>/exe resolves to the interpreter, not the script')
        assert 'not 1:1' in section, (
            'node names and PIDs are not 1:1 — component containers and '
            'duplicate node names')
        assert 'namespace-scoped' in section, (
            '/proc inspection is Linux-only and PID-namespace-scoped')

    def test_checklist_carries_the_whole_file_limits(self):
        section = _md_section(RUNTIME_PROVENANCE_MD, 'Provenance checklist')
        for needle in ('not 1:1', 'PID-namespace-scoped', 'Point-in-time'):
            assert needle in section, (
                f'provenance checklist missing global limit: {needle!r}')


SYSTEM_DIAGNOSTICS_MD = os.path.join(ROOT, 'references',
                                     'system-diagnostics.md')


class TestBagSplittingIsNotRetention:
    """`--max-bag-duration` starts a new file every N seconds and keeps
    every previous one; it does not delete anything or bound disk use.
    Pinned error: it was presented as a "rolling bag", which on a robot
    ends as a full disk rather than a bounded recording."""

    def test_splitting_is_distinguished_from_retention(self):
        content = _flat(_read(SYSTEM_DIAGNOSTICS_MD))
        assert 'does not bound total disk use' in content
        assert 'Do not assume splitting is retention' in content

    def test_retention_option_is_version_checked_not_asserted(self):
        """Circular-retention flags arrived later in rosbag2's history, so
        the doc must send readers to --help on the installed version rather
        than promising a flag exists."""
        content = _flat(_read(SYSTEM_DIAGNOSTICS_MD))
        assert 'ros2 bag record --help' in content, (
            'retention options must be checked against the installed '
            'version (Principle 11), not asserted per distro')

    def test_failure_table_no_longer_calls_it_a_rolling_bag(self):
        section = _flat(_md_section(SYSTEM_DIAGNOSTICS_MD,
                                    'Common failures and fixes'))
        assert 'Rolling bag of the chain' not in section


class TestNoDefinedCommandPriority:
    """Multiple publishers on a command topic is a real hazard, but "last
    writer wins" describes a rule the middleware does not define. Pinned
    overclaim: the actuator "follows whichever arrived last", which reads
    as deterministic arbitration where there is none."""

    def test_safety_estop_states_there_is_no_defined_priority(self):
        section = _flat(_md_section(
            SAFETY_ESTOP_MD, 'Zero on a topic is not a stopped robot'))
        assert 'no defined command priority' in section
        assert 'arrived last' not in section
        assert 'processing timing' in section

    def test_navigation_matches(self):
        content = _flat(_read(NAVIGATION_MD))
        assert 'no defined command priority on the topic' in content
        assert 'last-writer-wins' not in content

    def test_safety_failure_table_does_not_claim_last_writer_wins(self):
        """The failures table is where the old phrasing survived a first
        pass: the prose section was corrected while the table still said
        "last writer wins at 20 Hz"."""
        section = _flat(_md_section(
            SAFETY_ESTOP_MD, 'Common failures and fixes'))
        assert 'last writer wins' not in section.lower()

    def test_runtime_provenance_states_no_defined_priority(self):
        section = _flat(_md_section(
            RUNTIME_PROVENANCE_MD, 'Who actually publishes this topic?'))
        assert 'last-writer-wins' not in section.lower()
        assert 'no defined command priority' in section.lower()


class TestSmootherDeadbandPitfallIsGeneric:
    """Upstream nav2_velocity_smoother assigns last_cmd_ from the
    rate-limited value BEFORE applying deadband_velocities_ (verified on
    humble and main), so the ramp still accumulates. The pitfall is about
    hand-rolled/open-loop smoothers and must not be written as an upstream
    defect."""

    def test_navigation_md_credits_upstream_ordering(self):
        content = _read(NAVIGATION_MD)
        assert 'last_cmd_' in content
        assert re.search(
            r'before.{0,40}`?deadband_velocities_`?', content), (
            'navigation.md must state that last_cmd_ is assigned before the '
            'deadband is applied')

    def test_pitfall_row_does_not_blame_upstream_nav2(self):
        row = _skill_row("open-loop smoother's feedback path")
        assert 'nav2_velocity_smoother' in row and 'before' in row, (
            'the row must cite upstream ordering as the correct reference')
        lowered = row.lower()
        for wrong in ('nav2 bug', 'nav2 defect', 'nav2 issue'):
            assert wrong not in lowered, (
                f'the deadband row must not describe upstream nav2 as '
                f'defective: {wrong!r}')


class TestDistroDetectionOrder:
    """Detection comes before asking, and the latest-LTS default applies to
    greenfield projects only. Pinned error: Principle 1 told the agent to
    default to the latest LTS whenever the user did not specify, which pulls
    APIs an existing workspace does not have. Conflicting evidence must be
    reported rather than silently resolved — the shell's sourced distro and
    the workspace's build pin answer different questions."""

    def _principle_1_block(self):
        lines = _lines(PRINCIPLES_MD)
        start = next(i for i, line in enumerate(lines)
                     if line.startswith('### ') and 'Distro awareness' in line)
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith('### ')), len(lines))
        return '\n'.join(lines[start:end])

    def test_detection_precedes_asking(self):
        block = self._principle_1_block()
        assert 'echo $ROS_DISTRO' in block
        assert block.index('echo $ROS_DISTRO') < block.index('Ask the user'), (
            'environment detection must come before asking the user')

    def test_latest_lts_default_is_greenfield_only(self):
        block = self._principle_1_block()
        assert 'Greenfield only' in block
        assert 'Never resolve an *existing* workspace to the newest LTS' in (
            _flat(block))

    def test_conflicting_evidence_is_reported_not_resolved(self):
        block = self._principle_1_block()
        assert 'Conflict rule' in block
        assert 'select neither silently' in _flat(block)
        assert 'inventory evidence' in block, (
            'ls /opt/ros lists what is installed; it is not a selection')


class TestQosDefaultsAreStartingPoints:
    """The QoS table matches rmw_qos_profile_sensor_data, but a preset is a
    starting point, not a verdict — and QoS compatibility says nothing about
    whether the delivered data is timely or safe to act on."""

    def _principle_6_block(self):
        lines = _lines(PRINCIPLES_MD)
        start = next(i for i, line in enumerate(lines)
                     if line.startswith('### ') and 'Quality of Service' in
                     line)
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith('### ')), len(lines))
        return '\n'.join(lines[start:end])

    def test_table_is_marked_as_starting_points(self):
        block = self._principle_6_block()
        assert 'starting points, not verdicts' in _flat(block)

    def test_reliable_sensor_case_is_allowed(self):
        block = self._principle_6_block()
        assert 'RELIABLE' in block and 'safety' in block.lower(), (
            'the table must allow RELIABLE for sensor data on a safety path')

    def test_compatibility_is_not_validity(self):
        block = self._principle_6_block()
        assert 'compatibility alone does not prove' in _flat(block)


class TestLifecycleDefaultHasExceptions:
    """Lifecycle stays the default for resource-owning nodes, but the rule
    was absolute. Plain nodes are correct in named cases, and lifecycle has
    a cost — a manager plus new transition-failure states."""

    def _principle_9_block(self):
        lines = _lines(PRINCIPLES_MD)
        start = next(i for i, line in enumerate(lines)
                     if line.startswith('### ') and 'Lifecycle-first' in line)
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith('### ')), len(lines))
        return '\n'.join(lines[start:end])

    def test_plain_node_exceptions_are_named(self):
        block = self._principle_9_block()
        assert 'A plain node is the right call' in _flat(block)
        for needle in ('outer supervisor', 'third-party', 'micro-ROS'):
            assert needle in block, (
                f'lifecycle exception missing case: {needle!r}')

    def test_lifecycle_cost_is_stated(self):
        block = self._principle_9_block()
        assert 'not free' in _flat(block)
        assert 'transition-failure' in _flat(block)


class TestAiPitfallTableGrowth:
    """The pitfall table is the skill's accumulated failure memory; rows are
    append-only and numbered sequentially. This pins the field-review batch
    (15-22) so a future edit cannot quietly drop them.

    Counted inside the AI-pitfalls section only — other tables in engineering-principles.md
    also start rows with a digit, and a whole-file count would drift with
    any of them."""

    _EXPECTED_MINIMUM = 22

    def _pitfall_section(self):
        return _md_section(PRINCIPLES_MD, 'AI pitfalls')

    def _pitfall_numbers(self):
        return [int(n) for n in
                re.findall(r'^\|\s*(\d+)\s*\|', self._pitfall_section(),
                           re.M)]

    def test_rows_are_sequential_and_complete(self):
        numbers = self._pitfall_numbers()
        assert numbers, 'no numbered pitfall rows found in the AI section'
        assert numbers == list(range(1, len(numbers) + 1)), (
            f'pitfall numbering must be sequential from 1: {numbers}')
        assert len(numbers) >= self._EXPECTED_MINIMUM, (
            f'pitfall table shrank below {self._EXPECTED_MINIMUM} rows')

    def test_field_review_pitfalls_present(self):
        section = self._pitfall_section()
        for needle in ('the robot stopped',
                       'assuming that is what runs',
                       'array-field semantics',
                       'TF chain connects',
                       'hardware verification',
                       'actuation-onset threshold',
                       "open-loop smoother's feedback path",
                       'stale daemon cache'):
            assert needle in section, (
                f'engineering-principles.md pitfall table missing: {needle!r}')


REALTIME_MD = os.path.join(ROOT, 'references', 'realtime.md')


class TestCyclictestProcedureSingleSource:
    """realtime.md defines ONE cyclictest measurement procedure (in the
    Benchmark reference numbers section); the tail-latency section
    references it instead of carrying a second recipe. Pinned error: the
    tail-latency section kept an older command (-l 100000, -h 400 — a
    histogram ceiling that overflows non-RT spikes) and a 'sort histogram'
    analysis that conflicted with the cumulative-count readout."""

    def test_tail_latency_section_references_the_procedure(self):
        section = _md_section(REALTIME_MD, 'Tail latency')
        assert '(#benchmark-reference-numbers)' in section, (
            'tail-latency section must link to the single measurement '
            'procedure')
        for stale in ('sort histogram', '-h 400', '-l 100000',
                      'sudo cyclictest'):
            assert stale not in section, (
                f'tail-latency section must not carry its own recipe: '
                f'{stale!r}'
            )

    def test_benchmark_section_keeps_the_procedure(self):
        section = _md_section(REALTIME_MD, 'Benchmark reference numbers')
        for needle in ('-D 10m', '-h 20000', 'cumulative'):
            assert needle in section, (
                f'benchmark procedure missing element: {needle!r}')
        assert re.search(r'overflows?', section), (
            'benchmark procedure must keep the overflow rerun note')


SECURITY_MD = os.path.join(ROOT, 'references', 'security.md')
MESSAGE_TYPES_MD = os.path.join(ROOT, 'references', 'message-types.md')
DEPLOYMENT_MD = os.path.join(ROOT, 'references', 'deployment.md')


class TestSros2ParticipantAndRotationFacts:
    """Pin source-reviewed SROS2 facts without treating prose as runtime proof."""

    def test_security_identity_is_participant_context_scoped(self):
        content = _flat(_read(SECURITY_MD))
        for needle in ('DomainParticipant', 'ROS context',
                       'does not have to equal a node name'):
            assert needle in content
        for stale in ('Mutual TLS authentication',
                      'each node gets its own enclave',
                      'defeats access control'):
            assert stale not in content

    def test_permissive_is_not_an_authorization_test_recipe(self):
        for path in (SECURITY_MD, DEPLOYMENT_MD):
            content = _read(path)
            assert 'export ROS_SECURITY_STRATEGY=Permissive' not in content
            assert 'access-control test' in content or (
                'cannot validate that an access-control denial works' in content)

    def test_rotation_reissues_only_in_staging(self):
        section = _md_section(SECURITY_MD, 'Certificate rotation at fleet scale')
        remove = 'rm -f -- "$staged/cert.pem" "$staged/key.pem"'
        create = 'ros2 security create_enclave "$STAGING" "$enclave"'
        assert remove in section and create in section
        assert section.index(remove) < section.index(create)
        assert 'create_enclave "$KEYSTORE" "$enclave"' not in section
        for evidence in ('old_serial', 'new_serial', 'old_end', 'new_end',
                         'openssl verify'):
            assert evidence in section

    def test_certificate_lifetime_and_signing_key_match_upstream(self):
        content = _read(SECURITY_MD)
        assert '3,650-day' in content
        assert '~2000-day' not in content
        signing = _md_section(SECURITY_MD, 'Signing and validating policy files')
        assert 'private/permissions_ca.key.pem' in signing
        assert 'private/ca.key.pem' not in signing


class TestMessageDefinitionFacts:
    """Pin corrected message semantics whose examples can corrupt sensor use."""

    def test_foxy_quaternion_uses_identity_default(self):
        section = _md_section(MESSAGE_TYPES_MD,
                              '`geometry_msgs/msg/Quaternion`')
        assert 'Foxy and current ROS 2' in section
        assert 'Foxy (EOL)' not in section
        assert 'Changed to 1.0 in Galactic+' not in section

    def test_image_stride_includes_padding(self):
        section = _md_section(MESSAGE_TYPES_MD, '`sensor_msgs/msg/Image`')
        assert 'full row length in bytes, including any padding' in _flat(section)
        assert 'Must equal `width × bytes_per_pixel`' not in section
        assert '`step × height` bytes' in section

    def test_camera_projection_is_not_unconditionally_k(self):
        content = _read(MESSAGE_TYPES_MD)
        assert 'Normally `R` is identity' in content
        assert 'may make' in content and 'differ from `K`' in content

    def test_depth_units_are_scoped_to_rep_118(self):
        section = _md_section(MESSAGE_TYPES_MD,
                              'Depth image unit exception')
        assert 'REP-118 depth-image stream' in section
        assert 'encoding strings alone do not assign physical units' in (
            _flat(section))
