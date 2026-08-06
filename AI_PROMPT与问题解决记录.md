# AI Prompt 与问题解决记录

本文档对应《光辰笔试.md》第 16 节“实施顺序”，覆盖 12 个实施步骤和 3 个独立检查节点。每一项都包含可复用的 AI Prompt、实际成果和问题解决记录。

## 全局执行与记录规则

1. 进入第 n 步前，重新阅读第 16 节的第 n 项，再阅读该项列出的全部参考章节，然后实施。
2. 非检查节点之间连续推进，不为每个小步骤单独停测；到达独立检查节点后集中验收并停止，等待用户明确要求后再继续。
3. 每完成一个实施步骤或检查节点，立即更新本文档中对应的“成果”和“问题解决记录”，不得把计划写成已完成成果。
4. 问题解决记录统一使用“问题 → 观察证据 → 尝试方案 → 最终选择 → 结果/剩余风险”的结构。
5. 不记录 API key、Authorization、完整 `reasoning_content` 或隐藏思维链；只记录可公开的决策、证据和工程取舍。
6. 保持从零实现边界：第三方库只承担 API 通信、Schema 校验、终端展示和测试，不使用第三方 Agent Runner 或预制 Agent 工作流。

## 1. 项目骨架、配置与 SQLite

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 1 步“项目骨架、配置与 SQLite”。开始前重新阅读本步骤，并阅读第 2 节“技术选型与运行方式”、第 3 节“代码结构”、第 4 节“核心数据结构与状态划分”和第 8 节“Session 管理与隔离”。

从空目录建立 Python 3.11+ 项目：创建 pyproject、约定的包目录、环境变量加载、chat/sessions/history/trace CLI 子命令骨架、SQLite schema 和 SessionStore。用 (user_id, session_id) 作为 session 唯一键，所有消息查询同时带两个标识，启用事务、外键、busy timeout 和 WAL。持久化 sessions、messages、todos、runs，并保存工具协议所需字段。缺少或非法 API 配置时给出明确且不泄密的修复提示。

完成后应能创建两个隔离 session，分别写入和读取消息，重新打开数据库后数据仍然存在。不要使用第三方 Agent 框架，也不要提前实现后续步骤的复杂功能。
~~~

### 成果

- 状态：已完成。
- 建立了 `mini_agent` 包、职责分层目录、`pyproject.toml`、`.env.example`、CLI 骨架和基础说明文档。
- 实现了依赖最小的 `.env` 加载与配置校验；锁定了当前环境中可验证的 `openai`、`jsonschema`、`rich` 和 `pytest` 版本。
- 建立 `sessions/messages/todos/runs` 四张表及索引，启用 SQLite 外键、WAL、事务回滚和锁等待。
- `SessionStore` 支持 session 创建、恢复、列表、消息读写和 run 状态持久化，所有会话访问都以 `user_id + session_id` 隔离。
- 缺少 `DEEPSEEK_API_KEY` 时，CLI 会提示复制 `.env.example` 并配置密钥，不输出任何敏感值。

### 问题解决记录

- 问题：项目目录最初只有方案文档，没有可运行代码或依赖清单。
- 观察证据：目录扫描只发现《光辰笔试.md》；当前 Python 为 3.13.7，所需依赖已安装但版本各异。
- 尝试方案：按方案章节逐项拆分目录和持久态，先检查本机可用依赖版本，再建立项目文件。
- 最终选择：使用标准库承担控制流和 SQLite，第三方库仅负责 API、Schema、终端和测试，并在 `pyproject.toml` 锁定已验证版本。
- 结果/剩余风险：项目可以启动和持久化 session；数据库迁移目前只有初始 schema，后续若修改表结构需补充迁移策略。

## 2. ToolSpec、Registry 与四个本地工具

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 2 步“ToolSpec、Registry 与四个本地工具”。开始前重新阅读本步骤，并阅读第 6 节“工具注册机制与统一契约”、第 8 节“Session 管理与隔离”和第 11 节“异常处理与安全边界”。

实现统一的 ToolSpec、ToolContext、ToolResult 和 ToolRegistry。Registry 必须拒绝重名和非法工具名，验证 JSON Schema，导出 DeepSeek Tool Calls 所需 Schema，并在执行前完成工具白名单、参数 Schema 和跨字段校验。统一捕获工具错误、记录耗时、限制结果长度，绝不动态导入或执行未知工具。

实现 calculator、search、todo、weather：calculator 使用 AST 白名单且禁止 eval；search 和 weather 只读仓库 fixture 并明确标记 mock；todo 支持 add/list/complete/delete，user_id 和 session_id 只能由 ToolContext 注入。所有工具应可脱离 LLM 独立调用，未知工具和非法参数不得触发 handler，todo 必须严格隔离 session。
~~~

### 成果

- 状态：已完成。
- 建立统一的 `ToolSpec`、`ToolContext`、`ToolResult`、结构化错误和 `ToolRegistry`。
- 注册表支持工具名与 Schema 启动校验、LLM tools 导出、参数校验、跨字段校验、异步分发、异常包装和结果截断。
- calculator 使用 AST 白名单，只允许受限算术运算，并限制表达式长度、AST 节点数、指数和结果范围。
- search/weather 使用固定本地 JSON 数据，结果明确包含 `mock=true`，不声称访问互联网。
- todo 的身份只来自运行时 Context，数据操作同时受用户和 session 约束。

### 问题解决记录

- 问题：既要让模型灵活提出工具调用，又必须保证未知工具、恶意表达式和跨 session 参数永远不能执行。
- 观察证据：题目要求模型自主决策，但模型输出只是非可信 JSON；todo 若接受模型提供身份会破坏隔离。
- 尝试方案：分别依赖 prompt 约束、在 handler 内校验、在统一注册表执行前校验。
- 最终选择：以 ToolRegistry 作为唯一执行入口，先做白名单、JSON Schema 和跨字段校验；身份不进入工具 Schema，由 ToolContext 注入；calculator 使用 AST 递归求值。
- 结果/剩余风险：非法请求会得到结构化错误且 handler 不执行；搜索和天气目前是演示数据，真实网络能力属于可选增强。

## 3. DeepSeek API 适配器与输出 Parser

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 3 步“DeepSeek API 适配器与输出 Parser”。开始前重新阅读本步骤，并阅读第 2 节“技术选型与运行方式”、第 7 节“模型协议、输出解析与提示词”和第 11 节“异常处理与安全边界”。

使用锁定版本的 OpenAI Python SDK 接入 DeepSeek Chat Completions。显式配置 base_url、model、thinking、reasoning_effort、max_tokens 和 timeout，关闭 SDK 隐式重试并实现有限、可计数的错误分类与退避。不得传 tool_choice，使模型保持 auto 决策；不得在适配器中执行工具。

把响应归一化为 response id、model、完整 assistant message、finish_reason 和 usage。确保工具调用 assistant 消息中的 reasoning_content 能被原样保留并在下一轮协议请求中回传，但不得进入终端、trace、摘要或最终回答。Parser 应区分 final、tool_calls、invalid/retry，归一化 tool call，解析 arguments JSON；length/content_filter 等不完整响应不得执行工具。
~~~

### 成果

- 状态：已完成。
- 实现 `LLMClient` 协议、`LLMResponse/LLMUsage/LLMError` 和 `DeepSeekClient`。
- 请求保持 `tool_choice=auto`（通过不传该字段实现），thinking 启用时发送 `reasoning_effort`，SDK 隐式重试关闭。
- 对 429、连接/超时、500/503 和不可重试 HTTP 错误进行分类，并采用有限退避。
- 响应保留 `finish_reason`、usage 细分和 DeepSeek 扩展的 `reasoning_content`。
- Parser 支持最终回答、工具调用、损坏 JSON、空响应、截断/过滤和资源不足等分支。

### 问题解决记录

- 问题：`reasoning_content` 是 DeepSeek 工具调用链的协议字段，但 SDK 序列化行为可能因版本不同而丢失扩展字段，同时该内容又不能展示或写入 trace。
- 观察证据：方案明确要求第二轮完整回传该字段；仅依赖 `model_dump()` 存在扩展字段被遗漏的风险。
- 尝试方案：只读取标准序列化结果，或同时检查消息属性与 Pydantic extra fields。
- 最终选择：先调用 `model_dump(exclude_none=True)`，再显式从 `reasoning_content` 属性或 `model_extra` 补回；后续上下文只对含 tool_calls 的 assistant 消息回传该字段。
- 结果/剩余风险：离线适配器测试和真实 API 工具闭环均证明该字段可以保留；如果未来升级 SDK，需要重新运行专项兼容性测试。

