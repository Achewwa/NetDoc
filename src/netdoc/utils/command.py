"""Safe command execution helpers."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CommandResult:
    """Normalized result from an external command."""

    command: list[str] | str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return asdict(self)


def run_command(
    command: list[str] | str,
    *,
    timeout: float = 10.0,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run a command with a timeout and capture stdout/stderr."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            shell=shell,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            returncode=-1,
            stdout=_decode_timeout_output(exc.stdout),
            stderr=_decode_timeout_output(exc.stderr),
            timed_out=True,
        )

    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def _decode_timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output
