#!/usr/bin/env node
/**
 * Productionised Wenjuanxing exam assistant.
 *
 * Built on top of scripts/wjx_exam_mvp.js (MVP behaviour preserved):
 *   - access password via --password-env
 *   - personal-info filling via --identity '{"姓名":"…","工号":"…"}'
 *   - jqradio / jqcheck / .label clicking (not the hidden inputs)
 *   - default --no-submit; explicit --submit required
 *   - JSONL action log
 *
 * What the assist version adds, per docs/opus_next_handoff.md §"P0 -
 * 工程化问卷星 assist":
 *
 *   1. Live HTTP lookup (--lookup-url, default http://127.0.0.1:8765/lookup_v2)
 *      replaces the static --answers file as the primary answer source.
 *      The static file is kept as an offline fallback.
 *   2. Auto-select gating: only click when stem_score >= threshold AND
 *      no manual_review_required flags AND multi-choice mappings are
 *      complete.  Otherwise highlight the suggested option(s) for human
 *      review and continue (no click).
 *   3. SMS captcha detection: if a phone-number / verification-code page
 *      is detected, pause and poll until the question blocks reappear,
 *      then continue automatically.
 *   4. "This password has been used." detection: exit cleanly with a
 *      named status so a watching operator can rotate or unfreeze.
 *   5. Sensitive-field redaction: passwords, SMS codes, and phone
 *      numbers never enter the JSONL log — only their *presence* does.
 *
 * Default behaviour when no flag is given:
 *   --auto-select       on
 *   --auto-submit       off    (MVP also defaulted off)
 *   --score-threshold   92     (matches LookupEngine's auto threshold)
 */

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");
const crypto = require("crypto");
const readline = require("readline");
const { chromium } = require("playwright");

const PROJECT_ROOT = path.resolve(__dirname, "..");

const DEFAULT_LOOKUP_URL = "http://127.0.0.1:8765/lookup_v2";
const DEFAULT_SCORE_THRESHOLD = 92;
const SMS_KEYWORDS = [
  "短信验证",
  "验证码",
  "获取验证码",
  "手机号",
  "phone number",
  "verification code",
];
const PASSWORD_USED_TOKENS = [
  "this password has been used",
  "密码已被使用",
  "password has been used",
];

// Judge-question label synonyms, must mirror lookup_engine.JUDGE_LABELS_*.
// The bank emits "正确"/"错误" (engine canonical), but real Wenjuanxing
// pages commonly show "对"/"错". Map by polarity so any pair works.
const JUDGE_TRUE_LABELS = ["正确", "对", "是", "T", "True", "true", "TRUE", "✓", "√"];
const JUDGE_FALSE_LABELS = ["错误", "错", "否", "F", "False", "false", "FALSE", "✗", "×"];

// Notes that lookup_engine emits which mean "do NOT auto-click even if
// stem_score is high and an answer letter was returned".  Highlight is
// still useful, so the plan keeps indexes for the highlighter.
const RISKY_NOTES_EXACT = new Set([
  "negation_mismatch",
  "close_second_candidate",
  "single_option_close_second",
  "single_option_low_score",
  "multi_match_failed",
]);
const RISKY_NOTES_PREFIX = [
  "manual_review_required",
  "multi_partial_match",
  "judge_unknown_token",
];

// `--static-fallback-on-risk` recovery rules.  When the user asserts
// the on-page exam was imported from our own bank (so per-question
// answers in --answers can be trusted), a non-auto lookup plan whose
// reason matches one of these can be recovered via the static answers
// file.  The list is intentionally narrow: it covers only "engine
// could not find the answer" cases, not "engine found something
// suspicious" cases.
const STATIC_FALLBACK_RECOVERABLE_REASONS_EXACT = new Set([
  "lookup_no_match",
  "no_answer_letters",
  "letter_index_map_incomplete",
  "multi_match_failed",
]);
const STATIC_FALLBACK_BLOCK_NOTES_EXACT = new Set([
  "negation_mismatch",
]);
const STATIC_FALLBACK_BLOCK_NOTES_PREFIX = [
  "manual_review_required",
];
const STATIC_FALLBACK_RECOVERABLE_RISKY_NOTES_EXACT = new Set([
  "multi_match_failed",
  "single_option_close_second",
]);
const STATIC_FALLBACK_RECOVERABLE_RISKY_NOTES_PREFIX = [
  "multi_partial_match",
];

function parseArgs(argv) {
  const out = {
    headed: true,
    submit: false,
    autoSelect: true,
    autoSubmit: false,
    dryRun: false,
    log: "",
    screenshot: "",
    waitMs: 800,
    password: "",
    passwordSourceLabel: "",
    identity: "",
    answers: "",
    lookupUrl: DEFAULT_LOOKUP_URL,
    scoreThreshold: DEFAULT_SCORE_THRESHOLD,
    waitHumanAuth: false,
    humanAuthTimeoutSec: 600,
    staticFallbackOnRisk: false,
    pauseBeforeClose: false,
    paperManifest: "",
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--url") out.url = argv[++i];
    else if (arg === "--answers") out.answers = argv[++i];
    else if (arg === "--lookup-url") out.lookupUrl = argv[++i];
    else if (arg === "--score-threshold") out.scoreThreshold = Number(argv[++i]);
    else if (arg === "--auto-select") out.autoSelect = true;
    else if (arg === "--no-auto-select") out.autoSelect = false;
    else if (arg === "--auto-submit") { out.autoSubmit = true; out.submit = true; }
    else if (arg === "--no-auto-submit") { out.autoSubmit = false; out.submit = false; }
    else if (arg === "--submit") out.submit = true;
    else if (arg === "--no-submit") out.submit = false;
    else if (arg === "--dry-run") out.dryRun = true;
    else if (arg === "--headless") out.headed = false;
    else if (arg === "--log") out.log = argv[++i];
    else if (arg === "--screenshot") out.screenshot = argv[++i];
    else if (arg === "--wait-ms") out.waitMs = Number(argv[++i]);
    else if (arg === "--password") {
      out.password = argv[++i];
      out.passwordSourceLabel = "cli";
    } else if (arg === "--password-env") {
      const varName = argv[++i];
      out.password = process.env[varName] || "";
      out.passwordSourceLabel = `env:${varName}${out.password ? "" : ":missing"}`;
    } else if (arg === "--identity") out.identity = argv[++i];
    else if (arg === "--wait-human-auth") out.waitHumanAuth = true;
    else if (arg === "--human-auth-timeout") out.humanAuthTimeoutSec = Number(argv[++i]);
    else if (arg === "--static-fallback-on-risk") out.staticFallbackOnRisk = true;
    else if (arg === "--no-static-fallback-on-risk") out.staticFallbackOnRisk = false;
    else if (arg === "--pause-before-close") out.pauseBeforeClose = true;
    else if (arg === "--no-pause-before-close") out.pauseBeforeClose = false;
    else if (arg === "--paper-manifest") out.paperManifest = argv[++i];
    else if (arg === "-h" || arg === "--help") out.help = true;
    else throw new Error(`unknown arg: ${arg}`);
  }
  return out;
}

