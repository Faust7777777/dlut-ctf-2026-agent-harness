#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ctf_agents.submit.gzctf_adapter import GZCTFAdapter


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("GZCTF_LOCAL_BASE_URL", "http://127.0.0.1:8080")
ADMIN_USER = "Admin"
ADMIN_PASSWORD = os.environ.get("GZCTF_ADMIN_PASSWORD", "LocalGZCTFAdmin2026!")
PLAYER_USER = "player"
PLAYER_PASSWORD = os.environ.get("GZCTF_PLAYER_PASSWORD", "LocalGZCTFPlayer2026!")
PLAYER_EMAIL = os.environ.get("GZCTF_PLAYER_EMAIL", "player@example.invalid")
TEAM_NAME = "local-team"
GAME_ID = 1

STATIC_CHALLENGES = [
    {
        "title": "misc-static",
        "category": "Misc",
        "type": "StaticAttachment",
        "content": "Local static attachment",
        "asset": "misc.txt",
        "flags": ["flag{local_misc_static_ok}"],
        "enabled": True,
        "download_only": False,
    },
    {
        "title": "forensics-static",
        "category": "Forensics",
        "type": "StaticAttachment",
        "content": "Local forensics attachment",
        "asset": "forensics.txt",
        "flags": ["flag{local_forensics_static_ok}"],
        "enabled": True,
        "download_only": False,
    },
    {
        "title": "crypto-static",
        "category": "Crypto",
        "type": "StaticAttachment",
        "content": "Local crypto attachment",
        "asset": "crypto.txt",
        "flags": ["flag{local_crypto_real_ok}"],
        "enabled": True,
        "download_only": False,
    },
    {
        "title": "reverse-download",
        "category": "Reverse",
        "type": "StaticAttachment",
        "content": "Download-only reverse attachment",
        "asset": "reverse.bin",
        "flags": ["flag{local_reverse_download_ok}"],
        "enabled": True,
        "download_only": True,
    },
    {
        "title": "web-duplicate-hold",
        "category": "Web",
        "type": "StaticAttachment",
        "content": "Duplicate-skip probe for the local supervisor",
        "asset": "web_duplicate.txt",
        "flags": ["flag{local_web_duplicate_hold}"],
        "enabled": True,
        "download_only": False,
    },
]

CONTAINER_CHALLENGES = [
    {
        "title": "pwn-dynamic",
        "category": "Pwn",
        "type": "DynamicContainer",
        "content": "Local dynamic pwn container",
        "image": "local-gzctf-pwn:latest",
        "exposePort": 31337,
        "flagTemplate": "flag{[TEAM_HASH]-pwn}",
        "enabled": True,
    },
    {
        "title": "web-dynamic",
        "category": "Web",
        "type": "DynamicContainer",
        "content": "Local dynamic web container",
        "image": "local-gzctf-web:latest",
        "exposePort": 80,
        "flagTemplate": "flag{[TEAM_HASH]-web}",
        "enabled": True,
    },
]


def info(message: str) -> None:
    print(message)


