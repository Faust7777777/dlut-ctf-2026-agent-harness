#!/usr/bin/env node
/**
 * Narrow MVP for self-owned Wenjuanxing exam pages.
 *
 * It opens a supplied exam URL, extracts visible question blocks, matches them
 * against a local answer key, and clicks radio/checkbox choices. The script is
 * intentionally scoped to assisted answering on a page the operator controls:
 * it does not bypass login, captcha, fullscreen checks, or final submission.
 */

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

function parseArgs(argv) {
  const out = {
    headed: true,
    submit: false,
    dryRun: false,
    log: "",
    screenshot: "",
    waitMs: 800,
    password: "",
    profile: "",
    identity: "",
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--url") out.url = argv[++i];
    else if (arg === "--answers") out.answers = argv[++i];
    else if (arg === "--submit") out.submit = true;
    else if (arg === "--dry-run") out.dryRun = true;
    else if (arg === "--headless") out.headed = false;
    else if (arg === "--log") out.log = argv[++i];
    else if (arg === "--screenshot") out.screenshot = argv[++i];
    else if (arg === "--wait-ms") out.waitMs = Number(argv[++i]);
    else if (arg === "--password") out.password = argv[++i];
    else if (arg === "--password-env") out.password = process.env[argv[++i]] || "";
    else if (arg === "--identity") out.identity = argv[++i];
    else if (arg === "-h" || arg === "--help") out.help = true;
    else throw new Error(`unknown arg: ${arg}`);
  }
  return out;
}

function usage() {
  return `Usage:
  node scripts/wjx_exam_mvp.js --url <wjx_exam_url> --answers examples/wjx_answers.example.json

Options:
  --headless           Run Chromium headless
  --dry-run            Extract and match only; do not click choices
  --submit             Click final submit button after answering
  --password <value>   Fill simple Wenjuanxing access-password page
  --password-env <VAR> Read access password from an environment variable
  --identity <json>    Fill personal-info fields, e.g. '{"姓名":"张三","工号":"001"}'
  --log <path>         Write JSONL action log
  --screenshot <path>  Save screenshot after answering

Answer key format:
  {
    "answers": [
      {"number": 1, "answer": ["A"]},
      {"stem_contains": "网络安全", "answer": ["A", "C"]},
      {"stem": "判断题题干", "answer": ["正确"]}
    ]
  }`;
}

function readAnswerKey(file) {
  const payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const rows = Array.isArray(payload) ? payload : payload.answers;
  if (!Array.isArray(rows)) throw new Error("answer key must be an array or {answers: []}");
  return rows.map((row, idx) => ({
    id: row.id || row.qid || String(row.number || idx + 1),
    number: row.number == null ? null : Number(row.number),
    stem: row.stem || "",
    stemContains: row.stem_contains || row.stemContains || "",
    answer: normalizeAnswerArray(row.answer),
  }));
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
    .replace(/[，。、“”‘’：:；;！？!?（）()[\]【】《》<>]/g, "")
    .toLowerCase();
}

