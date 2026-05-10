#!/usr/bin/env node
/**
 * Pure-logic regression tests for scripts/wjx_exam_assist.js.
 *
 * Targets the two High issues Codex's review surfaced:
 *
 *   1. Judge-question 同义词 mapping: "正确" must map to a page option
 *      labelled "对" (and the other True/False synonyms in either
 *      direction).
 *   2. negation_mismatch and other risky lookup notes must NOT trigger
 *      auto-click — but indexes are still computed for the highlighter.
 *
 * Plus light coverage of password / SMS body detectors and risky-note
 * predicates so a regression in the constant table is caught here, not
 * during a real Wenjuanxing run.
 *
 * Run with:
 *   node tests/test_wjx_assist_logic.js
 *
 * Exit code 0 = all assertions pass.
 */

const assert = require("node:assert");
const path = require("node:path");

const m = require(path.join("..", "scripts", "wjx_exam_assist.js"));

let passed = 0;
function ok(label) {
  process.stdout.write(`  [PASS] ${label}\n`);
  passed += 1;
}

// ---------------------------------------------------------------------
// CLI parsing
// ---------------------------------------------------------------------

assert.strictEqual(
  m.parseArgs(["node", "wjx", "--url", "https://example.test"]).pauseBeforeClose,
  false
);
assert.strictEqual(
  m.parseArgs(["node", "wjx", "--url", "https://example.test", "--pause-before-close"])
    .pauseBeforeClose,
  true
);
ok("parseArgs supports --pause-before-close");

// ---------------------------------------------------------------------
// Judge-label synonym mapping (the High-1 fix)
// ---------------------------------------------------------------------

assert.strictEqual(m.judgeOptionPolarity("对"), "T", "对 → T");
assert.strictEqual(m.judgeOptionPolarity("错"), "F", "错 → F");
assert.strictEqual(m.judgeOptionPolarity("正确"), "T", "正确 → T");
assert.strictEqual(m.judgeOptionPolarity("错误"), "F", "错误 → F");
assert.strictEqual(m.judgeOptionPolarity("是"), "T");
assert.strictEqual(m.judgeOptionPolarity("否"), "F");
assert.strictEqual(m.judgeOptionPolarity("True"), "T");
assert.strictEqual(m.judgeOptionPolarity("False"), "F");
assert.strictEqual(m.judgeOptionPolarity("✓"), "T");
assert.strictEqual(m.judgeOptionPolarity("×"), "F");
assert.strictEqual(m.judgeOptionPolarity("无关词"), null);
ok("judgeOptionPolarity covers TRUE/FALSE synonyms");

// 正确 (engine canonical) → 对 (page text)
assert.strictEqual(
  m.judgeLabelToIndex("正确", [{ text: "对" }, { text: "错" }]),
  0,
  "正确 must map to 对 (the High-1 bug)"
);
ok("judgeLabelToIndex: 正确 → 对");

// 错误 → 错 (was already working)
assert.strictEqual(
  m.judgeLabelToIndex("错误", [{ text: "对" }, { text: "错" }]),
  1,
  "错误 must map to 错"
);
ok("judgeLabelToIndex: 错误 → 错");

// Identity case
assert.strictEqual(
  m.judgeLabelToIndex("正确", [{ text: "正确" }, { text: "错误" }]),
  0
);
ok("judgeLabelToIndex: 正确 ↔ 正确 same-form match");

// Reverse-shuffled options
assert.strictEqual(
  m.judgeLabelToIndex("正确", [{ text: "错" }, { text: "对" }]),
  1
);
ok("judgeLabelToIndex: 正确 → 对 even when 对 is at index 1");

// English form on page
assert.strictEqual(
  m.judgeLabelToIndex("正确", [{ text: "True" }, { text: "False" }]),
  0
);
ok("judgeLabelToIndex: 正确 → True");

// Not a judge-label option set
assert.strictEqual(
  m.judgeLabelToIndex("正确", [{ text: "选项A" }, { text: "选项B" }]),
  -1
);
ok("judgeLabelToIndex: returns -1 when no polarity match");

