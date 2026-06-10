# 阶段 3：skills 拆分 + 全部文档重写 + 清理

> 执行者：/ds-cli pro（DeepSeek V4 pro）
> 前置：阶段 1、2 已合入（`--backend` 可用，`handoff init` 从包内资源链接文件）。

## 1. skills 目录（放 `cli/skills/`，随包分发；阶段 1 的 pyproject 已 include）

以现有根目录 `SKILL.md` 为母版（它的 interaction contract 是经过实战调优的，结构不要动），
生成三份 Claude Code skill + 一份 Codex agent：

```
cli/skills/
├── handoff-ds/SKILL.md       # name: handoff-ds
├── handoff-codex/SKILL.md    # name: handoff-codex
├── handoff-opus/SKILL.md     # name: handoff-opus
└── handoff-ds.toml           # Codex subagent（以现 ds-agent.toml 为母版）
```

各文件相对母版的差异：

| 文件 | 命令模板 | description 侧重 |
| --- | --- | --- |
| handoff-ds | `handoff run --backend deepseek - <<'__HF_EOF__'` | 把执行性编码/调查任务整包交给 DeepSeek 后台执行，省主会话额度 |
| handoff-codex | `handoff run --backend codex - <<'__HF_EOF__'` | 向 Codex (GPT-5.5) 咨询复杂问题 / 要第二意见 / 派发需要强推理的任务 |
| handoff-opus | `handoff run --backend opus - <<'__HF_EOF__'` | 把关键决策/验收类任务交给 Claude Opus |
| handoff-ds.toml | 沿用现 ds-agent.toml 的最新机制（`handoff run <prompt-file> >/dev/null`，保留 stderr 进度、丢弃 stdout 结果文本），仅加 `--backend deepseek` | 同 handoff-ds |

母版同步修订（三份 SKILL.md 一致应用）：

- `ds-cli` → `handoff`；heredoc 界定符 `__DS_EOF__` → `__HF_EOF__`；
  run_id 示例 `ds-0608-07` → `hd-0611-03`；路径示例 `~/.ds-cli/` → `~/.handoff/`。
- 删除 `--fast` 相关规则；`--pro` 规则保留原文。
- resume 一节：续接命令同样带上该 skill 自己的 `--backend`（与阶段 2 的
  "续接必须用原 backend"语义一致）；其余约定原文保留。
- 删除根目录旧 `SKILL.md`、`ds-agent.toml`。
- 核对 `cli/commands/init.py` 的链接清单与上述四个文件一一对应
  （`~/.claude/skills/handoff-{ds,codex,opus}/SKILL.md`、`~/.codex/agents/handoff-ds.toml`）。

## 2. 文档（全部）

| 文件 | 动作 |
| --- | --- |
| `README.md` | **英文为主 README**（PyPI 项目页与 GitHub 门面渲染它）：内容取自现成的 `README.EN.md`（已由主会话翻译定稿，不要重写，只逐节核对与实现一致：flag 拼写、`handoff init`、模型名与 `cli/default_config.yaml` 实际值、`hd-` 前缀）。完成后删除 `README.EN.md` |
| `README.zh-CN.md` | 中文版：内容取自现成的 `readmev2.md`（同样只核对一致性，不要改写文案）。完成后删除 `readmev2.md`。两个版本结构必须逐节一一对应，顶部互链（英文版链 `README.zh-CN.md`，中文版链 `README.md`）已在文内写好 |
| `CLAUDE.md` | 全面重写：项目定位改为多 backend 调度（What handoff is）；Commands、Architecture（含 type_defaults、parser 抽象、init/迁移）、Key constraints 全部对齐新实现。Key constraints 里删除 "No --backend flag" 等过时条目 |
| `docs/cli-reference.zh-CN.md` | 重写：`run` / `resume` / `list` / `tail` / `init` 五个命令 + `--backend`/`--pro` 标志；删除 install/update/`--fast`；run id 编码、落盘布局更新为 `~/.handoff/` 与 `hd-` 前缀 |
| `docs/configuration.zh-CN.md` | 重写：`type_defaults` 合并机制（注明列表是整体替换不拼接）、三个内置 backend 与"最小配置只需 token / 或用 `${DEEPSEEK_API_KEY}` 环境变量插值"、自定义 backend 示例（再加一个 anthropic 兼容端点的例子）、include 机制保留；删除 backend_template / fast_backend |
| `docs/design.zh-CN.md` | 新建：为何 Claude Code 用后台 shell 而 Codex 用 subagent（从旧 README 的折叠块迁移）、RESULT= 协议、codex 集成结论（合并阶段 2 产出的 `docs/design-notes-codex.md`，合并后删除该 notes 文件） |
| `plans/handoff/*.md` | 不动（历史记录） |

文案红线（来自产品决策，违反即返工）：

- 不使用"后端"一词面向用户（代码/配置层的 backend 不受限）；说"换个模型/派给谁"。
- README 痛点列表保持"你说：『…』"的提示词示例格式，不得改回抽象描述。
- run/resume 定位为 AI 调用的命令，list/tail 才是面向用户的——文档措辞保持这个区分。

## 3. 清理与验证

```bash
grep -rn "ds-cli\|dscli\|ds-agent\|__DS_EOF__\|--fast\|fast_backend\|backend_template" \
  --include="*.md" --include="*.py" --include="*.yaml" --include="*.toml" . \
  | grep -v plans/ | grep -v assets/
# 仅允许的命中：迁移说明里的"原名 ds-cli"性质文句、core.py 迁移代码里的旧路径常量

HOME=$(mktemp -d) handoff init -y && ls ~/.claude/skills/   # 4 个落点齐全（注意 HOME 已换）
handoff run --text "smoke"                                  # 终态回归
```

新装环境完整走一遍 README 安装节的命令，确保文档与现实一致。

## 4. 收尾

单独 commit：`feat: split skills per backend; rewrite all docs for handoff`。不要 push。
