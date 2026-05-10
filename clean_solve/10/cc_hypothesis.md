# cc_hypothesis.md — sandbox cid 10

## Sandbox state

`clean_solve/10/` was opened with one file at the top level: a single
zip archive shipped by the lab.  Unzipping into `extracted/` reveals
two siblings:

- `script.py` (263 bytes)
- `enc` (57 bytes)

I worked only inside this directory.  No file outside
`clean_solve/10/` was opened.

## What `script.py` does

```python
encoded_flag = open("enc").read()
flag = ""
for i in range(0, len(encoded_flag)):
    character1 = chr((ord(encoded_flag[i]) >> 8))
    character2 = chr(encoded_flag[i].encode('utf-16be')[-1])
    flag += character1
    flag += character2

print("Flag: " + flag)
```

The script unambiguously **decodes** rather than encodes:

- `encoded_flag[i]` is one Unicode character; `ord(...)` is its
  codepoint, a single integer.
- `>> 8` extracts the high byte; `chr(...)` lifts that high byte
  back into an ASCII character (assuming the codepoint fits in 16
  bits).
- `.encode('utf-16be')[-1]` re-encodes the codepoint to big-endian
  UTF-16 (always 2 bytes for codepoints under U+10000) and grabs
  the last byte, i.e. the low byte of the codepoint.
- For each codepoint of `enc`, two ASCII characters are emitted —
  the high and low byte.

## Hypothesis

The encoder paired plaintext bytes two at a time and packed each
pair into one Unicode codepoint `((hi << 8) | lo)`, written out as
`enc`.  Running the script unpacks them back into the original
plaintext.  No further analysis needed.

## Plan

1. Delegate to a subagent: run `script.py` in the sandbox against
   `enc`, capture stdout into `evidence/decode_run.txt`.
2. Sanity-check the printed line against a generic flag regex
   `(?i)[a-z][a-z0-9_-]*\{[^{}]{4,400}\}`.

## Confidence

High.  The decoder is bundled with the data; the operation is
deterministic.
