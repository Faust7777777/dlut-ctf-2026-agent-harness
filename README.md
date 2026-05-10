# 五一来的第一次 bad case 复盘

友人告知我大工的 CTF 赛事时，我几近无甚经历，却还是带着一种不知从何而来的自信准备了。agent 赛道一路跌跌撞撞，harness 也是一改再改，最后自然不甚理想。若只说一句“输了”，那当然也对，可它太轻，轻到像是拿一个结果替代了整个过程。真正值得复盘的，不是我为什么在比赛里没有跑出好结果，而是我为什么从一开始就把问题看歪了。

这次 bad case，表面上像是 harness 能力弱，提交链不够稳，sidecar 没有真正用起来，route-control 又显得过重。可把代码翻开以后，问题并不神秘。它不是某个函数突然坏了，也不是某个 prompt 少写了两句，而是我把流程的完整，误认成了能力的完整。这个错认一旦成立，后面的许多动作都会顺理成章地歪下去：我会更关心 supervisor 像不像一个自治系统，guard 是否足够谨慎，validator 是否能留下证据，runbook 是否看起来齐整，却没有先追问一个朴素得近乎粗暴的问题：它到底能不能解题。

现在回头看，我写下这份文档，不是为了把失败说得更漂亮。恰恰相反，我需要把它说得更硬一些。因为我想做产品经理，尤其是 AI 产品经理，那么这次就不能只被我归结为“工程能力不够”或“学习懈怠”。那些当然可能是真的，但还不够准。产品经理的第一件事不是把愿望写完整，而是把主需求排对；若这一步排错，后面的 coding 再勤奋，也只是在替一个歪掉的需求修边。

## 1. 壳先长出来了

事情最早是从一个很常见的幻觉开始的。我看见一个结构清楚的 harness，里面有 supervisor，有 guard，有 validator，有 route-control，有 sidecar，有 prompt，有 runbook，就很自然地把它当成了一个“已经像样”的自治求解系统。可结构像样，不等于能力像样；门禁完整，不等于解题完整；日志齐备，也不等于路径齐备。

真正去看代码时，这个判断其实站不住。[scripts/ai_contest_supervisor.py](scripts/ai_contest_supervisor.py) 里默认真实注册的 agent 主要是 `misc` 和 `forensics`；[ctf_agents/skill/router.py](ctf_agents/skill/router.py) 仍然是关键词路由，命中不了就落到 `misc`；[ctf_agents/skill/workflow.py](ctf_agents/skill/workflow.py) 对没注册的类别会直接返回 `no_agent`；[configs/ai_contest.example.yaml](configs/ai_contest.example.yaml) 里的 `codex_sidecar` 和 `expert_sidecar` 又默认关闭。也就是说，表面上它像是一个能覆盖多类题的系统，实际上真实 solver 面很窄，更接近一个 solve-gated 的半自动流水线，而不是 solve-first 的求解器。

这个差别极要紧。若你以为自己面对的是能力宽的系统，自然会把精力放在流程、合同、审计、路由、门禁上；若你知道自己面对的是能力窄的系统，第一件事就该是先补 solver，再谈流程。我当时的次序正好反了。我先把壳看得太真，于是后面就不断修壳。

## 2. 我被可描述性骗了

我之所以会把流程当成能力，是因为我太容易被“可描述性”骗过去。一个系统只要把事情讲得足够清楚，分工足够齐整，文档足够像样，人就很容易误以为它真的已经具备那些能力。[docs/harness_route_control.md](docs/harness_route_control.md) 里把 route-control 写得很完整，`current_family`、`tried_families`、`failure_type`、`evidence_delta_score`、`public_search`、`expert_review`、`persistent_lane` 等字段摆在那里，读起来像一个成熟的路由系统。可那只是“应该有”的方向，不是“已经有”的事实。

我把这两者混成了一回事。于是分析时，我会下意识地往“怎么管”那边走，而不是往“能不能解”那边走。我关心日志、状态机、条件分支、提交链有没有闭合，却没有先把 solver 供给、题型覆盖、最小验证路径这些最硬的东西看透。说得难听一点，我先摸了骨架的形状，却没有先听心脏是不是在跳。

这也是我后来再看 [CTF-Sandbox-Orchestrator](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator) 时，差异会显得刺眼的原因。那个 repo 的顺序很清楚：先有总控入口，先建立 sandbox assumptions，先证明一条最小路径，再按主导证据面下钻到 child skill。它不是把门禁摆在最前面，而是先把能力拓扑铺开，再让路由收敛。我的习惯恰好反过来，先把 submit、guard、route-control、文档、合同做得越来越完整，再期待能力会自己长出来。

