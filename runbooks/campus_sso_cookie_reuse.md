# Campus SSO Cookie Reuse Runbook

## Why this exists

GZCTF instances behind 大连理工 campus SSO won't accept a plain
``POST /api/account/login`` with a username + password — the login
flow goes through DLUT's CAS / SSO portal and ends with a session
cookie set in the browser.  When you've already authenticated in
Windows Chrome, exporting that cookie into a jar that
``GZCTFAdapter`` can read lets the adapter act as the same browser
session, without re-authenticating and without ever knowing your
password.

Codex verified the reverse direction on 5/8: a jar exported from a
Windows Chrome session let WSL ``curl`` reach
``software.dlut.edu.cn`` with HTTP 200.  Same approach is used here.

## When to use which auth mode

| Situation | `auth_mode` | What you need |
|---|---|---|
| Standalone test instance, GZCTF account | `password` | `GZCTF_USERNAME` + `GZCTF_PASSWORD` in `.env` |
| Behind campus SSO, you've signed in via browser | `cookie` | exported cookie jar at `cookie_jar_path` |
| Either may work, prefer cookies | `auto` (default) | both if available |

`auth_mode='cookie'` will refuse to send a password under any
circumstance — safest for SSO-only deployments.

## Dual fallback for flaky campus network

If campus SSO or the route to the contest host is unreliable on contest day, prepare both
paths before the loop starts:

1. Put the live GZCTF credentials into your local, gitignored `.env` so `auth_mode: "password"` can work if cookies fail.
2. Export a fresh browser cookie jar into `.secrets/gzctf_cookies.json` so `auth_mode: "cookie"` can work if password login is blocked by SSO.
3. Keep `gzctf.auth_mode: "auto"` unless the live deployment proves one path is impossible.
4. Never write the actual credential values into tracked docs, prompts, logs, or state files.
5. If one path fails during the contest, switch to the other only through config or cookie re-export, not by editing the prompt.

## How to export cookies from Windows Chrome

The adapter accepts **three formats** (auto-detected by content):

| Format | When to use | Domain isolation |
|---|---|---|
| **Netscape ``cookies.txt``** (recommended) | curl-style export, multi-domain SSO | yes — full domain/path/secure/expires |
| **JSON array** (Cookie-Editor / Chrome) | extension export, multi-domain SSO | yes — full domain/path/secure/expires |
| **Legacy `{cookies: {name:value}}`** (fallback) | single-host pin, manual entry | **no** — same name will overwrite across domains |

> ⚠ For DLUT campus SSO the same cookie name (e.g. ``JSESSIONID``)
> often exists at ``.dlut.edu.cn`` AND under the GZCTF subdomain.
> Use Netscape or JSON array, NOT the legacy format, otherwise the
> two cookies overwrite each other and authentication breaks.

### Path A (recommended): Cookie-Editor → JSON array

1. Install **Cookie-Editor** in Chrome (audit the extension before
   installing — cookie jars are bearer credentials).
2. Sign in to GZCTF in Chrome.
3. Click the extension → switch to the GZCTF host → **Export** as JSON.
4. The exported JSON is already the array shape the adapter accepts:

```json
[
  {
    "name": "JSESSIONID",
    "value": "...",
    "domain": ".dlut.edu.cn",
    "path": "/",
    "secure": true,
    "httpOnly": true,
    "expirationDate": 4102444800
  },
  {
    "name": "JSESSIONID",
    "value": "...",
    "domain": "gzctf.dlut.edu.cn",
    "path": "/",
    "secure": true
  },
  ...
]
```

5. Save the array AS-IS to ``.secrets/gzctf_cookies.json``.  Do NOT
   convert it back to the flat ``{cookies: {name:value}}`` shape;
   doing so loses the domain anchor and the SSO session breaks.

### Path B (also recommended): curl-style Netscape jar

