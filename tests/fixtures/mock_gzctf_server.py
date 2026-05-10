"""Tiny in-process mock of GZCTF for rehearsals + integration tests.

Implements just enough of the API surface the supervisor + adapter
walk through, parameterised by a scenario dict so the same fixture
can drive: Accepted, WrongAnswer, FlagSubmitted-then-Accepted,
NotFound, CheatDetected, Duplicate, Attachment.

Run as a thread-mounted FastAPI app (used by rehearsal_ai_identity.py
and unit tests); also runnable directly with uvicorn for ad-hoc poking.
"""
from __future__ import annotations

import argparse
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request


@dataclass
class MockChallengeFixture:
    """How the mock should behave for one challenge."""
    id: int
    title: str
    category: str
    type: str = "StaticAttachment"
    attachment: Optional[str] = None  # path component returned via /files/...
    accept_flag: Optional[str] = None  # if submitted flag matches → Accepted
    initial_pending_polls: int = 0  # number of polls returning FlagSubmitted before terminal
    cheat_flag: Optional[str] = None  # specific flag → CheatDetected
    not_found: bool = False  # always returns NotFound

    # internal state
    _submit_results: dict[int, list[str]] = field(default_factory=dict)
    _next_submit_id: int = 1


def make_app(fixtures: list[MockChallengeFixture]) -> FastAPI:
    app = FastAPI(title="mock GZCTF")
    by_id: dict[int, MockChallengeFixture] = {f.id: f for f in fixtures}

    # very loose state tracking
    state: dict[str, Any] = {
        "logged_in": False,
        "submit_seq": 0,
    }

    @app.post("/api/account/login")
    async def login(req: Request):
        body = await req.json()
        if not body.get("userName") or not body.get("password"):
            raise HTTPException(401, "missing credentials")
        state["logged_in"] = True
        return {"ok": True}

    @app.get("/api/account/profile")
    def profile():
        if not state["logged_in"]:
            raise HTTPException(401, "not logged in")
        return {"id": 1, "userName": "tester"}

    @app.get("/api/team")
    def team():
        return {"id": 7, "name": "mock-team"}

    @app.get("/api/game")
    def games_list(count: int = 50, skip: int = 0):
        return {"data": [{"id": 99, "title": "mock-game"}], "total": 1}

    @app.get("/api/game/{gid}")
    def game(gid: int):
        return {"id": gid, "title": "mock-game"}

    @app.get("/api/game/{gid}/details")
    def game_details(gid: int):
        # Real GZCTF GameDetailModel.challenges is a
        # Dictionary<string, ChallengeInfo[]> keyed by category.
        by_cat: dict[str, list[dict]] = {}
        for f in fixtures:
            by_cat.setdefault(f.category, []).append({
                "id": f.id,
                "title": f.title,
                "category": f.category,  # backfilled for clients that need it
                "type": f.type,
            })
        return {"id": gid, "challenges": by_cat}

    @app.get("/api/game/{gid}/challenges/{cid}")
    def challenge_detail(gid: int, cid: int):
        f = by_id.get(cid)
        if not f:
            raise HTTPException(404, "no such challenge")
        ctx = {}
        if f.attachment:
            ctx["url"] = f"/files/{f.attachment}"
        return {
            "id": f.id, "title": f.title, "category": f.category,
            "type": f.type, "context": ctx, "content": f"mock detail for {f.title}",
        }

    @app.get("/files/{name:path}")
    def attachment(name: str):
        # Tiny fake binary; the supervisor's misc agent just looks for
        # the file's existence, not its contents.
        from fastapi.responses import Response
        return Response(content=b"MOCK_ATTACHMENT_BYTES", media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/game/{gid}/challenges/{cid}")
    async def submit(gid: int, cid: int, req: Request):
        f = by_id.get(cid)
        if not f:
            raise HTTPException(404, "no such challenge")
        body = await req.json()
        flag = body.get("flag", "")
        state["submit_seq"] += 1
        sid = state["submit_seq"]

        # Decide terminal status for this submit_id
        if f.not_found:
            terminal = "NotFound"
        elif f.cheat_flag and flag == f.cheat_flag:
            terminal = "CheatDetected"
        elif f.accept_flag and flag == f.accept_flag:
            terminal = "Accepted"
        else:
            terminal = "WrongAnswer"

        seq = ["FlagSubmitted"] * max(0, f.initial_pending_polls) + [terminal]
        f._submit_results[sid] = seq
        return sid

    @app.get("/api/game/{gid}/challenges/{cid}/status/{sid}")
    def status(gid: int, cid: int, sid: int):
        # Real GZCTF returns the AnswerResult enum as a bare JSON
        # string, e.g. "Accepted" — not {"status": "Accepted"}.
        f = by_id.get(cid)
        if not f:
            raise HTTPException(404, "no such challenge")
        seq = f._submit_results.get(sid)
        if not seq:
            return "FlagSubmitted"
        head = seq[0]
        if len(seq) > 1:
            seq.pop(0)
        return head

    return app


# ---- thread runner -------------------------------------------------


class MockServerRunner:
    """Spawns the mock app on a free port in a background thread.
    Use with ``with`` so the thread is torn down after the test."""

    def __init__(self, fixtures: list[MockChallengeFixture], host: str = "127.0.0.1"):
        self.fixtures = fixtures
        self.host = host
        self.port: Optional[int] = None
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "MockServerRunner":
        # Pick a free port
        import socket
        s = socket.socket()
        s.bind((self.host, 0))
        self.port = s.getsockname()[1]
        s.close()

        app = make_app(self.fixtures)
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        # wait for the server to be ready
        for _ in range(50):
            if self._server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


# ---- standalone CLI ------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    fixtures = [
        MockChallengeFixture(
            id=11, title="Misc accepted", category="Misc",
            attachment="puzzle.zip",
            accept_flag="flag{mock-accepted-correct}",
            initial_pending_polls=1,
        ),
        MockChallengeFixture(id=12, title="Misc wrong", category="Misc",
                             accept_flag="flag{never-this}", initial_pending_polls=0),
        MockChallengeFixture(id=13, title="Cheat", category="Misc",
                             cheat_flag="flag{shared-from-other-team}"),
        MockChallengeFixture(id=14, title="Not Found", category="Misc",
                             not_found=True),
    ]
    app = make_app(fixtures)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