function usage() {
  return `Usage:
  node scripts/wjx_exam_assist.js --url <wjx_url> [--lookup-url URL]
                                  [--password-env VAR] [--identity JSON]
                                  [--auto-select|--no-auto-select]
                                  [--submit|--no-submit] [--dry-run]

Source of answers (priority order):
  --paper-manifest <path>
                  Per-paper manifest with bank sha256 + verified_override
                  list.  Required when --static-fallback-on-risk is set;
                  enforces the "same-bank" trust boundary.  See
                  examples/wjx_paper_manifest.example.json for schema.
  --lookup-url    HTTP service exposing POST /lookup_v2 (default ${DEFAULT_LOOKUP_URL})
  --answers       static JSON fallback (used when lookup unreachable)
  --static-fallback-on-risk
                  Also use --answers when lookup returns
                  recoverable-but-non-auto reasons (lookup_no_match,
                  no_answer_letters, low stem_score, multi_match_failed,
                  multi_partial_match, single_option_close_second).
                  Hard blocks: negation_mismatch, manual_review_required:*.
                  REQUIRES --paper-manifest with matching bank sha256.

Decision priority (per question):
  1. verified_override from manifest        (trumps lookup hard-block)
  2. lookup auto-click  (high-conf, no risk)
  3. static fallback    (--static-fallback-on-risk + manifest verified)
  4. LLM suggestion     (suggestion-only, never auto-click; future tier)
  5. human review       (highlight only)

Auto-select gating:
  Only auto-click when stem_score >= --score-threshold (default ${DEFAULT_SCORE_THRESHOLD})
  AND notes do not contain manual_review_required.*
  AND for multi-choice, all bank correct options were mapped.

Human-in-the-loop:
  --wait-human-auth   Pause for SMS-captcha completion; poll until question
                       blocks reappear, then continue.

Defaults:
  --auto-select on, --submit off, --dry-run off, --headless off`;
}

function waitForEnter(prompt) {
  if (!process.stdin.isTTY) {
    console.error(`${prompt} stdin is not interactive; leaving browser open until process is terminated.`);
    return new Promise(() => {});
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  return new Promise((resolve) => {
    rl.question(prompt, () => {
      rl.close();
      resolve();
    });
  });
}

function safeReadAnswers(file) {
  if (!file) return [];
  try {
    const payload = JSON.parse(fs.readFileSync(file, "utf8"));
    const rows = Array.isArray(payload) ? payload : payload.answers;
    if (!Array.isArray(rows)) return [];
    return rows.map((row, idx) => ({
      id: row.id || row.qid || String(row.number || idx + 1),
      number: row.number == null ? null : Number(row.number),
      stem: row.stem || "",
      stemContains: row.stem_contains || row.stemContains || "",
      answer: normalizeAnswerArray(row.answer),
    }));
  } catch (err) {
    console.error(`warning: --answers file unreadable: ${err.message}`);
    return [];
  }
}

function normalizeAnswerArray(value) {
  if (Array.isArray(value)) return value.map((x) => String(x).trim()).filter(Boolean);
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return [];
    if (trimmed.includes(",") || trimmed.includes("，")) {
      return trimmed.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
    }
    if (/^[A-Za-z]{2,}$/.test(trimmed)) return trimmed.split("");
    return [trimmed];
  }
  return [];
}

function normalizeText(s) {
  return String(s || "")
    .normalize("NFKC")
    .replace(/\s+/g, "")
    .replace(/[，。、""''：:；;！？!?（）()[\]【】《》<>]/g, "")
    .toLowerCase();
}

function cleanWjxStemForLookup(s) {
  return String(s || "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\*?\s*\d+\s*[\.\u3001]\s*/, "")
    .replace(/\s*\[(?:Multiple|Single|Judge|单选题|多选题|判断题)\]\s*$/i, "")
    .trim();
}

function cleanWjxOptionForLookup(s) {
  return String(s || "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[A-Z]\s*[\u3001\.\uff0e\uff1a:]\s*/i, "")
    .replace(/^\s*[\[(（【]\s*正确\s*[\])）】]\s*/, "")
    .replace(/\s*[\[(（【]\s*正确答案\s*[\])）】]\s*$/i, "")
    .trim();
}

function parseIdentity(value) {
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch (err) {
    throw new Error(`--identity must be JSON: ${err.message}`);
  }
}

function appendLog(logPath, record) {
  if (!logPath) return;
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  const safe = redactSensitive(record);
  fs.appendFileSync(
    logPath,
    JSON.stringify({ ts: new Date().toISOString(), ...safe }) + "\n"
  );
}

function redactSensitive(record) {
  const blocked = new Set(["password", "verification_code", "sms_code", "phone_number"]);
  const out = {};
  for (const [k, v] of Object.entries(record)) {
    if (blocked.has(k)) {
      out[k] = "<REDACTED>";
    } else if (k === "filled" && Array.isArray(v)) {
      out[k] = v.map((row) => ({ ...row, value_set: true, value: undefined }));
    } else {
      out[k] = v;
    }
  }
  return out;
}

function postJson(urlString, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(urlString);
    const data = Buffer.from(JSON.stringify(body), "utf8");
    const lib = u.protocol === "https:" ? https : http;
    const req = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname + u.search,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": data.length,
        },
        timeout: 5000,
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          if (res.statusCode >= 400) {
            return reject(new Error(`HTTP ${res.statusCode}: ${text.slice(0, 200)}`));
          }
          try {
            resolve(JSON.parse(text));
          } catch (err) {
            reject(err);
          }
        });
      }
    );
    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error("lookup timeout")));
    req.write(data);
    req.end();
  });
}

async function detectPasswordUsedAnywhere(page) {
  try {
    const body = await page.locator("body").innerText({ timeout: 2000 });
    return bodyContainsPasswordUsed(body);
  } catch (_err) {
    return false;
  }
}

async function detectSmsCaptcha(page) {
  try {
    const body = await page.locator("body").innerText({ timeout: 2000 });
    return bodyContainsSmsCaptcha(body);
  } catch (_err) {
    return false;
  }
}

