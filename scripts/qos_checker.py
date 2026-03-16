#!/usr/bin/env python3
"""Check QoS compatibility between publisher and subscriber profiles.

Usage:
    python qos_checker.py --pub reliable,volatile,keep_last,5 --sub best_effort,volatile,keep_last,10

Planned features:
- Parse ros2 topic info -v output
- Detect incompatible reliability/durability pairs
- Suggest fixes
"""
# Phase 2: Full implementation
print("QoS Checker — coming in Phase 2")
