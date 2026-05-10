#!/usr/bin/env python3
"""Bootstrap the local 127.0.0.1 GZCTF rehearsal.

This script only targets the local lab from this directory. It creates:

- player user: local_player
- team: local-team
- game: local-rehearsal-game
- two Misc StaticAttachment challenges:
  - local-accepted
  - local-wrong-freeze

It uses GZCTF's official admin/edit/team APIs and refuses non-local
base URLs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


BASE_URL = os.environ.get("LOCAL_GZCTF_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
ADMIN_USERNAME = os.environ.get("LOCAL_GZCTF_ADMIN_USERNAME", "Admin")
ADMIN_PASSWORD = os.environ.get("GZCTF_ADMIN_PASSWORD", "LocalGZCTFAdmin20260509!")
PLAYER_USERNAME = os.environ.get("GZCTF_USERNAME", "local_player")
PLAYER_PASSWORD = os.environ.get("GZCTF_PASSWORD", "LocalPlayer20260509!")
PLAYER_EMAIL = "local_player@example.invalid"
TEAM_NAME = "local-team"
GAME_TITLE = "local-rehearsal-game"


def assert_local_base_url() -> None:
    parsed = urlparse(BASE_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit(f"refusing non-local GZCTF base URL: {BASE_URL}")


def wait_ready() -> None:
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            resp = requests.get(BASE_URL, timeout=5)
            if resp.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError(f"GZCTF did not become ready at {BASE_URL}")


def req(session: requests.Session, method: str, path: str, **kwargs) -> requests.Response:
    last_exc: requests.RequestException | None = None
    for _ in range(20):
        try:
            resp = session.request(method, BASE_URL + path, timeout=20, **kwargs)
            break
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(1)
    else:
        raise RuntimeError(f"{method} {path} failed after retries: {last_exc}")
    if resp.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} failed HTTP {resp.status_code}: {resp.text[:500]}"
        )
    return resp


def as_json(resp: requests.Response):
    return resp.json() if resp.text else None


def login(username: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    req(s, "POST", "/api/account/login", json={"userName": username, "password": password})
    return s


def find_user(admin: requests.Session, username: str) -> dict | None:
    data = as_json(req(admin, "GET", "/api/admin/users")) or {}
    for user in data.get("data", []):
        if user.get("userName") == username:
            return user
    return None


def ensure_player(admin: requests.Session) -> dict:
    existing = find_user(admin, PLAYER_USERNAME)
    if existing:
        return existing
    req(
        admin,
        "POST",
        "/api/admin/users",
        json=[
            {
                "userName": PLAYER_USERNAME,
                "password": PLAYER_PASSWORD,
                "email": PLAYER_EMAIL,
                "realName": "Local Player",
                "stdNumber": "20260509",
            }
        ],
    )
    user = find_user(admin, PLAYER_USERNAME)
    if not user:
        raise RuntimeError("player creation returned success but user was not found")
    return user


def current_team(player: requests.Session) -> dict | None:
    resp = req(player, "GET", "/api/team")
    payload = as_json(resp)
    if isinstance(payload, list):
        return payload[0] if payload else None
    if isinstance(payload, dict) and payload.get("id"):
        return payload
    return None


def ensure_team() -> dict:
    player = login(PLAYER_USERNAME, PLAYER_PASSWORD)
    team = current_team(player)
    if team:
        return team
    payload = as_json(
        req(player, "POST", "/api/team", json={"name": TEAM_NAME, "bio": "local rehearsal"})
    )
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError(f"unexpected team create response: {payload}")
    return payload


def ensure_game(admin: requests.Session) -> dict:
    games = as_json(req(admin, "GET", "/api/edit/games", params={"count": 50, "skip": 0})) or {}
    matching = []
    for game in games.get("data", []):
        if game.get("title") == GAME_TITLE:
            matching.append(game)
    if matching:
        # Reuse the newest one if a previous failed bootstrap created
        # duplicates before this script became idempotent.
        return sorted(matching, key=lambda g: int(g.get("id", 0)), reverse=True)[0]

    now_ms = int(time.time() * 1000)
    end_ms = now_ms + 7 * 24 * 60 * 60 * 1000
    payload = {
        "title": GAME_TITLE,
        "hidden": False,
        "summary": "Local GZCTF rehearsal for adapter and supervisor.",
        "content": "Local-only rehearsal game.",
        "acceptWithoutReview": True,
        "writeupRequired": False,
        "inviteCode": None,
        "teamMemberCountLimit": 0,
        "containerCountLimit": 0,
        "poster": None,
        "practiceMode": True,
        "start": now_ms - 60_000,
        "end": end_ms,
        "writeupDeadline": end_ms,
        "writeupNote": "",
        "bloodBonus": 0,
    }
    created = as_json(req(admin, "POST", "/api/edit/games", json=payload))
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError(f"unexpected game create response: {created}")
    return created


def ensure_participation(game_id: int, team_id: int) -> None:
    player = login(PLAYER_USERNAME, PLAYER_PASSWORD)
    detail = as_json(req(player, "GET", f"/api/game/{game_id}")) or {}
    if detail.get("status") == "Accepted":
        return
    req(
        player,
        "POST",
        f"/api/game/{game_id}",
        json={"teamId": int(team_id), "divisionId": None, "inviteCode": None},
    )


def list_challenges(admin: requests.Session, game_id: int) -> list[dict]:
    payload = as_json(req(admin, "GET", f"/api/edit/games/{game_id}/challenges"))
    return payload if isinstance(payload, list) else []


def ensure_challenge(admin: requests.Session, game_id: int, title: str, flag: str) -> dict:
    attachment_url = f"http://files:8081/{title}.txt"
    for challenge in list_challenges(admin, game_id):
        if challenge.get("title") == title:
            if not challenge.get("isEnabled"):
                req(
                    admin,
                    "PUT",
                    f"/api/edit/games/{game_id}/challenges/{int(challenge['id'])}",
                    json={"isEnabled": True},
                )
            req(
                admin,
                "POST",
                f"/api/edit/games/{game_id}/challenges/{int(challenge['id'])}/attachment",
                json={"attachmentType": "Remote", "fileHash": None, "remoteUrl": attachment_url},
            )
            return challenge

    payload = {
        "title": title,
        "category": "Misc",
        "type": "StaticAttachment",
        "isEnabled": True,
        "score": 100,
        "minScore": 100,
        "originalScore": 100,
        "deadlineUtc": None,
    }
    created = as_json(req(admin, "POST", f"/api/edit/games/{game_id}/challenges", json=payload))
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError(f"unexpected challenge create response: {created}")
    challenge_id = int(created["id"])
    req(
        admin,
        "PUT",
        f"/api/edit/games/{game_id}/challenges/{challenge_id}",
        json={"isEnabled": True},
    )

    # Use remoteUrl so no separate file upload endpoint is needed. The
    # file is served by GZCTF itself from the bind-mounted files dir.
    req(
        admin,
        "POST",
        f"/api/edit/games/{game_id}/challenges/{challenge_id}/attachment",
        json={"attachmentType": "Remote", "fileHash": None, "remoteUrl": attachment_url},
    )
    req(
        admin,
        "POST",
        f"/api/edit/games/{game_id}/challenges/{challenge_id}/flags",
        json=[{"flag": flag, "attachmentType": "None", "fileHash": None, "remoteUrl": None}],
    )
    detail = as_json(req(admin, "GET", f"/api/edit/games/{game_id}/challenges/{challenge_id}"))
    return detail if isinstance(detail, dict) else created


def ensure_files() -> None:
    files_dir = Path(__file__).resolve().parent / "challenges" / "static-attachment"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "local-accepted.txt").write_text(
        "flag{local_static_accept}\n", encoding="utf-8"
    )
    (files_dir / "local-wrong-freeze.txt").write_text(
        "flag{local_static_wrong}\n", encoding="utf-8"
    )


def main() -> int:
    assert_local_base_url()
    wait_ready()
    ensure_files()
    admin = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    player = ensure_player(admin)
    team = ensure_team()
    game = ensure_game(admin)
    game_id = int(game["id"])
    ensure_participation(game_id, int(team["id"]))
    accepted = ensure_challenge(admin, game_id, "local-accepted", "flag{local_static_accept}")
    wrong = ensure_challenge(admin, game_id, "local-wrong-freeze", "flag{local_static_real}")

    summary = {
        "base_url": BASE_URL,
        "player": {"id": player.get("id"), "userName": player.get("userName")},
        "team": {"id": team.get("id"), "name": team.get("name")},
        "game": {"id": game_id, "title": game.get("title")},
        "challenges": [
            {"id": accepted.get("id"), "title": accepted.get("title")},
            {"id": wrong.get("id"), "title": wrong.get("title")},
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
