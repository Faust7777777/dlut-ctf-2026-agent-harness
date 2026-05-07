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

console.log(`\nOK: ${passed} assertion groups pass`);
