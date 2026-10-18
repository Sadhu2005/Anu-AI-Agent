#!/usr/bin/env python3
"""V0.6.9: systemd unit and anu-policy agree on secrets/tests RO."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_secrets_and_tests_readonly_everywhere():
    unit = (REPO / "deploy" / "systemd" / "anu-agent.service").read_text(
        encoding="utf-8"
    )
    policy = (REPO / "config" / "anu-policy.yaml").read_text(encoding="utf-8")

    for path in ("/srv/anu/secrets", "/srv/anu/tests"):
        assert f"ReadOnlyPaths={path}" in unit, path
        assert f"ReadWritePaths={path}" not in unit, path

    ro = policy.split("read_only:", 1)[1].split("forbidden:", 1)[0]
    wr = policy.split("writable:", 1)[1].split("read_only:", 1)[0]
    assert "/srv/anu/secrets" in ro
    assert "/srv/anu/tests" in ro
    assert "/srv/anu/secrets" not in wr
    assert "/srv/anu/tests" not in wr
    print("PASS G1/G2 secrets+tests RO in unit and policy")


def main():
    print("===== ANU V0.6.9 G1/G2 POLICY UNIT SYNC =====")
    test_secrets_and_tests_readonly_everywhere()
    print("G1_G2_POLICY_UNIT_SYNC_TEST_COMPLETE")


if __name__ == "__main__":
    main()