1. Sign in to GZCTF in Chrome.
2. Use a "Get cookies.txt" extension (e.g. *cookies.txt-LOCALLY* —
   audit before installing) or browse the cookie DB directly.
3. Export as ``cookies.txt`` (Netscape format).  Each line is
   tab-separated: ``domain\\tflag\\tpath\\tsecure\\texpires\\tname\\tvalue``.
   Comments start with ``#``.  ``#HttpOnly_<domain>`` is the curl
   convention for HttpOnly cookies and is supported.
4. Save to ``.secrets/gzctf_cookies.txt`` and point
   ``gzctf.cookie_jar_path`` at it.

### Path C: DevTools manual entry (single-host fallback only)

1. Chrome → **F12** → **Application** → **Cookies** → GZCTF host.
2. Copy each cookie's **Name** and **Value**.
3. Build:

```json
{ "cookies": { "GZCTF_token": "...", "another_name": "..." } }
```

4. Save to ``.secrets/gzctf_cookies.json``.

This is the legacy single-host format.  Use it only if the GZCTF
deployment doesn't sit behind a multi-subdomain SSO (no shared
``JSESSIONID`` across hosts).  The first time you hit a
multi-domain redirect this format will break.

### Path D: Chrome cookie DB (advanced)

Chrome stores cookies in an SQLite DB at
``%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies``.
Use a dedicated Python tool such as ``pycookiecheat`` to decrypt and
emit either Netscape or JSON array.  Requires Chrome closed +
Windows DPAPI for decryption.  Document the exact tool you used in
the WriteUp if you go this route.

## Wiring it into the adapter

1. Drop the jar at the path your config points to.  Default is
   ``.secrets/gzctf_cookies.json``.
2. Set ``configs/ai_contest.yaml``:

```yaml
gzctf:
  auth_mode: "cookie"        # or "auto" if you also want password fallback
  cookie_jar_path: ".secrets/gzctf_cookies.json"
```

3. Verify:

```bash
source tools/env.sh
python scripts/ai_contest_supervisor.py --healthcheck-only
```

Expected: ``[PASS] healthcheck`` with ``profile.user`` populated.
If you see ``cookie session not authenticated: HTTP 401`` the cookies
expired (re-export from a fresh browser session) or the cookie name
mismatched the host.

## Security guardrails

- **Cookies are bearer credentials**.  Anyone with the jar can act as
  you on GZCTF until cookies expire.  Treat ``.secrets/`` like
  ``~/.ssh``.
- ``.secrets/`` is in ``.gitignore`` (added via Codex review).  The
  ``*.cookies.json`` glob is also gitignored as a belt-and-suspenders
  measure.
- Do not paste cookie jars into chat / pastebin / screenshots.
- After contest, ``rm -rf .secrets/`` or rotate by signing out of
  GZCTF in Chrome (which invalidates the session token).
- Adapter logs never include cookie values — only ``profile.user``
  on success.

## Failure modes & recovery

| Symptom | Cause | Fix |
|---|---|---|
| ``cookie session not authenticated: HTTP 401`` | jar expired | re-sign-in in Chrome, re-export |
| ``no cookies loaded`` | jar path wrong / file empty | check ``configs/ai_contest.yaml`` ``cookie_jar_path`` |
| ``ScopeError`` | cookies sent to wrong domain | jar must match the GZCTF base host; cookies for unrelated hosts will trigger scope refusal |
| ``healthcheck`` passes but submit returns 401 mid-run | cookie rotation by SSO | resort to ``auth_mode='auto'`` so password kicks in, or re-export jar |

## 5/9 dress-rehearsal checklist

- [ ] Export jar from Windows Chrome after signing into GZCTF
- [ ] ``ls -l .secrets/gzctf_cookies.json`` shows recent mtime
- [ ] ``python scripts/ai_contest_supervisor.py --healthcheck-only`` passes
- [ ] One submit-status round trip works against the test challenge
- [ ] No cookie value appears in ``logs/ai-contest-*.jsonl``