// ---------------------------------------------------------------------
// Risky-note predicates (the High-2 fix)
// ---------------------------------------------------------------------

assert.strictEqual(m.isRiskyNote("negation_mismatch"), true);
assert.strictEqual(m.isRiskyNote("close_second_candidate"), true);
assert.strictEqual(m.isRiskyNote("single_option_low_score"), true);
assert.strictEqual(m.isRiskyNote("single_option_close_second"), true);
assert.strictEqual(m.isRiskyNote("multi_match_failed"), true);
assert.strictEqual(m.isRiskyNote("manual_review_required:foo"), true);
assert.strictEqual(m.isRiskyNote("multi_partial_match:1/3_no_silent_intersection"), true);
assert.strictEqual(m.isRiskyNote("judge_unknown_token:Z"), true);
assert.strictEqual(m.isRiskyNote(""), false);
assert.strictEqual(m.isRiskyNote("no_match"), false);
assert.strictEqual(m.isRiskyNote("kill_switch_active"), false);
ok("isRiskyNote covers all engine-emitted risk codes");

// ---------------------------------------------------------------------
// buildClickPlanFromLookup — auto-gate behaviour
// ---------------------------------------------------------------------

// Clean single-choice → auto
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "single",
      stem_score: 100,
      answer_letters: ["B"],
      answer_label: null,
      notes: [],
    },
    [{ text: "选项A" }, { text: "选项B" }, { text: "选项C" }, { text: "选项D" }],
    92
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [1]);
}
ok("clean high-score single → auto-click");

// negation_mismatch → no auto, but indexes preserved for highlight
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "single",
      stem_score: 95,
      answer_letters: ["A"],
      answer_label: null,
      notes: ["negation_mismatch"],
    },
    [{ text: "甲" }, { text: "乙" }],
    92
  );
  assert.strictEqual(plan.auto, false, "negation_mismatch must block auto-click");
  assert.deepStrictEqual(plan.indexes, [0], "indexes still computed for highlight");
  assert.match(plan.reason, /risky_notes:.*negation_mismatch/);
}
ok("negation_mismatch → highlight only (the High-2 fix)");

// manual_review_required → no auto
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "multi",
      stem_score: 96,
      answer_letters: ["A", "B"],
      answer_label: null,
      notes: ["manual_review_required:source_answer_out_of_options"],
    },
    [{ text: "甲" }, { text: "乙" }, { text: "丙" }],
    92
  );
  assert.strictEqual(plan.auto, false);
  assert.deepStrictEqual(plan.indexes, [0, 1]);
}
ok("manual_review_required → highlight only");

// close_second_candidate → no auto
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "single",
      stem_score: 90,
      answer_letters: ["C"],
      answer_label: null,
      notes: ["close_second_candidate"],
    },
    [{ text: "1" }, { text: "2" }, { text: "3" }, { text: "4" }],
    88
  );
  assert.strictEqual(plan.auto, false);
}
ok("close_second_candidate → highlight only");

// stem_score below threshold → no indexes, no auto
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "single",
      stem_score: 70,
      answer_letters: ["A"],
      notes: [],
    },
    [{ text: "x" }, { text: "y" }],
    92
  );
  assert.strictEqual(plan.auto, false);
  assert.deepStrictEqual(plan.indexes, []);
}
ok("low stem_score → highlight skipped");

// Judge plan: 正确 → 对 with empty notes → auto
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "judge",
      stem_score: 100,
      answer_letters: [],
      answer_label: "正确",
      notes: [],
    },
    [{ text: "对" }, { text: "错" }],
    92
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [0]);
}
ok("judge plan: 正确 → 对 auto-clicks");

// Judge plan with negation_mismatch → no auto but indexes computed
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "judge",
      stem_score: 100,
      answer_label: "错误",
      notes: ["negation_mismatch"],
    },
    [{ text: "对" }, { text: "错" }],
    92
  );
  assert.strictEqual(plan.auto, false);
  assert.deepStrictEqual(plan.indexes, [1]);
}
ok("judge plan + negation_mismatch → highlight only");

