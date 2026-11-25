#!/usr/bin/env python3
"""Portable commands used by students and by the course documentation."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CAR_SERVICE = ROOT / "apps" / "car-rental-service"
PRICING_SERVICE = ROOT / "apps" / "pricing-service"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def gradle_command() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "gradlew.bat"]
    return ["./gradlew"]


def test_java() -> None:
    run([*gradle_command(), "clean", "test"], cwd=CAR_SERVICE)


def test_python() -> None:
    run([sys.executable, "-m", "pytest"], cwd=PRICING_SERVICE)


def test() -> None:
    test_java()
    test_python()


def lint_java() -> None:
    run([*gradle_command(), "checkstyleMain", "checkstyleTest"], cwd=CAR_SERVICE)


def lint_python() -> None:
    run([sys.executable, "-m", "ruff", "check", "app", "tests"], cwd=PRICING_SERVICE)
    run(
        [sys.executable, "-m", "ruff", "format", "--check", "app", "tests"],
        cwd=PRICING_SERVICE,
    )


def lint() -> None:
    lint_java()
    lint_python()


def verify() -> None:
    test()
    lint()


USES_RE = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)")
FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
FULL_SHA_RE = re.compile(r"@[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def check_workflows() -> list[str]:
    errors = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_RE.match(line)
            if not match:
                continue
            ref = match.group(1)
            if ref.startswith("./"):
                continue  # action locale du depot
            if ref.startswith("docker://"):
                ok = DIGEST_RE.search(ref)
            else:
                ok = FULL_SHA_RE.search(ref)
            if not ok:
                errors.append(f"{path.relative_to(ROOT)}:{number}: reference mutable {ref}")
    return errors


def check_dockerfiles() -> list[str]:
    errors = []
    for path in sorted((ROOT / "apps").glob("*/Dockerfile")):
        stages: set[str] = set()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = FROM_RE.match(line)
            if not match:
                continue
            image, alias = match.group(1), match.group(2)
            # FROM d'un stage precedent ou scratch : pas d'image a epingler
            if image.lower() not in stages and image != "scratch" and not DIGEST_RE.search(image):
                errors.append(f"{path.relative_to(ROOT)}:{number}: FROM sans digest {image}")
            if alias:
                stages.add(alias.lower())
    return errors


def supply_chain_policy() -> None:
    errors = check_workflows() + check_dockerfiles()
    for error in errors:
        print(error)
    if errors:
        raise RuntimeError(f"{len(errors)} reference(s) mutable(s)")
    print("OK supply-chain-policy")


def compose_up() -> None:
    require_command("docker")
    run(["docker", "compose", "up", "--build", "--detach"])


def compose_down() -> None:
    require_command("docker")
    run(["docker", "compose", "down"])


def wait_for(url: str, *, timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                if 200 <= response.status < 300:
                    print(f"OK {url}")
                    return
        except (URLError, TimeoutError) as error:
            last_error = error
        time.sleep(1)

    raise RuntimeError(f"Service unavailable at {url}: {last_error}")


def smoke() -> None:
    wait_for("http://localhost:8080/health")
    wait_for("http://localhost:8080/cars")
    wait_for("http://localhost:8000/health")
    request = Request(
        "http://localhost:8000/quotes",
        data=json.dumps({"category": "standard", "days": 2}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        quote = json.load(response)
    if quote.get("total") != 120.0 or quote.get("currency") != "EUR":
        raise RuntimeError(f"Unexpected pricing response: {quote}")
    print("OK pricing quote")


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"Required command not found: {command}")


def prerequisites() -> None:
    checks = {
        "Python": sys.executable,
        "Java": shutil.which("java"),
        "Docker": shutil.which("docker"),
    }
    missing = False

    for name, location in checks.items():
        if location:
            print(f"OK {name}: {location}")
        else:
            print(f"MISSING {name}")
            missing = True

    if missing:
        raise RuntimeError("One or more prerequisites are missing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "prerequisites",
            "test",
            "test-java",
            "test-python",
            "lint",
            "lint-java",
            "lint-python",
            "verify",
            "supply-chain-policy",
            "compose-up",
            "compose-down",
            "smoke",
        ],
    )
    args = parser.parse_args()

    commands = {
        "prerequisites": prerequisites,
        "test": test,
        "test-java": test_java,
        "test-python": test_python,
        "lint": lint,
        "lint-java": lint_java,
        "lint-python": lint_python,
        "verify": verify,
        "supply-chain-policy": supply_chain_policy,
        "compose-up": compose_up,
        "compose-down": compose_down,
        "smoke": smoke,
    }

    try:
        commands[args.command]()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
