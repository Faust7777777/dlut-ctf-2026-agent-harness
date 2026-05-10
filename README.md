# 五一来的第一次 bad case 复盘

友人告知我大工的 CTF 赛事时，我其实几近无甚经历，却还是自信地准备了，agent 赛道也跟着我一路跌跌撞撞，harness 更是一改再改，终究不甚理想。若只说结果，那自然是坏；可若只说结果，倒也太轻了，像是拿一个不成形的影子去替代整块骨头。真正该复盘的，不是“我为什么输了”这一句空话，而是“我为什么会在一开始就把问题看歪”。

这次 bad case，最根本的错，不在于我做得不够快，也不在于我临场不够狠，而在于我把流程的完整，误认成了能力的完整。这个错认一旦成立，后面的很多动作便都顺理成章地歪了：我会更关心 prompt 写得像不像一个自治系统，提示词写得是否周全，记录链是否漂亮，提交链是否严谨，却没有先问一个更朴素的问题，系统到底有没有足够的 solver，能力入口到底有没有打开，router 只是理解题目还是只是在分词，sidecar 到底是可用能力还是文档里的摆设。等我把这些问题慢慢想清楚时，许多力气已经花在了低层次的流程装饰上。

## 1. 事情从哪里开始歪

事情最早是从一个很常见、也很容易让人掉进去的幻觉开始的：我看见一个结构清晰的 harness，里面有 supervisor，有 guard，有 validator，有 route-control，有 sidecar，有 prompt，有 runbook，就很自然地把它当成了一个“已经很像样”的自治求解系统。可结构像样，不等于能力像样；门禁完整，不等于解题完整；日志齐备，不等于路径齐备。真正去看代码时，才发现这个判断其实是站不住的。

`scripts/ai_contest_supervisor.py` 里默认真实 agent 只有 `misc` 和 `forensics`，见得很清楚；`ctf_agents/skill/router.py` 只是关键词路由，命中不了就落到 `misc`；`ctf_agents/skill/workflow.py` 对没注册的类别直接返回 `no_agent`；`configs/ai_contest.example.yaml` 里 `codex_sidecar` 和 `expert_sidecar` 又默认关闭。表面上看，它像是一个能覆盖六类题的系统，实际上它的真实 solver 面却很窄，更接近“solve-gated”的半自动流水线，而不是“solve-first”的全能求解器。

这个差别很要紧。因为当你以为自己面对的是一个能力宽的系统时，你会自然把重点放在流程、合同、审计、路由、门禁上；而当你知道自己面对的是一个能力窄的系统时，第一件事就应该是先补能力，再谈流程。可我当时的次序正好反了。

## 2. 为什么这次会把流程当成能力

我之所以会把流程当成能力，是因为我太容易被“可描述性”骗过去了。一个系统只要把事情讲得足够清楚，写得足够像样，分工足够齐整，人就很容易误以为它真的能做那些事。`docs/harness_route_control.md` 里把 route control 讲得很完整，什么 `current_family`、`tried_families`、`failure_type`、`evidence_delta_score`、`public_search`、`expert_review`、`persistent_lane` 都写出来了，听起来像一个成熟的路由系统；可那只是该有的方向，不是已经完成的能力。

问题就在这里。我把“应该有”与“已经有”混成了一回事。于是我在分析时就会下意识地往“怎么管”那边走，而不是往“能不能解”那边走。结果就是，我会更在意日志、状态机、条件分支、提交链有没有闭合，却没有先把 solver 供给、题型覆盖、最小验证路径这些最硬的东西看透。换句话说，我在看一个系统时，先盯住了皮肤和骨架的连接处，没先看心脏到底跳不跳。

这也是为什么我后来回头看 `CTF-Sandbox-Orchestrator` 时，差异会那么刺眼。那个 repo 的顺序是很明确的：先立总控入口，先建立沙盒假设，先证明一条最小路径，再按主导证据面下钻到 child skill；它不是把门禁放在最前面，而是把能力拓扑先铺开，再让路由去收敛[README.md:5](</tmp/CTF-Sandbox-Orchestrator/README.md:5>) [ctf-sandbox-orchestrator/SKILL.md:17](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/SKILL.md:17>) [router-matrix.md:7](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/router-matrix.md:7>)。而我这边的习惯却更像是反过来，先把 submit、guard、route-control、文档、合同都弄得越来越完整，再期待能力会自己长出来。

## 3. harness 到底差在哪里

