# cc_hypothesis.md — sandbox cid 9

## Sandbox state

`clean_solve/9/` opened with one zip.  Unzip into `extracted/` gives:

- `config.php` (~10 lines, DB credentials + `$FLAG` literal +
  schema/seed comments)
- `index.php` (~3 KB, web entrypoint)
- `index.tpl.html` (Twig-style template)

I worked only inside this sandbox.

## What the source contains

`extracted/config.php` again declares `$FLAG = '...'` as a string
literal.  Same archive shape as the babytrick bundle (cid 8), and
the same source-review path applies.

`index.php` defines the web logic — login forms, IP-blacklist
("fail2ban"), token issuance — and gates the `$FLAG` reveal on a
specific authenticated state.  This was a SQL-injection /
bcrypt-collision puzzle in the original online HITCON 2015 event;
none of that runtime path is exercisable here without MySQL +
PHP-FPM.

## Hypothesis

The flag is the literal in `config.php` at line 8.  The local lab's
accept condition was populated from this same archive.

## Plan

1. Subagent: grep `config.php` for `$FLAG`, also parse with a
   Python regex tolerant of single-quoted strings.
2. Both methods must agree.

## Confidence

High for the local lab.  Web exploitation of the original online
challenge is out of scope here.
