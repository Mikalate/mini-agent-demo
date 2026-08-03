# 终端录屏

录屏入口：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_demo.ps1
~~~

脚本只在终端中运行，并按《光辰笔试.md》第 18 章依次展示：

1. 四个工具与参数摘要；
2. calculator 与直接回答；
3. weather → todo → final；
4. 两个 session 的待办隔离；
5. 重建 Store/Agent 后继续追问；
6. 使用明确标注的低字符预算触发滚动摘要；
7. Trace 摘要和默认离线测试。

`scripts/run_demo.ps1` 优先使用当前进程中的 `DEEPSEEK_API_KEY`；如果不存在，则临时读取已忽略的本地 `APIkey.txt`，在脚本结束时清除环境变量。脚本不会显示 `.env`、key、请求头、SQLite 内容或 `reasoning_content`。

演示数据写入已忽略的 `.agent_data/recording_demo/`。运行前会重置三个固定 demo session，避免旧数据影响录屏，不会操作其他 session。

若手动录制，建议只捕获标题为 `MiniAgentDemo` 的 Windows Terminal 窗口。录屏文件放入 `artifacts/`；MP4/MKV 默认不进入 Git，以免仓库包含体积较大的二进制文件。

本机存在 Windows Terminal 和支持 `gdigrab` 的 FFmpeg 时，也可自动捕获指定窗口：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\record_demo.ps1
~~~

自动录制只按窗口标题捕获 `MiniAgentDemo`，不会录制整个桌面；输出为 `artifacts/mini-agent-terminal-demo.mp4`。

如果 Windows Terminal 使用 GPU 合成而导致窗口捕获黑屏，可使用脱敏 Trace 和公开消息生成终端回放视频：

~~~powershell
C:\Users\Lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\render_recording.py
~~~

回放脚本只读取 `.agent_data/recording_demo` 中的公开消息、工具事实、摘要和 Trace 事件，代码中没有读取 `reasoning_content`；生成的视频仍为纯终端画面。

本机最终采用终端回放方案，已生成 `artifacts/mini-agent-terminal-demo.mp4`：H.264、1600×900、约 95 秒、约 7 MB。已抽帧确认中文、工具链、session 恢复、滚动摘要、Trace 和 `35 passed, 4 deselected` 均可见。

演示脚本运行 pytest 时显式使用项目内的 `.agent_data/recording_demo/pytest_tmp`，避免 Windows 用户临时目录权限异常；该目录随 `.agent_data/` 一同被 Git 忽略。
