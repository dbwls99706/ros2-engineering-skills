#!/usr/bin/env python3
"""Require real XML schema validation in the network-isolated ROS container.

The catalog resolves the original generated manifest's URI without modifying its
content or disabling ament_xmllint. Both a valid and an invalid control must work.
"""

from pathlib import Path
import subprocess
import tempfile

SCHEMA_URI = 'http://download.ros.org/schema/package_format3.xsd'
VALID = f'''<?xml version="1.0"?>
<?xml-model href="{SCHEMA_URI}" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>schema_control</name>
  <version>0.1.0</version>
  <description>Offline schema control</description>
  <maintainer email="test@example.com">Test</maintainer>
  <license>Apache-2.0</license>
</package>
'''
INVALID = VALID.replace('  <license>Apache-2.0</license>\n', '')


def check(command, valid, invalid, rejection_code):
    """A positive control rules out a missing tool/schema masquerading as rejection."""
    for path, expected in ((valid, 0), (invalid, rejection_code)):
        result = subprocess.run([*command, str(path)], text=True, capture_output=True, timeout=20)
        if result.returncode != expected:
            raise RuntimeError(f'{command[0]} {path.name}: unexpected result {result.returncode}\n'
                               + result.stdout + result.stderr)


def main():
    with tempfile.TemporaryDirectory(prefix='ros-schema-controls-') as folder:
        valid, invalid = Path(folder) / 'valid.xml', Path(folder) / 'invalid.xml'
        valid.write_text(VALID, encoding='utf-8')
        invalid.write_text(INVALID, encoding='utf-8')
        check(['xmllint', '--nonet', '--noout', '--schema', SCHEMA_URI], valid, invalid, rejection_code=3)
        check(['ament_xmllint'], valid, invalid, rejection_code=1)
    print('Offline schema controls passed: valid accepted, missing-license manifest rejected.')


if __name__ == '__main__':
    main()