async function waitForQuestionBlocks(page, timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const count = await page
      .locator(".field.ui-field-contain, .field, .div_question, .question, [data-question-id]")
      .count();
    if (count > 0) return true;
    await page.waitForTimeout(2000);
  }
  return false;
}

async function enterAccessPasswordIfPresent(page, password) {
  const pwd = page
    .locator("#txtPassword, input[placeholder='Password'], input.TxtPasswordCssClass")
    .first();
  if ((await pwd.count()) === 0) return { entered: false, page_kind: "no_password" };
  if (!password) return { entered: false, page_kind: "password_required" };
  await pwd.fill(password);
  let btn = page.locator("#btnContinue").first();
  if ((await btn.count()) === 0) btn = page.getByText(/Next Step|下一步|进入/).first();
  await btn.click({ timeout: 5000, force: true });
  await page.waitForTimeout(1500);
  if (await detectPasswordUsedAnywhere(page)) {
    return { entered: true, password_used: true };
  }
  return { entered: true, password_used: false };
}

async function fillIdentityFields(page, identity) {
  return page.evaluate((identity) => {
    function clean(s) {
      return String(s || "").replace(/\s+/g, " ").trim();
    }
    function inferLabel(block) {
      return clean(
        block.querySelector(".field-label, .topic, .title, .q-title, h3, legend")?.innerText ||
          block.innerText ||
          ""
      );
    }
    function chooseValue(label) {
      for (const [key, value] of Object.entries(identity || {})) {
        if (label.includes(key)) return String(value);
      }
      return "";
    }
    const blocks = Array.from(
      document.querySelectorAll(
        ".field, .div_question, .question, .ui-field-contain, .wjx-question, [data-question-id]"
      )
    );
    const filled = [];
    for (const block of blocks) {
      const label = inferLabel(block);
      const value = chooseValue(label);
      if (!value) continue;
      const controls = Array.from(
        block.querySelectorAll("input[type=text], input:not([type]), textarea")
      ).filter((x) =>
        !["radio", "checkbox", "hidden", "button", "submit"].includes(
          (x.type || "").toLowerCase()
        )
      );
      for (const control of controls) {
        control.value = value;
        control.dispatchEvent(new Event("input", { bubbles: true }));
        control.dispatchEvent(new Event("change", { bubbles: true }));
        filled.push({ label, id: control.id || "", name: control.name || "", value });
      }
    }
    return filled;
  }, identity);
}

async function extractQuestions(page) {
  return page.evaluate(() => {
    function clean(s) {
      return String(s || "").replace(/\s+/g, " ").trim();
    }

    function cleanWjxStemForLookup(s) {
      return String(s || "")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/^\*?\s*\d+\s*[\.\u3001]\s*/, "")
        .replace(/\s*\[(?:Multiple|Single|Judge|单选题|多选题|判断题)\]\s*$/i, "")
        .trim();
    }

    function cleanWjxOptionForLookup(s) {
      return String(s || "")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/^[A-Z]\s*[\u3001\.\uff0e\uff1a:]\s*/i, "")
        .replace(/^\s*[\[(（【]\s*正确\s*[\])）】]\s*/, "")
        .replace(/\s*[\[(（【]\s*正确答案\s*[\])）】]\s*$/i, "")
        .trim();
    }

    function optionTextFrom(el) {
      const clone = el.cloneNode(true);
      clone
        .querySelectorAll("input, i, em, span.jqradio, span.jqcheck")
        .forEach((n) => n.remove());
      return cleanWjxOptionForLookup(clean(clone.innerText || clone.textContent || ""));
    }

    function inferNumber(block, fallback) {
      const raw = clean(
        block.querySelector(".field-label, .topic, .title, .q-title, h3, legend")?.innerText ||
          block.innerText ||
          ""
      );
      const m = raw.match(/^\s*\*?\s*(\d+)[\.\、]/);
      return m ? Number(m[1]) : fallback;
    }

    const blocks = Array.from(
      document.querySelectorAll(
        ".field, .div_question, .question, .ui-field-contain, .wjx-question, [data-question-id]"
      )
    ).filter((el) => {
      const text = clean(el.innerText || el.textContent || "");
      const choices = el.querySelectorAll(
        "input[type=radio], input[type=checkbox], .ui-radio, .ui-checkbox, .jqradio, .jqcheck, li"
      );
      return text.length > 0 && choices.length > 0;
    });

    return blocks.map((block, i) => {
      const titleEl = block.querySelector(
        ".field-label, .topic, .title, .q-title, h3, legend"
      );
      const titleText = clean(titleEl?.innerText || titleEl?.textContent || "");
      const options = Array.from(
        block.querySelectorAll(
          "input[type=radio], input[type=checkbox], .ui-radio, .ui-checkbox, li, .option"
        )
      )
        .map((el) => {
          const input = el.matches("input")
            ? el
            : el.querySelector("input[type=radio], input[type=checkbox]");
          return {
            text: optionTextFrom(el),
            type:
              input?.type ||
              (el.querySelector("input[type=checkbox], .jqcheck") ? "checkbox" : "radio"),
          };
        })
        .filter((x) => x.text);
      const rawStem =
        titleText ||
        clean(block.innerText || block.textContent || "")
          .split("\n")
          .filter(Boolean)[0] ||
        "";
      return {
        number: inferNumber(block, i + 1),
        stem: cleanWjxStemForLookup(rawStem),
        optionCount: options.length,
        options,
      };
    });
  });
}

async function clickOption(page, questionNumber, optionIndex) {
  return page.evaluate(
    ({ questionNumber, optionIndex }) => {
      function clean(s) {
        return String(s || "").replace(/\s+/g, " ").trim();
      }
      function inferNumber(block, fallback) {
        const raw = clean(
          block.querySelector(".field-label, .topic, .title, .q-title, h3, legend")?.innerText ||
            block.innerText ||
            ""
        );
        const m = raw.match(/^\s*\*?\s*(\d+)[\.\、]/);
        return m ? Number(m[1]) : fallback;
      }
      const blocks = Array.from(
        document.querySelectorAll(
          ".field, .div_question, .question, .ui-field-contain, .wjx-question, [data-question-id]"
        )
      ).filter((el) => {
        const text = clean(el.innerText || el.textContent || "");
        const choices = el.querySelectorAll(
          "input[type=radio], input[type=checkbox], .ui-radio, .ui-checkbox, .jqradio, .jqcheck, li"
        );
        return text.length > 0 && choices.length > 0;
      });
      const block = blocks.find((el, idx) => inferNumber(el, idx + 1) === questionNumber);
      if (!block) return { ok: false, reason: "question_block_not_found" };
      const choices = Array.from(
        block.querySelectorAll(".ui-radio, .ui-checkbox, li, .option")
      ).filter((el) => el.querySelector("input[type=radio], input[type=checkbox]"));
      const choice = choices[optionIndex];
      if (!choice) return { ok: false, reason: "option_not_found" };
      const clickable =
        choice.querySelector(".jqradio, .jqcheck") ||
        choice.querySelector(".label") ||
        choice.querySelector("label") ||
        choice;
      clickable.scrollIntoView({ block: "center", inline: "nearest" });
      clickable.click();
      const input = choice.querySelector("input[type=radio], input[type=checkbox]");
      return {
        ok: !!(input && input.checked),
        reason: input && input.checked ? "checked" : "click_did_not_check",
      };
    },
    { questionNumber, optionIndex }
  );
}