def run(cmd: list[str], *, check: bool = True, capture: bool = False, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {"text": True}
    if capture:
        kwargs["capture_output"] = True
    if input_text is not None:
        kwargs["input"] = input_text
    proc = subprocess.run(cmd, check=check, **kwargs)
    return proc


def psql(sql: str, *, capture: bool = False) -> str:
    cmd = [
        "docker",
        "exec",
        "local-gzctf-db",
        "psql",
        "-U",
        "postgres",
        "-d",
        "gzctf",
        "-v",
        "ON_ERROR_STOP=1",
        "-t",
        "-A",
        "-c",
        sql,
    ]
    proc = run(cmd, capture=True)
    if capture:
        return (proc.stdout or "").strip()
    return ""


def wait_for_api(timeout_s: float = 120.0) -> None:
    deadline = time.time() + timeout_s
    last_error = ""
    ready_hits = 0
    while time.time() < deadline:
        try:
            resp = requests.get(f"{BASE_URL}/api/account/profile", timeout=5.0)
            if resp.status_code in {200, 401}:
                ready_hits += 1
                if ready_hits >= 2:
                    return
                time.sleep(2.0)
                continue
            last_error = f"HTTP {resp.status_code}"
            ready_hits = 0
        except requests.RequestException as exc:
            last_error = str(exc)
            ready_hits = 0
        time.sleep(2.0)
    raise RuntimeError(f"local GZCTF did not become ready: {last_error}")


def login(session: requests.Session, username: str, password: str) -> None:
    resp = session.post(
        f"{BASE_URL}/api/account/login",
        json={"userName": username, "password": password},
        timeout=10.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"login failed for {username}: HTTP {resp.status_code}: {resp.text[:200]}")


def maybe_json(resp: requests.Response) -> Any:
    if not resp.text:
        return {}
    try:
        return resp.json()
    except ValueError:
        return resp.text


def request_with_retry(
    session: requests.Session,
    method: str,
    path: str,
    *,
    attempts: int = 10,
    delay_s: float = 2.0,
    timeout_s: float = 10.0,
    **kwargs: Any,
) -> requests.Response:
    url = f"{BASE_URL}{path}"
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            resp = session.request(method, url, timeout=timeout_s, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(delay_s)
            continue
        if resp.status_code >= 500:
            last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            time.sleep(delay_s)
            continue
        return resp
    raise RuntimeError(f"{method} {path} failed after retries: {last_error}")


def ensure_game_window() -> None:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=1)).isoformat()
    end = (now + timedelta(hours=8)).isoformat()
    writeup = (now + timedelta(days=1)).isoformat()
    sql = f"""
        UPDATE "Games"
        SET "StartTimeUtc" = '{start}',
            "EndTimeUtc" = '{end}',
            "WriteupDeadline" = '{writeup}',
            "AcceptWithoutReview" = true,
            "PracticeMode" = true,
            "Hidden" = false
        WHERE "Id" = {GAME_ID};
    """
    psql(sql)


def ensure_player_and_team() -> tuple[requests.Session, requests.Session, int, int]:
    admin = requests.Session()
    player = requests.Session()
    login(admin, ADMIN_USER, ADMIN_PASSWORD)

    # Registering the player is idempotent enough for the local lab;
    # existing accounts may return a 4xx and can be ignored.
    player.post(
        f"{BASE_URL}/api/account/register",
        json={"userName": PLAYER_USER, "password": PLAYER_PASSWORD, "email": PLAYER_EMAIL},
        timeout=10.0,
    )
    login(player, PLAYER_USER, PLAYER_PASSWORD)

    team_id = find_or_create_team(player)
    participation_id = ensure_participation(player, team_id)
    if participation_id:
        psql(f'UPDATE "Participations" SET "Status" = 1 WHERE "Id" = {participation_id};')
    return admin, player, team_id, participation_id


def find_or_create_team(player: requests.Session) -> int:
    resp = player.get(f"{BASE_URL}/api/team", timeout=10.0)
    resp.raise_for_status()
    payload = maybe_json(resp)
    if isinstance(payload, list):
        for team in payload:
            if team.get("name") == TEAM_NAME:
                return int(team["id"])
        current = payload[0] if payload else None
        if current and current.get("name") == TEAM_NAME:
            return int(current["id"])
    elif isinstance(payload, dict) and payload.get("name") == TEAM_NAME:
        return int(payload["id"])

    create = player.post(f"{BASE_URL}/api/team", json={"name": TEAM_NAME, "bio": ""}, timeout=10.0)
    if create.status_code >= 400:
        raise RuntimeError(f"team create failed: HTTP {create.status_code}: {create.text[:200]}")
    created = maybe_json(create)
    if isinstance(created, dict) and "id" in created:
        return int(created["id"])
    # fallback to re-query
    return find_or_create_team(player)