// Multi with letter map incomplete (one letter not in options) → no auto
{
  const plan = m.buildClickPlanFromLookup(
    {
      matched: true,
      branch: "multi",
      stem_score: 100,
      answer_letters: ["A", "F"],
      notes: [],
    },
    [{ text: "x" }, { text: "y" }, { text: "z" }],
    92
  );
  assert.strictEqual(plan.auto, false);
  assert.match(plan.reason, /letter_index_map_incomplete/);
}
ok("multi map incomplete → not auto");

// Lookup unmatched → no auto, no indexes
{
  const plan = m.buildClickPlanFromLookup(
    { matched: false, notes: ["no_match"] },
    [{ text: "a" }],
    92
  );
  assert.strictEqual(plan.auto, false);
  assert.deepStrictEqual(plan.indexes, []);
}
ok("unmatched lookup → highlight skipped");

// ---------------------------------------------------------------------
// Body-content detectors
// ---------------------------------------------------------------------

assert.strictEqual(
  m.bodyContainsPasswordUsed("This password has been used."),
  true
);
assert.strictEqual(
  m.bodyContainsPasswordUsed("the user's PASSWORD HAS BEEN USED for the second time"),
  true
);
assert.strictEqual(
  m.bodyContainsPasswordUsed("welcome to the test, please enter password"),
  false
);
assert.strictEqual(m.bodyContainsPasswordUsed(""), false);
ok("bodyContainsPasswordUsed detects all token variants");

assert.strictEqual(m.bodyContainsSmsCaptcha("请输入您的手机号"), true);
assert.strictEqual(m.bodyContainsSmsCaptcha("点击获取验证码"), true);
assert.strictEqual(m.bodyContainsSmsCaptcha("Enter the verification code"), true);
assert.strictEqual(m.bodyContainsSmsCaptcha("题目1: 选择最佳答案"), false);
ok("bodyContainsSmsCaptcha catches Chinese and English variants");

// ---------------------------------------------------------------------
// Wenjuanxing page-text cleanup before lookup
// ---------------------------------------------------------------------

assert.strictEqual(
  m.cleanWjxStemForLookup("* 15.互联网信息内容安全管理的主体具有多元性，包括( )。[Multiple]"),
  "互联网信息内容安全管理的主体具有多元性，包括( )。"
);
assert.strictEqual(
  m.cleanWjxStemForLookup("  9、制作、复制、出版淫秽物品牟利罪（）以牟利为目的。[单选题]"),
  "制作、复制、出版淫秽物品牟利罪（）以牟利为目的。"
);
assert.strictEqual(
  m.cleanWjxStemForLookup("* 22.信息包括在网络上传输的一切消息。"),
  "信息包括在网络上传输的一切消息。"
);
ok("cleanWjxStemForLookup strips WJX number and type markers");

assert.strictEqual(m.cleanWjxOptionForLookup("A、域名管理"), "域名管理");
assert.strictEqual(m.cleanWjxOptionForLookup("B. 网络安全管理"), "网络安全管理");
assert.strictEqual(m.cleanWjxOptionForLookup("C．数据安全"), "数据安全");
assert.strictEqual(m.cleanWjxOptionForLookup("D：其他"), "其他");
assert.strictEqual(m.cleanWjxOptionForLookup("D、过滤进出的网络流量(正确答案)"), "过滤进出的网络流量");
assert.strictEqual(m.cleanWjxOptionForLookup("B. [正确] 严格验证包含文件的路径"), "严格验证包含文件的路径");
assert.strictEqual(m.cleanWjxOptionForLookup("A 股市场"), "A 股市场");
ok("cleanWjxOptionForLookup strips WJX option labels and answer markers without hurting content");

// ---------------------------------------------------------------------
// --static-fallback-on-risk recovery (per opus_wjx_real_exam_handoff.md)
// ---------------------------------------------------------------------