async function highlightOption(page, questionNumber, optionIndex) {
  return page.evaluate(
    ({ questionNumber, optionIndex }) => {
      function clean(s) {
        return String(s || "").replace(/\s+/g, " ").trim();
      }
      function inferNumber(block, fallback) {
        const raw = clean(
          block.querySelector(".field-label, .topic, .title, .q-title, h3, legend")?.innerText ||
            block.innerText ||
            ""
        );
        const m = raw.match(/^\s*\*?\s*(\d+)[\.\、]/);
        return m ? Number(m[1]) : fallback;
      }
      const blocks = Array.from(
        document.querySelectorAll(
          ".field, .div_question, .question, .ui-field-contain, .wjx-question, [data-question-id]"
        )
      );
      const block = blocks.find((el, idx) => inferNumber(el, idx + 1) === questionNumber);
      if (!block) return { ok: false };
      const choices = Array.from(
        block.querySelectorAll(".ui-radio, .ui-checkbox, li, .option")
      );
      const choice = choices[optionIndex];
      if (!choice) return { ok: false };
      choice.style.outline = "3px dashed #ff9500";
      choice.style.background = "#fff7e6";
      return { ok: true };
    },
    { questionNumber, optionIndex }
  );
}

async function maybeSubmit(page) {
  const candidates = [
    "#submit_button",
    "#ctlNext",
    ".submitbutton",
    "text=提交",
    "text=交卷",
    "text=完成",
  ];
  const resultTokens = [
    /Correct\s+\d+\s+questions:\s*\d+/i,
    /total points:\s*\d+/i,
    /View result/i,
    /答卷已经提交/,
    /感谢您的参与/,
    /提交成功/,
  ];
  const validationTokens = [
    /请.*作答/,
    /未答/,
    /必答/,
    /请先/,
    /请完成/,
    /题目.*未完成/,
    /还有.*未答/,
    /不能为空/,
  ];
  const confirmSelectors = [
    "text=确定",
    "text=确认",
    "text=确认提交",
    "button:has-text('确定')",
    "button:has-text('确认')",
    "input[value='确定']",
    ".layui-layer-btn0",
  ];
  const dialogHandler = async (dialog) => {
    try {
      await dialog.accept();
    } catch (_err) {
      // ignore dialog dismissal failures
    }
  };
  page.on("dialog", dialogHandler);
  try {
    for (const sel of candidates) {
      const loc = page.locator(sel).first();
      if ((await loc.count()) > 0) {
        await loc.scrollIntoViewIfNeeded();
        const popupPromise = page.waitForEvent("popup", { timeout: 5000 }).catch(() => null);
        await loc.click({ timeout: 3000, force: true });
        const popup = await popupPromise;
        const targets = popup ? [popup, page] : [page];
        if (popup) {
          await popup.waitForLoadState("domcontentloaded", { timeout: 5000 }).catch(() => {});
        }

        for (let round = 0; round < 12; round += 1) {
          for (const target of targets) {
            const bodyText = await target.locator("body").innerText({ timeout: 3000 }).catch(() => "");
            const compact = bodyText.replace(/\s+/g, " ").trim();
            if (resultTokens.some((rx) => rx.test(compact)) || /completemobile2\.aspx/i.test(target.url())) {
              return {
                selector: sel,
                url: target.url(),
                body_excerpt: compact.slice(0, 300),
              };
            }
            const validationHit = validationTokens.find((rx) => rx.test(compact));
            if (validationHit) {
              const match = compact.match(validationHit);
              const idx = match ? compact.indexOf(match[0]) : -1;
              const start = idx >= 0 ? Math.max(0, idx - 120) : 0;
              const end = idx >= 0 ? Math.min(compact.length, idx + 180) : 300;
              return {
                selector: sel,
                url: target.url(),
                body_excerpt: compact.slice(start, end),
                validation_blocked: true,
              };
            }
          }

          for (const target of targets) {
            for (const confirmSel of confirmSelectors) {
              const confirmLoc = target.locator(confirmSel).first();
              if ((await confirmLoc.count()) > 0) {
                await confirmLoc.scrollIntoViewIfNeeded().catch(() => {});
                await confirmLoc.click({ timeout: 3000, force: true }).catch(() => {});
                await target.waitForLoadState("domcontentloaded", { timeout: 3000 }).catch(() => {});
                await target.waitForTimeout(1000).catch(() => {});
                break;
              }
            }
          }

          await page.waitForTimeout(2500);
        }

        const bodyText = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
        return {
          selector: sel,
          url: page.url(),
          body_excerpt: bodyText.replace(/\s+/g, " ").trim().slice(0, 300),
        };
      }
    }
    return null;
  } finally {
    page.off("dialog", dialogHandler);
  }
}

function pickStaticAnswer(question, answerRows) {
  const stemNorm = normalizeText(question.stem);
  let best = null;
  for (const row of answerRows) {
    let score = 0;
    if (row.number != null && row.number === question.number) score = Math.max(score, 70);
    if (row.stem && normalizeText(row.stem) === stemNorm) score = Math.max(score, 100);
    if (row.stemContains && stemNorm.includes(normalizeText(row.stemContains))) {
      score = Math.max(score, 92);
    }
    if (score > 0 && (!best || score > best.score)) best = { row, score };
  }
  return best;
}

function answerLettersToIndexes(letters, options) {
  const used = new Set();
  const out = [];
  for (const l of letters || []) {
    const upper = String(l).toUpperCase();
    if (/^[A-G]$/.test(upper)) {
      const idx = upper.charCodeAt(0) - "A".charCodeAt(0);
      if (idx < options.length && !used.has(idx)) {
        used.add(idx);
        out.push(idx);
      }
    }
  }
  return out;
}