## 4. 手写 Agent Loop

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 4 步“手写 Agent Loop”。开始前重新阅读本步骤，并阅读第 4 节“核心数据结构与状态划分”、第 5 节“完整 Agent Loop”、第 7 节“模型协议、输出解析与提示词”、第 10 节“轮次预算、终止与无进展检测”和第 11 节“异常处理与安全边界”。

从零实现显式 Agent Loop，不使用第三方 Agent Runner。一次用户输入创建一个 RunState 和 run 记录，写入用户消息并从当前 session 组装上下文。每轮调用 LLM 后累计 attempt、成功调用和 token usage；只有无 tool_calls 且 content 非空时才正常完成。

工具调用时必须先保存并追加完整 assistant 工具消息，再按返回顺序处理全部 tool_calls；每个真实结果或结构化错误都用完全一致的 tool_call_id 作为 role=tool 消息回填，然后继续下一轮。支持同轮多个工具、多轮连续工具、损坏参数后修正、未知工具和工具错误。达到 round/tool/协议错误/连续工具错误上限或遇到致命 API 错误时，持久化明确的 incomplete 结果，绝不假报成功。
~~~

### 成果

- 状态：已完成。
- 实现独立的 `Agent.run_turn()`，连接 RunState、SessionStore、ContextManager、LLMClient、Parser 和 ToolRegistry。
- 支持直接回答、一次工具、同轮多工具、多轮连续工具以及工具错误后由模型修正。
- assistant 工具消息与每个 role=tool 结果严格按 `tool_call_id` 对齐，并把完整工具链持久化到 SQLite。
- 实现 round、tool、协议错误和连续工具错误的基础上限，以及 completed/incomplete 状态和真实用量统计。
- CLI `chat` 已接入真实 Agent Loop，可恢复指定 session 后进行多轮输入。

### 问题解决记录

- 问题：损坏的 `function.arguments` 仍然需要保留原始 tool call id 并向模型回填错误，但它无法构造成正常的已校验 ToolCall。
- 观察证据：如果直接丢弃损坏调用，模型无法知道哪次调用失败；如果强行执行，则违反“先验证后执行”。
- 尝试方案：把整条 assistant 响应判为无效，或把“调用提案”和“可执行调用”分成两层。
- 最终选择：增加 `ParsedToolCall`，同时保存 id、name、原始 arguments、可选 ToolCall 和 ParseIssue；JSON 损坏时不执行 handler，但仍用同一 id 回填结构化错误。缺少 id/type/name 的不可配对消息则整体判为 invalid。
- 结果/剩余风险：FakeLLM 已验证损坏 JSON 后修正、除零后修正和多工具路径；更完整的重复无进展检测安排在第 8 步。

## 检查节点 1：核心闭环

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节“检查节点 1：核心闭环”。只在连续完成第 1～4 步后开始。

补齐并运行 SessionStore、工具注册表、四个工具、Parser、DeepSeek 请求封装和 Agent Loop 的核心离线测试。必须覆盖两个 session 的持久化隔离、未知工具和非法参数不执行、todo 隔离、直接回答、一次工具、同轮多个工具、多轮工具、工具错误后修正、损坏 arguments 后修正、tool_call_id 对齐和 reasoning_content 回传。

随后使用真实 DeepSeek API 运行 calculator 闭环：模型提出工具调用，本地得到 42，再把真实结果回填并由模型生成最终回答。验证第二次请求保留首轮 assistant 工具消息及 reasoning_content，且 API key 不进入代码、数据库或输出。验收通过或确认具体阻塞后停止，不得开始第 5 步。
~~~

### 成果

- 状态：已通过，并已停在本检查节点。
- 核心离线测试结果：17 项全部通过。
- 真实 API 测试结果：DeepSeek 自主提出 calculator 调用，本地计算得到 42，模型随后生成包含 42 的最终回答。
- 第二次请求中的 assistant tool_calls、对应 tool_call_id 和 reasoning_content 原样回传检查通过。
- 用户提供的 `APIkey.txt` 只在测试进程中临时读取，已加入 `.gitignore`，未打印或写入数据库。

### 问题解决记录

- 问题：第一次到达检查节点时，环境中没有 `.env` 或 `DEEPSEEK_API_KEY`，无法完成题目要求的真实 API 验收。
- 观察证据：只检查配置是否存在时，两项结果均为 false；离线测试不受影响。
- 尝试方案：先完成全部离线验收并准确报告外部条件；用户随后提供单独的密钥文件。
- 最终选择：把密钥文件加入忽略列表，只在单次 PowerShell 测试进程中读入环境变量，新增可跳过的 live pytest 用例，不把密钥复制到源码或测试数据。
- 结果/剩余风险：离线与真实闭环均一次通过；密钥文件仍位于本地工作区，提交前检查节点必须再次确认它未被纳入仓库。

## 5. Trace 与终端 Renderer

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 5 步“Trace 与终端 Renderer”。开始前重新阅读本步骤，并阅读第 11 节“异常处理与安全边界”、第 12 节“终端交互与展示效果”和第 13 节“Trace 与可观察性”。

实现与 Runtime 解耦的结构化事件、JSONL TraceWriter 和 Rich/Plain 两种终端 Renderer。实时展示 run、round、公开决策摘要、工具名、已脱敏参数、执行结果、错误、usage 和终止状态；不得展示 API key、Authorization 或 reasoning_content。每个 run 的 trace 应能还原轮次和终止原因，写入或渲染失败不得改变 Agent 控制流。补充 Ctrl+C/EOF 的 interrupted 记录和 run 汇总。
~~~

### 成果

- 状态：已完成。
- 新增结构化 `TraceEvent`、容错 `EventBus`、逐行落盘的 `JSONLTraceWriter`，每个 run 写入独立的 `.agent_data/runs/<run_id>/trace.jsonl`。
- 实现 `RichRenderer` 与 `PlainRenderer`；终端可实时看到模型轮次、公开决策摘要、工具参数与结果、错误、耗时、token 和 run 汇总。
- Agent Loop 已产出 run、context、LLM、决策、工具、重试、错误和结束事件；trace 可还原轮次、工具调用及终止原因。
- 统一脱敏层会删除 `reasoning_content`，遮盖 API key、Authorization、口令类字段及常见密钥文本；渲染器或 Writer 故障由事件总线隔离，不改变控制流。
- 异步任务被取消时会把 durable run 标记为 `interrupted`，保留已经写入的消息和工具结果。

### 问题解决记录

- 问题：终端展示和 JSONL 需要消费同一批运行事实，但任何展示/落盘故障都不能拖垮 Agent，且隐藏推理和密钥不能经由通用事件数据泄露。
- 观察证据：Agent Loop 原先只有返回值和 SQLite 状态，无法实时观察；直接记录模型原始响应会包含 `reasoning_content`，直接在各分支打印又会让执行逻辑与 UI 紧耦合。
- 尝试方案：在 Loop 内零散打印、只写日志文件，或先生成统一事件再分发给多个独立消费者。
- 最终选择：建立 best-effort EventBus，所有事件在序列化和渲染前经过同一脱敏函数；JSONL Writer 始终启用，Rich/Plain Renderer 由终端注入，每个 sink 单独捕获异常。
- 结果/剩余风险：现有 Agent Loop 回归用例全部通过，trace 与 UI 已接通；API 适配器内部每一次退避的细粒度事件将在第 8 步补齐，完整脱敏测试在第 9 步集中执行。

## 6. 多 session 终端交互

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 6 步“多 session 终端交互”。开始前重新阅读本步骤，并阅读第 8 节“Session 管理与隔离”和第 12 节“终端交互与展示效果”。

完善 chat REPL 和 /help、/tools、/sessions、/new、/switch、/history、/context、/trace、/reset、/exit 等命令。切换和恢复 session 时始终使用当前 user_id；/reset 必须二次确认且只能清空当前 session。确保两个终端同时使用同一用户的不同 session 时消息、待办、上下文和 trace 不串线，关闭后用相同 user/session 重开可以续聊。
~~~

### 成果

- 状态：已完成。
- chat REPL 已支持 `/help`、`/tools`、`/sessions`、`/new`、`/switch`、`/history`、`/context`、`/trace`、`/reset` 和 `/exit`。
- session 切换始终固定当前 `user_id`；`/switch` 只允许已有 session，`/new` 显式创建，关闭后以相同 user/session 启动会自动恢复。
- `/reset` 要求用户再次输入当前 session 名，仅在一个数据库事务内清空该 session 的消息、待办、run 和摘要，不影响同用户或其他用户的其他 session。
- `/history`、`/context` 和 `/trace` 不展示隐藏推理；顶层 `sessions/history/trace` 子命令也已可用。
- SQLite 的查询、切换、重置和最近 run 定位全部同时使用 `(user_id, session_id)`，保留 WAL 与独立连接，支持两个终端并行使用。

