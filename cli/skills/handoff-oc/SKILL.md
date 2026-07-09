---
name: handoff-oc
description: 把执行性编码/调查任务整包交给 opencode（本地免费 Zen 模型，如 deepseek-v4-flash-free）后台执行，零额外 API key，省主会话额度。后台运行，完成后自动通知。支持并行多任务，支持续接（resume）上次会话继续派发后续任务。
---

# handoff-oc Skill

<interaction_contract>
This skill is executed by Claude Code (an AI agent). The rules below are BINDING — follow them exactly; do not simplify or reinterpret.
## 命令模板（照抄，勿改结构）

```bash
handoff run --backend opencode --slug <≤3个英文单词的任务助记词> - <<'__HF_EOF__'
[prompt 内容]
__HF_EOF__
```

必须用 `run_in_background: true` 启动——handoff 耗时 2~20 分钟，前台会阻塞整个会话。

**关键规则：**
- `--slug` 只写≤3个英文单词、`-` 分隔的语义助记词（如 `fix-auth`）；禁止日期/时间戳/随机数/UUID/计数器，唯一性由 `handoff run` 自动分配的 seq 保证。
- heredoc 界定符固定用 `__HF_EOF__`，prompt 原样粘贴、不转义。
- 不要自己拼任务文件名，也不要用 `> RESULT 2> OUT` 重定向——handoff 自己管命名和落盘。
- 用户提到 `pro`（或要求更强/专业模型处理复杂任务）时，在 `handoff run` 后加 `--pro`。
- 回显任何 home 下的任务路径时，缩写成 `~/.handoff/...`，不要暴露 `/Users/<name>/...`。
- opencode 使用本机已登录的 opencode 账号（免费 Zen 额度），不消耗 DeepSeek/Anthropic 付费 API — 无需额外配置 token。
- opencode 以 `--auto`（自动批准所有工具调用）方式在后台无人值守运行，等同 handoff-ds 用 `--dangerously-skip-permissions`、handoff-codex 用 `--sandbox workspace-write` — 派发的任务应限定在可信目录内，不要用于用户明确要求人工审核每一步的场景。
- 固定用 `--agent handoff-safe`（写死在 backend_types.yaml，非用户可配置）：屏蔽完整 Supabase/Firebase MCP（`supabase_*`、`firebase_*` 全拒绝），保留只读 Supabase 连接（`supabase-ro_*`，仅当项目自己的 `opencode.json` 定义了它）用于查数据/读取分析，但 `supabase-ro_apply_migration`/`create_branch`/`delete_branch`/`deploy_edge_function`/`merge_branch`/`rebase_branch`/`reset_branch` 依旧拒绝——`read_only=true` 只保证 Postgres 角色只读，管不到这些管理面 API。派发"读数据库找失败模式"这类调查任务是安全的；派发任何涉及 schema/migration/基础设施变更的任务，模型会自己停下报告而不是找变通方法。

启动后从 **stdout/stderr** 捕获 `RUN_ID=<id>`（如 `RUN_ID=0709-op-03-fix-auth`）；这就是本次任务的 run_id。记住它，用户要求"继续上次/接着再做 X"时靠它 `resume`。

命令进程退出后，从 **stdout/stderr** 捕获唯一完成标记 `RESULT=<任务路径>`（如 `~/.handoff/tasks/0709-op-03-fix-auth.result.md`），缩写成 `~/.handoff/...` 后回显给用户。`RESULT=` 只表示任务已结束且结果文件已落盘。

其余不要读：进度信息在 **stderr**（Claude Code 的 shell view 自动实时显示，别读进上下文）；同名 `.out.txt` 是进度日志，仅诊断（无结果/超时）时才 `tail -f`/`Read`；同名 `.prompt.md` 就是你刚发的内容。

收到完成通知后，用 `Read` 读对应的 `.result.md` 汇报，**不要**再读后台输出（结果已在文件里，重复读只会把进度噪音吃进上下文）。`.result.md` 为空或异常时才读 `.out.txt` 诊断。
</interaction_contract>

## 多任务

- **并行**：在**同一条消息**里发出多个 `run_in_background: true` 的 Bash 调用，各自用 `handoff run --slug ... - <<'__HF_EOF__'` 派发不同 prompt（seq 自动递增），各自先记录 `RUN_ID=`，等命令完成后捕获 `RESULT=`，分别等通知、分别读 `.result.md` 汇报。
- **串行**：等上一个的完成通知到达、读并汇报后，再启动下一个。

## 续接上次会话（resume 续派）

要保留某次任务的上下文继续，而非开新会话：用 `resume` 替代 `run`，继续通过 heredoc/stdin 传入后续任务；其余约定（后台、捕获新 `RUN_ID=` 和最终 `RESULT=`、读 `.result.md`）完全相同：

```bash
handoff resume <run_id> --backend opencode --slug <任务助记词> - <<'__HF_EOF__'
[后续任务内容]
__HF_EOF__
```

- `<run_id>` 用该会话**首次**任务的 `RUN_ID`；它是稳定句柄，每轮续接都用它，不要追每轮新生成的 run_id。
- **必须带后续 prompt**：不带 heredoc、`-`、输入文件或 `--text` 的 `resume <run_id>` 是交互式重开，后台会卡死。
- 续接默认只继承 backend；原会话用过 `--pro` 的，续接要再次带上才沿用 pro_model。
- 不确定用户指哪次任务时，报候选 run_id + 摘要让其确认，别猜。