真正的差，不是“少了一个判断条件”，也不是“某个 prompt 写得不够狠”，而是 topology 不对。这个词我现在觉得最贴切。所谓 topology，不是说文件多不多，也不是说文档厚不厚，而是说能力在系统里是怎么排布的，谁是入口，谁是下游，谁负责理解题目，谁负责执行，谁负责验证，谁负责替代，谁负责兜底。

`CTF-Sandbox-Orchestrator` 的 topology 很清楚：只有一个默认总控入口 `[ctf-sandbox-orchestrator/SKILL.md:8](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/SKILL.md:8>)`，所有 child skill 都是 downstream-only，且每个 child 都明确写了自己只能在总控已经建立 sandbox assumptions 之后再进入。它的路由矩阵也不是关键词糊一层，而是按主导证据面分类，web、runtime、agent/cloud、Windows、Reverse、Crypto 都各有自己的分支[router-matrix.md:17](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/router-matrix.md:17>)。这样一来，系统先解决“该去哪里”，再解决“进去以后怎么做”。

我的 harness 则不同。`ctf_agents/skill/router.py` 仍然只是一个小小的关键词分流器，`pwn`、`reverse`、`web`、`forensics`、`misc`，几个词撞一撞就完了；`workflow.py` 里如果 agent 没注册，直接 `no_agent`，并没有一个真正的下游能力网络去接住它；`ai_contest_supervisor.py` 里默认能干活的 agent 仍然只是 `misc` 和 `forensics`，其余类别大多只是被允许存在，却没有实际 solver stack 去承接。也就是说，我有一个很强的“把题目分开看”的壳，却没有一张足够宽的“把题目真正解开”的网。

这就是为什么它会显得弱。不是因为 guard 太强，也不是因为 submit 太严，而是因为真正能解题的部分太薄，薄到最后只能靠门禁、限频、冻结、证书、记录来维持系统表面的秩序。秩序是有的，进展却未必有；日志是漂亮的，答案却未必会自己出来。

如果把它和 `CTF-Sandbox-Orchestrator` 的能力形态放在一起看，这个差异会更明显。那个 repo 不是一个“先把控制面修好”的仓库，而是一个“先把可解题的能力图搭出来”的仓库。它先给出一个总控入口，再把 Web、Agent/Cloud、Windows、Reverse/Pwn、Crypto、Mobile 等域拆成独立的 downstream skill；每个 skill 都不抢总控入口，而是围绕一个主导证据面去补自己的那一块[README.md:15](</tmp/CTF-Sandbox-Orchestrator/README.md:15>) [README.md:21](</tmp/CTF-Sandbox-Orchestrator/README.md:21>) [ctf-sandbox-orchestrator/SKILL.md:24](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/SKILL.md:24>)。

它的强，不止在“分类多”，而在“每一类都有自己的最低能力单位”。Web skill 不是泛泛地说“这是个 web 题”，而是要求先看 entry HTML、boot scripts、route registration、hydration data、runtime config，再去抓 cookies、localStorage、sessionStorage、Cache Storage、service workers、真实请求顺序，最后才往 auth、worker、queue、upload、proxy headers 这些地方推进[web-api.md:1](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/web-api.md:1>)。这和我这边的习惯差得很远，因为我这边更容易先写 route-control、先写 guard、先写 submit，等到真正要落地解题时，才发现 solver 面并不够宽。

Agent/Cloud 的 skill 也是这样。它不是把“AI agent”当成一个口号，而是明确要求 map control stack：instruction layers、retrieval layers、memory layers、tool gates、auth material、side effects，要证明一条最小 exploit chain from untrusted content to model-visible instruction to tool side effect[agent-cloud.md:1](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/agent-cloud.md:1>)。这意味着它的重点不是“怎么把流程写漂亮”，而是“怎么证明一条最短的控制链真的存在”。而我这边的 harness，虽然也有 sidecar、validator、route-control，但更多还是在做治理，不是在证明一条真正的控制链或求解链。

Identity/Windows 的 skill 则体现了另一种能力：它不是抽象地说“有 AD、Kerberos、DPAPI、WinRM、SMB、RDP”，而是要求把 principal origin、token/ticket minting、group resolution、final consumer，一路串成可复现的 host -> artifact -> replay -> pivot -> capability 链[identity-windows.md:1](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/identity-windows.md:1>)。Reverse/Pwn 那边也一样，它先看 file type、headers、sections、imports、strings、entropy，再谈 loader、payload、config、post-decode behavior，明确分开 primitive、proof、crash offsets、register state、heap layout、protocol steps[reverse-native.md:1](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/reverse-native.md:1>)。这些 repo 的能力，不是“多做一些流程控制”，而是“每个域都有自己的最小判断单位和最小验证路径”。