function answerLabelToIndex(label, options) {
  if (!label) return -1;
  const norm = normalizeText(label);
  for (let i = 0; i < options.length; i += 1) {
    const optNorm = normalizeText(options[i].text);
    if (optNorm === norm || optNorm.includes(norm) || norm.includes(optNorm)) return i;
  }
  return -1;
}

function judgeOptionPolarity(text) {
  const norm = normalizeText(text);
  if (!norm) return null;
  for (const t of JUDGE_TRUE_LABELS) {
    if (norm === normalizeText(t)) return "T";
  }
  for (const t of JUDGE_FALSE_LABELS) {
    if (norm === normalizeText(t)) return "F";
  }
  // Soft-contains fallback for option text like "对的" / "正确选项"
  for (const t of JUDGE_TRUE_LABELS) {
    const tn = normalizeText(t);
    if (tn && (norm.includes(tn) || tn.includes(norm))) return "T";
  }
  for (const t of JUDGE_FALSE_LABELS) {
    const tn = normalizeText(t);
    if (tn && (norm.includes(tn) || tn.includes(norm))) return "F";
  }
  return null;
}

function judgeLabelToIndex(bankLabel, options) {
  const target = judgeOptionPolarity(bankLabel);
  if (!target) return -1;
  for (let i = 0; i < options.length; i += 1) {
    if (judgeOptionPolarity(options[i].text) === target) return i;
  }
  return -1;
}

function isRiskyNote(note) {
  const s = String(note);
  if (RISKY_NOTES_EXACT.has(s)) return true;
  return RISKY_NOTES_PREFIX.some((p) => s.startsWith(p));
}

function bodyContainsPasswordUsed(body) {
  const lower = String(body || "").toLowerCase();
  return PASSWORD_USED_TOKENS.some((t) => lower.includes(t));
}

function bodyContainsSmsCaptcha(body) {
  const lower = String(body || "").toLowerCase();
  return SMS_KEYWORDS.some((k) => lower.includes(k.toLowerCase()));
}

