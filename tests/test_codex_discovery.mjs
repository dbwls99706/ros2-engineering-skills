/** Synthetic parser tests; live client discovery is a separate CI job. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { validateListing } from './check_codex_discovery.mjs';

const cwd = '/work';
const path = '/home/user/.agents/skills/ros2-engineering-skills/SKILL.md';
const skill = { name: 'ros2-engineering-skills', path, enabled: true, description: 'ROS 2' };
const listing = skills => ({ data: [{ cwd, errors: [], skills }] });

test('negative control requires absence', () => {
  assert.equal(validateListing(listing([]), cwd, 'absent').present, false);
  assert.throws(() => validateListing(listing([skill]), cwd, 'absent'));
});
test('positive control requires exact enabled installed copy', () => {
  assert.equal(validateListing(listing([skill]), cwd, path).present, true);
  for (const skills of [[], [skill, skill], [{ ...skill, enabled: false }],
    [{ ...skill, path: '/other/SKILL.md' }], [{ ...skill, path: undefined }],
    [{ ...skill, description: '' }]]) {
    assert.throws(() => validateListing(listing(skills), cwd, path));
  }
});
test('invalid envelopes and reported errors fail', () => {
  for (const result of [null, {}, { data: [] },
    { data: [{ cwd, errors: ['parse error'], skills: [skill] }] },
    { data: [{ cwd, errors: [], skills: null }] },
    { data: [{ cwd: '/wrong', errors: [], skills: [skill] }] }]) {
    assert.throws(() => validateListing(result, cwd, path));
  }
});