def ensure_participation(player: requests.Session, team_id: int) -> int:
    existing = psql(
        f'SELECT "Id" FROM "Participations" WHERE "GameId" = {GAME_ID} AND "TeamId" = {team_id} ORDER BY "Id" LIMIT 1;',
        capture=True,
    )
    if existing:
        return int(existing.splitlines()[0])

    join = player.post(f"{BASE_URL}/api/game/{GAME_ID}", json={"teamId": team_id}, timeout=10.0)
    if join.status_code >= 400:
        raise RuntimeError(f"join game failed: HTTP {join.status_code}: {join.text[:200]}")

    existing = psql(
        f'SELECT "Id" FROM "Participations" WHERE "GameId" = {GAME_ID} AND "TeamId" = {team_id} ORDER BY "Id" LIMIT 1;',
        capture=True,
    )
    if not existing:
        raise RuntimeError("participation row not found after join")
    return int(existing.splitlines()[0])


def upload_asset(admin: requests.Session, filename: str) -> str:
    path = ROOT / "assets" / filename
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("rb") as fh:
        resp = admin.post(
            f"{BASE_URL}/api/assets",
            files={"files": (path.name, fh, "application/octet-stream")},
            timeout=30.0,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"asset upload failed for {path.name}: HTTP {resp.status_code}: {resp.text[:200]}")
    payload = maybe_json(resp)
    if isinstance(payload, list) and payload:
        return str(payload[0]["hash"])
    if isinstance(payload, dict) and "hash" in payload:
        return str(payload["hash"])
    raise RuntimeError(f"unexpected asset upload response: {payload!r}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            if chunk:
                h.update(chunk)
    return h.hexdigest()


def list_edit_challenges(admin: requests.Session) -> list[dict[str, Any]]:
    resp = request_with_retry(admin, "GET", f"/api/edit/games/{GAME_ID}/challenges")
    if resp.status_code >= 400:
        raise RuntimeError(f"list challenges failed: HTTP {resp.status_code}: {resp.text[:200]}")
    payload = maybe_json(resp)
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected challenge list response: {payload!r}")
    return payload


def get_edit_challenge_detail(admin: requests.Session, challenge_id: int) -> dict[str, Any]:
    resp = request_with_retry(admin, "GET", f"/api/edit/games/{GAME_ID}/challenges/{challenge_id}")
    if resp.status_code >= 400:
        raise RuntimeError(f"challenge detail failed: HTTP {resp.status_code}: {resp.text[:200]}")
    payload = maybe_json(resp)
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected challenge detail response: {payload!r}")
    return payload


def find_challenge_id(admin: requests.Session, title: str) -> int | None:
    for item in list_edit_challenges(admin):
        if item.get("title") == title:
            return int(item["id"])
    return None


def create_challenge(admin: requests.Session, title: str, category: str, type_: str) -> int:
    resp = request_with_retry(
        admin,
        "POST",
        f"/api/edit/games/{GAME_ID}/challenges",
        json={"title": title, "category": category, "type": type_},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"create challenge {title} failed: HTTP {resp.status_code}: {resp.text[:200]}")
    payload = maybe_json(resp)
    if isinstance(payload, dict) and "id" in payload:
        return int(payload["id"])
    if isinstance(payload, int):
        return payload
    raise RuntimeError(f"unexpected create response for {title}: {payload!r}")


def update_challenge(admin: requests.Session, challenge_id: int, payload: dict[str, Any]) -> None:
    resp = request_with_retry(
        admin,
        "PUT",
        f"/api/edit/games/{GAME_ID}/challenges/{challenge_id}",
        json=payload,
        timeout_s=20.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"update challenge {challenge_id} failed: HTTP {resp.status_code}: {resp.text[:300]}")


def attach_static_file(admin: requests.Session, challenge_id: int, file_hash: str) -> None:
    resp = request_with_retry(
        admin,
        "POST",
        f"/api/edit/games/{GAME_ID}/challenges/{challenge_id}/attachment",
        json={"attachmentType": "Local", "fileHash": file_hash},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"attach challenge {challenge_id} failed: HTTP {resp.status_code}: {resp.text[:200]}")


def set_static_flags(admin: requests.Session, challenge_id: int, flags: list[str]) -> None:
    resp = request_with_retry(
        admin,
        "POST",
        f"/api/edit/games/{GAME_ID}/challenges/{challenge_id}/flags",
        json=[{"flag": flag} for flag in flags],
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"flags for challenge {challenge_id} failed: HTTP {resp.status_code}: {resp.text[:200]}")


def ensure_attachment_matches(admin: requests.Session, challenge_id: int, asset_name: str) -> None:
    path = ROOT / "assets" / asset_name
    expected_hash = sha256_file(path)
    expected_url = f"/assets/{expected_hash}/{path.name}"
    detail = get_edit_challenge_detail(admin, challenge_id)
    attachment = detail.get("attachment") or {}
    if attachment.get("url") == expected_url:
        return
    file_hash = upload_asset(admin, asset_name)
    attach_static_file(admin, challenge_id, file_hash)


def ensure_static_attachment(admin: requests.Session, spec: dict[str, Any]) -> int:
    challenge_id = find_challenge_id(admin, spec["title"])
    if challenge_id is None:
        challenge_id = create_challenge(admin, spec["title"], spec["category"], spec["type"])
    update_challenge(
        admin,
        challenge_id,
        {
            "title": spec["title"],
            "content": spec["content"],
            "category": spec["category"],
            "hints": [],
            "isEnabled": False,
            "originalScore": 1000,
            "minScoreRate": 0.25,
            "difficulty": 5,
            "enableTrafficCapture": False,
        },
    )
    ensure_attachment_matches(admin, challenge_id, spec["asset"])
    detail = get_edit_challenge_detail(admin, challenge_id)
    existing_flags = {row.get("flag") for row in detail.get("flags", []) if isinstance(row, dict)}
    missing_flags = [flag for flag in spec["flags"] if flag not in existing_flags]
    if missing_flags:
        set_static_flags(admin, challenge_id, missing_flags)
    update_challenge(
        admin,
        challenge_id,
        {
            "title": spec["title"],
            "content": spec["content"],
            "category": spec["category"],
            "hints": [],
            "isEnabled": bool(spec["enabled"]),
            "originalScore": 1000,
            "minScoreRate": 0.25,
            "difficulty": 5,
            "enableTrafficCapture": False,
        },
    )
    return challenge_id


def ensure_dynamic_container(admin: requests.Session, spec: dict[str, Any]) -> int:
    challenge_id = find_challenge_id(admin, spec["title"])
    if challenge_id is None:
        challenge_id = create_challenge(admin, spec["title"], spec["category"], spec["type"])
    update_challenge(
        admin,
        challenge_id,
        {
            "title": spec["title"],
            "content": spec["content"],
            "category": spec["category"],
            "hints": [],
            "isEnabled": bool(spec["enabled"]),
            "flagTemplate": spec["flagTemplate"],
            "containerImage": spec["image"],
            "exposePort": spec["exposePort"],
            "cpuCount": 1,
            "memoryLimit": 64,
            "storageLimit": 256,
            "networkMode": "Open",
            "enableTrafficCapture": False,
            "disableBloodBonus": False,
            "originalScore": 1000,
            "minScoreRate": 0.25,
            "difficulty": 5,
        },
    )
    return challenge_id


def ensure_challenges(admin: requests.Session) -> dict[str, int]:
    challenge_ids: dict[str, int] = {}
    for spec in STATIC_CHALLENGES:
        challenge_ids[spec["title"]] = ensure_static_attachment(admin, spec)
    for spec in CONTAINER_CHALLENGES:
        challenge_ids[spec["title"]] = ensure_dynamic_container(admin, spec)
    return challenge_ids


def build_images() -> None:
    for tag, subdir in (
        ("local-gzctf-web:latest", ROOT / "challenges" / "web"),
        ("local-gzctf-pwn:latest", ROOT / "challenges" / "pwn"),
    ):
        if not subdir.exists():
            raise FileNotFoundError(subdir)
        run(["docker", "build", "-t", tag, str(subdir)], check=True)


def cleanup_containers(admin: requests.Session) -> list[str]:
    resp = request_with_retry(admin, "GET", "/api/admin/instances")
    if resp.status_code >= 400:
        raise RuntimeError(f"list instances failed: HTTP {resp.status_code}: {resp.text[:200]}")
    payload = maybe_json(resp)
    ids: list[str] = []
    if isinstance(payload, dict):
        rows = payload.get("data", [])
    else:
        rows = payload
    for row in rows or []:
        container_id = row.get("containerId") or row.get("id")
        if not container_id:
            continue
        ids.append(str(container_id))
        admin.delete(f"{BASE_URL}/api/admin/instances/{container_id}", timeout=10.0)
    return ids


def get_scalar(sql: str) -> str:
    out = psql(sql, capture=True)
    if not out:
        raise RuntimeError(f"empty SQL result for: {sql}")
    return out.splitlines()[0].strip()


def make_container_extendable(challenge_id: int) -> str:
    container_id = get_scalar(
        f'SELECT "ContainerId" FROM "GameInstances" WHERE "ChallengeId" = {challenge_id} ORDER BY "ParticipationId" LIMIT 1;'
    )
    psql(
        f'UPDATE "Containers" SET "ExpectStopAt" = now() + interval \'10 minutes\' WHERE "Id" = \'{container_id}\';'
    )
    psql(
        f'UPDATE "GameInstances" SET "LastContainerOperation" = now() - interval \'1 hour\' WHERE "ChallengeId" = {challenge_id};'
    )
    return container_id


def cool_down_container_ops(challenge_id: int) -> None:
    psql(
        f'UPDATE "GameInstances" SET "LastContainerOperation" = now() - interval \'1 hour\' WHERE "ChallengeId" = {challenge_id};'
    )


def delete_container_if_exists(adapter: GZCTFAdapter, challenge_id: int) -> dict[str, Any]:
    try:
        return adapter.delete_container(GAME_ID, challenge_id)
    except RuntimeError as exc:
        text = str(exc)
        if "hasn't been created" in text or "not been created" in text:
            return {"status_code": 400, "ignored": "not_created"}
        raise


def seed() -> dict[str, Any]:
    wait_for_api()
    ensure_game_window()
    build_images()
    wait_for_api()
    admin, player, team_id, participation_id = ensure_player_and_team()
    if participation_id:
        psql(f'UPDATE "Participations" SET "Status" = 1 WHERE "Id" = {participation_id};')
    challenge_ids = ensure_challenges(admin)
    return {
        "team_id": team_id,
        "participation_id": participation_id,
        "challenge_ids": challenge_ids,
    }


def verify() -> dict[str, Any]:
    wait_for_api()
    adapter = GZCTFAdapter(
        base_url=BASE_URL,
        username=PLAYER_USER,
        password=PLAYER_PASSWORD,
        scope_cfg={"allowed_domains": ["127.0.0.1", "localhost"]},
        submit_payload_mode="plaintext",
        auth_mode="password",
        default_game_id=GAME_ID,
    )
    profile = adapter.login()
    team = adapter.current_team()
    game = adapter.game(GAME_ID)
    details = adapter.game_details(GAME_ID)

    # use admin login for details that need the full edit listing
    admin = requests.Session()
    login(admin, ADMIN_USER, ADMIN_PASSWORD)
    challenge_ids = {
        item["title"]: int(item["id"])
        for item in list_edit_challenges(admin)
    }

    attachments: dict[str, str] = {}
    for title in ("misc-static", "forensics-static", "crypto-static", "reverse-download"):
        challenge_id = challenge_ids[title]
        challenge = adapter.challenge_detail(GAME_ID, challenge_id)
        context = challenge.get("context") or {}
        attachment = challenge.get("attachment") or {}
        url = attachment.get("url") or context.get("url")
        if title != "reverse-download":
            if not url:
                raise RuntimeError(f"missing attachment for {title}")
            downloaded = adapter.download_attachment(url, ROOT / "artifacts" / "local-gzctf" / "downloads")
            attachments[title] = str(downloaded)
        else:
            if not url:
                raise RuntimeError("reverse download challenge missing attachment")
            downloaded = adapter.download_attachment(url, ROOT / "artifacts" / "local-gzctf" / "downloads")
            attachments[title] = str(downloaded)

    accepted = adapter.submit_flag_for_game(
        GAME_ID,
        challenge_ids["misc-static"],
        "flag{local_misc_static_ok}",
        poll_timeout_s=30.0,
        poll_interval_s=1.0,
    )
    if accepted.status != "Accepted":
        raise RuntimeError(f"expected Accepted, got {accepted.status}")
    poll = adapter.poll_submission_status(GAME_ID, challenge_ids["misc-static"], accepted.submit_id or -1, timeout_s=10.0, interval_s=0.5)
    wrong = adapter.submit_flag_for_game(
        GAME_ID,
        challenge_ids["crypto-static"],
        "flag{local_crypto_wrong_candidate}",
        poll_timeout_s=30.0,
        poll_interval_s=1.0,
    )
    if wrong.status != "WrongAnswer":
        raise RuntimeError(f"expected WrongAnswer, got {wrong.status}")

    delete_container_if_exists(adapter, challenge_ids["pwn-dynamic"])
    delete_container_if_exists(adapter, challenge_ids["web-dynamic"])
    cool_down_container_ops(challenge_ids["pwn-dynamic"])
    cool_down_container_ops(challenge_ids["web-dynamic"])
    pwn_container = adapter.create_container(GAME_ID, challenge_ids["pwn-dynamic"])
    web_container = adapter.create_container(GAME_ID, challenge_ids["web-dynamic"])
    pwn_container_id = make_container_extendable(challenge_ids["pwn-dynamic"])
    web_container_id = make_container_extendable(challenge_ids["web-dynamic"])
    pwn_extend = adapter.extend_container(GAME_ID, challenge_ids["pwn-dynamic"])
    web_extend = adapter.extend_container(GAME_ID, challenge_ids["web-dynamic"])
    pwn_delete = adapter.delete_container(GAME_ID, challenge_ids["pwn-dynamic"])
    web_delete = adapter.delete_container(GAME_ID, challenge_ids["web-dynamic"])

    return {
        "profile": profile,
        "team": team,
        "game": game,
        "details_challenges": len(details.get("challenges", [])) if isinstance(details, dict) else None,
        "attachments": attachments,
        "accepted": {
            "status": accepted.status,
            "submit_id": accepted.submit_id,
            "correct": accepted.correct,
            "poll_status": poll.status,
        },
        "wrong": {
            "status": wrong.status,
            "submit_id": wrong.submit_id,
            "correct": wrong.correct,
        },
        "containers": {
            "pwn_container_id": pwn_container_id,
            "web_container_id": web_container_id,
            "pwn_create": pwn_container,
            "web_create": web_container,
            "pwn_extend": pwn_extend,
            "web_extend": web_extend,
            "pwn_delete": pwn_delete,
            "web_delete": web_delete,
        },
    }


def reset() -> dict[str, Any]:
    destroyed: list[str] = []
    try:
        wait_for_api(30.0)
        admin = requests.Session()
        login(admin, ADMIN_USER, ADMIN_PASSWORD)
        destroyed = cleanup_containers(admin)
    except Exception:
        destroyed = []
    for path in (ROOT / "state" / "local-gzctf", ROOT / "logs" / "local-gzctf", ROOT / "artifacts" / "local-gzctf"):
        if path.exists():
            shutil.rmtree(path)
    return {"destroyed_containers": destroyed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "verify", "reset", "wait"], help="lab subcommand")
    args = parser.parse_args()
    if args.command == "wait":
        wait_for_api()
        return 0
    if args.command == "seed":
        print(json.dumps(seed(), indent=2, sort_keys=True))
        return 0
    if args.command == "verify":
        print(json.dumps(verify(), indent=2, sort_keys=True))
        return 0
    if args.command == "reset":
        print(json.dumps(reset(), indent=2, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