// Recovery predicate: which lookup outcomes can fall back to --answers?
{
  // Recoverable reasons → true
  for (const reason of [
    "lookup_no_match",
    "no_answer_letters",
    "letter_index_map_incomplete",
    "multi_match_failed",
    "stem_score 70.0 < 92",
    "stem_score 91.5 < 92",
  ]) {
    const ok = m.isLookupRecoverableViaStatic({
      auto: false, reason, indexes: [], lookupNotes: [],
    });
    assert.strictEqual(ok, true, `should recover: ${reason}`);
  }
  // Non-recoverable reasons → false
  for (const reason of [
    "judge_label_not_mapped",
    "lookup_disabled",
  ]) {
    const ok = m.isLookupRecoverableViaStatic({
      auto: false, reason, indexes: [], lookupNotes: [],
    });
    assert.strictEqual(ok, false, `should NOT recover: ${reason}`);
  }
  // Auto-clickable plan → no recovery needed
  assert.strictEqual(
    m.isLookupRecoverableViaStatic({ auto: true, reason: "single_match", indexes: [0] }),
    false
  );
}
ok("isLookupRecoverableViaStatic: per-reason whitelist");

// Hard-block notes never recover (even if reason looks recoverable)
{
  const blocked = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "no_answer_letters",
    indexes: [],
    lookupNotes: ["negation_mismatch"],
  });
  assert.strictEqual(blocked, false, "negation_mismatch must block static fallback");

  const blocked2 = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "stem_score 75 < 92",
    indexes: [],
    lookupNotes: ["manual_review_required:source_truncated_options"],
  });
  assert.strictEqual(blocked2, false, "manual_review_required:* must block static fallback");
}
ok("isLookupRecoverableViaStatic: hard-block notes");

// risky_notes wrapper: selected static-answer-safe ambiguity recovers, low_score does NOT
{
  const recoverFromMultiPartial = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "risky_notes:multi_partial_match:2/3_no_silent_intersection",
    indexes: [0, 1],
    lookupNotes: ["multi_partial_match:2/3_no_silent_intersection"],
  });
  assert.strictEqual(recoverFromMultiPartial, true);

  const recoverFromMultiFailed = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "risky_notes:multi_match_failed",
    indexes: [],
    lookupNotes: ["multi_match_failed"],
  });
  assert.strictEqual(recoverFromMultiFailed, true);

  const blockedCloseSecond = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "risky_notes:close_second_candidate",
    indexes: [0],
    lookupNotes: ["close_second_candidate"],
  });
  assert.strictEqual(blockedCloseSecond, false, "close_second is genuine ambiguity, no static override");

  const recoverFromSingleCloseSecond = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "risky_notes:single_option_close_second",
    indexes: [0],
    lookupNotes: ["single_option_close_second"],
  });
  assert.strictEqual(
    recoverFromSingleCloseSecond,
    true,
    "single-option close_second should recover when --static-fallback-on-risk has exact static answers"
  );

  const blockedLowScore = m.isLookupRecoverableViaStatic({
    auto: false,
    reason: "risky_notes:single_option_low_score",
    indexes: [],
    lookupNotes: ["single_option_low_score"],
  });
  assert.strictEqual(blockedLowScore, false);
}
ok("isLookupRecoverableViaStatic: risky_notes selected ambiguity recovers");

// pickStaticAnswerByNumber: number-exact returns score 100
{
  const rows = [
    { id: "q-001", number: 1, stem: "first", answer: ["A"] },
    { id: "q-005", number: 5, stem: "fifth", answer: ["B"] },
  ];
  const m1 = m.pickStaticAnswerByNumber({ number: 5 }, rows);
  assert.strictEqual(m1.row.id, "q-005");
  assert.strictEqual(m1.score, 100);

  const m2 = m.pickStaticAnswerByNumber({ number: 999 }, rows);
  assert.strictEqual(m2, null);

  const m3 = m.pickStaticAnswerByNumber({ number: null }, rows);
  assert.strictEqual(m3, null);
}
ok("pickStaticAnswerByNumber: exact number match only");