### 问题解决记录

- 问题：一个 REPL 内可切换 session，但不能让命令参数或进程级历史导致跨用户/跨 session 读取；同时 `/reset` 是破坏性操作。
- 观察证据：原 CLI 只在启动时固定 session，只有 `/exit`；SessionStore 的基础读写已隔离，但缺少重置和最近 trace 查询接口。
- 尝试方案：切换时重建整个 Runtime，或保持无状态 Agent 并只改变每次 `run_turn` 显式传入的 session。
- 最终选择：Agent/ContextManager 不保存当前会话全局变量，REPL 只维护 `active_session`；所有 Store API 继续显式接收 user/session。重置采用“再次输入 session 名”的确认方式，并在单事务中限定 session 主键。
- 结果/剩余风险：CLI 编译、SessionStore 与 Agent Loop 基础回归通过；两个终端的完整隔离与恢复场景在第 9 步和检查节点 2 集中验证。

## 7. ContextManager 与滚动摘要

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 7 步“ContextManager 与滚动摘要”。开始前重新阅读本步骤，并阅读第 4 节“核心数据结构与状态划分”、第 9 节“Context 选择、Memory 与基础压缩”和第 11 节“异常处理与安全边界”。

在 ContextManager 中实现字符预算、最近消息窗口、滚动摘要、事务更新和摘要失败回退。以完整用户回合和闭合工具链为最小裁剪单位，绝不能拆开 assistant tool_calls 与对应 tool 消息，也不能保留缺失 reasoning_content 的活跃工具链。当前 run 不压缩；摘要只包含经过限制的历史事实、最终回答和未完成事项，不包含 raw reasoning 或 trace。用低阈值测试验证压缩触发、最近消息保留和关键事实召回。
~~~

### 成果

- 状态：已完成。
- `ContextManager.prepare()` 会在每个新 run 的首次决策前检查序列化字符预算；未超限时保持原始协议顺序。
- 超限时按完整用户回合分组，始终保留满足 `KEEP_RECENT_MESSAGES` 的最近完整回合，只压缩状态已闭合且不属于当前 run 的连续旧回合。
- 摘要请求使用固定四小节 Prompt；输入只含用户文本、公开回答、受限工具调用/结果和旧摘要，不含 `reasoning_content`、trace、凭据或内部状态。
- 摘要与旧消息 `is_compressed=1` 标记在同一 SQLite 事务中更新；原消息不删除，滚动摘要以独立 system memory 段放在最近消息之前。
- 摘要 API 或事务失败时保留数据库原状，本次请求确定性地只使用旧摘要与最近完整回合，并记录 `CONTEXT_COMPACTION_FAILED`；若活跃工具链本身仍超预算，则以 `MAX_CONTEXT_REACHED` 明确结束。

### 问题解决记录

- 问题：简单按消息条数裁剪会拆开 assistant tool_calls、tool 结果和 DeepSeek 协议所需的 `reasoning_content`；直接摘要当前 run 又可能破坏尚未结束的工具链。
- 观察证据：消息表已保存 run_id 和运行状态，因此可以区分已闭合历史与当前活跃 run；最近 N 条协议消息可能落在同一工具链中间。
- 尝试方案：按单条消息裁剪、按固定字符截断，或按从 user 开始的完整回合分组并结合 run 状态选择。
- 最终选择：以完整回合为压缩原子，从最新回合向前累计最近窗口；只压缩最早的连续闭合前缀。摘要源重新构造成安全语义文本，绝不读取隐藏推理；成功后事务提交，失败只做本次内存回退。
- 结果/剩余风险：原有 Loop 与持久化回归仍通过；低阈值触发、失败回退和工具链不可拆测试将在第 9 步补齐。字符数是 P0 近似，精确 tokenizer 属于可选增强。

## 8. 预算、重试与无进展检测

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 8 步“预算、重试与无进展检测”。开始前重新阅读本步骤，并阅读第 10 节“轮次预算、终止与无进展检测”、第 11 节“异常处理与安全边界”和第 13 节“Trace 与可观察性”。

完善最大 LLM round、工具调用、可选 token、协议错误、连续工具错误和重复调用预算。为相同工具名、规范化参数和结果生成稳定签名，检测重复无进展；将 API 尝试次数、成功调用数和 usage 分开计数。对 429、500、503、连接和超时执行有限指数退避，对 400/401/402/403/422 等致命错误停止。所有终止分支必须记录统一错误码和真实 incomplete 说明，不能无限循环或伪装完成。
~~~

### 成果

- 状态：已完成。
- round、tool、协议错误、连续工具错误和 context 预算均有硬上限；新增可选 `MAX_TOTAL_TOKENS_PER_TURN`，为 0/未配置时关闭，配置后按 API usage 停止后续决策。
- 对“工具名 + 排序后的参数 + 去除耗时后的结果”生成 SHA-256 稳定签名；同一签名达到 `MAX_REPEATED_CALLS` 时以 `NO_PROGRESS` 停止。
- API 适配器对 429、连接/超时及 500/503 使用有限指数退避，并在每次实际等待前实时产出 attempt、错误码和延迟事件；致命 HTTP 错误不重试。
- RunState 分开累计 API attempts、成功 LLM 调用、prompt/completion/total tokens、连续工具错误和最高重复次数。
- token、round、tool、协议、重复、工具错误、致命 API、中断、context 和 SQLite 失败均返回明确的 completed/incomplete/interrupted 状态与统一错误码，不由 Runtime 猜测最终答案。

### 问题解决记录

- 问题：仅比较工具名和参数会把“同样查询但数据已变化”误判为无进展；把 ToolResult 整体序列化又会因 `duration_ms` 每次不同而永远检测不到重复。
- 观察证据：ToolResult 的业务数据和错误决定是否取得进展，耗时只属于观察层；todo 等实时结果即使参数相同也可能变化。
- 尝试方案：只比较调用参数、比较完整 ToolResult，或规范化业务结果后生成稳定摘要。
- 最终选择：签名包含工具名、排序参数、ok/data/error/truncated，排除 duration；按整个 run 计数，达到配置阈值立即持久化 `NO_PROGRESS`。API 重试则由 transport 在实际退避点回调结构化事件，执行层只统计 attempts。
- 结果/剩余风险：现有适配器、Loop 和持久化回归通过；预算、重复、退避与异常终止的专项测试在第 9 步集中补齐。token 硬预算只能在服务返回 usage 后判断，这是非流式 API 的固有限制。

## 9. 自动测试与真实 API 验收

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 9 步“自动测试与真实 API 验收”。开始前重新阅读本步骤，并阅读第 14 节“测试策略与测试用例”和第 15 节“端到端验收矩阵”。

系统补齐工具、Registry、Parser、Agent Loop、SessionStore、ContextManager、Trace、预算和异常路径的离线测试；构建两个并行 session 的 E2E 场景，并把真实 DeepSeek 测试单独标记为 live，默认测试不得依赖网络。真实测试至少覆盖直接回答、calculator、weather→todo 连续调用和多 session 恢复。检查 trace、终端和仓库内容中没有 API key、Authorization 或完整 reasoning_content。
~~~

### 成果

- 状态：已完成。
- 默认离线测试扩展为 35 项，覆盖工具/Registry、Parser、Agent Loop、SessionStore、ContextManager、Trace、预算、API 重试与两个 session 的 E2E；运行结果为 35 passed，且不需要网络。
- 新增 Trace 脱敏与 sink 故障隔离、完整工具链压缩、摘要失败回退、重复无进展、round/tool/token/协议预算、致命 API、中断、SQLite 双连接和定向 reset 等专项用例。
- 两个 session 的 E2E 使用并发 Agent turn，覆盖 weather→todo、多 session 各自 list、关闭后重建 SessionStore 再追问，以及历史、待办和 trace 不串线。
- live 测试保持 `@pytest.mark.live` 独立标记，共四个场景：直接回答、calculator、search，以及组合的 weather→todo、todo add→list、多 session 恢复；四项均使用真实 DeepSeek API 通过。
- live 与离线测试均检查 trace 不含 API key 和完整 `reasoning_content`；真实 key 只由验收命令从本地文件临时注入进程环境。

### 问题解决记录

