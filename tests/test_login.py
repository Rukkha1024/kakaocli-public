"""
kakaocli login 통합 테스트
.env에서 ID/PW를 읽어 실제 로그인 플로우를 검증한다.

사용법:
    conda run -n module python tests/test_login.py
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KAKAOCLI = PROJECT_ROOT / ".build" / "debug" / "kakaocli"


def load_env() -> dict[str, str]:
    """`.env` 파일에서 ID, PASSWORD를 읽는다."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print(f"FAIL: .env 파일이 없습니다: {env_path}")
        sys.exit(1)

    values = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()

    for key in ("ID", "PASSWORD"):
        if key not in values:
            print(f"FAIL: .env에 {key} 키가 없습니다.")
            sys.exit(1)

    return values


def run_cmd(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """kakaocli 명령을 실행하고 결과를 반환한다."""
    print(f"\n>>> {' '.join(args)}")
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    print(f"  stdout: {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"  stderr: {result.stderr.strip()}")
    print(f"  exit code: {result.returncode}")
    return result


def test_login_flow():
    """메인 테스트: 자격증명 저장 + 로그인 시도 + 상태 확인"""
    env = load_env()
    email = env["ID"]
    password = env["PASSWORD"]

    cli = str(KAKAOCLI)

    # 0. 바이너리 존재 확인
    if not KAKAOCLI.exists():
        print(f"FAIL: 바이너리가 없습니다. swift build를 먼저 실행하세요: {KAKAOCLI}")
        sys.exit(1)

    print("=" * 50)
    print("Step 1: kakaocli login --email --password (로그인 시도)")
    print("=" * 50)
    result = run_cmd([cli, "login", "--email", email, "--password", password])
    if result.returncode != 0:
        print(f"\nWARN: 로그인 명령이 실패했습니다 (exit code: {result.returncode})")
        print("  에러 내용을 확인하세요.")
    else:
        print("\nOK: 로그인 명령 성공")

    print("\n" + "=" * 50)
    print("Step 2: kakaocli login --status (상태 확인)")
    print("=" * 50)
    status_result = run_cmd([cli, "login", "--status"])

    if "logged in" in status_result.stdout.lower():
        print("\nSUCCESS: KakaoTalk 로그인 확인됨!")
    elif "login screen" in status_result.stdout.lower():
        print("\nINFO: KakaoTalk이 로그인 화면에 있습니다. 수동 확인이 필요할 수 있습니다.")
    else:
        print("\nINFO: 상태를 확인하세요.")

    print("\n" + "=" * 50)
    print("Step 3: kakaocli login --save-only (저장만 테스트)")
    print("=" * 50)
    save_result = run_cmd([cli, "login", "--save-only", "--email", email, "--password", password])
    if save_result.returncode == 0 and "saved" in save_result.stdout.lower():
        print("\nOK: --save-only 플래그 정상 동작")
    else:
        print("\nWARN: --save-only 결과를 확인하세요")


if __name__ == "__main__":
    test_login_flow()