// buildClickPlanFromStatic with judge polarity
{
  // Static answer "正确" → page option "对"
  const plan = m.buildClickPlanFromStatic(
    { row: { answer: ["正确"] }, score: 100 },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan.auto, true, "static 正确 → 对 must auto");
  assert.deepStrictEqual(plan.indexes, [0]);

  // Static answer "错" → page option "错误"
  const plan2 = m.buildClickPlanFromStatic(
    { row: { answer: ["错"] }, score: 100 },
    [{ text: "正确" }, { text: "错误" }]
  );
  assert.strictEqual(plan2.auto, true);
  assert.deepStrictEqual(plan2.indexes, [1]);

  // Static answer "对" → page option "对"
  const plan3 = m.buildClickPlanFromStatic(
    { row: { answer: ["对"] }, score: 100 },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan3.auto, true);
  assert.deepStrictEqual(plan3.indexes, [0]);

  // Static answer "T" → page option "对"
  const plan4 = m.buildClickPlanFromStatic(
    { row: { answer: ["T"] }, score: 100 },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan4.auto, true, "standalone 'T' should fall back to judge polarity when out of option range");
  assert.deepStrictEqual(plan4.indexes, [0]);

  // Static answer "F" → page option "错"
  const plan5 = m.buildClickPlanFromStatic(
    { row: { answer: ["F"] }, score: 100 },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan5.auto, true);
  assert.deepStrictEqual(plan5.indexes, [1]);
}
ok("buildClickPlanFromStatic: judge polarity for 对/错/正确/错误");

// buildClickPlanFromStatic with multi-answer letters
{
  const plan = m.buildClickPlanFromStatic(
    { row: { answer: ["A", "B", "C"] }, score: 100 },
    [{ text: "选项一" }, { text: "选项二" }, { text: "选项三" }, { text: "选项四" }]
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [0, 1, 2]);

  const planPartial = m.buildClickPlanFromStatic(
    { row: { answer: ["A", "Z"] }, score: 100 },
    [{ text: "x" }, { text: "y" }]
  );
  assert.strictEqual(planPartial.auto, false);
  assert.match(planPartial.reason, /static_letter_index_map_incomplete/);
}
ok("buildClickPlanFromStatic: multi-answer letter mapping");

// buildClickPlanFromStatic with score < 92 → not auto even if mapping complete
// (used for stem-based fuzzy matches; number-exact path forces score=100)
{
  const plan = m.buildClickPlanFromStatic(
    { row: { answer: ["A"] }, score: 70 },
    [{ text: "x" }, { text: "y" }]
  );
  assert.strictEqual(plan.auto, false, "score 70 below 92 → highlight only");
  assert.deepStrictEqual(plan.indexes, [0]);
}
ok("buildClickPlanFromStatic: stem-fuzzy score < 92 stays manual");

// ---------------------------------------------------------------------
// Tier 1: verified_override (per-paper manifest)
// ---------------------------------------------------------------------

const fs = require("node:fs");
const os = require("node:os");
const crypto = require("node:crypto");

// pickVerifiedOverride: number-exact match, no fuzzy
{
  const manifest = {
    verifiedOverrides: [
      { number: 7, qid: "test-7", answer: ["B"], reason: "verified" },
      { number: 12, qid: "test-12", answer: ["A", "C"], reason: "" },
    ],
  };
  assert.strictEqual(m.pickVerifiedOverride({ number: 7 }, manifest).qid, "test-7");
  assert.strictEqual(m.pickVerifiedOverride({ number: 12 }, manifest).qid, "test-12");
  assert.strictEqual(m.pickVerifiedOverride({ number: 99 }, manifest), null);
  assert.strictEqual(m.pickVerifiedOverride({ number: null }, manifest), null);
  assert.strictEqual(m.pickVerifiedOverride({ number: 7 }, null), null);
  assert.strictEqual(m.pickVerifiedOverride({ number: 7 }, {}), null);
}
ok("pickVerifiedOverride: number-exact match only");