- 问题：默认回归必须稳定、免费且无网络，但题目同时要求证明模型会真实地自主选择直接回答或多种工具；真实模型输出又不能按整句断言。
- 观察证据：检查节点 1 已证明 calculator 最小闭环，但尚未覆盖 Trace、Context、预算、多 session 和其他真实工具路径。
- 尝试方案：所有测试都调用真实 API、完全依赖 FakeLLM，或把确定性控制流回归与少量结构化 live smoke 分层。
- 最终选择：35 项默认测试只使用 FakeLLM 和本地 fixture，精确断言状态、消息协议、数据库及 trace；四项 live 只断言工具名、调用次数、关键事实、持久化结果和 session 隔离，不断言完整措辞。
- 结果/剩余风险：离线 35 项与真实 4 项全部通过；真实测试仍受外部服务可用性和费用影响，因此默认跳过并单独执行。

## 检查节点 2：完整功能回归

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节“检查节点 2：完整功能回归”。只在连续完成第 5～9 步后开始。

一次性运行默认离线测试、两个 session 的 E2E 场景和全部真实 API smoke tests，集中验证终端展示、JSONL trace、脱敏、上下文压缩、session 隔离与恢复、预算终止、重复无进展、API 重试和异常恢复。确认计划内 P0 功能和关键异常路径全部通过。验收完成后停止并汇报；只有用户明确要求继续时，才能进入第 10 步。
~~~

### 成果

- 状态：已通过，并已停在本检查节点。
- 默认测试：35 passed，4 个 live 用例按标记跳过；离线总耗时约 4 秒。
- 两个 session E2E：并发写入、weather→todo、各自待办查询、重建 Store 后历史追问及 Trace 事件链全部通过。
- 真实 API：4 passed，覆盖直接回答、calculator、search、weather→todo、todo add/list 与 session 恢复。
- Context：低阈值压缩、工具链不可拆、摘要脱敏、事务提交和失败回退通过。
- 预算与异常：round/tool/token、协议错误、重复无进展、连续错误、API 重试/致命错误和 Ctrl+C 取消路径通过。
- 安全复核：真实密钥在 `APIkey.txt` 之外的工作区文本中命中 0 项；`APIkey.txt` 已被 `.gitignore` 忽略；测试中的 live trace 已断言不含 key 或完整隐藏推理。
- 按全局规则未开始第 10 步，等待用户明确继续。

### 问题解决记录

- 问题：需要一次验收同时覆盖确定性异常分支和真实模型行为，又不能让密钥或隐藏推理出现在日志和测试输出中。
- 观察证据：离线回归可以精确验证所有控制流；真实场景更适合验证模型是否按 Schema 自主使用工具。工作区内测试 Trace 位于 pytest 临时目录，因此另由 live 用例在运行当下读取并检查。
- 尝试方案：在一个测试命令内混跑全部场景，或先跑默认回归再仅对 live marker 临时注入密钥。
- 最终选择：先执行默认测试并确认 35 项通过，再从 `APIkey.txt` 只读取到单次进程环境中运行四个 live 用例，finally 清除环境变量；最后仅以命中数量扫描工作区，避免把密钥打印到命令输出。
- 结果/剩余风险：检查节点 2 全部通过，无阻断第 10～11 步的问题；外部 API 将来可能因服务状态产生临时失败，但重试与离线回归已分层处理。

## 10. README、Prompt 与问题解决记录

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 10 步“README、Prompt 与问题解决记录”。开始前重新阅读本步骤，并以第 1～15 节的实际实现为准，重点阅读第 1、2、5～9、13、14 节。

根据最终代码完善 README：运行方式、配置、系统设计、Agent Loop、工具扩展、session 隔离与恢复、context/memory 的召回时机和放置方式、trace 与脱敏、测试命令、限制和后续改进。确保 PROMPTS.md 与运行时代码一致，PROBLEM_SOLVING.md 说明关键问题、证据、尝试、选择和结果。同时复核并整理本《AI Prompt 与问题解决记录》，确保第 16 节每个已执行条目都有真实 Prompt、成果和问题解决记录，不包含敏感数据或隐藏思维链。
~~~

### 成果

- 状态：已完成。
- `README.md` 已按真实实现重写，覆盖从零实现边界、安装与一条命令启动、全部环境变量、终端命令、四个工具、Agent Loop、终止条件、Session 隔离与恢复、Context/Memory、Trace 脱敏、工具扩展、测试结果和已知限制。
- `PROMPTS.md` 已补齐运行时 system prompt、滚动摘要 system prompt 和 user template，并通过脚本确认三段文本与 `mini_agent/core/prompts.py` 一致。
- `PROBLEM_SOLVING.md` 已整理九个实际工程问题，逐项说明问题、证据、选择和已验证结果，不包含隐藏思维链。
- 本《AI Prompt 与问题解决记录》保留第 16 节每个条目的 Prompt、成果和问题解决记录；已执行项目均由占位状态更新为实际结果。
- CLI 帮助命令按 README 验证可运行；完整干净环境安装将在检查节点 3 执行。

### 问题解决记录

- 问题：原三份文档仍是第一阶段占位内容，而计划文档包含部分“建议实现”，如果直接复制会把尚未实现的 P1 或预期行为写成事实。
- 观察证据：当前代码已经加入 Trace、Context、预算和完整 CLI，测试结果也从最初 17 项变为离线 35 项与 live 4 项；摘要 Prompt 此前没有进入 PROMPTS.md。
- 尝试方案：只增补原简短 README，或以最终代码、配置、命令和检查节点 2 证据为唯一事实来源进行完整重写。
- 最终选择：重写 README/PROMPTS/PROBLEM_SOLVING；明确区分 P0 已实现、P1 未实现和本地 mock，并用运行时常量自动核对 Prompt 文本。
- 结果/剩余风险：文档与当前代码及测试结果一致；公开仓库地址、录屏文件和最终提交状态需要在第 11 步完成后补充。

## 11. 终端录屏与最终提交

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 11 步“终端录屏与最终提交”。开始前重新阅读本步骤，并阅读第 11 节“异常处理与安全边界”、第 12 节“终端交互与展示效果”、第 13 节“Trace 与可观察性”和第 15 节“端到端验收矩阵”。

按第 18 节脚本录制纯终端演示，覆盖真实 LLM 调用、直接回答、工具链、两个 session 隔离与恢复、context/trace 和测试。准备可访问的公开代码仓库。录制和提交前检查 API key、.env、APIkey.txt、运行数据库、WAL、trace 和本地缓存均未泄露或误提交；终端可见工具调用和公开决策摘要，但不得显示 reasoning_content。
~~~

### 成果

