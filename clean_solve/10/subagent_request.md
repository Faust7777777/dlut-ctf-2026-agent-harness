# subagent_request.md — sandbox cid 10

To: decode-runner subagent
Scope: clean_solve/10/

## Task

Run the bundled Python decoder against the bundled encoded payload
and capture stdout verbatim.

## Inputs

- `extracted/script.py`
- `extracted/enc`

## Steps

```bash
cd extracted
python3 script.py
```

## Allowed

- Read and write inside `clean_solve/10/` only.
- Plain CPython 3.

## Forbidden

- Touching anything outside the sandbox.
- Network I/O.
- Submitting a flag.

## What I want back

The line `script.py` prints, persisted to
`evidence/decode_run.txt` so the orchestrator can verify it later.
