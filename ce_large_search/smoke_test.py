#!/usr/bin/env python3
"""Quick smoke test for the CE large worker at normal + large m values."""
import subprocess, sys, os, json
from pathlib import Path

HERE = Path(__file__).parent
GP_SCRIPT = HERE / "worker_ec_large.gp"
WORKER = HERE / "worker_pari_large.py"

def test_gp_direct(m_start, m_end):
    """Run gp directly and print output."""
    gp_input = f"\\r {GP_SCRIPT}\nec_search_large({m_start},{m_end})\nquit\n"
    r = subprocess.run(
        ["gp", "-q", "--stacksize=128m"],
        input=gp_input,
        capture_output=True, text=True, timeout=120
    )
    print(f"=== gp test m=[{m_start},{m_end}] ===")
    print("STDOUT:", r.stdout[:2000])
    if r.stderr.strip():
        print("STDERR:", r.stderr[:500])
    return r.stdout

def test_worker(m_start, m_end):
    """Run full Python worker."""
    wu = Path("/tmp/smoke_wu.txt")
    res = Path("/tmp/smoke_result.txt")
    ckpt = Path("/tmp/smoke_ckpt.json")
    wu.write_text(f"m_start {m_start}\nm_end {m_end}\ntimeout_per_m 90\ngp_stack_mb 128\n")
    res.unlink(missing_ok=True)
    r = subprocess.run(
        [sys.executable, str(WORKER), str(wu), str(res), str(ckpt)],
        capture_output=True, text=True, timeout=300
    )
    print(f"=== worker test m=[{m_start},{m_end}] ===")
    print("STDOUT:", r.stdout[:2000])
    if res.exists():
        print("RESULT FILE:", res.read_text()[:1000])
    return r.returncode

if __name__ == "__main__":
    print("── Test 1: small m (should reproduce known solutions) ──")
    test_gp_direct(1, 5)

    print("\n── Test 2: medium m ──")
    test_gp_direct(100, 103)

    print("\n── Test 3: large m via full worker ──")
    rc = test_worker(100000000000000000001, 100000000000000000003)
    print(f"worker exit code: {rc}")

    print("\n── All smoke tests done ──")