- 状态：已完成。
- 已新增 `scripts/demo_recording.py` 与 `scripts/run_demo.ps1`，使用真实 DeepSeek 依次演示工具注册、直接回答、calculator、weather→todo、两个 session 隔离、进程内 Store/Agent 重建后的历史追问、滚动摘要、Trace 与离线测试。
- 已新增 `scripts/record_demo.ps1` 作为 Windows Terminal 窗口捕获入口；因本机 Windows Terminal GPU 合成导致 `gdigrab` 成片黑屏，新增 `scripts/render_recording.py`，只基于真实演示产生的公开消息、工具事实、摘要和脱敏 Trace 生成纯终端回放。
- 最终成品为 `artifacts/mini-agent-terminal-demo.mp4`：H.264、1600×900、约 95 秒、约 7 MB；已抽帧确认中文、Context、Trace 和 `35 passed, 4 deselected` 结尾正常。
- `.gitignore` 已覆盖 `APIkey.txt`、`.env`、`.agent_data/`、数据库/WAL、Trace、Python 缓存与录屏二进制；录屏脚本仅临时读取 key，结束时清除环境变量。
- pytest 已改用项目内 `--basetemp`，复验结果为 `35 passed, 4 deselected in 3.60s`，不再访问无权限的系统临时目录。
- 已创建公开仓库 [Mikalate/mini-agent-demo](https://github.com/Mikalate/mini-agent-demo)，本地 Git 已初始化并形成首个安全提交；远端 `main` 的完整实现提交为 `44747b2312050887adb694e1ce81b1f5857d1ad3`。
- 远端共发布 51 个经扫描的源码、测试、fixture、文档和演示脚本文件；`APIkey.txt`、`.env`、`.agent_data/`、数据库/WAL、Trace 与 MP4 均未进入源码提交。

### 问题解决记录

- 问题：Windows PowerShell 5 对无 BOM 中文脚本解析异常；带空格的窗口标题被拆分为错误的程序名；Windows Terminal 的 GPU 合成又导致按窗口录制得到黑屏；默认 pytest 还因用户临时目录权限失败。
- 观察证据：先后出现脚本语法错误、`系统找不到指定的文件`、可播放但全黑的 MP4，以及 `PermissionError: pytest-of-Lenovo`；与此同时真实 DeepSeek 演示数据、Trace 和会话数据库均已正常产生。
- 尝试方案：将 PowerShell 控制消息改为 ASCII 并使用无空格内部标题；尝试 Windows Terminal 与传统控制台窗口捕获；在确定 GPU 合成无法被本机 `gdigrab` 正确捕获后，改由真实演示产物生成终端回放；pytest 改用项目内独立临时目录。
- 最终选择：保留可复现的真实演示脚本和窗口录制入口，同时以脱敏终端回放作为本机最终成片；回放代码明确不读取 `reasoning_content`，并抽检首段、中段、Context 与收尾画面。
- 追加问题：本机 `git-remote-https.exe` 在凭据流程中崩溃，Git Credential Manager 又遇到 TLS 证书链错误，GitHub CLI 的设备登录也发生 TLS 握手超时。
- 追加处理：保留本地提交，改用当前已认证的 GitHub 连接逐个创建内容 blob、完整 tree 和带父提交的 commit，再以非强制快进更新远端 `main`；随后用不带凭据的 `git ls-remote` 确认公开分支可读。
- 结果/剩余风险：本地录屏、测试和公开源码仓库均已完成；MP4 保留为本地交付物，若招聘方要求可直接访问的视频 URL，仍需将该文件上传到其指定网盘或视频平台。

## 检查节点 3：提交前检查

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节“检查节点 3：提交前检查”。只在连续完成第 10～11 步后开始。

严格按 README 在干净环境安装和启动，复核代码仓库与录屏链接可访问，确认关键真实 API 冒烟场景可运行，检查自动测试结果仍适用于最终代码。扫描提交内容，确保没有 API key、.env、APIkey.txt、运行数据库、WAL、敏感 trace 或完整 reasoning_content。若检查节点 2 后未修改运行代码，不机械重复全部测试；只执行必要的最终确认。全部通过后记录最终交付状态并停止。
~~~

### 成果

- 状态：已通过；第三阶段到此停止，不进入第 12 步可选增强。
- 在新建的 `.agent_data/checkpoint3_venv` 干净虚拟环境中，严格按 README 执行 `python -m pip install -e ".[test]"`，依赖安装和 editable package 构建成功。
- 使用该干净环境运行 `python -m mini_agent --help`，CLI 正常显示 `chat/sessions/history/trace` 四个入口。
- 使用项目内 `--basetemp` 运行最终离线回归：`35 passed, 4 deselected in 2.58s`。
- 只重复一个必要的真实 DeepSeek calculator 冒烟场景：`1 passed, 3 deselected in 3.54s`；key 仅临时注入子进程并在 finally 中清除。
- 公开仓库为 [Mikalate/mini-agent-demo](https://github.com/Mikalate/mini-agent-demo)；无凭据读取 `main` 得到提交 `44747b2312050887adb694e1ce81b1f5857d1ad3`，证明仓库和分支可公开访问。
- 最终录屏 `artifacts/mini-agent-terminal-demo.mp4` 已确认 H.264、1600×900、94.667 秒、7,308,951 字节；抽帧内容正常，视频内 API key 字节命中为 0。
- 远端 51 个发布路径中禁止项为 0；提交前对真实 key 的精确扫描命中为 0，未发布 `.env`、`APIkey.txt`、数据库/WAL、敏感 Trace、运行缓存或完整隐藏推理。

### 问题解决记录

- 问题：检查节点需要同时验证干净安装、Windows 临时目录权限、真实 API、公开访问、录屏和远端内容安全；本机原生 Git 凭据链路还存在独立故障。
- 观察证据：第一次在受限网络中安装构建依赖超时；此前默认 pytest 使用系统 Temp 时出现拒绝访问；原生 Git 推送产生 `git-remote-https.exe` 崩溃弹窗，但公开仓库的无凭据读取和 GitHub 已认证连接正常。
- 尝试方案：为 pytest 指定项目内 `--basetemp`；允许干净 venv 仅下载 `pyproject.toml` 声明的依赖；Git 发布先尝试 GCM、GitHub CLI 和 SSH，均失败后使用已认证连接构造完整 Git tree/commit。
- 最终选择：不降低 TLS 校验、不把 token 写入命令或文件、不强推；以项目内临时目录完成测试，以远端非强制快进完成发布，并分别验证远端 SHA、路径白名单和密钥零命中。
- 结果/剩余风险：检查节点全部通过。Windows 本机的 Git HTTPS 凭据程序仍需用户日后单独修复，但不影响本次公开仓库、源码内容或最终验收结果。

## 12. 可选增强

### AI Prompt

~~~text
执行《光辰笔试.md》第 16 节第 12 步“可选增强”。开始前重新阅读本步骤，并阅读第 1 节 P1 范围、第 2 节“技术选型与运行方式”、第 6 节“工具注册机制与统一契约”、第 9 节“Context 选择、Memory 与基础压缩”和第 10 节“轮次预算、终止与无进展检测”。

只有全部 P0 功能和提交检查都通过后，才根据剩余时间选择流式 token、真实天气/搜索、精确 token 估算、更复杂 memory 检索或并发压力测试。不得为了可选增强破坏现有稳定闭环。每实现一项，只测试受影响功能并运行必要核心回归；未实现的增强如实写入 README，不新增固定检查节点。
~~~

### 成果

- 状态：待执行，且不影响 P0 交付。

### 问题解决记录

- 若实际实施某项增强，再按统一结构补充；未实施则记录为明确的范围取舍。

## 13. 后续改动

本节记录 P0 提交之后，用户在新一轮会话中要求的后续改动。每项都包含正式 Prompt、成果与问题解决记录，沿用第 1-12 节的结构。

### 13.1 费用估算（已知限制 3-B）

#### AI Prompt

~~~text
用户决定在已有提交基础上继续增强：按 3B → 3A → 4 的固定顺序处理 README“已知限制”中的第 3、4 条。本次只做 3-B“费用估算”：基于 API 返回的 usage 与可配置单价，为每次 run 计算估算费用，并写入 run_end Trace 与终端输出；不得改动 Agent Loop 的决策逻辑，不得引入第三方计费依赖；默认单价必须是可覆盖的估算值，并明确标注；同步更新 .env.example、README 与离线测试。
~~~

#### 成果

- 状态：已完成。
- `Settings` 新增三个可配置单价：`DEEPSEEK_PRICE_PER_1M_INPUT`（默认 0.27）、`DEEPSEEK_PRICE_PER_1M_INPUT_CACHE_HIT`（默认 0.07）、`DEEPSEEK_PRICE_PER_1M_OUTPUT`（默认 1.10），单位均为美元/百万 token，标注为估算值。
- `RunState` 新增 `cost_usd`；`Agent._accumulate_usage` 统一累计 prompt/completion/total token 与费用，输入部分区分缓存命中与未命中，缓存字段缺失时按未命中保守估算。
- `run_end` Trace 事件新增 `cost_usd` 字段；终端 run 摘要行显示 `cost: $X.XXXXXX`。
- `.env.example` 配置表、README 配置表与已知限制第 3 条同步更新。
- 新增 3 项离线测试（三价累计、缓存字段缺失回退、零 usage 零费用）；全部 38 项离线测试通过。

#### 问题解决记录

- 问题：已知限制第 3 条实际包含两个独立子问题——“Context 用字符预算近似 token”和“没有精确费用估算”，且用户询问“改成流式请求是否就能顺便解决”。
- 观察证据：非流式响应自带精确 usage，费用估算只缺单价表与累计逻辑；字符→token 近似发生在请求发出前的上下文构建阶段；流式只影响响应侧传输，对这两者均无帮助。
- 尝试方案：先做结论分析（流式不能解决 3），再把 3-B 与 3-A 拆开独立实施，费用估算先行。
- 最终选择：费用基于 API usage 三类 token（缓存命中/未命中/输出）乘以可配置单价累计，零第三方依赖；默认单价取 DeepSeek 常见计价的估算值并允许覆盖。
- 结果/剩余风险：38 项离线测试通过；默认单价可能偏离模型实际账单，需用户按账单覆盖；缓存字段缺失时按未命中保守估算，可能略微高估费用。3-A（token 精确估算）与 4（流式）尚未开始。

### 13.2 上下文 token 精确估算（已知限制 3-A）

#### AI Prompt

~~~text
继续 3B 之后的第 3-A 项：把 ContextManager 的上下文压缩判据从“字符预算近似”替换为“token 预算精确估算”。用户已选定方案 B：下载 DeepSeek 官方 HF tokenizer 的 tokenizer.json，打包进仓库，用 tokenizers 库离线加载（不得在运行时联网下载）。要求：新增可复用的 token 计数模块与打包文件；配置项从 MAX_CONTEXT_CHARS 升级为 MAX_CONTEXT_TOKENS（默认 12000）；同步更新 Agent、CLI、demo 脚本、pyproject 打包配置、.env.example、README 与全部相关测试；保持离线测试全部离线通过。若模型词表与打包文件不一致，如实记录为剩余风险，不得声称完全精确。
~~~

#### 成果

- 状态：已完成。
- 新增 `mini_agent/llm/tokenizer.py`：线程安全的懒加载单例，`Tokenizer.from_file` 离线加载打包词表；提供 `count_tokens` 与 `count_messages_tokens`（对将发送的 JSON 序列化文本计数）。
- 打包词表 `mini_agent/llm/tokenizer/deepseek_tokenizer.json`（约 7.5 MB，BPE，128,815 词表），来自 DeepSeek 官方 HF 仓库 `deepseek-ai/DeepSeek-V3`（经 hf-mirror 下载，wget 断点续传）。
- `ContextManager` 的 `max_context_chars`/`serialized_chars` 升级为 `max_context_tokens`/`serialized_tokens`；`MAX_CONTEXT_CHARS` 环境变量升级为 `MAX_CONTEXT_TOKENS`（默认 12000）。
- `Agent` 的 `context_built` Trace 事件与 `cli.py` 的 `/context` 展示从字符改为 token 估算。
- `pyproject.toml` 新增 `tokenizers==0.22.2` 依赖与 `mini_agent.llm = ["tokenizer/*.json"]` 打包配置；`.env.example`、README、`scripts/demo_recording.py` 同步更新。
- 新增 4 项 tokenizer 离线测试；全部 42 项离线测试通过。

#### 问题解决记录

- 问题：选官方 HF tokenizer 会引入网络下载依赖，与项目“默认测试完全离线”的验收口径冲突；用户先质疑“调用 API 不总归需要网络吗”。
- 观察证据：项目离线指的是测试、非 API 命令与可复现性；HF 直连超时（curl exit 28），hf-mirror 可用但连接不稳定，wget 断点续传最终完整下载 7.5 MB；本地已装 `tokenizers 0.22.2`。
- 尝试方案：tiktoken 近似（本地未安装）、经验系数、官方 HF 在线加载（破坏离线）、官方 HF 打包加载（做法 B，最终选择）。
- 最终选择：把 tokenizer.json 打包进 `mini_agent/llm/tokenizer/`，用 `tokenizers` 库离线加载并做懒加载单例；压缩判据基于真实 BPE 编码的 token 数。
- 结果/剩余风险：42 项离线测试全部通过；打包词表来自 `deepseek-ai/DeepSeek-V3`，若 `deepseek-v4-flash` 实际使用不同词表需替换该文件；估算未计入 tools Schema 与 system 模板的额外 token。4（流式）尚未开始。

### 13.3 流式请求与实时 token 预算断流（已知限制 4）

#### AI Prompt

~~~text
继续 3A 之后的第 4 项：把 DeepSeek 适配器从非流式请求改为流式请求，使 Agent 能在接收过程中实时判断并提前终止超预算输出，解决“只能在 API 返回 usage 后判断 token 硬预算”的限制。要求：先对真实 DeepSeek API 验证流式行为（include_usage、reasoning_content、tool_calls delta、finish_reason 与 usage 的出现位置），再实现；保持 LLMResponse 返回结构与 Agent Loop 决策逻辑不变；流式累积的 content/reasoning_content/tool_calls 必须与现有非流式路径语义一致，reasoning_content 仍能跨轮回传；实时预算用打包 tokenizer 对 delta 计数，达到剩余预算时提前断流并复用现有 MAX_TOTAL_TOKENS_REACHED 终止语义；同步更新 Protocol、所有测试替身与适配器单测，保持离线测试全部通过。
~~~

#### 成果

- 状态：已完成。
- 真实 API 验证结论：`stream_options={"include_usage": True}` 被 DeepSeek 接受；`usage` 与 `finish_reason` 只在最后一个 chunk 返回；`reasoning_content`、`content`、`tool_calls` 均按 delta 分片传输（tool_calls 按 index 合并 id/name/arguments）；`reasoning_effort` 合法值为 none/minimal/low/medium/high/xhigh/max（无 min）。
- `DeepSeekClient.complete` 增加 `max_output_tokens` 可选参数；`_create_stream` 以 `stream=True` + `include_usage` 发起请求；`_accumulate_stream` 合并流式 delta 为与非流式同构的响应对象，并实时用打包 tokenizer 累计输出 token，超过剩余预算立即抛出 `LLMError(MAX_TOTAL_TOKENS_REACHED)`。
- `Agent._remaining_output_budget` 把 `MAX_TOTAL_TOKENS_PER_TURN` 的剩余额度传给每次流式调用。
- `LLMClient` Protocol 签名更新；全部测试替身 `complete` 增加 `**kwargs` 兼容；适配器单测重写为流式 chunk mock，新增预算断流测试。
- 43 项离线测试与 4 项真实 live smoke test 全部通过（含 calculator 多轮、reasoning_content 轮回传、weather→todo、session 恢复）。
- 官方文档核对（https://api-docs.deepseek.com/zh-cn/guides/thinking_mode）：`thinking` 参数经 `extra_body` 传入、流式下 `reasoning_content` 经 delta 分片累积、携带 tools 的请求必须完整回传 `reasoning_content`（否则 400）、无工具调用轮次的 `reasoning_content` 会被忽略、思考模式不支持 temperature/top_p 等——全部与项目实现一致；官方流式示例未提 `include_usage`，但真实 API 验证确认可用。

#### 问题解决记录

- 问题：非流式请求必须等完整响应返回后才能根据 usage 判断 token 硬预算，超预算也无法中途停止。
- 观察证据：流式验证显示 usage 与 finish_reason 只在最后一个 chunk；工具调用参数按 index 分片渐进到达；流式累积的 delta 文本并非 token 边界对齐。
- 尝试方案：逐字渲染（终端改动面大、风险高）与后台流式（保持响应结构、Agent Loop 零决策改动）；实时计数用打包 tokenizer 对每个 delta 编码（跨 chunk 边界会偏大）或用整段重编码。
- 最终选择：后台流式累积，保持 LLMResponse 结构与 Parser/Agent 不变；实时预算用 delta 逐段 tokenizer 计数，达到剩余预算即断流并复用现有 LLMError → MAX_TOTAL_TOKENS_REACHED 终止路径；偏大的计数方向是更早断流，属于保守方向。
- 结果/剩余风险：43 项离线与 4 项 live 测试全部通过；delta 计数跨 chunk 边界可能略高估，导致断流略早于精确预算；尚未实现逐字渲染；断流时拿不到精确 usage（仅近似累计），README 已如实记录。

### 13.4 read_docs 工具（README 已知限制项）

#### AI Prompt

~~~text
用户要求在既有提交基础上补齐 README 已知限制中的 read_docs 工具，并明确两点：文档内容来源采用方案 A1（白名单读取仓库真实 md：README.md、PROMPTS.md、PROBLEM_SOLVING.md）；同时支持 txt（编码兜底），并新增第 5 项 live smoke test。要求：注册表白名单按 id 查表、不接受任意路径并做 resolve 防穿越校验；默认截断返回（max_chars 默认 3000、上限 8000）；未知 doc_id 返回结构化错误并列出可用列表；返回标注 mock=true；Agent Loop 零改动；同步更新 pyproject package-data、README、记录文件与测试。
~~~

#### 成果

- 状态：已完成。
- 新增 `data/docs_index.json` 白名单：readme / prompts / problem-solving（仓库真实 md）+ demo-notes（`data/demo_notes.txt`，用于展示 txt 支持）。
- 新增 `mini_agent/tools/read_docs.py`：`read_docs(doc_id, max_chars?)`；按白名单查表取路径，`Path.resolve()` 校验在项目根内；md/txt 纯文本读取（UTF-8 优先，GBK 兜底）；内容截断并返回 `{doc_id, title, content, chars, truncated, format, mock}`；未知 id 抛 `DOC_NOT_FOUND`（retryable 且带可用列表），读失败抛 `DOC_READ_FAILED`，非 md/txt 抛 `DOC_FORMAT_UNSUPPORTED`。
- `build_default_registry()` 注册 read_docs；pyproject `package-data` 增加 `data/*.txt`。
- 新增 6 项离线测试（成功读 md、txt、未知 id 列表、截断、路径注入拦截、缺参数 schema 失败），并更新注册表导出集合断言；新增 live 第 5 项（真实模型调用 read_docs 总结 README）。
- 49 项离线测试与 5 项 live smoke test 全部通过；README 工具表与已知限制同步更新。

#### 问题解决记录

- 问题：read_docs 的文档来源与格式支持范围如何界定，以及如何保证只读、不越权。
- 观察证据：README 已知限制仅一句“read_docs 工具未实现”；笔试题允许 read_docs 自定义；仓库真实 md 内容有说服力但 wheel 打包后不在包内；docx 本质是 zip+XML，需要额外解析层。
- 尝试方案：来源三选一（读仓库 md / 独立演示文档 / 混合）；格式三档（md-only / md+txt / 加 docx）。用户选定 A1 + md/txt。
- 最终选择：白名单按 id 查表天然拒绝任意路径，`resolve().is_relative_to(root)` 作为纵深防御；txt 复用纯文本读取并做编码兜底；docx 暂不支持并如实记录。
- 结果/剩余风险：49 项离线与 5 项 live 全部通过；wheel 打包后 `PROMPTS.md`、`PROBLEM_SOLVING.md` 不在包内（README 已知限制已记录）；docx/老式 doc 未支持；`docs_index.json` 为仓库内可被提交修改的文件，路径被篡改时由 resolve 校验兜底。

### 13.5 中断恢复提示与 search/weather 演示引导

#### AI Prompt

~~~text
用户基于对已知限制的问答，要求做两处小改进：1）中断（interrupted）回复中加入“可输入‘继续’或重述目标来恢复任务”的提示文案；2）search 在空结果时返回预设语料的推荐关键词（suggested），weather 在未知城市/日期错误中加入可用城市/日期列表，方便演示时快速找到可用的查询词，避免乱输搜不到。要求：改动保持最小，只影响返回结构与提示文案，不改变工具 Schema 与 Agent Loop 决策；更新相应测试断言与 README 工具表说明；全部离线测试保持通过。
~~~

#### 成果

- 状态：已完成。
- `Agent._finish_interrupted` 的中断回复追加“可输入‘继续’或重述目标来恢复任务”，引导用户恢复而非自动重放。
- `search` 空结果时返回 `suggested` 字段（从 `search_corpus.json` 提取全部 keywords 去重），模型可据此换词重试。
- `weather` 未知城市错误消息列出可用城市（上海、北京、深圳）；未知日期错误列出该城市可用日期并提示“不传 date 使用默认值”。
- 增强现有测试断言：空搜索 `suggested` 非空、未知城市错误含“上海”、中断回复含“继续”；README 工具表补充说明。
- 49 项离线测试全部通过；live 测试不受影响（未改动 Schema）。

#### 问题解决记录

- 问题：中断后用户不知道如何恢复；演示时乱输关键词容易搜不到、查不到天气。
- 观察证据：中断回复原本只说“数据已保留，可以继续”，没有恢复引导；search 空结果返回空数组，weather 未知城市只有“没有 xx 的天气”，模型只能盲目重试。
- 尝试方案：a) 仅改文案/返回字段（最小改动）；b) 自动重放幂等工具（成本高，超出本次范围，已在上轮问答中否决）。
- 最终选择：在返回层加引导信息——中断文案提示“继续”，search 空结果带推荐关键词，weather 错误带可用城市/日期；不改 Schema 与 Agent Loop。
- 结果/剩余风险：49 项离线测试通过；suggested 列表随语料 keywords 变化，若语料扩展需重新确认推荐词覆盖；weather 可用日期是硬编码排除 default 后的排序，多日期时列表自动扩展。