反过来看我自己的 harness，它比较擅长的是把事情收束成统一的流程：route-control、提交链、guard、日志、证书、冻结、限频。这些东西当然重要，但它们属于控制面，不属于求解面。它没有像那个 repo 那样，把每个大类都拆成一个独立的 downstream skill，并为每个 skill 定义自己要看的证据、自己的最小路径、自己的默认边界、自己的证据优先级。于是我最后得到的是一个“很像竞赛系统”的系统，而不是一个“真的会解不同类型题”的系统。

这个差距，也正好解释了为什么我在需求分析时会偏。因为我在看自己的 harness 时，看到的是一个已经很完整的控制面，于是很自然地就继续去补控制面；而那个 repo 告诉我，真正该先补的是能力面。它先回答“每一类题该怎么证明一条路径”，再回答“怎么把这些路径组织起来”；我却先回答“怎么把所有路径管起来”，却迟迟没有把路径本身做厚。

## 4. 我的需求分析为什么会歪

回到我自己。我这次需求分析最明显的歪，是把“流程能否闭合”当成了“能力能否闭合”的前置条件，而不是反过来。按理说，做这类 harness 的需求分析，第一步应该是问：现有 solver 覆盖什么、缺什么、最小可验证路径在哪里、哪些题型只有理论接口没有实际实现。可我当时更容易问的是：prompt 怎么写才对、route-control 怎么补、guard 怎么不出错、提交链怎么更稳。这样问并非全错，但它天然偏向后半段，于是前半段最要命的事就被压住了。

更具体一点说，我把以下几件事混在了一起。

第一，我把文档承诺当成 runtime 事实。  
`docs/harness_route_control.md` 和相关 runbook 当然把 route-control 讲得很完整，像是在告诉你一个完整的系统蓝图；可蓝图不是房子。`codex_sidecar`、`expert_sidecar` 这些入口在配置里默认关闭，`scripts/ai_contest_supervisor.py` 对它们的行为也只是“如果开启则 ingest”，并没有把它们当作常规可用能力。也就是说，我当时如果只看 prompt 和文档，很容易误判这个系统“几乎什么都具备”，而实际代码并不支持这个结论。

第二，我把路由问题看得比能力问题更重。  
我很容易沉迷于“如何把题分得更准”“如何把 route-control 做得更细”“如何让 NO_CANDIDATE 更像证书”，因为这些东西都能被形式化、被记录、被写成漂亮的状态机。但真正决定系统强弱的，不是它会不会给失败做证，而是它有没有足够宽的 solver 面。一个只有少数真实 agent 的系统，再精密的路由也只是给窄路修栏杆。

第三，我把 review 理解得太表面了。  
真正的 review，不是把代码从头看一遍，而是要看控制流里的默认值、终止分支、降级路径、是否真的有对应能力。比如 `workflow.py` 里 `no_agent` 的终止意义是什么，`router.py` 的关键词命中率有没有足够的题型覆盖，`misc_real_agent` 能做的只是少数取证类技术，`ai_contest_supervisor.py` 默认注册范围又有多窄。若不先看这些，review 只是在读字，不是在判断系统。

第四，我把需求生成也交给了模型闭环。  
这件事才是更深的一层错。我的 harness 不是传统意义上先由人写完需求再交给 AI 实现，而是我先用一句很粗的需求起点开火，然后让 5.5 thinking 帮我整理，再让 5.5 pro 补充，最后把 5.5 pro 的内容直接复制成需求文档，再让 AI 按这个文档去 coding。这个链条的问题不在于用了 AI，而在于我把“需求生成”这一步也外包给了 AI，于是模型既是解释者，也是扩写者，最后还是实现者。它不会主动替我做真正的取舍，只会把它认为合理的东西越写越完整，越写越像一个自洽系统。

这就带来一个很典型的后果：模型最擅长写的部分，会被写得非常完整，比如状态机、路由、门禁、日志、sidecar、prompt、文档、流程；模型最难替你补的部分，反而会被悄悄压薄，比如 solver 覆盖、题型边界、最小可执行路径、真实运行时接口、默认关闭的能力入口。于是表面上看，整个 harness 越来越完整，实际上只是“叙述完整”和“结构完整”在增加，真正的解题能力却没有同比增长。也正因为如此，最后做出来的东西会天然偏向治理、偏向审计、偏向门禁，而不是偏向解题。

换句话说，我不是简单地用 vibe coding 写代码，我是把需求分析、需求扩写、需求落文档、按文档写代码，整条链都交给了模型去磨。这样一来，最初那个不够准确的直觉，经过 5.5 thinking 和 5.5 pro 的多轮转述，会变得越来越像一个“合理的产品方向”，直到连我自己都容易被它说服。它不是在纠正我的判断，而是在帮我的判断包装得更完整。