// buildClickPlanFromOverride: single letter
{
  const plan = m.buildClickPlanFromOverride(
    { number: 1, answer: ["B"], reason: "verified during 5/8 dry-run" },
    [{ text: "x" }, { text: "y" }, { text: "z" }]
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [1]);
  assert.match(plan.reason, /^verified_override/);
  assert.strictEqual(plan.fromOverride, true);
}
ok("buildClickPlanFromOverride: single letter answer");

// buildClickPlanFromOverride: multi letters
{
  const plan = m.buildClickPlanFromOverride(
    { number: 2, answer: ["A", "C", "D"] },
    [{ text: "1" }, { text: "2" }, { text: "3" }, { text: "4" }]
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [0, 2, 3]);
}
ok("buildClickPlanFromOverride: multi-letter answer");

// buildClickPlanFromOverride: judge polarity (override answer "正确" → page "对")
{
  const plan = m.buildClickPlanFromOverride(
    { number: 3, answer: ["正确"] },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [0]);
}
ok("buildClickPlanFromOverride: judge polarity 正确 → 对");

// buildClickPlanFromOverride: standalone 'T' must reset out-of-bounds idx
// and route through polarity → 对.  Mirrors the static-fallback fix
// surfaced in Codex's Medium review.
{
  const plan = m.buildClickPlanFromOverride(
    { number: 11, answer: ["T"], reason: "manifest used T/F shorthand for judge" },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan.auto, true, "override 'T' must auto via polarity");
  assert.deepStrictEqual(plan.indexes, [0]);
  assert.match(plan.reason, /^verified_override/);
}
ok("buildClickPlanFromOverride: 'T' → 对 via bounds-reset polarity");

// buildClickPlanFromOverride: standalone 'F' → 错
{
  const plan = m.buildClickPlanFromOverride(
    { number: 12, answer: ["F"] },
    [{ text: "对" }, { text: "错" }]
  );
  assert.strictEqual(plan.auto, true);
  assert.deepStrictEqual(plan.indexes, [1]);
}
ok("buildClickPlanFromOverride: 'F' → 错 via bounds-reset polarity");

// buildClickPlanFromOverride: 'T' on a 3-option page (not a judge page)
// must NOT silently latch onto idx 2.  Out-of-bounds reset still kicks
// in only for 1-option/2-option pages; for 3+ options 'T' = idx 19, also
// out of bounds, so reset → polarity → polarity returns null for non-
// judge option text → no map → incomplete.
{
  const plan = m.buildClickPlanFromOverride(
    { number: 13, answer: ["T"] },
    [{ text: "选项A" }, { text: "选项B" }, { text: "选项C" }]
  );
  assert.strictEqual(plan.auto, false, "T on non-judge page should NOT auto-click");
  assert.match(plan.reason, /override_letter_index_map_incomplete/);
}
ok("buildClickPlanFromOverride: 'T' on non-judge page stays manual");

// buildClickPlanFromOverride: incomplete map → not auto
{
  const plan = m.buildClickPlanFromOverride(
    { number: 4, answer: ["A", "Z"] },
    [{ text: "x" }, { text: "y" }]
  );
  assert.strictEqual(plan.auto, false);
  assert.match(plan.reason, /override_letter_index_map_incomplete/);
}
ok("buildClickPlanFromOverride: incomplete map → highlight only");

// buildClickPlanFromOverride: null / empty
{
  assert.strictEqual(m.buildClickPlanFromOverride(null, [{ text: "x" }]), null);
  assert.strictEqual(m.buildClickPlanFromOverride({ number: 1 }, [{ text: "x" }]), null);
}
ok("buildClickPlanFromOverride: null override returns null");