### 13.6 自进化 Loop、search 精排与 loop 改进（JD 加分项）

#### AI Prompt

~~~text
用户根据岗位 JD 加分项“agent 自进化 loop：错题集 / 正确经验管理与调用、种子集合生成、Agent 设计”，在光辰笔试.md 末尾写入第 20 节实施计划，并确认按计划执行、全部完成后等待指令。要求：新增 experiences 表与 ExperienceManager（读侧触发注入、写侧自动提炼、三层种子：人工 seeds + Trace 挖掘脚本 + 运行时沉淀）；search 加轻量 rerank（粗召回 recall_k + 可替换精排 + 返回摘要化，保扩展性）；loop 小改进（仅 tool_step==0 且 retryable 时 run 级自动重试一次、滚动摘要质量校验告警）；Agent Loop 决策核心零改动；保持离线测试原则；全部完成后等待指令统一提交。
~~~

#### 成果

- 状态：已完成（等待统一提交指令）。
- `experiences` 表：`kind('error'|'lesson')/trigger/content/source_run_id/hit_count`，`(kind, trigger)` 唯一，upsert 合并；Store 新增 `upsert_experience/get_experiences/list_experiences/increment_experience_hit/count_experiences/latest_run_error/recent_tool_names`。
- `ExperienceManager`（core/experience.py）：构造时幂等加载 5 条人工种子；`read()` 按最近错误码（`err:<code>`）与工具序列（`seq:<tool1>→<tool2>`）触发召回并递增 hit_count；`write()` 在 run 结束后自动提炼（completed→lesson、incomplete→error），模型不参与写入。
- Agent 集成：`run_turn` 包装 `_run_turn_core` 并在结束时沉淀经验（best-effort，失败不影响结果）；`context.prepare` 注入命中的低优先级 system 经验段；run 级自动重试一次（仅 `round==1 && tool_step==0 && retryable`，防副作用）；摘要质量告警 `context_summary_quality` Trace 事件。
- search rerank：粗召回（现有加权打分取 `recall_k=max(10, top_k*3)`）→ `_rerank` 轻量精排（覆盖率/title/关键词权重，接口可替换）→ 摘要化返回（去 score、snippet ≤160）。
- `scripts/extract_experiences.py`：离线扫描 `.agent_data/runs/*/trace.jsonl`，按 `tool_call_end.error_code` 与 `run_end.error_code` 挖掘错误种子（去重、排除 INTERRUPTED）。
- 新增 13 项离线测试（经验 CRUD/种子/读侧召回/写侧提炼/Agent 注入/Trace 挖掘/重试 3 项/摘要校验 2 项/rerank 稳定性）；62 项离线 + 5 项 live 全部通过。

