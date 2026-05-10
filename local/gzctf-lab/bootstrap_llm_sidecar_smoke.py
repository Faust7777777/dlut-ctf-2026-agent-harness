#!/usr/bin/env python3
"""Create/reuse the local LLM sidecar smoke challenge.

Local-only helper for the rehearsal lab. It uses a public CTFlearn
attachment mirrored into the existing local static file service, then
creates a GZCTF StaticAttachment challenge whose correct flag matches
the attachment metadata. The flag is intentionally not printed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import requests

import bootstrap_rehearsal as lab


TITLE = "llm-sidecar-rubber-duck"
CATEGORY = "Misc"
SOURCE_ATTACHMENT = Path(os.environ.get("LLM_SIDECAR_SOURCE", "/tmp/RubberDuck.jpg"))
FILES_DIR = Path(__file__).resolve().parent / "challenges" / "static-attachment"
LOCAL_ATTACHMENT = FILES_DIR / "llm-sidecar-rubber-duck.jpg"
REMOTE_URL = "http://files:8081/llm-sidecar-rubber-duck.jpg"
CORRECT_FLAG = "CTFlearn{ILoveJakarta}"


def ensure_public_attachment() -> None:
    if not SOURCE_ATTACHMENT.exists():
        raise RuntimeError(f"source attachment not found: {SOURCE_ATTACHMENT}")
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_ATTACHMENT, LOCAL_ATTACHMENT)


def ensure_challenge(admin: requests.Session, game_id: int) -> dict:
    for challenge in lab.list_challenges(admin, game_id):
        if challenge.get("title") == TITLE:
            cid = int(challenge["id"])
            lab.req(
                admin,
                "POST",
                f"/api/edit/games/{game_id}/challenges/{cid}/attachment",
                json={"attachmentType": "Remote", "fileHash": None, "remoteUrl": REMOTE_URL},
            )
            lab.req(
                admin,
                "POST",
                f"/api/edit/games/{game_id}/challenges/{cid}/flags",
                json=[{"flag": CORRECT_FLAG, "attachmentType": "None", "fileHash": None, "remoteUrl": None}],
            )
            if not challenge.get("isEnabled"):
                lab.req(
                    admin,
                    "PUT",
                    f"/api/edit/games/{game_id}/challenges/{cid}",
                    json={"isEnabled": True},
                )
            return challenge

    created = lab.as_json(
        lab.req(
            admin,
            "POST",
            f"/api/edit/games/{game_id}/challenges",
            json={
                "title": TITLE,
                "category": CATEGORY,
                "type": "StaticAttachment",
                "isEnabled": False,
                "score": 100,
                "minScore": 100,
                "originalScore": 100,
                "deadlineUtc": None,
            },
        )
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError(f"unexpected challenge create response: {created}")
    cid = int(created["id"])
    lab.req(
        admin,
        "POST",
        f"/api/edit/games/{game_id}/challenges/{cid}/attachment",
        json={"attachmentType": "Remote", "fileHash": None, "remoteUrl": REMOTE_URL},
    )
    lab.req(
        admin,
        "POST",
        f"/api/edit/games/{game_id}/challenges/{cid}/flags",
        json=[{"flag": CORRECT_FLAG, "attachmentType": "None", "fileHash": None, "remoteUrl": None}],
    )
    lab.req(
        admin,
        "PUT",
        f"/api/edit/games/{game_id}/challenges/{cid}",
        json={"isEnabled": True},
    )
    detail = lab.as_json(lab.req(admin, "GET", f"/api/edit/games/{game_id}/challenges/{cid}"))
    return detail if isinstance(detail, dict) else created


def main() -> int:
    lab.assert_local_base_url()
    lab.wait_ready()
    ensure_public_attachment()
    admin = lab.login(lab.ADMIN_USERNAME, lab.ADMIN_PASSWORD)
    team = lab.ensure_team()
    game = lab.ensure_game(admin)
    game_id = int(game["id"])
    lab.ensure_participation(game_id, int(team["id"]))
    challenge = ensure_challenge(admin, game_id)
    print(json.dumps(
        {
            "base_url": lab.BASE_URL,
            "game_id": game_id,
            "challenge": {
                "id": challenge.get("id"),
                "title": challenge.get("title"),
                "category": challenge.get("category"),
            },
            "remote_url": REMOTE_URL,
            "local_attachment": str(LOCAL_ATTACHMENT),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