能力不会自己长出来。

## 3. 差距不在文件数量

真正的差，不是少一个判断条件，也不是某个 prompt 写得不够狠，而是 topology 不对。这里说的 topology，不是文件多不多，文档厚不厚，而是能力在系统里如何排布：谁是入口，谁是下游，谁理解题目，谁执行，谁验证，谁替代，谁兜底。

`CTF-Sandbox-Orchestrator` 的 topology 很明确。[ctf-sandbox-orchestrator/SKILL.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/SKILL.md) 里只有一个默认总控入口，child skill 都是 downstream-only，而且每个 child 都是在总控已经建立 sandbox assumptions 以后才进入。它的 [router-matrix.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/references/router-matrix.md) 也不是拿关键词糊一层，而是按主导证据面分类，web、runtime、agent/cloud、Windows、reverse、crypto 等各有自己的分支。这样一来，系统先回答“该去哪里”，再回答“进去以后怎么做”。

我的 harness 则不同。[ctf_agents/skill/router.py](ctf_agents/skill/router.py) 仍只是一个小小的关键词分流器，`pwn`、`reverse`、`web`、`forensics`、`misc`，几个词撞一撞就完了；[ctf_agents/skill/workflow.py](ctf_agents/skill/workflow.py) 如果没有对应 agent，直接 `no_agent`；[scripts/ai_contest_supervisor.py](scripts/ai_contest_supervisor.py) 默认能干活的 agent 又偏少。换言之，我有一个很强的“把题目分开看”的壳，却没有一张足够宽的“把题目真正解开”的网。

这就是它显得弱的原因。不是 guard 太强，也不是 submit 太严，而是真正能解题的部分太薄。薄到最后只能靠门禁、限频、冻结、证书、记录来维持系统表面的秩序。秩序是有的，进展却未必有；日志是漂亮的，答案却不会因为日志漂亮就自己出现。

把对照再拉细一点，差距更明显。`CTF-Sandbox-Orchestrator` 不是一个“先把控制面修好”的仓库，而是一个“先把可解题的能力图搭出来”的仓库。它先给一个总控入口，再把 Web、Agent/Cloud、Windows、Reverse/Pwn、Crypto、Mobile 等域拆成独立的 downstream skill；每个 skill 都不抢总控入口，而是围绕自己的主导证据面补能力。

比如 [web-api.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/references/web-api.md) 不是泛泛地说“这是个 web 题”，而是要求看 entry HTML、boot scripts、route registration、hydration data、runtime config，再去抓 cookies、localStorage、sessionStorage、Cache Storage、service workers、真实请求顺序，最后才往 auth、worker、queue、upload、proxy headers 这些地方推进。它的强，不在“分类多”这一点上，而在每一类都有最低能力单位。

[agent-cloud.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/references/agent-cloud.md) 也是如此。它不是把“AI agent”当成一个口号，而是要求 map control stack：instruction layers、retrieval layers、memory layers、tool gates、auth material、side effects，并证明一条从 untrusted content 到 model-visible instruction 再到 tool side effect 的最小链路。它关心的不是流程看起来是否漂亮，而是最短控制链是否真的存在。

[identity-windows.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/references/identity-windows.md) 和 [reverse-native.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/references/reverse-native.md) 也提供了同样的启发。前者会把 principal origin、token/ticket minting、group resolution、final consumer 串成可复现的 host -> artifact -> replay -> pivot -> capability 链；后者先看 file type、headers、sections、imports、strings、entropy，再谈 loader、payload、config、post-decode behavior。它们不是在“多做流程控制”，而是在每个域里写清楚最小判断单位和最小验证路径。

反过来看我的 harness，它擅长的是把事情收束到统一流程：route-control、提交链、guard、日志、证书、冻结、限频。这些并非无用，只是它们属于控制面，不属于求解面。我的需求分析偏偏把控制面看得太重，于是最后得到的是一个很像竞赛系统的系统，而不是一个真的会解不同类型题的系统。

## 4. 需求怎样把代码带偏

我这次需求分析最明显的歪，是把“流程能否闭合”放到了“能力能否闭合”前面。按理说，做这类 harness 的第一步应该是问：现有 solver 覆盖什么，缺什么，最小可验证路径在哪里，哪些题型只有理论接口而没有实际实现。可我当时更容易问的是：prompt 怎么写才对，route-control 怎么补，guard 怎么不出错，提交链怎么更稳。这样问并非全错，但它天然偏向后半段，于是前半段最要命的事情被压住了。