#### 问题解决记录

- 问题：现有 Agent 每轮“无记忆重来”——错误只当场自纠、Trace 从不召回、无种子经验；search 单层打分无精排扩展性；incomplete 瞬时错误直接结束；滚动摘要无质量校验。
- 观察证据：README Memory 表只有 session 内记忆；live 测试确认错误可自纠但不沉淀；语料仅 4 篇时 top_k≈全量返回。
- 尝试方案：rerank 轻量精排（无依赖、可替换接口）与不做（用户要求做，保扩展性）；run 级重试限定“首次调用且无副作用”以避免重复 todo 等副作用；摘要校验用高频词保留率（阈值 0.4）告警而非阻断。
- 最终选择：触发式注入 + 自动沉淀 + 三层种子；精排接口独立于粗召回可替换；重试仅在 `tool_step==0` 时触发一次；摘要告警只进 Trace 不改变控制流。
- 结果/剩余风险：62 项离线与 5 项 live 全部通过；经验 trigger 目前仅精确匹配（错误码/工具序列），意图相似度检索是后续增强；`hit_count` 在每次 prepare 递增（含未命中后重试的 run，语义上可接受）；运行时沉淀 upsert 会覆盖同 trigger 的种子内容，种子仅作为初始先例；全部改动待用户确认后统一提交。

### 13.7 录屏脚本更新与全功能测试

#### AI Prompt

~~~text
用户要求：1）在 AI_PROMPT与问题解决记录.md 记录本次工作（已由 13.1-13.6 覆盖）；2）检查光辰笔试.md“### 18. 终端录屏脚本”是否需要修改并更新；3）评估是否有必要做一次全功能测试。结论：录屏脚本需同步新功能（五个工具、read_docs、自进化、rerank、MAX_CONTEXT_TOKENS、测试数量），且有必要做全功能测试。执行：更新光辰笔试.md 第 18 章、scripts/demo_recording.py、RECORDING.md，然后以真实 API 跑通完整演示并修复发现的问题。
~~~

