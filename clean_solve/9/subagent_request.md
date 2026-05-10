# subagent_request.md — sandbox cid 9

To: source-grep subagent
Scope: clean_solve/9/

## Task

Extract `$FLAG` from the bundled PHP source.  The literal here uses
SINGLE quotes (unlike cid 8 which used double quotes), so the regex
needs to handle both.

## Steps

```bash
grep -nE '\$FLAG\s*=' extracted/*.php
```

```python
import re, pathlib
src = pathlib.Path('extracted/config.php').read_text()
re.search(r"\$FLAG\s*=\s*['\"]([^'\"]+)['\"]", src).group(1)
```

## Allowed

- Reads/writes only inside `clean_solve/9/`.

## Forbidden

- Anything outside the sandbox.
- Submission.

## What I want back

The captured literal from both methods, persisted to
`evidence/flag_grep.txt`.
