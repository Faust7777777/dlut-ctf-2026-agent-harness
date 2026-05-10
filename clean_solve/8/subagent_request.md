# subagent_request.md — sandbox cid 8

To: source-grep subagent
Scope: clean_solve/8/

## Task

Extract the value of the PHP variable `$FLAG` from the bundled
source archive.  Do not modify the source files.

## Steps

```bash
grep -nE '\$FLAG\s*=' extracted/config.php extracted/index.php
```

Then independently parse the same file in Python and confirm both
methods agree on the same string.

## Allowed

- Read inside `clean_solve/8/`.
- Write evidence to `clean_solve/8/evidence/`.

## Forbidden

- Anything outside the sandbox.
- Submission.

## What I want back

`{file}:{line}: $FLAG = "<literal>";` from grep, plus the same
literal as captured by a Python regex.
