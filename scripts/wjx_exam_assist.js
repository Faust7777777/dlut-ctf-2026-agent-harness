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
const { chromium } = require("playwright");

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
  --lookup-url    HTTP service exposing POST /lookup_v2 (default ${DEFAULT_LOOKUP_URL})
  --answers       static JSON fallback (only used if lookup unreachable)

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

    function optionTextFrom(el) {
      const clone = el.cloneNode(true);
      clone
        .querySelectorAll("input, i, em, span.jqradio, span.jqcheck")
        .forEach((n) => n.remove());
      return clean(clone.innerText || clone.textContent || "");
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
      const stem =
        titleText ||
        clean(block.innerText || block.textContent || "")
          .split("\n")
          .filter(Boolean)[0] ||
        "";
      return {
        number: inferNumber(block, i + 1),
        stem,
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
    const upper = String(tok).toUpperCase();
    let idx = -1;
    if (/^[A-Za-z]$/.test(tok)) idx = upper.charCodeAt(0) - "A".charCodeAt(0);
    if (idx < 0) idx = answerLabelToIndex(tok, options);
    if (idx >= 0 && idx < options.length && !indexes.includes(idx)) indexes.push(idx);
  }
  if (indexes.length !== (staticMatch.row.answer || []).length) {
    return { auto: false, reason: "static_letter_index_map_incomplete", indexes };
  }
  return { auto: staticMatch.score >= 92, reason: `static_match_${staticMatch.score}`, indexes };
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

    if (args.lookupUrl) {
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
    if (!plan) {
      staticMatch = pickStaticAnswer(q, answerRows);
      plan = buildClickPlanFromStatic(staticMatch, q.options);
    }

    const decision = {
      event_type: "wjx_answer_decision",
      question_no: q.number,
      stem_excerpt: stemExcerpt,
      branch: lookupOutcome?.branch || "static",
      qid: lookupOutcome?.qid || staticMatch?.row?.id || null,
      score: lookupOutcome?.stem_score || staticMatch?.score || 0,
      answer_letters: lookupOutcome?.answer_letters || (staticMatch?.row?.answer ?? []),
      auto_clicked: false,
      auto_select_enabled: args.autoSelect,
      reason: plan.reason,
      notes: plan.lookupNotes || [],
      indexes: plan.indexes,
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
  await browser.close();
  return 0;
}

// Pure-logic exports for unit tests; do not affect the CLI path when
// this file is invoked directly via `node scripts/wjx_exam_assist.js`.
module.exports = {
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
  flagRedacted,
  redactSensitive,
};

if (require.main === module) {
  main()
    .then((code) => process.exit(code || 0))
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