#### 4.1 需求生成链为什么会把问题越磨越滑

这一步很关键。因为问题并不是“我用了模型”，而是“我让模型承担了需求收敛的职责”。一开始你给出的只是一个方向，一点模糊的愿望，一个还没分清主次的题目，而 5.5 thinking 的工作，是把这些零散想法整理成像样的结构；5.5 pro 的工作，是把这个结构补全，让它读起来更完整、更成熟、更像一个可以交付的系统。到了这一步，文档会越来越顺，越来越没有空洞，越来越像一个经过认真设计的方案。

可偏偏“越来越顺”未必等于“越来越准”。模型最擅长做的事情，是把不清楚的表达变得清楚，把断裂的句子补成完整的段落，把零散的想法串成一个自洽的故事。它不擅长替你做那种真正刺耳的判断：这条需求是不是根本不该要，这个模块是不是其实不需要，这个能力是不是只是看起来高级，这个结构是不是只是为了让文档更好看。于是，经过两轮模型整理以后，很多原本还可以被推翻的东西，都会被磨成“看起来很合理”的东西。

这就是最危险的地方。因为一旦需求文档是模型扩写出来的，代码实现又继续交给同一类模型去做，系统就会沿着同一种统计偏好持续自我强化：喜欢把结构写得更完整，喜欢把职责切得更细，喜欢把路由做得更像路由，把守门做得更像守门，把审计做得更像审计。它会自然地偏向那些容易被文本化、容易被模块化、容易被状态机化的部分，而那些最需要人来拍板的东西，往往是被压缩得最轻的部分。

所以，回头看这次 harness，真正的问题不是某一个模块写歪了，而是需求最初的“歪”被模型放大、润色、结构化，最后变成了一份很难再直接质疑的需求文档。它看上去像我自己的判断，实际上已经掺进了很多模型默认的偏好；它看上去像“我已经想清楚了”，实际上只是“模型已经帮我把模糊想法说顺了”。

## 5. 为什么这次和 bad case 是同一个问题

如果把这次的需求分析歪掉和前面的 bad case 放在一起看，会发现它们其实是同一个根上的两种表现。bad case 是我把一个本来就偏窄的 harness，当成了一个只要补提示词和流程就能自洽的系统；需求分析歪掉，则是我在真正动手前，又一次把“系统设计得像样”误认成“系统能力足够”。一个发生在执行阶段，一个发生在分析阶段，但错的都是同一个判断：我高估了流程和门禁，低估了 solver 和能力入口。

这也解释了为什么我会在很长一段时间里，越做越忙，越忙越像在推进，最后却没有真正长出多少解题能力。因为我做的是一个容易显得正确的方向：补文档，补合同，补 state，补 guard，补 route，补审计，补日志。它们都重要，但它们是秩序，不是解题本身。若系统本来就没有足够的 solver，再多秩序也只是一个整齐的空壳。

## 6. 更贴近事实的复盘说法

以后复盘，不要再只说“我不如人”或者“我太懈怠”，那样太快，也太粗。更贴近事实的说法应该是这样的：

- 不是“我不行”，而是“我把流程的完整，误认成了能力的完整”。
- 不是“我没分析”，而是“我先分析了流程，后分析了能力”。
- 不是“harness 限制太多”，而是“harness 的 solver 面太窄，路由和门禁却太完整”。
- 不是“我不会 review 了”，而是“我没有先看控制流里的默认值和终止分支”。
- 不是“我太差”，而是“我把该先做的能力审计，做成了后做的流程润色”。

这些句子比“我很难过”“我很失败”更冷，也更有用，因为它们指向的是可以纠正的地方，而不是只剩态度的自责。

## 7. 下次该怎么纠正

下次做同类事情，起手式必须换掉。

第一步，不写 prompt，先做能力清单。  
把题型按域拆开，先问每一类题现在有没有真实 solver，最小验证路径是什么，依赖什么工具，缺什么能力，是否只是文档里写了，runtime 其实没开。只要这个表没写出来，就不要急着往下做路由、合同和 prompt。

第二步，不先补门禁，先补 solver。  
如果一个类别在代码里只有 `no_agent`，那就说明它不是“差一个配置”，而是“还没有可执行能力”。这时候去调 route-control、调 prompt、调 guard，往往只是把已有的窄路修得更漂亮，问题本身并不会因此消失。

