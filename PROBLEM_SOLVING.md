# Problem Solving

本文记录实现过程中影响架构或正确性的关键问题。逐项 AI Prompt、成果与更细的实施记录见 `AI_PROMPT与问题解决记录.md`。

## 1. DeepSeek reasoning_content：协议完整性与隐私边界

- 问题：思考模式的工具链要求下一轮回传 assistant 的 `reasoning_content`，但题目不应展示或记录隐藏推理。
- 证据：仅依赖 SDK 的标准字段序列化可能丢失 DeepSeek 扩展字段；缺失后工具链可能收到 HTTP 400。
- 选择：适配器同时检查消息属性和 Pydantic extra fields；SQLite 保存活跃协议字段，ContextManager 只在完整工具链中回传。终端、Trace、摘要和最终回答统一排除该字段。
- 结果：真实 calculator 和连续工具测试已证明字段能跨轮回传，脱敏测试证明其不会进入可观察输出。

## 2. 损坏 arguments 仍需保持 tool_call_id

- 问题：`function.arguments` 可能不是合法 JSON。直接丢弃会让模型不知道哪次调用失败，执行则违反安全边界。
- 证据：tool 消息必须使用 assistant 返回的同一个 call id，否则协议链无效。
- 选择：Parser 使用 `ParsedToolCall` 同时保存 id、名称、原始参数、可选的合法 ToolCall 和 ParseIssue。损坏 JSON 不执行 handler，但用原 id 回填 `INVALID_ARGUMENTS_JSON`。
- 结果：FakeLLM 已验证模型收到结构化错误后能够修正参数并完成任务。

## 3. Session 隔离不能只依赖终端状态

- 问题：REPL 支持 `/switch` 后，如果 Agent 保存进程级历史或 todo 接受模型提供身份，会导致跨 session 数据泄露。
- 证据：同一用户可同时打开两个终端，不同用户也可能使用相同 session 名。
- 选择：所有 Store API 显式携带 `(user_id, session_id)`；Agent 和 ContextManager 不保存全局当前历史；todo 身份只由 ToolContext 注入。`/reset` 再次确认 session 名并在单事务中限定 session 主键。
- 结果：并发 E2E、重启恢复、同名 session 跨用户隔离和定向 reset 测试均通过。

## 4. Context 压缩必须以完整回合为原子

- 问题：按单条消息或固定字符删除会留下孤立 tool 消息，或保留 tool_calls 却删除其 reasoning 协议字段。
- 证据：消息表包含 run_id 和 run 状态，可以准确判断哪些用户回合已经闭合；最近 N 条可能落在工具链中间。
- 选择：从最近完整回合向前累计保留窗口，只压缩最早的连续闭合前缀；当前 run 永不压缩。摘要与压缩标记同事务提交，失败时数据库不变，只对本次请求做确定性回退。
- 结果：低阈值测试证明完整工具链会一起压缩、最近消息仍在摘要之后、摘要失败不丢数据。

## 5. Trace 不能成为 Runtime 的故障源

- 问题：终端 UI 和 JSONL 都要消费同一批事实，但渲染或磁盘观察故障不能改变 Agent 控制流。
- 证据：在 Loop 各分支直接打印会把执行层与 UI 紧耦合，也容易遗漏脱敏。
- 选择：Loop 只产生结构化 TraceEvent，best-effort EventBus 分发给 JSONL Writer 和 Rich/Plain Renderer；每个 sink 单独捕获异常，所有出口共用脱敏函数。
- 结果：每个 run 可按事件复盘，故障 sink 测试不会阻断正常 Writer，API key 和 reasoning_content 均未泄露。

## 6. 无进展检测要排除耗时噪声

- 问题：只比较工具名和参数会误判实时结果变化；比较完整 ToolResult 又会因为 `duration_ms` 不同而检测不到重复。
- 证据：业务进展由 ok/data/error 决定，耗时只属于观察层。
- 选择：对工具名、排序参数以及 ok/data/error/truncated 生成稳定 SHA-256 签名，排除耗时。相同签名达到阈值即以 `NO_PROGRESS` 结束。
- 结果：重复调用在两次后确定终止，正常的不同参数或变化结果不会被混为同一签名。

## 7. API attempts、成功调用和 usage 分开统计

