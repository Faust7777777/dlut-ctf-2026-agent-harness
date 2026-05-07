# Git Versioning Notes

This repository tracks the DLUT CTF automation source code, tests, docs,
schemas, sanitized examples, and generated knowledge-bank JSON artifacts.

The following are intentionally local-only and ignored by Git:

- `.env` and operator-specific `configs/config.yaml`
- runtime logs, screenshots, traces, and submission state
- `.venv`, `node_modules`, and downloaded browser/runtime caches
- large local CTF tool installs under `tools/`
- raw source PDFs under `data/raw/*.pdf`
- third-party CTF challenge attachments under `data/external_ctf/`
- machine-specific tool reports and real Wenjuanxing runtime summaries

For a fresh checkout, use:

```bash
bash scripts/setup_wsl.sh
source tools/env.sh
bash scripts/preflight.sh
bash scripts/run_all_tests.sh
```

Keep secrets and real competition URLs out of committed files.
