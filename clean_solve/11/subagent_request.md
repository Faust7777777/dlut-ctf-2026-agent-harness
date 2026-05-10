# subagent_request.md — sandbox cid 11

To: keygen-derivation subagent
Scope: clean_solve/11/

## Task

Statically derive the unlock key from the bundled Python source.
Do NOT execute the interactive program — it requires stdin and may
print a misleading "Key is NOT VALID" if the harness pipes something
short.  Use only Python regex + SHA-256 reasoning over the source
text.

## Inputs

- `extracted/keygenme-trial.py`

## Steps

1. Parse the source for the assignments:
   - `username_trial = "..."` → bytes used in `hashlib.sha256`.
   - `key_part_static1_trial = "..."` → flag prefix.
   - `key_part_static2_trial = "..."` → flag suffix.
   - `hashlib.sha256(username_trial).hexdigest()[N]` literals in
     `check_key` → indices, in encounter order.
2. Compute `h = sha256(username).hexdigest()`.
3. Build the dynamic middle as the indexed concatenation.
4. Concatenate `prefix + dynamic + suffix`.
5. Persist the derivation to `evidence/keygen_derivation.txt`.

## Allowed

- Pure Python over the bundled source.

## Forbidden

- Reading or writing outside `clean_solve/11/`.
- Running the interactive REPL of the bundled program.
- Submitting the candidate anywhere.

## What I want back

The derived flag string and the index-order list, so the
orchestrator can validate the derivation by re-running the same
arithmetic.