#### 成果

- 状态：已完成（等待统一提交指令）。
- 修复实现与设计不一致：`latest_run_error` / `recent_tool_names` 增加可选 `session_id`（不传时为 user 级），`ExperienceManager.read` 改为按 user 全局召回——“用户 A 的教训用户 B 受益”真正生效（此前是 session 级，跨 session 不命中）。
- 更新录屏脚本：`scripts/demo_recording.py` 新增 section 7（read_docs + search 精排）、section 8（自进化经验：错误引导 + 经验库表格 + 机制说明），section 1 改为五个工具，原 Trace 测试改为 section 9，最终面板文案同步。
- 同步文档：光辰笔试.md 第 18 章（五个工具、`MAX_CONTEXT_TOKENS`、新增 7/8 步、测试数量 62）；RECORDING.md 步骤列表扩展为 9 步并说明需重录。
- 修复光辰笔试.md 章节编号冲突：此前追加的“自进化”计划误用 `### 18.`（与原文第 18 章终端录屏脚本、第 19 章检查清单冲突），已改为 `### 20.`（子节 20.x），并同步修正记录文件 13.6 中的引用。
- 修复 Windows 控制台编码崩溃：`trace.py` 与 `render_recording.py` 中的 Unicode ✓/✗ 改为 ASCII `ok/err`（GBK 控制台下 rich 渲染 Unicode 对勾会抛 UnicodeEncodeError）。
- 全功能测试通过：以真实 DeepSeek API 跑通 demo_recording.py 全部 9 段（工具注册、calculator、直接回答、weather→todo、session 隔离、恢复、压缩、read_docs+search、自进化、Trace + 62 passed / 5 deselected），费用显示（cost）与 token 预算均正常；离线测试 62 项保持通过。

#### 问题解决记录

- 问题一：经验召回设计为“全局共享”，但实现按 session 查询，跨 session 不会命中。
- 观察证据：`ExperienceManager.read` 用 `latest_run_error(user_id, session_id)` 与 `recent_tool_names(user_id, session_id)`，window_1 的失败在 window_2 不可见；全功能演示脚本设计依赖跨 session 效果。
- 尝试方案：a) 保留 session 级并降级文档表述；b) 改为 user 级（store 方法 session_id 可选，read 传 None）。
- 最终选择：方案 b，全局召回与“错题集/正确经验”定位一致；测试不受影响（read 测试本就期望命中）。
- 问题二：全功能测试在 Windows GBK 控制台崩溃（`UnicodeEncodeError: '\u2713'`）。
- 观察证据：rich 的 legacy windows renderer 在非 UTF-8 终端无法编码 ✓；Windows Terminal 下不崩，git-bash 管道崩。
- 尝试方案：a) 运行前设 `PYTHONIOENCODING=utf-8`（能跑通但治标）；b) 渲染改用 ASCII `ok/err`。
- 最终选择：方案 b（同时改 trace.py 与 render_recording.py），任意控制台均安全。
- 问题三：光辰笔试.md 章节编号冲突（追加计划误用 18）。
- 最终选择：改为 `### 20.`，同步修正记录文件 13.6 引用；确认第 18 章（录屏）与第 19 章（检查清单）为原文既有章节。
- 结果/剩余风险：全功能演示 9 段全部跑通，62 项离线保持通过；旧版录屏（35 项时代）需按 RECORDING.md 9 步重录；`MAX_CONTEXT_TOKENS` 等新配置名已在录屏脚本与文档中同步。

### 13.8 录屏检查与 Context 压缩演示修复

#### AI Prompt

~~~text
录屏视频已生成，用户用 OCR 识别 11 个关键帧（内容在 Mini_Agent_Terminal_Demo.md）并请我检查。检查发现：Section 6 Context 压缩演示失败——压缩后追问返回 MAX_CONTEXT_REACHED（0 轮调用），且经验库残留上一轮失败沉淀的 err:MAX_CONTEXT_REACHED。要求：定位根因（演示阈值选择），离线验证“轮次 × 阈值 × 演示文本长度”组合，修复后重跑真实演示并重新生成视频，全部检查通过后请用户重新目检。
~~~

#### 成果

- 状态：已完成（等待用户对新视频关键帧的最终目检）。
- 根因：`demo_recording.py` 压缩演示阈值 700 token 过小——压缩后上下文（真实摘要 + 最近 4 条消息 + system）实测 781 token 仍超预算，导致第 3 次确认起每个 run 直接 MAX_CONTEXT_REACHED 终止；且失败回答自身又占约 210 token 进入 context，形成恶性循环。
- 修复：演示文本 `*12` 减为 `*6`；新增 `SessionStore.clear_experiences()`，`demo_recording.py` 开头清空经验库（录屏从 5 条种子开始，不残留旧数据）；用临时离线脚本验证四组“文本长度 × 阈值 × 轮次”组合，确定 **演示文本×6 + 阈值 800 + 6 次确认**：第 6 次确认触发压缩 6 条、全程无超预算、追问上下文 638 token（< 800）。
- 重新运行真实演示：压缩标记 8、摘要版本 2（压缩发生两次）、最后追问回答“项目代号：晨星；未解决事项：补充录屏说明”，无 MAX_CONTEXT_REACHED。
- 重新生成视频（126.1s，H.264/1600×900/15fps）：无黑帧；timeline 验证 8 项全部通过（压缩触发、摘要 v2、无 MAX_CONTEXT_REACHED、追问成功、read_docs、自进化、62 passed、经验记录）；11 张关键帧已更新到 `.agent_data/frames_check/`。
- 遇到 DeepSeek API `HTTP 402 Insufficient Balance`：属账户余额不足，不影响本地操作；真实演示/录屏重跑需充值后继续。

#### 问题解决记录

- 问题：录屏中 Context 压缩演示的“压缩后追问”失败，Agent 答不出“项目代号/未解决事项”。
- 观察证据：OCR 识别显示第 135 行追问返回 MAX_CONTEXT_REACHED；实测压缩后上下文 781 token 超过 700 阈值；压缩标记存在（压缩本身成功，是压缩后仍超预算）。
- 尝试方案：阈值 700→1200（不触发压缩）→600（触发但压缩后仍超）→离线模拟四组组合确定 800+×6+6 轮。
- 最终选择：降低演示文本体积（×12→×6）+ 阈值 800 + 确认轮次 4→6，压缩后余量约 160 token；演示脚本重置经验库保证录屏可复现。
- 结果/剩余风险：压缩触发（8 条、摘要 v2）、追问成功、无 MAX_CONTEXT_REACHED；视频检查全部通过；剩余风险为真实模型摘要长度波动（本轮摘要较短，若未来摘要显著变长需复核阈值）；DeepSeek 账户余额不足时真实演示/录屏重跑会失败。

### 13.9 录屏经验库展示优化（错题集与正确经验同屏）

#### AI Prompt

~~~text
v2 录屏 OCR 检查通过后，用户指出一个小瑕疵：Section 8 自进化经验库只展示 limit=5 的最新条目（全是 lesson 成功路径），种子错题集（err:WEATHER_NO_DATA 等）未上画面，与 JD 加分项“错题集”重点不符。要求优化展示：让错题集（error）与正确经验（lesson）同屏可见。
~~~

#### 成果

- 状态：已完成（等待用户对新视频关键帧的最终目检）。
- `render_recording.py` section 8 改为分组展示：`list_experiences(limit=50)` 后按 kind 过滤，error 取前 3（错题集，红色样式）排在 lesson 前 3（正确经验，info 样式）之前，两类同屏。
- 重新生成视频（127.1s，H.264/1600×900/15fps）；timeline 验证：经验条目 6 条 = 3 error（NO_PROGRESS / DOC_NOT_FOUND / WEATHER_NO_DATA）+ 3 lesson（工具序列成功路径）；11 张关键帧已更新到 `.agent_data/frames_check/`。

#### 问题解决记录

- 问题：经验库展示被“按 updated_at 取最新 5 条”挤掉种子错题集，录屏只见正确经验不见错题。
- 观察证据：v2 OCR 中经验沉淀 5 条全为 lesson；实际库含 5 种子（3 error + 2 lesson）+ 5 运行时 lesson。
- 尝试方案：a) 单纯加大 limit（仍按时间排序，错题集可能被挤掉）；b) 按 kind 分组，error 优先 + lesson 各取 3 条（最终选择）。
- 结果/剩余风险：错题集与正确经验同屏，红/灰样式区分；画面行数控制为 6 条经验，不挤占其他 section；无剩余风险，等待最终目检。