// loadPaperManifest: round-trip with nested static_answers
{
  const tmp = path.join(os.tmpdir(), `wjx_manifest_${Date.now()}.json`);
  const payload = {
    paper_id: "test-paper",
    url: "https://example/wjx/test.aspx#",
    bank: { path: "data/processed/question_bank_merged.json", sha256: "abc123" },
    static_answers: {
      path: "examples/dlut_bank_wjx_import_300_answers.json",
      sha256: "def456",
    },
    verified_overrides: [{ number: 5, answer: ["B"] }],
  };
  fs.writeFileSync(tmp, JSON.stringify(payload), "utf8");
  try {
    const loaded = m.loadPaperManifest(tmp);
    assert.strictEqual(loaded.paperId, "test-paper");
    assert.strictEqual(loaded.bankSha256Expected, "abc123");
    assert.strictEqual(loaded.staticAnswersSha256Expected, "def456");
    assert.strictEqual(loaded.verifiedOverrides.length, 1);
    assert.strictEqual(loaded.verifiedOverrides[0].number, 5);
    assert.ok(path.isAbsolute(loaded.bankPath), "bankPath should be absolute");
    assert.ok(path.isAbsolute(loaded.staticAnswersPath));
  } finally {
    fs.unlinkSync(tmp);
  }
}
ok("loadPaperManifest: parses nested static_answers + resolves paths");

// loadPaperManifest: legacy flat static_answers_path → no sha256 carried
{
  const tmp = path.join(os.tmpdir(), `wjx_manifest_legacy_${Date.now()}.json`);
  const payload = {
    paper_id: "legacy",
    bank: { path: "data/processed/question_bank_merged.json", sha256: "abc123" },
    static_answers_path: "examples/dlut_bank_wjx_import_300_answers.json",
    verified_overrides: [],
  };
  fs.writeFileSync(tmp, JSON.stringify(payload), "utf8");
  try {
    const loaded = m.loadPaperManifest(tmp);
    assert.ok(loaded.staticAnswersPath, "legacy flat path still resolved");
    assert.strictEqual(
      loaded.staticAnswersSha256Expected,
      null,
      "legacy schema has no sha256 → caller will reject in same-bank check"
    );
  } finally {
    fs.unlinkSync(tmp);
  }
}
ok("loadPaperManifest: legacy flat schema → no sha256");

// loadPaperManifest: missing returns null
{
  assert.strictEqual(m.loadPaperManifest(""), null);
  assert.strictEqual(m.loadPaperManifest(null), null);
}
ok("loadPaperManifest: empty path returns null");

// loadPaperManifest: invalid file throws
{
  const tmp = path.join(os.tmpdir(), `wjx_manifest_bad_${Date.now()}.json`);
  fs.writeFileSync(tmp, "{ this is not json", "utf8");
  try {
    assert.throws(() => m.loadPaperManifest(tmp), /unreadable/);
  } finally {
    fs.unlinkSync(tmp);
  }
}
ok("loadPaperManifest: invalid JSON throws");

// verifyManifestBankHash: actual hash match
{
  const tmpBank = path.join(os.tmpdir(), `wjx_test_bank_${Date.now()}.json`);
  fs.writeFileSync(tmpBank, '{"questions":[]}', "utf8");
  const expectedSha = crypto
    .createHash("sha256")
    .update(fs.readFileSync(tmpBank))
    .digest("hex");
  try {
    const ok1 = m.verifyManifestBankHash({
      bankPath: tmpBank,
      bankSha256Expected: expectedSha,
    });
    assert.strictEqual(ok1.ok, true);
    assert.strictEqual(ok1.reason, "match");

    const ok2 = m.verifyManifestBankHash({
      bankPath: tmpBank,
      bankSha256Expected: "deadbeef",
    });
    assert.strictEqual(ok2.ok, false);
    assert.strictEqual(ok2.reason, "hash_mismatch");
  } finally {
    fs.unlinkSync(tmpBank);
  }
}
ok("verifyManifestBankHash: detects match vs mismatch");

// verifyManifestBankHash: missing inputs
{
  assert.strictEqual(m.verifyManifestBankHash(null).ok, false);
  assert.strictEqual(
    m.verifyManifestBankHash({ bankPath: null, bankSha256Expected: "x" }).ok,
    false
  );
  assert.strictEqual(
    m.verifyManifestBankHash({ bankPath: "/tmp/x", bankSha256Expected: null }).ok,
    false
  );
  // nonexistent file
  const r = m.verifyManifestBankHash({
    bankPath: "/nonexistent/path/bank.json",
    bankSha256Expected: "abc",
  });
  assert.strictEqual(r.ok, false);
  assert.match(r.reason, /bank_read_error/);
}
ok("verifyManifestBankHash: missing inputs handled");