function matchAnswer(question, answerRows) {
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

function answerTokenToOptionIndexes(answerTokens, options) {
  const indexes = [];
  const used = new Set();
  for (const raw of answerTokens) {
    const token = String(raw).trim();
    let idx = -1;
    if (/^[A-Za-z]$/.test(token)) {
      idx = token.toUpperCase().charCodeAt(0) - "A".charCodeAt(0);
    }
    if (idx < 0) {
      const tokenNorm = normalizeText(token);
      idx = options.findIndex((opt) => {
        const textNorm = normalizeText(opt.text);
        return textNorm === tokenNorm || textNorm.includes(tokenNorm) || tokenNorm.includes(textNorm);
      });
    }
    if (idx >= 0 && idx < options.length && !used.has(idx)) {
      used.add(idx);
      indexes.push(idx);
    }
  }
  return indexes;
}

async function extractQuestions(page) {
  return page.evaluate(() => {
    function clean(s) {
      return String(s || "").replace(/\s+/g, " ").trim();
    }

    function optionTextFrom(el) {
      const clone = el.cloneNode(true);
      clone.querySelectorAll("input, i, em, span.jqradio, span.jqcheck").forEach((n) => n.remove());
      return clean(clone.innerText || clone.textContent || "");
    }

    function cssPath(el) {
      if (!el || !el.tagName) return "";
      const parts = [];
      let cur = el;
      while (cur && cur.nodeType === Node.ELEMENT_NODE && parts.length < 8) {
        let part = cur.tagName.toLowerCase();
        if (cur.id) {
          part += `#${CSS.escape(cur.id)}`;
          parts.unshift(part);
          break;
        }
        const cls = Array.from(cur.classList || []).slice(0, 2).map((c) => `.${CSS.escape(c)}`).join("");
        part += cls;
        const parent = cur.parentElement;
        if (parent) {
          const siblings = Array.from(parent.children).filter((x) => x.tagName === cur.tagName);
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
        }
        parts.unshift(part);
        cur = parent;
      }
      return parts.join(" > ");
    }

    function inferNumber(block, fallback) {
      const raw = clean(
        block.querySelector(".field-label, .topic, .title, .q-title, h3, legend")?.innerText ||
          block.innerText ||
          ""
      );
      const m = raw.match(/^\s*(\d+)[\.\、]/);
      return m ? Number(m[1]) : fallback;
    }

    const blocks = Array.from(
      document.querySelectorAll(
        ".field, .div_question, .question, .ui-field-contain, .wjx-question, [data-question-id]"
      )
    ).filter((el) => {
      const text = clean(el.innerText || el.textContent || "");
      const choices = el.querySelectorAll("input[type=radio], input[type=checkbox], .ui-radio, .ui-checkbox, .jqradio, .jqcheck, li");
      return text.length > 0 && choices.length > 0;
    });

    return blocks.map((block, i) => {
      const titleEl = block.querySelector(".field-label, .topic, .title, .q-title, h3, legend");
      const titleText = clean(titleEl?.innerText || titleEl?.textContent || "");
      const options = Array.from(
        block.querySelectorAll("input[type=radio], input[type=checkbox], .ui-radio, .ui-checkbox, li, .option")
      )
        .map((el) => {
          const input = el.matches("input") ? el : el.querySelector("input[type=radio], input[type=checkbox]");
          const clickable =
            input ||
            el.querySelector(".jqradio, .jqcheck, a, label") ||
            el.closest("label") ||
            el;
          return {
            text: optionTextFrom(el),
            type: input?.type || (el.querySelector("input[type=checkbox], .jqcheck") ? "checkbox" : "radio"),
            selector: cssPath(clickable),
            inputSelector: input ? cssPath(input) : "",
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
        const choices = el.querySelectorAll("input[type=radio], input[type=checkbox], .ui-radio, .ui-checkbox, .jqradio, .jqcheck, li");
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
      return { ok: !!(input && input.checked), reason: input && input.checked ? "checked" : "click_did_not_check" };
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
  for (const sel of candidates) {
    const loc = page.locator(sel).first();
    if ((await loc.count()) > 0) {
      await loc.scrollIntoViewIfNeeded();
      await loc.click({ timeout: 3000, force: true });
      await page.waitForLoadState("domcontentloaded", { timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(1500);
      const bodyText = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
      return {
        selector: sel,
        url: page.url(),
        body_excerpt: bodyText.replace(/\s+/g, " ").trim().slice(0, 300),
      };
    }
  }
  return null;
}

function parseIdentity(value) {
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch (err) {
    throw new Error(`--identity must be JSON: ${err.message}`);
  }
}

async function enterAccessPasswordIfPresent(page, password) {
  const pwd = page.locator("#txtPassword, input[placeholder='Password'], input.TxtPasswordCssClass").first();
  if ((await pwd.count()) === 0) return { entered: false };
  if (!password) return { entered: false, reason: "password_required" };
  await pwd.fill(password);
  let btn = page.locator("#btnContinue").first();
  if ((await btn.count()) === 0) btn = page.getByText(/Next Step|下一步|进入/).first();
  await btn.click({ timeout: 5000, force: true });
  await page.waitForTimeout(1500);
  return { entered: true };
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
      document.querySelectorAll(".field, .div_question, .question, .ui-field-contain, .wjx-question, [data-question-id]")
    );
    const filled = [];
    for (const block of blocks) {
      const label = inferLabel(block);
      const value = chooseValue(label);
      if (!value) continue;
      const controls = Array.from(block.querySelectorAll("input[type=text], input:not([type]), textarea"))
        .filter((x) => !["radio", "checkbox", "hidden", "button", "submit"].includes((x.type || "").toLowerCase()));
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

function appendLog(logPath, record) {
  if (!logPath) return;
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  fs.appendFileSync(logPath, JSON.stringify({ ts: new Date().toISOString(), ...record }, null, 0) + "\n");
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || !args.url || !args.answers) {
    console.log(usage());
    return args.help ? 0 : 2;
  }

  const answerRows = readAnswerKey(args.answers);
  const identity = parseIdentity(args.identity);
  const browser = await chromium.launch({ headless: !args.headed });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(8000);

  await page.goto(args.url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(args.waitMs);
  const passwordResult = await enterAccessPasswordIfPresent(page, args.password);
  appendLog(args.log, { event_type: "wjx_access", ...passwordResult });
  const identityResult = await fillIdentityFields(page, identity);
  appendLog(args.log, {
    event_type: "wjx_identity",
    filled: identityResult.map((x) => ({ label: x.label, id: x.id, name: x.name, value_set: true })),
  });

  const questions = await extractQuestions(page);
  const summary = [];
  for (const q of questions) {
    const matched = matchAnswer(q, answerRows);
    if (!matched) {
      summary.push({ number: q.number, stem: q.stem, status: "no_answer_match" });
      appendLog(args.log, { event_type: "wjx_answer", question: q, status: "no_answer_match" });
      continue;
    }
    const indexes = answerTokenToOptionIndexes(matched.row.answer, q.options);
    const row = {
      number: q.number,
      stem: q.stem,
      answer_id: matched.row.id,
      match_score: matched.score,
      answer: matched.row.answer,
      option_indexes: indexes,
      status: "matched",
    };
    if (indexes.length !== matched.row.answer.length) {
      row.status = "option_map_incomplete";
    } else if (!args.dryRun) {
      for (const idx of indexes) {
        const clicked = await clickOption(page, q.number, idx);
        if (!clicked.ok) {
          row.status = "click_failed";
          row.click_error = clicked.reason;
          break;
        }
      }
      if (row.status !== "click_failed") row.status = "clicked";
    }
    summary.push(row);
    appendLog(args.log, { event_type: "wjx_answer", ...row });
  }

  let submitResult = null;
  if (args.submit && !args.dryRun) {
    submitResult = await maybeSubmit(page);
    appendLog(args.log, { event_type: "wjx_submit", submit: submitResult });
  }

  if (args.screenshot) {
    fs.mkdirSync(path.dirname(args.screenshot), { recursive: true });
    await page.screenshot({ path: args.screenshot, fullPage: true });
  }

  console.log(JSON.stringify({
    questions: questions.length,
    summary,
    submitted_by: submitResult ? submitResult.selector : null,
    submit_result: submitResult,
  }, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
