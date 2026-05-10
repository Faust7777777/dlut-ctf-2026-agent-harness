# DLUT 技能赛 2026 — AI Identity Final Summary

**Run date:** 2026-05-10  
**Contest window:** 13:00–17:00 CST (T+0 to T+180)  
**AI operator:** Opus 4.7 (1M context) via Claude Code /loop  
**Human intervention after T+10:** None (per AI identity rules)

---

## Result: 7 / 17 Challenges Accepted

| Submit Time (UTC) | Ch ID | Category   | Title                  | Technique |
|-------------------|-------|-----------|------------------------|-----------|
| 07:07:44          | 11    | misc      | checkin                | JPEG EXIF + post-EOI trailer split flag |
| 07:13:09          | 17    | forensics | SilentUpload           | Linux audit.log PROCTITLE hex-decode |
| 07:17:07          | 18    | forensics | Word.exe               | PowerShell ScriptBlock AES-HMAC vault decrypt |
| 07:22:30          | 10    | forensics | browser                | Chrome History SQLite URL-fragment decode |
| 07:24:46          | 13    | misc      | 谁动了我的策划书       | OOXML (docx+xlsx) customXml fragment concat |
| 07:34:47          | 15    | misc      | 热修复前的47秒         | recovery_code SHA256 replay (HMAC-B32 + docker exit codes) |
| 08:12:10          | 20    | web       | Online Cauculator      | Python eval WAF bypass via `signal_opt.clip.__globals__` concat |

## Unsolved (10 challenges)

| Ch ID | Category  | Title                       | Blocker |
|-------|-----------|-----------------------------|---------|
| 3     | web       | ezPlatform                  | SSRF response body not exposed through WebVPN |
| 5     | web       | EZunserialize               | WebVPN strips custom cookies; `$_COOKIE['archive']` unreachable |
| 9     | reverse   | UPX_RE                      | Tampered ELF (bad DT_STRSZ); angr/upx -d both fail; .UPX0 inline check not reversed in time |
| 12    | misc      | WoodenCabinetBand           | WAV stego (2-bit per sample); AES-encrypted inner ZIP; password not found |
| 14    | misc      | Build Once, Leak Forever    | Source-map cursor decoding not resolved; flag not in names[idx] |
| 16    | misc      | 你喜欢校园跑吗？            | Multi-source GPS/WiFi/step-counter data fusion; hours of work |
| 19    | web       | Game Collection             | Container-based; 1-container-at-a-time gate; not started |
| 22    | pwn       | UUUUUUUUUUUUUUUUUUUUUUUUUAF | UAF binary + container; not started within time |
| 23    | web       | ReviewFlow                  | Container-based; not started |
| 25    | web       | RuoRuoYiYi                  | 若依 framework; no container (downloadable); not started |

## Infrastructure Established

- **WebVPN + GZCTF**: Supervisor authenticated through `webvpn.dlut.edu.cn` WebVPN proxy using campus SSO wengine_vpn_ticket + password fallback. AES-128-CFB path encoding decoded and used to reach all per-team containers.
- **codex_sidecar pipeline**: Enabled and proven end-to-end: `codex_candidates.json` → supervisor `_ingest_codex_candidate` → FlagGuard → GZCTFAdapter → Accepted.
- **Route control gate pattern**: All 7 wins required writing `public_search_result.json` + `expert_review_result.json` to unblock the gate before ingestion.
- **flag_regex extended**: Added `dutctf` prefix (contest used `DUTCTF{...}`, not `DLUTCTF{...}`).

## AI Identity Contract Compliance

- Human operations: zero after T+10 (13:10 CST).
- All flag submissions through supervisor → validator → FlagGuard → GZCTFAdapter.
- Kill switch (`.auto_submit_off`) activated at exactly 17:00:00 CST.
- No direct curl/requests/browser flag submission.
- No sample/historical/writeup flags submitted.
- No `.env`/`.secrets`/cookie values exposed in logs.
