# Mini Agent

一个完全运行在终端中的最小可用 Agent：DeepSeek 自主决定直接回答或调用工具，Python Runtime 负责校验、执行、回填、持久化和终止控制。

当前验收结果：62 项默认离线测试与 5 项真实 DeepSeek smoke test 全部通过。

公开仓库：[Mikalate/mini-agent-demo](https://github.com/Mikalate/mini-agent-demo)

## 从零实现的边界

项目没有使用 LangChain Agent、LangGraph、OpenAI Agents SDK Runner、CrewAI 等 Agent 框架。以下部分由本项目自行实现：

- 消息组装、输出解析和显式 Agent Loop；
- ToolSpec、工具注册、参数校验、分发和错误包装；
- round/tool/token 预算、无进展检测和终止状态；
- SQLite session 隔离、恢复、待办和 run 持久化；
- Context 选择、完整回合压缩、滚动摘要和失败回退；
- 结构化事件、JSONL Trace 及 Rich/纯文本终端渲染。

第三方库只承担基础能力：`openai` 负责 DeepSeek 的 OpenAI 兼容 API 通信，`jsonschema` 校验工具参数，`rich` 渲染终端，`pytest` 运行测试；它们都不控制 Agent 的决策循环。

## 环境与安装

- Python 3.11 或更高版本；本项目已在 Python 3.13 验证。
- 可用的 DeepSeek API key。

Windows PowerShell：

~~~powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
~~~

## 一条命令启动

~~~powershell
python -m mini_agent chat --user user_a --session window_1
~~~

另开一个终端即可同时运行隔离的第二个 session：

~~~powershell
python -m mini_agent chat --user user_a --session window_2
~~~

CI 或不支持颜色的终端可添加 `--no-color`。

## 配置

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `DEEPSEEK_API_KEY` | 无 | 必填；只用于 API 客户端 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名 |
| `DEEPSEEK_THINKING` | `enabled` | `enabled` 或 `disabled` |
| `DEEPSEEK_REASONING_EFFORT` | `max` | thinking 启用时发送 |
| `DEEPSEEK_MAX_TOKENS` | `4096` | 单次 completion 上限 |
| `DEEPSEEK_TIMEOUT_SECONDS` | `60` | 单次请求超时 |
| `DEEPSEEK_MAX_RETRIES` | `3` | 429、500/503、连接/超时的总尝试上限 |
| `DEEPSEEK_PRICE_PER_1M_INPUT` | `0.27` | 费用估算：输入单价（美元/百万 token，缓存未命中） |
| `DEEPSEEK_PRICE_PER_1M_INPUT_CACHE_HIT` | `0.07` | 费用估算：缓存命中输入单价（美元/百万 token） |
| `DEEPSEEK_PRICE_PER_1M_OUTPUT` | `1.10` | 费用估算：输出单价（美元/百万 token） |
| `MAX_LLM_ROUNDS_PER_TURN` | `8` | 一次用户输入的模型决策轮数 |
| `MAX_TOOL_CALLS_PER_TURN` | `12` | 一次 run 的工具调用上限 |
| `MAX_PROTOCOL_ERRORS` | `2` | 空响应或损坏协议的纠正上限 |
| `MAX_CONSECUTIVE_TOOL_ERRORS` | `3` | 连续工具失败上限 |
| `MAX_REPEATED_CALLS` | `2` | 相同调用与结果的无进展阈值 |
| `MAX_TOTAL_TOKENS_PER_TURN` | `0` | 可选 token 硬预算；`0` 表示关闭 |
| `MAX_CONTEXT_TOKENS` | `12000` | 触发上下文压缩的 token 预算（打包的 DeepSeek tokenizer 估算） |
| `KEEP_RECENT_MESSAGES` | `12` | 压缩时至少保留的最近协议消息数 |
| `AGENT_DATA_DIR` | `.agent_data` | SQLite 与 run Trace 根目录 |

OpenAI SDK 的隐式重试固定关闭，Runtime 自己计数并记录每次实际退避。请求不传 `tool_choice`，保持模型自主选择直接回答或使用工具。

## 终端命令

| 命令 | 作用 |
|---|---|
| `/help` | 显示命令帮助 |
| `/tools` | 查看已注册工具和参数摘要 |
| `/sessions` | 列出当前用户的 sessions |
| `/new <session>` | 新建并切换 session |
| `/switch <session>` | 切换到当前用户已有的 session |
| `/history [n]` | 查看最近 n 条消息，不显示隐藏推理 |
| `/context` | 查看上下文字符数、未压缩消息数和摘要版本 |
| `/trace` | 查看最近一次 run 的 Trace 路径和可读摘要 |
| `/reset` | 再次输入当前 session 名后，仅清空该 session |
| `/exit` | 安全退出 |

也可以直接使用顶层命令：

~~~powershell
python -m mini_agent sessions --user user_a
python -m mini_agent history --user user_a --session window_1 --limit 20
python -m mini_agent trace --user user_a --session window_1
~~~

## 五个本地工具

| 工具 | 能力 | 数据来源/安全边界 |
|---|---|---|
| `calculator` | 受限算术表达式 | AST 白名单，不使用 `eval` |
| `search` | 搜索演示语料 | `data/search_corpus.json`，明确返回 `mock=true`，空结果返回推荐关键词 |
| `todo` | add/list/complete/delete | 身份由 ToolContext 注入，只操作当前 session |
| `weather` | 查询演示天气 | `data/weather_fixture.json`，未知城市/日期返回结构化错误（含可用城市列表） |
| `read_docs` | 读取白名单文档（md/txt） | `data/docs_index.json` 白名单，只读仓库内注册文件，未知 id 返回可用列表 |

search、weather 和 read_docs 不访问互联网，不会把 fixture 或本地文档结果描述成实时数据。

## Agent Loop

一次用户输入对应一个 run，一次模型请求对应一个 round，一次真实工具执行对应一个 tool step。

~~~text
创建/恢复 session，写入 user 消息
  → 构建或压缩 Context
  → 调用 DeepSeek（tools 始终由 Registry 动态导出）
  → Parser 判断 final / tool_calls / invalid
  → final：持久化回答并完成
  → tool_calls：保存完整 assistant 工具消息
      → Registry 校验工具名、JSON、Schema 和跨字段规则
      → 执行工具并用相同 tool_call_id 回填结果
      → 回到下一轮模型决策
~~~

只有“没有 tool_calls 且 content 非空”才算完成。工具错误会作为结构化 tool 消息回填，让模型有机会修正；达到 round、tool、token、协议、连续错误或重复无进展上限时返回明确的 `incomplete`，不会猜测一个看似成功的答案。Ctrl+C 对应 `interrupted`，已写入的数据仍保留。

## Session、Context 与 Memory

SQLite 使用 `(user_id, session_id)` 唯一键，所有消息、待办和 run 查询都同时携带这两个标识。todo 的身份不出现在模型参数 Schema 中，只能由 Runtime 注入。数据库启用 WAL，每次操作使用独立事务；重新启动并使用相同 user/session 即可继续聊天。

每次 LLM 调用的 Context 顺序为：

1. 稳定 system prompt；
2. 可选的 session 滚动摘要，标明为低优先级历史数据；
3. 最近未压缩的完整 role 消息；
4. 当前 run 内完整的 assistant tool_calls 与对应 tool 结果。

当序列化消息超过 `MAX_CONTEXT_TOKENS`（token 数由打包在 `mini_agent/llm/tokenizer/` 的 DeepSeek 官方 BPE tokenizer 离线估算）时，ContextManager 只压缩最早的、已经闭合的完整用户回合。assistant tool_calls、`reasoning_content` 和对应 tool 消息不可拆分，当前 run 永不压缩。摘要成功后，摘要更新与旧消息 `is_compressed=1` 在同一事务中提交，原始消息不会删除；失败时数据库不变，本轮使用旧摘要和最近完整回合安全回退。

Memory 的召回规则：

| 类型 | 召回时机 | 放置位置 |
|---|---|---|
| 最近对话 | 每次 LLM 调用 | 滚动摘要之后的原始 role 消息 |
| 滚动摘要 | session 有摘要时 | 独立 system memory 段 |
| 经验召回 | 本次 run 特征命中（最近错误码/工具序列） | 低优先级 system 段，模型不参与写入 |
| todo 实时状态 | 模型判断需要准确状态时 | 调用 todo 后的 tool 消息 |
| 工具 Schema | 每次 LLM 调用 | API 的 `tools` 参数 |
| Trace | 从不召回 | 只用于观察、复现和经验挖掘 |

摘要不是事实数据库；可变的待办状态始终以 todo 工具查询结果为准，摘要与最新消息冲突时以最新消息为准。

## Trace 与脱敏

每个 run 写入：

~~~text
.agent_data/runs/<run_id>/trace.jsonl
~~~

主要事件包括 `run_start`、`context_built`、`llm_call_start/end`、`assistant_decision`、`tool_call_start/end`、`context_compacted`、`retry`、`error` 和 `run_end`。Trace 可以复盘轮次、公开决策摘要、脱敏参数、工具结果、usage、耗时和终止原因。

`reasoning_content` 仅为 DeepSeek 活跃工具链的协议回传字段，可能持久化到 SQLite 以维持协议，但绝不进入终端、Trace、滚动摘要或最终回答。统一脱敏层会删除该字段并遮盖 API key、Authorization、口令类字段和常见密钥文本。Renderer 或 Trace Writer 失败由事件总线隔离，不影响 Agent Loop。

## 新增工具

1. 新建一个返回 `ToolSpec` 的模块，定义小写工具名、描述、JSON Schema 和 async handler。
2. Schema 使用 `type=object`、列出 `properties/required`，并设置 `additionalProperties=false`。
3. 如有跨字段约束，提供 `argument_validator`；不要只依赖 prompt。
4. handler 只使用验证后的参数和 Runtime 注入的 `ToolContext`，返回统一 `ToolResult`。
5. 在 `build_default_registry()` 中注册；Agent Loop 不需要修改。
6. 添加成功、非法参数、handler 错误和权限隔离测试。

## 测试

默认测试完全离线：

~~~powershell
python -m pytest -m "not live"
~~~

运行真实 DeepSeek smoke tests 前，把 key 放入当前进程环境变量，再执行：

~~~powershell
python -m pytest -m live
~~~

当前测试结果：

- 62 项离线测试通过，覆盖工具、Parser、Loop、Session、Context、Trace、预算、重试、费用估算、token 估算、流式累积、read_docs、自进化经验、rerank 和两个 session E2E；
- 5 项 live 测试通过，覆盖直接回答、calculator、search、read_docs、weather→todo、todo add/list 和 session 恢复；
- live 测试只断言结构和关键事实，不依赖完整自然语言措辞。

## 项目结构

~~~text
mini_agent/
  cli.py                 终端入口与 session 命令
  config.py              配置、预算和数据路径
  core/agent.py          手写 Agent Loop
  core/context.py        Context 选择与滚动摘要
  core/experience.py     跨 session 经验库（错题/经验召回与沉淀）
  core/parser.py         模型响应解析
  core/prompts.py        运行时 Prompt
  core/trace.py          事件、JSONL 与终端 Renderer
  llm/deepseek.py        DeepSeek API 适配器
  llm/tokenizer.py       打包 DeepSeek tokenizer 的离线 token 计数
  llm/tokenizer/         随包分发的 DeepSeek BPE 词表（离线使用）
  sessions/store.py      SQLite SessionStore
  tools/                 五个工具与 Registry
tests/                   unit / integration / e2e / live
data/                    本地 search/weather fixture
scripts/                 真实终端演示、窗口录制与脱敏回放
artifacts/               本地录屏成品（默认不提交 Git）
~~~

## 已知限制

- search 和 weather 是可重复的本地 mock，不是实时服务；
- read_docs 只读仓库内白名单注册的 md/txt（默认 readme/prompts/problem-solving/demo-notes）；wheel 打包后 `PROMPTS.md`、`PROBLEM_SOLVING.md` 不在包内（README 因 pyproject `readme` 字段会随包），读取失败会返回明确错误；
- Context 压缩阈值基于打包的 DeepSeek tokenizer 估算 token 数，但未计入 tools Schema 与 system 模板差异；费用基于 API usage 与可配置单价估算，默认单价是估算值，需按实际账单覆盖；
- 流式请求在接收过程中实时估算输出 token，达到 `MAX_TOTAL_TOKENS_PER_TURN` 剩余预算时提前断流；精确 usage 仍在流末尾返回；
- SQLite 适合本题的本地多终端演示，未做高并发压力测试；
- 中断后可继续 session，但不会从未完成工具调用的中间指令自动恢复执行；
- 数据库目前只有初始 schema，没有通用迁移框架；
- 流式输出已在传输层实现（后台流式累积，非逐字渲染）；向量检索、真实搜索或真实天气尚未实现，这些均属于 P1。

运行时 Prompt 见 [PROMPTS.md](PROMPTS.md)，关键工程问题见 [PROBLEM_SOLVING.md](PROBLEM_SOLVING.md)，录屏及复现方法见 [RECORDING.md](RECORDING.md)，逐项 AI Prompt 与成果记录见 [AI_PROMPT与问题解决记录.md](AI_PROMPT与问题解决记录.md)。完整功能演示录屏见 [artifacts/mini-agent-terminal-demo.mp4](artifacts/mini-agent-terminal-demo.mp4)（约 2 分钟，覆盖五个工具、多 session、压缩、read_docs、自进化经验与 Trace）。