- 问题：SDK 隐式重试会让终端记录的请求次数与真实 HTTP 次数不一致，失败请求也不应增加 token usage。
- 证据：429、500/503、连接和超时需要退避，而 400/401/402/403/422 不应盲目重试。
- 选择：关闭 SDK 重试，在 DeepSeekClient 中实施有限指数退避；每次实际等待前发出 retry 事件。RunState 分开累计 api_attempts、successful_llm_calls 和 usage。
- 结果：两次失败后成功、认证错误立即终止和 attempt 事件测试均通过。

## 8. 确定性回归与真实模型验收分层

- 问题：全部调用真实 API 会受到费用、网络和模型措辞随机性影响；只用 FakeLLM 又不能证明真实模型会自主选择工具。
- 证据：控制流适合精确脚本化断言，真实模型更适合断言结构、工具名和关键事实。
- 选择：62 项默认测试完全离线，覆盖所有协议、异常分支、费用/token 估算、流式、read_docs、自进化与 rerank；5 项 `live` smoke tests 单独运行，只断言关键行为与持久化结果。
- 结果：当前离线 62 项和真实 5 项均通过，默认测试数秒内可重复完成。

## 9. 本地密钥的验收使用

- 问题：用户提供了本地 `APIkey.txt`，真实测试需要使用，但代码、Trace、SQLite 和输出都不能保存它。
- 证据：应用正式配置只读取 `.env`/进程环境；测试 marker 只在进程环境存在 key 时启用。
- 选择：把 `APIkey.txt` 加入 `.gitignore`，验收命令只在单次测试进程中临时设置环境变量，并在 finally 中清除；提交前按命中数量扫描，不打印密钥。
- 结果：live 测试通过，密钥在该文件之外的工作区文本中命中 0 项。

## 10. 流式 usage 与 finish_reason 只在流末尾

- 问题：非流式请求必须等完整响应返回才能判断 token 硬预算；改为流式后，usage 与 finish_reason 的出现位置决定实时断流是否可行。
- 证据：真实 API 验证显示 `stream_options={"include_usage": True}` 被接受，但 `usage` 与 `finish_reason` 只在最后一个 chunk 返回；`reasoning_content`/`content`/`tool_calls` 均按 delta 分片到达且非 token 边界对齐。
- 选择：后台流式累积，保持 LLMResponse 结构与 Agent Loop 不变；实时预算用打包 tokenizer 对每个 delta 编码累计，达到剩余预算即断流并复用 `MAX_TOTAL_TOKENS_REACHED` 终止语义；delta 跨边界计数偏大，方向是更早断流（保守）。
- 结果：43→62 项离线与 5 项 live 通过；断流时无精确 usage，README 已记录。

## 11. token 精确估算必须离线打包词表

- 问题：Context 压缩阈值用字符数近似 token 不精确；官方 tokenizer 从 HuggingFace 分发，直接在线加载会破坏“默认测试完全离线”的验收口径。
- 证据：HF 直连超时、镜像站不稳定；`tokenizers` 库已装且支持 `Tokenizer.from_file` 离线加载。
- 选择：把官方 BPE `tokenizer.json`（约 7.5 MB）打包进 `mini_agent/llm/tokenizer/`，懒加载单例离线计数；配置项从 `MAX_CONTEXT_CHARS` 升级为 `MAX_CONTEXT_TOKENS`。
- 结果：62 项离线测试通过；词表来自 `deepseek-ai/DeepSeek-V3`，若模型词表不同需替换；估算未计入 tools Schema 与 system 模板差异。

## 12. 自进化经验：全局召回与低优先级注入

- 问题：Agent 每轮“无记忆重来”，错误只当场自纠、Trace 从不召回；经验库若做成 session 级则跨 session 无效，若全量注入则挤占 context 且可能被误读为指令。
- 证据：设计目标为“用户 A 的教训用户 B 受益”；`latest_run_error`/`recent_tool_names` 按 session 查询时跨 session 不命中。
- 选择：经验表按 `(kind, trigger)` 唯一 upsert；召回改为 user 级（store 方法 session_id 可选），按“最近错误码/工具序列”精确匹配；注入为标注低优先级的 system 段；模型不参与写入，防止幻觉沉淀。
- 结果：62 项离线与 5 项 live 通过，全功能演示确认经验库展示与跨 session 机制说明；trigger 精确匹配，意图相似度检索留作后续。
