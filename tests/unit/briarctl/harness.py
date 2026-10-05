"""Runs `briarctl` against a `FakeBriarClient` and collects what it printed."""

import io
import json
from dataclasses import dataclass, field
from typing import Any

from briarctl.cli import run
from briarctl.testing import FakeBriarClient

TOKEN = "s3cret-token"


@dataclass
class Result:
    code: int
    out: str
    err: str
    questions: list[str] = field(default_factory=list)

    @property
    def json(self) -> Any:
        return json.loads(self.out)

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


def briarctl(
    fake: FakeBriarClient,
    *argv: str,
    answers: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Result:
    """Run one command line. `answers` are the replies to questions, `no` when it runs out."""
    out, err = io.StringIO(), io.StringIO()
    replies = list(answers or [])
    questions: list[str] = []

    def confirm(question: str) -> bool:
        questions.append(question)
        return bool(replies) and replies.pop(0) == "y"

    code = run(
        argv,
        env={"BRIAR_AUTH_TOKEN": TOKEN} if env is None else env,
        stdout=out,
        stderr=err,
        confirm=confirm,
        connect=lambda _url, _token: fake,
    )
    return Result(code, out.getvalue(), err.getvalue(), questions)