async function decideViaLookup(question, lookupUrl) {
  const body = {
    text: question.stem,
    options: question.options.map((o) => o.text),
  };
  try {
    const resp = await postJson(lookupUrl, body);
    return { ok: true, lookup: resp };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

function buildClickPlanFromLookup(lookup, options, threshold) {
  if (!lookup || !lookup.matched) {
    return {
      auto: false,
      reason: "lookup_no_match",
      indexes: [],
      lookupNotes: lookup?.notes || [],
    };
  }
  const score = Number(lookup.stem_score || 0);
  if (score < threshold) {
    return {
      auto: false,
      reason: `stem_score ${score.toFixed(1)} < ${threshold}`,
      indexes: [],
      lookupNotes: lookup.notes || [],
    };
  }

  // Compute candidate indexes for the branch. Done BEFORE the risky-note
  // gate so the highlighter still has something to draw when auto-click
  // is blocked by negation_mismatch / close_second_candidate / etc.
  let indexes = [];
  let mapReason = "";
  if (lookup.branch === "judge") {
    const idx = judgeLabelToIndex(lookup.answer_label, options);
    if (idx === -1) {
      return {
        auto: false,
        reason: "judge_label_not_mapped",
        indexes: [],
        lookupNotes: lookup.notes || [],
      };
    }
    indexes = [idx];
    mapReason = "judge_match";
  } else {
    const letters = lookup.answer_letters || [];
    if (letters.length === 0) {
      return {
        auto: false,
        reason: "no_answer_letters",
        indexes: [],
        lookupNotes: lookup.notes || [],
      };
    }
    indexes = answerLettersToIndexes(letters, options);
    if (indexes.length !== letters.length) {
      return {
        auto: false,
        reason: "letter_index_map_incomplete",
        indexes,
        lookupNotes: lookup.notes || [],
      };
    }
    mapReason = `${lookup.branch}_match`;
  }

  // Final gate: lookup-emitted risky notes block auto-click but keep
  // indexes for the highlighter. negation_mismatch is the primary case
  // — same stem fuzz score yet flipped semantics.
  const riskyNotes = (lookup.notes || []).filter(isRiskyNote);
  if (riskyNotes.length > 0) {
    return {
      auto: false,
      reason: `risky_notes:${riskyNotes.join(",")}`,
      indexes,
      lookupNotes: lookup.notes || [],
    };
  }

  return { auto: true, reason: mapReason, indexes, lookupNotes: [] };
}

function buildClickPlanFromStatic(staticMatch, options) {
  if (!staticMatch) {
    return { auto: false, reason: "no_static_match", indexes: [] };
  }
  const indexes = [];
  for (const tok of staticMatch.row.answer || []) {
    let idx = -1;
    // 1. Single-letter answer (A/B/C/D/...).
    if (/^[A-Za-z]$/.test(tok)) {
      idx = String(tok).toUpperCase().charCodeAt(0) - "A".charCodeAt(0);
      if (idx >= options.length) idx = -1;
    }
    // 2. Judge-polarity answer (对/错/正确/错误/T/F/是/否/...).  Mirrors
    //    the High-1 fix: the page may render the opposite synonym from
    //    what the answer key uses.
    if (idx < 0 && judgeOptionPolarity(tok)) {
      idx = judgeLabelToIndex(tok, options);
    }
    // 3. Free-form label substring match.
    if (idx < 0) idx = answerLabelToIndex(tok, options);
    if (idx >= 0 && idx < options.length && !indexes.includes(idx)) indexes.push(idx);
  }
  if (indexes.length !== (staticMatch.row.answer || []).length) {
    return { auto: false, reason: "static_letter_index_map_incomplete", indexes };
  }
  return { auto: staticMatch.score >= 92, reason: `static_match_${staticMatch.score}`, indexes };
}

function pickStaticAnswerByNumber(question, answerRows) {
  // High-trust path used only when --static-fallback-on-risk is on:
  // the page is known to be the same set we exported, so a number
  // collision means the answer row applies.
  if (!question || question.number == null) return null;
  for (const row of answerRows || []) {
    if (row.number != null && Number(row.number) === Number(question.number)) {
      return { row, score: 100 };
    }
  }
  return null;
}

// -----------------------------------------------------------------------
// Paper manifest (Tier 1) — per-paper sidecar that:
//   1. carries a bank sha256, used to gate --static-fallback-on-risk
//   2. carries verified_overrides, the highest-priority decision tier,
//      authoritative even over lookup hard-blocks like negation_mismatch
//
// Schema:
//   {
//     "paper_id": "mBfE06C",
//     "url": "...",
//     "bank": { "path": "data/processed/question_bank_merged.json",
//               "sha256": "abc..." },
//     "static_answers_path": "examples/dlut_bank_wjx_import_300_answers.json",
//     "verified_overrides": [
//       { "number": 7, "qid": "2020-content-0042",
//         "answer": ["B"], "reason": "verified during 5/8 dry-run" }
//     ]
//   }
// -----------------------------------------------------------------------

function _resolveProjectPath(p) {
  if (!p) return null;
  return path.isAbsolute(p) ? p : path.resolve(PROJECT_ROOT, p);
}

function loadPaperManifest(manifestPath) {
  if (!manifestPath) return null;
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (err) {
    throw new Error(`paper manifest unreadable: ${manifestPath} (${err.message})`);
  }
  // Prefer the nested ``static_answers: {path, sha256}`` shape; fall
  // back to the legacy flat ``static_answers_path`` for older manifests
  // (those will fail the hash check below — same outcome, clearer error).
  const nestedStatic = raw.static_answers || null;
  const legacyStaticPath = raw.static_answers_path || null;
  const staticPath =
    nestedStatic?.path || legacyStaticPath || null;
  return {
    raw,
    paperId: raw.paper_id || "?",
    bankPath: _resolveProjectPath(raw.bank?.path),
    bankSha256Expected: raw.bank?.sha256 || null,
    staticAnswersPath: _resolveProjectPath(staticPath),
    staticAnswersSha256Expected: nestedStatic?.sha256 || null,
    verifiedOverrides: Array.isArray(raw.verified_overrides) ? raw.verified_overrides : [],
  };
}

function verifyManifestBankHash(manifest) {
  if (!manifest) return { ok: false, reason: "no_manifest" };
  const { bankPath, bankSha256Expected } = manifest;
  if (!bankPath) return { ok: false, reason: "manifest_missing_bank_path" };
  if (!bankSha256Expected) return { ok: false, reason: "manifest_missing_bank_sha256" };
  let actual;
  try {
    actual = crypto.createHash("sha256").update(fs.readFileSync(bankPath)).digest("hex");
  } catch (err) {
    return { ok: false, reason: `bank_read_error:${err.code || err.message}` };
  }
  if (actual !== bankSha256Expected) {
    return { ok: false, reason: "hash_mismatch", actual, expected: bankSha256Expected };
  }
  return { ok: true, reason: "match", actual, expected: bankSha256Expected };
}

function verifyStaticAnswersHash(manifest, providedAnswersPath) {
  // Closes the "same-bank" trust boundary: even with a verified bank
  // hash, the answers file driving static fallback must match the
  // manifest's recorded sha256.  Otherwise the operator can plug in a
  // different answers JSON and silently override fallback decisions.
  if (!manifest) return { ok: false, reason: "no_manifest" };
  const expected = manifest.staticAnswersSha256Expected;
  if (!expected) {
    return { ok: false, reason: "manifest_missing_static_answers_sha256" };
  }
  const target = providedAnswersPath
    ? _resolveProjectPath(providedAnswersPath)
    : manifest.staticAnswersPath;
  if (!target) return { ok: false, reason: "no_answers_path" };
  let actual;
  try {
    actual = crypto.createHash("sha256").update(fs.readFileSync(target)).digest("hex");
  } catch (err) {
    return { ok: false, reason: `answers_read_error:${err.code || err.message}` };
  }
  if (actual !== expected) {
    return { ok: false, reason: "static_answers_hash_mismatch", actual, expected, path: target };
  }
  return { ok: true, reason: "match", actual, expected, path: target };
}

function pickVerifiedOverride(question, manifest) {
  if (!manifest || !Array.isArray(manifest.verifiedOverrides)) return null;
  if (!question || question.number == null) return null;
  for (const o of manifest.verifiedOverrides) {
    if (o && o.number != null && Number(o.number) === Number(question.number)) {
      return o;
    }
  }
  return null;
}

function buildClickPlanFromOverride(override, options) {
  if (!override || !Array.isArray(override.answer)) return null;
  const indexes = [];
  for (const tok of override.answer) {
    let idx = -1;
    if (/^[A-Za-z]$/.test(tok)) {
      idx = String(tok).toUpperCase().charCodeAt(0) - "A".charCodeAt(0);
      // Mirror the static-fallback fix: out-of-bounds single letters
      // (e.g. "T" / "F" against a 2-option judge page) are polarity
      // tokens, not position indexes.  Reset so the polarity branch
      // below can route them to 对/错 / 正确/错误.
      if (idx >= options.length) idx = -1;
    }
    if (idx < 0 && judgeOptionPolarity(tok)) {
      idx = judgeLabelToIndex(tok, options);
    }
    if (idx < 0) idx = answerLabelToIndex(tok, options);
    if (idx >= 0 && idx < options.length && !indexes.includes(idx)) indexes.push(idx);
  }
  if (indexes.length !== override.answer.length) {
    return {
      auto: false,
      reason: "override_letter_index_map_incomplete",
      indexes,
      lookupNotes: [],
      fromOverride: true,
    };
  }
  // verified_override is by definition human-vetted truth: it is allowed
  // to bypass lookup hard-blocks like negation_mismatch.  See B1 in
  // docs/opus_next_handoff.md §"问卷星导入稿、原题库、运行时兜底三层分离".
  return {
    auto: true,
    reason: `verified_override${override.reason ? `:${String(override.reason).slice(0, 60)}` : ""}`,
    indexes,
    lookupNotes: [],
    fromOverride: true,
  };
}

function isLookupRecoverableViaStatic(plan) {
  if (!plan || plan.auto) return false;

  const notes = plan.lookupNotes || [];
  // Hard block: never recover via static when the lookup explicitly
  // flagged the question as semantically risky.
  for (const note of notes) {
    const s = String(note);
    if (STATIC_FALLBACK_BLOCK_NOTES_EXACT.has(s)) return false;
    if (STATIC_FALLBACK_BLOCK_NOTES_PREFIX.some((p) => s.startsWith(p))) return false;
  }

  const reason = String(plan.reason || "");
  if (STATIC_FALLBACK_RECOVERABLE_REASONS_EXACT.has(reason)) return true;
  if (reason.startsWith("stem_score ")) return true;

  if (reason.startsWith("risky_notes:")) {
    // Only recover ambiguity classes that the operator can safely
    // override with a same-import static answer file.  Semantic risk
    // notes were hard-blocked above.
    return notes.some((n) => {
      const s = String(n);
      if (STATIC_FALLBACK_RECOVERABLE_RISKY_NOTES_EXACT.has(s)) return true;
      return STATIC_FALLBACK_RECOVERABLE_RISKY_NOTES_PREFIX.some((p) =>
        s.startsWith(p)
      );
    });
  }

  return false;
}

function flagRedacted(value) {
  if (!value) return "";
  const s = String(value);
  if (s.length <= 14) return s.slice(0, 6) + "…";
  return s.slice(0, 6) + "…" + s.slice(-4);
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || !args.url) {
    console.log(usage());
    return args.help ? 0 : 2;
  }

  // Load the per-paper manifest before any browser work.  When provided,
  // its bank sha256 is verified against the bank file on disk, its
  // static_answers.sha256 is verified against whichever answers file
  // will be used (CLI --answers if provided, otherwise the manifest's
  // own static_answers.path), and the verified_overrides list will be
  // consulted as Tier 1 of the decision tree.
  let manifest = null;
  let bankHashCheck = { ok: false, reason: "no_manifest" };
  let staticAnswersHashCheck = { ok: false, reason: "no_manifest" };
  if (args.paperManifest) {
    try {
      manifest = loadPaperManifest(args.paperManifest);
    } catch (err) {
      console.error(err.message);
      return 5;
    }
    bankHashCheck = verifyManifestBankHash(manifest);
  }

  // If the operator did not pass --answers but the manifest specifies a
  // static_answers.path, adopt it (manifest is the canonical source of
  // truth for this paper).  Done BEFORE the answers-hash check so that
  // verifyStaticAnswersHash hashes the actual file we'll use.
  if (!args.answers && manifest?.staticAnswersPath) {
    args.answers = manifest.staticAnswersPath;
  }

  if (manifest) {
    staticAnswersHashCheck = verifyStaticAnswersHash(manifest, args.answers);
  }

  // Static fallback now requires a manifest with BOTH a verified bank
  // hash AND a verified static-answers hash.  This closes the
  // "same-bank" trust boundary: the answers file driving fallback
  // clicks must be the exact one recorded at import time.
  if (
    args.staticFallbackOnRisk &&
    (!bankHashCheck.ok || !staticAnswersHashCheck.ok)
  ) {
    console.error(
      `--static-fallback-on-risk requires --paper-manifest with matching ` +
      `bank+static_answers sha256. ` +
      `manifest_loaded=${!!manifest} ` +
      `bank_hash=${bankHashCheck.reason} ` +
      `answers_hash=${staticAnswersHashCheck.reason}`
    );
    return 6;
  }

  const answerRows = safeReadAnswers(args.answers);
  const identity = parseIdentity(args.identity);
  const browser = await chromium.launch({ headless: !args.headed });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(8000);

  appendLog(args.log, {
    event_type: "wjx_assist_start",
    url: args.url,
    auto_select: args.autoSelect,
    auto_submit: args.autoSubmit,
    score_threshold: args.scoreThreshold,
    lookup_url: args.lookupUrl,
    static_answers_count: answerRows.length,
    static_fallback_on_risk: args.staticFallbackOnRisk,
    paper_manifest: args.paperManifest || null,
    paper_id: manifest?.paperId || null,
    verified_overrides_count: manifest?.verifiedOverrides?.length || 0,
    bank_hash_check: bankHashCheck.reason,
    static_answers_hash_check: staticAnswersHashCheck.reason,
    password_source: args.passwordSourceLabel || "",
    identity_keys: Object.keys(identity),
    wait_human_auth: args.waitHumanAuth,
  });

  await page.goto(args.url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(args.waitMs);

  const passwordResult = await enterAccessPasswordIfPresent(page, args.password);
  appendLog(args.log, { event_type: "wjx_access", ...passwordResult });
  if (passwordResult.password_used) {
    console.error("password_used: This password has been used. Rotate or unfreeze.");
    appendLog(args.log, { event_type: "wjx_password_used", action: "exit" });
    await browser.close();
    return 3;
  }

  if (args.waitHumanAuth) {
    const sms = await detectSmsCaptcha(page);
    appendLog(args.log, { event_type: "wjx_sms_check", sms_detected: sms });
    if (sms) {
      console.error(
        "SMS captcha detected. Complete it in the browser; the script will resume when question blocks appear."
      );
      const ok = await waitForQuestionBlocks(page, args.humanAuthTimeoutSec * 1000);
      if (!ok) {
        console.error("timed out waiting for human SMS auth");
        appendLog(args.log, { event_type: "wjx_sms_wait", outcome: "timeout" });
        await browser.close();
        return 4;
      }
      appendLog(args.log, { event_type: "wjx_sms_wait", outcome: "resumed" });
    }
  }

  const identityResult = await fillIdentityFields(page, identity);
  appendLog(args.log, {
    event_type: "wjx_identity",
    filled: identityResult.map((x) => ({
      label: x.label,
      id: x.id,
      name: x.name,
      value_set: true,
    })),
  });

  const questions = await extractQuestions(page);
  const summary = [];
  for (const q of questions) {
    const stemExcerpt = q.stem.slice(0, 80);
    let plan;
    let lookupOutcome = null;
    let staticMatch = null;
    let verifiedOverride = null;

    // Tier 1: verified_override (per-paper manifest).  Trumps everything
    // including lookup hard-blocks like negation_mismatch — the override
    // is by definition human-vetted truth for this specific paper.
    if (manifest) {
      verifiedOverride = pickVerifiedOverride(q, manifest);
      if (verifiedOverride) {
        const overridePlan = buildClickPlanFromOverride(verifiedOverride, q.options);
        if (overridePlan && overridePlan.auto) {
          appendLog(args.log, {
            event_type: "wjx_verified_override_used",
            question_no: q.number,
            stem_excerpt: stemExcerpt,
            override_qid: verifiedOverride.qid || null,
            override_answer: verifiedOverride.answer,
            override_reason_excerpt: (verifiedOverride.reason || "").slice(0, 80),
          });
          plan = overridePlan;
        } else if (overridePlan) {
          // Override exists but couldn't be mapped to current page
          // options.  This is exactly the case where automation must
          // STOP — the only authoritative source for this question
          // disagreed with the page (variant text, shuffled label
          // formatting, page mutation, etc.).  Force the question into
          // highlight-only/no-action; do NOT silently fall through to
          // lookup or static fallback (per Codex review).
          appendLog(args.log, {
            event_type: "wjx_verified_override_unmapped",
            question_no: q.number,
            stem_excerpt: stemExcerpt,
            reason: overridePlan.reason,
            override_qid: verifiedOverride.qid || null,
            override_answer: verifiedOverride.answer,
            page_option_count: q.options.length,
          });
          plan = {
            auto: false,
            reason: `verified_override_unmapped:${overridePlan.reason}`,
            indexes: overridePlan.indexes || [],
            lookupNotes: [],
            fromOverride: true,
            forcedHumanReview: true,
          };
        }
      }
    }

    // Tier 2: lookup_v2 (skipped when a verified_override already won).
    if (!plan && args.lookupUrl) {
      const probe = await decideViaLookup(q, args.lookupUrl);
      if (probe.ok) {
        lookupOutcome = probe.lookup;
        plan = buildClickPlanFromLookup(probe.lookup, q.options, args.scoreThreshold);
      } else {
        appendLog(args.log, {
          event_type: "wjx_lookup_error",
          question_no: q.number,
          stem_excerpt: stemExcerpt,
          error: probe.error,
        });
      }
    }

    // Static-answers fallback when lookup returned a recoverable
    // non-auto plan AND the operator explicitly opted in.  The trust
    // anchor is question.number → static answers JSON: the JSON was
    // generated alongside the import, so a number collision is taken
    // as ground truth.
    if (
      plan &&
      !plan.auto &&
      args.staticFallbackOnRisk &&
      answerRows.length > 0 &&
      isLookupRecoverableViaStatic(plan)
    ) {
      const fallbackMatch =
        pickStaticAnswerByNumber(q, answerRows) || pickStaticAnswer(q, answerRows);
      if (fallbackMatch) {
        const fallbackPlan = buildClickPlanFromStatic(fallbackMatch, q.options);
        if (fallbackPlan.auto) {
          appendLog(args.log, {
            event_type: "wjx_static_fallback_used",
            question_no: q.number,
            stem_excerpt: stemExcerpt,
            lookup_reason: plan.reason,
            lookup_notes: plan.lookupNotes || [],
            static_match_score: fallbackMatch.score,
            static_answer_id: fallbackMatch.row.id,
          });
          plan = {
            ...fallbackPlan,
            lookupNotes: plan.lookupNotes || [],
            reason: `static_fallback:${fallbackPlan.reason}`,
            fallbackUsed: "static_on_risk",
          };
          staticMatch = fallbackMatch;
        }
      }
    }

    if (!plan) {
      staticMatch = pickStaticAnswer(q, answerRows);
      plan = buildClickPlanFromStatic(staticMatch, q.options);
    }

    const decision = {
      event_type: "wjx_answer_decision",
      question_no: q.number,
      stem_excerpt: stemExcerpt,
      branch: verifiedOverride
        ? "verified_override"
        : (lookupOutcome?.branch || (staticMatch ? "static" : "unknown")),
      qid:
        verifiedOverride?.qid ||
        lookupOutcome?.qid ||
        staticMatch?.row?.id ||
        null,
      score: verifiedOverride
        ? 100
        : (lookupOutcome?.stem_score || staticMatch?.score || 0),
      answer_letters:
        verifiedOverride?.answer ||
        lookupOutcome?.answer_letters ||
        (staticMatch?.row?.answer ?? []),
      auto_clicked: false,
      auto_select_enabled: args.autoSelect,
      reason: plan.reason,
      notes: plan.lookupNotes || [],
      indexes: plan.indexes,
      from_override: !!verifiedOverride && plan.fromOverride === true,
    };

    if (args.dryRun) {
      decision.status = "dry_run";
    } else if (plan.auto && args.autoSelect) {
      let allClicked = true;
      for (const idx of plan.indexes) {
        const clickRes = await clickOption(page, q.number, idx);
        if (!clickRes.ok) {
          allClicked = false;
          decision.status = "click_failed";
          decision.click_error = clickRes.reason;
          break;
        }
      }
      if (allClicked) {
        decision.status = "clicked";
        decision.auto_clicked = true;
      }
    } else {
      for (const idx of plan.indexes) {
        await highlightOption(page, q.number, idx);
      }
      decision.status = plan.indexes.length > 0 ? "highlighted_no_click" : "no_action";
    }

    summary.push(decision);
    appendLog(args.log, decision);
  }

  let submitResult = null;
  if (args.submit && !args.dryRun) {
    submitResult = await maybeSubmit(page);
    appendLog(args.log, { event_type: "wjx_submit", submit: submitResult });
  } else {
    appendLog(args.log, { event_type: "wjx_submit_skipped", submit_flag: args.submit, dry_run: args.dryRun });
  }

  if (args.screenshot) {
    fs.mkdirSync(path.dirname(args.screenshot), { recursive: true });
    await page.screenshot({ path: args.screenshot, fullPage: true });
  }

	  console.log(
	    JSON.stringify(
	      {
	        questions: questions.length,
	        summary,
        submitted_by: submitResult ? submitResult.selector : null,
        submit_result: submitResult,
      },
      null,
	      2
	    )
	  );
	  if (args.pauseBeforeClose) {
	    appendLog(args.log, { event_type: "wjx_pause_before_close", action: "waiting_for_operator" });
	    await waitForEnter(
	      "WJX assist paused with browser open. Finish manual review/submission, then press Enter here to close browser..."
	    );
	  }
	  await browser.close();
	  return 0;
	}

// Pure-logic exports for unit tests; do not affect the CLI path when
// this file is invoked directly via `node scripts/wjx_exam_assist.js`.
module.exports = {
  parseArgs,
  JUDGE_TRUE_LABELS,
  JUDGE_FALSE_LABELS,
  PASSWORD_USED_TOKENS,
  SMS_KEYWORDS,
  RISKY_NOTES_EXACT,
  RISKY_NOTES_PREFIX,
  normalizeText,
  judgeOptionPolarity,
  judgeLabelToIndex,
  answerLabelToIndex,
  answerLettersToIndexes,
  isRiskyNote,
  bodyContainsPasswordUsed,
  bodyContainsSmsCaptcha,
  buildClickPlanFromLookup,
  buildClickPlanFromStatic,
  pickStaticAnswer,
  pickStaticAnswerByNumber,
  isLookupRecoverableViaStatic,
  loadPaperManifest,
  verifyManifestBankHash,
  verifyStaticAnswersHash,
  pickVerifiedOverride,
  buildClickPlanFromOverride,
  cleanWjxStemForLookup,
  cleanWjxOptionForLookup,
  flagRedacted,
  redactSensitive,
  STATIC_FALLBACK_RECOVERABLE_REASONS_EXACT,
  STATIC_FALLBACK_BLOCK_NOTES_EXACT,
  STATIC_FALLBACK_BLOCK_NOTES_PREFIX,
};

if (require.main === module) {
  main()
    .then((code) => process.exit(code || 0))
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