// verifyStaticAnswersHash: actual answers file matches manifest sha256
{
  const tmp = path.join(os.tmpdir(), `wjx_answers_${Date.now()}.json`);
  fs.writeFileSync(tmp, '{"answers":[{"number":1,"answer":["A"]}]}', "utf8");
  const expectedSha = crypto
    .createHash("sha256")
    .update(fs.readFileSync(tmp))
    .digest("hex");
  try {
    // No --answers passed → manifest's static_answers.path is hashed
    const r1 = m.verifyStaticAnswersHash({
      staticAnswersPath: tmp,
      staticAnswersSha256Expected: expectedSha,
    });
    assert.strictEqual(r1.ok, true);
    assert.strictEqual(r1.reason, "match");

    // Correct --answers path passed in
    const r2 = m.verifyStaticAnswersHash(
      { staticAnswersPath: "/different/path", staticAnswersSha256Expected: expectedSha },
      tmp
    );
    assert.strictEqual(r2.ok, true);

    // Wrong file content → mismatch
    const wrong = path.join(os.tmpdir(), `wjx_answers_wrong_${Date.now()}.json`);
    fs.writeFileSync(wrong, '{"answers":[{"number":99,"answer":["Z"]}]}', "utf8");
    try {
      const r3 = m.verifyStaticAnswersHash(
        { staticAnswersPath: tmp, staticAnswersSha256Expected: expectedSha },
        wrong
      );
      assert.strictEqual(r3.ok, false);
      assert.strictEqual(r3.reason, "static_answers_hash_mismatch");
    } finally {
      fs.unlinkSync(wrong);
    }
  } finally {
    fs.unlinkSync(tmp);
  }
}
ok("verifyStaticAnswersHash: detects match vs mismatch with provided --answers");

// verifyStaticAnswersHash: missing manifest / sha256 / file
{
  assert.strictEqual(m.verifyStaticAnswersHash(null).ok, false);
  assert.strictEqual(
    m.verifyStaticAnswersHash({ staticAnswersPath: "/tmp/x" }).ok,
    false,
    "missing sha256 in manifest must refuse"
  );
  const r = m.verifyStaticAnswersHash({
    staticAnswersPath: "/nonexistent/answers.json",
    staticAnswersSha256Expected: "abc",
  });
  assert.strictEqual(r.ok, false);
  assert.match(r.reason, /answers_read_error/);
}
ok("verifyStaticAnswersHash: missing inputs handled");

// verifyStaticAnswersHash: legacy manifest (no sha256) refused
{
  // Legacy manifest from old build_paper_manifest.py output won't have
  // static_answers.sha256 — Codex review explicitly demands refusal so
  // operators rebuild the manifest with the hash.
  const r = m.verifyStaticAnswersHash({
    staticAnswersPath: "/tmp/whatever",
    staticAnswersSha256Expected: null,
  });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.reason, "manifest_missing_static_answers_sha256");
}
ok("verifyStaticAnswersHash: legacy schema refused");

// verified_override answer that includes risky-note semantics:
// the override should still produce auto=true (B1 ruling).
// We demonstrate this by building the override plan directly; the
// runtime decision tree in main() places verified_override at Tier 1
// before the lookup risky-note gate runs at all.
{
  const overridePlan = m.buildClickPlanFromOverride(
    { number: 99, answer: ["A"], reason: "human-verified despite negation_mismatch in lookup" },
    [{ text: "选A" }, { text: "选B" }]
  );
  assert.strictEqual(overridePlan.auto, true, "override never blocked by lookup notes");
  assert.match(overridePlan.reason, /verified_override/);
}
ok("verified_override bypasses lookup hard-block (B1)");

// ---------------------------------------------------------------------

console.log(`\nOK: ${passed} assertion groups pass`);
