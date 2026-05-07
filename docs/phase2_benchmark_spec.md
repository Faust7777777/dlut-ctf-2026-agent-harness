# Phase 2 Benchmark Spec — 300-question wjx end-to-end timing

> 给 Codex 执行的窄任务 spec。Opus 不做。
> 上下文：`docs/codex_next_handoff.md`、Phase 1 结果在
> `logs/benchmark-300-*-summary.json`。

## 目标

测量 `scripts/wjx_exam_assist.js` 在 **真实 chromium 浏览器** 里答 300
题的端到端速度，验证 1 小时（60 分钟）预算够用。Phase 1 已经证明
**后端 lookup 4.3 ms / 题，1.3 秒答完 300 题**；Phase 2 测的是
**chromium DOM + 网络往返 + 点击交互**的真实开销。

期望产出的关键数字：

- 平均单题端到端耗时（headed / headless 各一组）
- 300 题总耗时
- 端到端准确率（应与 Phase 1 99.3% 接近，差异即 chromium 路径丢的信息）
- 失败题的 challenge_id 列表

## 不要做

1. **不要改** `ctf_agents/*` 任何文件——状态机和 lookup engine 已经冻结
2. **不要改** `configs/config.yaml`、`requirements.txt`、`package.json`
3. **不要改** 任何已经通过的 unit test 或集成 test
4. **不要联网**——所有题目从本地 `question_bank_merged.json` 取；
   chromium 打开本地 HTTP server，不要打真实问卷星
5. **不要扩展 wjx_exam_assist.js**——任何"为了 benchmark 顺手补的功能"都先写到 spec 里，等 Opus / 用户拍

## 输入

- 题库：`data/processed/question_bank_merged.json`（2,815 题）
- lookup 服务：`python -m ctf_agents.knowledge.lookup_service` 已在 8765 端口
- assist：`scripts/wjx_exam_assist.js`，已支持 `--url` 任意 URL

## 输出（你要创建的文件）

```
scripts/generate_wjx_300_mock.py            # 生成 mock HTML
scripts/benchmark_wjx_e2e_300.py            # 跑端到端 benchmark
tests/fixtures/wjx_300_mock_template.html   # 单题模板（可选，也可在 generate 脚本里硬编码）
logs/benchmark-wjx-e2e-<ts>.jsonl           # 运行时写
logs/benchmark-wjx-e2e-<ts>-summary.json    # 总结
```

## 任务分解

### Step 1：生成 mock HTML

`scripts/generate_wjx_300_mock.py`：

- 命令行参数：`--n 300 --seed 7 --output build/wjx_300_mock.html`
- 从 `question_bank_merged.json` 按比例 stratified 抽样：
  - judge：~120 题
  - single：~120 题
  - multi：~60 题