我把文档承诺当成了 runtime 事实。`docs/harness_route_control.md` 和 runbook 像是在告诉我一个完整系统的蓝图，可蓝图不是房子。`codex_sidecar`、`expert_sidecar` 这些入口在配置里默认关闭，[scripts/ai_contest_supervisor.py](scripts/ai_contest_supervisor.py) 对它们的行为也只是“如果开启则 ingest”，并没有把它们当作常规可用能力。若只看 prompt 和文档，我当然容易误判这个系统“几乎什么都具备”；实际代码并不支持这个结论。

我也把路由问题看得比能力问题更重。如何把题分得更准，如何把 route-control 做得更细，如何让 `NO_CANDIDATE` 更像证书，这些东西都能被形式化、被记录、被写成漂亮的状态机。可真正决定系统强弱的，不是它会不会给失败做证，而是它有没有足够宽的 solver 面。一个只有少数真实 agent 的系统，再精密的路由也只是给窄路修栏杆。

还有 review。真正的 review，不是把代码从头扫一遍，而是看控制流里的默认值、终止分支、降级路径、以及这些路径背后是否真有对应能力。比如 `workflow.py` 里的 `no_agent` 到底意味着什么，`router.py` 的关键词命中率是否足以覆盖题型，`misc_real_agent` 能做的是不是主要还是少数取证类技术，`ai_contest_supervisor.py` 默认注册范围到底多窄。若不先看这些，review 只是在读字，不是在判断系统。

## 5. Vibe coding 的放大效应

这次还多了一层更麻烦的东西：我的 harness 不是传统意义上先由人写完需求，再交给 AI 实现。我是先用一句很粗的需求起点开火，然后让 5.5 thinking 帮我整理，再让 5.5 pro 补充，最后把 5.5 pro 的内容复制成需求文档，再让 AI 按这个文档去 coding。问题不在于用了 AI，而在于我把“需求生成”这一步也外包给了 AI。

于是模型既是解释者，也是扩写者，最后还是实现者。它不会主动替我做真正刺耳的取舍：这条需求是不是根本不该要，这个模块是不是其实不需要，这个能力是不是只是看起来高级，这个结构是不是只是为了让文档更完整。它更擅长把不清楚的表达变得清楚，把断裂的句子补成完整段落，把零散想法串成一个自洽故事。文档因此越来越顺，越来越像一个成熟方案。

可“越来越顺”未必等于“越来越准”。

一旦需求文档是模型扩写出来的，代码实现又继续交给同一类模型去做，系统就会沿着同一种偏好自我强化。模型容易把状态机、路由、门禁、日志、sidecar、prompt、文档、流程写得很完整；它难以替我补的是 solver 覆盖、题型边界、最小可执行路径、真实运行时接口、默认关闭的能力入口。于是表面上看，harness 越来越完整，实际上只是叙述完整和结构完整在增加，解题能力没有同比增长。

这才是 vibe coding 在这里真正危险的地方。它不只是帮我写代码，也帮我把模糊判断包装成了一份很难再质疑的需求文档。最初那个不够准确的直觉，经过 5.5 thinking 和 5.5 pro 的多轮转述，会越来越像一个合理的产品方向，直到连我自己都被它说服。它不是在纠正我的判断，而是在帮我的判断变得更像判断。

## 6. Bad case 本来就是需求问题

所以这里不能写成“需求分析歪掉”和“bad case”是两个相似问题。它们不是两个问题。bad case 本来就是需求分析失真一路传导下来的结果。

我的 harness 坏掉，不是因为某个函数偶然写错了，也不是因为某个 guard 偶然太严了，而是因为一开始对主需求的定义就不够硬。我没有把“解题”钉成唯一主线，反而让流程、路由、门禁、日志、sidecar、复盘这些次需求获得了过高的位置。需求一旦这样排错，coding 自然会跟着排错。

更贴近事实的因果链应当这样写：需求分析是上游，coding 是中游，比赛里的 bad case 是下游。上游把“系统设计得像样”误认成“系统能力足够”，中游便会优先实现那些看起来像系统的东西，下游自然得到一个控制面完整、solver 面偏薄的 harness。若把它拆成“分析阶段一个错、执行阶段一个错”，反而冲淡了真正的关系。

这也解释了我为什么会在很长一段时间里越做越忙，越忙越像在推进，最后却没有长出多少解题能力。因为我做的是一个容易显得正确的方向：补文档，补合同，补 state，补 guard，补 route，补审计，补日志。它们都重要，但它们是秩序，不是解题本身。系统本来没有足够 solver，再多秩序也只是整齐的空壳。