第三步，把文档和 runtime 拆开看。  
文档是承诺，runtime 才是事实。只要 `codex_sidecar` 默认关闭，`expert_sidecar` 默认关闭，`router.py` 还是关键词路由，`workflow.py` 还会在没 agent 时直接收束，那就不能把这些东西当成已具备的能力去设计系统。

第四步，先证最小路径，再扩展。  
这是那个 repo 最值得学的地方。它不是先把大话说满，而是先建立一个可验证的最小路径，再把 child skill 一层层下钻。这样做的好处，是你不会在一开始就把自己骗进一个“看上去很完备”的错觉里。

第五步，给自己留一个反向检查。  
每次觉得系统已经很完整时，强迫自己回答一句：现在它到底能解什么？如果答案只能是“能记录、能分流、能守门”，那就说明求解能力还远远不够，不能再把“流程做好了”当成“问题解决了”。

## 8. 这次应该记住的核心判断

这次 bad case 和需求分析歪掉，本质上是同一条线上的两次失误：我先把流程和门禁做成了主角，再去期待能力会跟着出现；而事实是，能力没有先长出来，流程就算再漂亮，也只是在替空缺做包装。

所以以后真正该先问的，不是“怎么把系统做得更像一个完整的产品”，而是“它现在到底有没有足够的 solver，它的入口在哪里，它的路由是不是理解题目，它的 sidecar 是不是默认可用，它的 route-control 是在扩展解题，还是只是在给失败记账”。先把这几件事问清楚，再去写流程，才不会再把壳当成肉，把图纸当成房子。

## 9. 主需求与次需求，必须重新排

这次我还必须把一个判断钉死：**主需求只有一个，就是解题。** 更准确地说，是在限定时间内，找到一条能被验证的路径，产出 candidate、证据链、或者至少是可以清楚说明“为什么现在还不能解”的结果。除此之外，其他东西都只能算次需求。

次需求可以很多，甚至可以很漂亮。比如 route-control 可以更细，日志可以更全，状态机可以更严格，prompt 可以更统一，文档可以更整齐，sidecar 可以更好看，复盘可以更完整，输出风格可以更稳定。这些东西都重要，但它们的重要性必须服从主需求。它们的职责不是替代解题，而是服务解题；不是压缩 solver 面，而是在 solver 面成立之后，尽量把这条路走稳、走清、走可复现。

这条排序，我现在觉得必须写成硬规则：

- 主需求：解题
- 次需求：可复盘、可审计、可自动化、可统一风格、可补齐流程
- 允许失败的部分：所有次需求
- 不允许失败的部分：任何会伤到主需求的次需求

这里的边界也要说清楚。次需求失败不是问题，前提是它不堵最小路径、不关 solver 入口、不让题从“可解”变成“不可解”。如果 route-control 更漂亮，但它把真正的解题入口拖慢了；如果日志更完整，但它让 prompt、文档、实现都开始围着记录打转；如果 sidecar 更像一个“体系”，但默认又是关的，那这些次需求就不是失败得体，而是越界了。次需求可以失败，但不能反客为主。

把这个排序放到 `CTF-Sandbox-Orchestrator` 和你自己的 harness 上看，差别就很清楚。那个 repo 的主线始终是“先证明一条最小路径，再向外扩展”，它的 downstream skill 再多，也都围着主导证据面转[ctf-sandbox-orchestrator/SKILL.md:17](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/SKILL.md:17>) [router-matrix.md:7](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/router-matrix.md:7>)。它并不是说路由、报告、证据包装不重要，而是说这些东西都应该放在“已经能解”的前提下去做[reporting.md:1](</tmp/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/references/reporting.md:1>)。而我这边的问题，恰恰是把这些次需求做得太像主需求，最后把主需求本身挤薄了。

这也正好解释了为什么我会在 vibe coding 的链条里越走越远。因为一旦需求生成、需求扩写、文档整理、实现落地，整条链都交给同一类模型去磨，模型最容易做的，就是把次需求越磨越完整，把主需求却悄悄往后推。它会把一个本来只是“能不能解题”的问题，扩写成一个“流程是否完整、治理是否漂亮、路由是否齐整”的问题。于是我就会越来越像在经营一个系统，而不是在解决一类题。

所以以后无论再写 harness，还是再做别的 CTF 相关系统，我都应该把这句话钉在最前面：

> 主需求只有一个，就是解题。  
> 剩下的都是次需求。次需求可以失败，只要它们不伤主需求。

这个次序一旦写错，后面的很多工作都会偏。这个次序一旦写对，很多不必要的设计、文档、门禁、合同和流程，都会自动降级，不再抢主角。