- 选项打乱顺序（reproducible，由 seed 控制）
- 输出一个**单页** HTML，结构必须**严格**模仿真实问卷星考试页（参考
  `docs/opus_next_handoff.md` §"问卷星真实 DOM 结论"）：

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>wjx mock</title>
<style>
  .field.ui-field-contain { padding: 12px; border-bottom: 1px solid #eee; }
  .field-label { font-weight: bold; margin-bottom: 8px; }
  .ui-radio, .ui-checkbox { padding: 6px; cursor: pointer; }
  input[type=radio], input[type=checkbox] { display: none; }
  .jqradio, .jqcheck { display: inline-block; width: 14px; height: 14px;
                       border: 1px solid #888; margin-right: 6px;
                       vertical-align: middle; }
  .jqradio.jqradio_choose, .jqcheck.jqcheck_choose { background: #4080ff; }
  .label { display: inline-block; }
  #ctlNext { margin: 20px; padding: 8px 24px; cursor: pointer; }
</style></head><body>
  <!-- 单选示例 -->
  <div class="field ui-field-contain" id="div1">
    <div class="field-label">1. 下列哪项属于强密码特征？</div>
    <div class="ui-radio">
      <input type="radio" id="q1_1" name="q1">
      <a class="jqradio" href="javascript:;"></a>
      <div class="label" for="q1_1">A、使用生日</div>
    </div>
    <div class="ui-radio">
      <input type="radio" id="q1_2" name="q1">
      <a class="jqradio" href="javascript:;"></a>
      <div class="label" for="q1_2">B、长度足够且包含多类字符</div>
    </div>
    ...
  </div>
  <!-- 多选 -->
  <div class="field ui-field-contain" id="div2">
    <div class="field-label">2. ...[多选题]</div>
    <div class="ui-checkbox">
      <input type="checkbox" id="q2_1" name="q2">
      <a class="jqcheck" href="javascript:;"></a>
      <div class="label" for="q2_1">A、消息</div>
    </div>
    ...
  </div>
  <!-- 判断 -->
  <div class="field ui-field-contain" id="div3">
    <div class="field-label">3. ...[判断题]</div>
    <div class="ui-radio">
      <input type="radio" id="q3_1" name="q3">
      <a class="jqradio" href="javascript:;"></a>
      <div class="label" for="q3_1">对</div>
    </div>
    <div class="ui-radio">
      <input type="radio" id="q3_2" name="q3">
      <a class="jqradio" href="javascript:;"></a>
      <div class="label" for="q3_2">错</div>
    </div>
  </div>
  <!-- 提交按钮 -->
  <button id="ctlNext" type="button" onclick="document.getElementById('result').textContent='已提交'">提交</button>
  <div id="result"></div>
  <script>
    // 点击 .jqradio / .jqcheck 时切换 input checked
    document.addEventListener('click', function(e) {
      const t = e.target;
      const opt = t.closest('.ui-radio, .ui-checkbox');
      if (!opt) return;
      const input = opt.querySelector('input');
      if (!input) return;
      if (input.type === 'radio') {
        // single-choice: clear siblings
        document.querySelectorAll(`input[name="${input.name}"]`).forEach(x => {
          x.checked = false;
          const sibling = x.closest('.ui-radio')?.querySelector('.jqradio');
          if (sibling) sibling.classList.remove('jqradio_choose');
        });
        input.checked = true;
        opt.querySelector('.jqradio')?.classList.add('jqradio_choose');
      } else {
        input.checked = !input.checked;
        opt.querySelector('.jqcheck')?.classList.toggle('jqcheck_choose');
      }
    });
  </script>
</body></html>
```

**关键要求**：

- 判断题选项文本必须用 `对` / `错`（**不是** `正确`/`错误`），这是
  Codex 之前 review High-1 修过的同义词映射场景；用 `对/错` 才能验证
  `judgeOptionPolarity` 真的工作
- 每题选项**乱序**：原题库 answer 在 PDF 是 B，mock HTML 里可能在 D
- 题号显示在 field-label 里，格式 `1. 题干内容`（assist 用
  `inferNumber` 抓数字）

### Step 2：跑 benchmark

`scripts/benchmark_wjx_e2e_300.py`：

- 启 lookup_service（如果没启）：`python -m ctf_agents.knowledge.lookup_service ... &`
- 启本地 HTTP：`python -m http.server 8000 --directory build/ &`
- mock URL：`http://127.0.0.1:8000/wjx_300_mock.html`
- 调用 wjx_exam_assist 两轮：
  - 轮 1：headless（性能上限）
  - 轮 2：headed（真实赛中模式）
- 各轮命令模板：

```bash
node scripts/wjx_exam_assist.js \
  --url 'http://127.0.0.1:8000/wjx_300_mock.html' \
  --lookup-url 'http://127.0.0.1:8765/lookup_v2' \
  --no-submit \
  --headless \
  --log "logs/benchmark-wjx-e2e-headless-$(date +%Y%m%d-%H%M%S).jsonl"
```

- 对比期望：
  - 每题 expected_letters 从 mock HTML 内嵌的 `data-expected` 属性
    取（generate 时埋好），或从生成时的元数据 JSON 文件读
  - 总时间从 wrap script 的 `time.perf_counter()` 取
  - 准确率从 assist 的 JSONL 读 `auto_clicked` 和实际点击的字母

### Step 3：summary

写一个 `logs/benchmark-wjx-e2e-<ts>-summary.json`：

```json
{
  "run_id": "...",
  "n": 300,
  "headless": {
    "total_seconds": 187.4,
    "mean_per_question_ms": 624,
    "p99_per_question_ms": 1850,
    "auto_clicked": 287,
    "highlighted_only": 12,
    "no_action": 1,
    "accuracy_vs_expected": 0.99
  },
  "headed": { ... },
  "fail_qids": ["2020-tech-0588", ...]
}
```

控制台打印简洁版（mean / total / accuracy）。

### Step 4：回归

不要破坏既有：

```bash
bash scripts/preflight.sh                 # 期望 29/0/0
bash scripts/run_all_tests.sh             # 期望 ALL CHECKS PASSED
python scripts/benchmark_lookup_300.py    # Phase 1 仍能跑
```

新加的 benchmark **不进** `run_all_tests.sh`（chromium 启动太慢，不
适合每次回归）。需要时单独跑。

## 验收标准

完成 Phase 2 应该满足：

1. `python scripts/generate_wjx_300_mock.py --n 300 --seed 7` 输出
   `build/wjx_300_mock.html`，文件大小 > 100 KB（300 题 + DOM 模板）
2. 用浏览器打开 `build/wjx_300_mock.html`，能看到 300 道题，点击单选
   能切换、多选能多选、判断能选对/错
3. `python scripts/benchmark_wjx_e2e_300.py` 跑完输出 summary，含
   headless 和 headed 两组数据
4. headless 总耗时 **< 10 分钟**，headed **< 25 分钟**（合理上限）
5. 端到端准确率 **≥ 95%**（理想 99% 与 Phase 1 持平；headless 模式可
   能因为 timing 略低；如果显著低于 95% 说明 chromium 路径有 bug，回报）
6. 不破坏 `run_all_tests.sh` 的既有 ALL CHECKS PASSED

## 报告格式

跑完后给 Opus / 用户的报告应该包含：

- headless / headed 两组的 mean / total / accuracy
- 跟 Phase 1（4.3 ms / 99.3%）的差距分析：DOM 开销是多少 ms？哪些题
  Phase 1 对、Phase 2 错（说明 chromium 路径丢了什么信息）
- 任何遇到的工程问题（chromium 版本 / 字体缺失 / display unset 等）
- 如果端到端时间真的有担心（比如 headless 也要 30 分钟），单独提
  raise，由 Opus 决定是否要改 wjx_exam_assist（我倾向不改）

## 给 Codex 的下一步

1. 读这份 spec
2. 读 `docs/opus_next_handoff.md` §"问卷星真实 DOM 结论"
3. 跑 `python scripts/benchmark_lookup_300.py` 拿一遍 Phase 1 基线
4. 实现 Step 1 → Step 2 → Step 3
5. 跑 `bash scripts/run_all_tests.sh` 确认没破坏
6. 把 summary JSON 和工程感受报回来