## 7. 主需求只能有一个

这次我必须把一个判断钉死：主需求只有一个，就是解题。更准确地说，是在限定时间内找到一条能被验证的路径，产出 candidate、证据链，或者至少清楚说明为什么现在还不能解。除此之外，其他东西都只能算次需求。

次需求当然可以很多。route-control 可以更细，日志可以更全，状态机可以更严格，prompt 可以更统一，文档可以更整齐，sidecar 可以更像体系，复盘可以更完整，输出风格可以更稳定。它们都重要，但重要性必须服从主需求。它们的职责不是替代解题，而是服务解题；不是压缩 solver 面，而是在 solver 面成立以后，把这条路走稳、走清、走可复现。

这条排序以后要写成硬规则：

- 主需求：解题。
- 次需求：可复盘、可审计、可自动化、可统一风格、可补齐流程。
- 允许失败的部分：所有不伤主需求的次需求。
- 不允许失败的部分：任何会堵住最小求解路径的设计。

次需求失败不是问题，前提是它不堵最小路径，不关 solver 入口，不让题从“可解”变成“不可解”。如果 route-control 更漂亮，但拖慢了真正的解题入口；如果日志更完整，但让 prompt、文档、实现都开始围着记录打转；如果 sidecar 更像一个体系，但默认又是关的，那么这些次需求就不是失败得体，而是反客为主。

把这个排序放回 `CTF-Sandbox-Orchestrator` 和我的 harness 上看，差别很清楚。那个 repo 的主线始终是先证明一条最小路径，再向外扩展；downstream skill 再多，也都围着主导证据面转。它并不是说路由、报告、证据包装不重要，而是把这些东西放在“已经能解”的前提下去做，[reporting.md](https://github.com/GALIAIS/CTF-Sandbox-Orchestrator/blob/main/ctf-sandbox-orchestrator/references/reporting.md) 也是这个意义上的下游工作。我的问题，恰恰是把次需求做得太像主需求，最后把主需求本身挤薄了。

## 8. 下次怎么纠正

下次再做同类事情，起手式必须换掉。

先做能力清单，不先写 prompt。把题型按域拆开，问每一类题有没有真实 solver，最小验证路径是什么，依赖什么工具，缺什么能力，是否只是文档里写了而 runtime 没开。只要这个表没写出来，就不要急着补路由、合同和 prompt。

先补 solver，不先补门禁。一个类别在代码里只有 `no_agent`，就说明它不是差一个配置，而是还没有可执行能力。此时去调 route-control、调 prompt、调 guard，只是把已有的窄路修得更漂亮，问题本身不会因此消失。

把文档和 runtime 拆开看。文档是承诺，runtime 才是事实。只要 `codex_sidecar` 默认关闭，`expert_sidecar` 默认关闭，`router.py` 还是关键词路由，`workflow.py` 还会在没 agent 时直接收束，就不能把这些东西当成已具备的能力去设计系统。

先证最小路径，再扩展。这是 `CTF-Sandbox-Orchestrator` 最值得学的地方。它不是先把大话说满，而是先建立一条可验证的最小路径，再把 child skill 一层层下钻。这样做，至少不会一开始就把自己骗进一个“看上去很完备”的错觉里。

最后，要给自己留一个反向检查。每次觉得系统已经很完整时，强迫自己回答一句：它现在到底能解什么？如果答案只能是“能记录、能分流、能守门”，那就说明求解能力还远远不够，不能把流程做好了当成问题解决了。

## 9. 我该记住什么

以后复盘，不要再只说“我不如人”或者“我太懈怠”。那样太快，也太粗。更贴近事实的说法应当是：我把流程完整误认成能力完整；我先分析了流程，后分析了能力；我没有先看控制流里的默认值和终止分支；我把本该先做的能力审计，做成了后做的流程润色。

这些句子比“我很失败”更冷，也更有用。它们指向的是可以纠正的地方，而不是只剩态度的自责。

如果把这次复盘压成一句话，那就是：我先把流程和门禁做成了主角，再去期待能力会跟着出现；而事实是，能力没有先长出来，流程再漂亮，也只是在替空缺做包装。以后无论再写 harness，还是再做别的 CTF 相关系统，我都应该把这句话放在最前面：

> 主需求只有一个，就是解题。
> 剩下的都是次需求。
> 次需求可以失败，只要它们不伤主需求。

这个次序一旦写错，后面的很多工作都会偏。这个次序一旦写对，许多不必要的设计、文档、门禁、合同和流程，都会自动降级，不再抢主角。
