# VEMO / VEMO_SKILLS Playbook Usage

本文把 `claude_usage.zip` 和 `agent-playbook-v1.0-20260707.zip` 里的 agent playbook 泛化成 VEMO 与 VEMO_SKILLS 的用法。不要把 `.claude/playbook/` 原样搬进每个项目；应把其中的制度翻译成 VEMO 的机制、任务账本、验证门和共享 skill 生命周期。

## 迁移原则

| playbook 概念 | VEMO / VEMO_SKILLS 承载方式 |
| --- | --- |
| 根入口只做路由 | `AGENTS.md` 保持短小；细则按 `specs/_manifest.yaml` 按需加载 |
| 开工先诊断与控 context | `python bin\vemo context`、`python bin\vemo status`，避免全量读配置和大文件 |
| 任务四阶段：探索、计划、实现、提交 | `tasks/_TASK_TEMPLATE.md` + `specs/task.spec.md`；任务文件记录 scope、计划、证据和验收 |
| “完成”必须有实跑证据 | `paths.build` / `paths.smoke` + `python bin\vemo verify`；机器 receipt 比口头 exit code 更可信 |
| 模型分派与升级 | `vemo.config.yaml -> capability.tier` 和 `model_routing`；高风险验收走 `agents/governance-judge.md` |
| 派工 prompt 要有目标、范围、验收、回报格式 | 做成 VEMO_SKILLS skill 的 `SKILL.md` 触发描述与步骤；复杂产物放 `references/` |
| 机制优先于文字规则 | VEMO hooks、git hooks、CI workflow 负责拦 scope、危险命令、未验收 push、secret |
| lessons 逐步升格 | 单次教训写入任务执行记录；重复问题再提炼进 `specs/`、VEMO skill 或 VEMO_SKILLS |
| playbook 维护协议 | VEMO 自带 `governance-sync` / `docs-sync`；VEMO_SKILLS 用 `syncing-frameworks`、`publishing-skills`、`contributing-framework-changes` |

## 本项目的当前用法

本项目已经接入：

- VEMO `v1.9.0`：本地镜像在 `VEMO/`，业务项目实际使用根目录的 `AGENTS.md`、`specs/`、`enforcement/`、`skill/`、`tasks/`、`bin/`、`vemo.config*.yaml`。
- VEMO_SKILLS `v1.2.0` + unreleased local updates：本地镜像在 `VEMO_SKILLS/`，生成副本在 `.claude/skills/`；当前已绑定 29 个 skill。
- 源仓同步文档：`VEMO/docs/PLAYBOOK_ADOPTION.md` 讲 playbook 如何迁移成 VEMO 机制；`VEMO_SKILLS/docs/PLAYBOOK_GENERALIZATION.md` 讲 playbook 流程如何抽成共享 skill。
- 诊断提示流程：`docs/DIAGNOSTIC_PROMPTING.md` 讲 VEMO 层的 intake -> map -> constraint -> plan -> loop -> boundary；`designing-diagnostic-prompts` skill 负责生成 Human 3.0 / Mr. Ranedeer 风格的通用诊断或导师提示流程。
- Windows 解释器：本机用 `python`，不要假设 `python3` 可用。
- 当前 smoke gate：`python -m pytest tests -q`，只验证根目录 `tests/`，避免收进旧的 `vemo_photo/tests/...`。

常用命令：

```powershell
python bin\vemo status
python bin\vemo context
python bin\vemo tier <paths...>
python bin\vemo verify
python bin\vemo doctor
python bin\vemo selfcheck
python bin\vemo skill-roster
```

重新绑定 VEMO_SKILLS：

```powershell
$dest = Join-Path (Get-Location) '.claude\skills'
python VEMO_SKILLS\tools\vemo_skills_check.py --root VEMO_SKILLS bind --dest $dest
```

`.claude/skills/` 是生成产物，不要手改。要改 skill，改 `VEMO_SKILLS/skills/<category>/<name>/` 的源，再重新 bind。

## 新任务工作流

1. 先跑 `python bin\vemo context`，确认 active task、风险、预算、auto mode。
2. 判断是 `non-task`、`continue-task` 还是 `new-task`。
3. 对会改的路径跑 `python bin\vemo tier <paths...>`，按最低合适风险建任务。
4. 从 `tasks/_TASK_TEMPLATE.md` 复制一个 `tasks/T-YYYYMMDD-name.md`，填 `risk`、`scope_in`、`state`、验收标准和计划。
5. 只在 `scope_in` 内修改；需要越界先改任务 scope，而不是绕 hook。
6. 实现后跑项目真实验证，再跑 `python bin\vemo verify` 生成 `.vemo/run/receipt.json`。
7. R2 或配置要求时，按 VEMO judge 流程做独立验收；缺证据就报告“未完成，卡在某验证项”。

## 什么时候做成 VEMO_SKILLS

适合做成 VEMO_SKILLS 的内容：

- 可跨项目复用的流程，比如发布交付物、评审决策、量化模型、渲染报告。
- 诊断型或导师型提示词流程，比如自我探索、学习路径、定制 GPT onboarding、教练式访谈。
- 需要一套稳定 prompt + 检查步骤 + 参考脚本的工作。
- 不包含项目路径、账号、群 ID、密钥、特定测试命令等项目事实。

不适合做成 VEMO_SKILLS 的内容：

- 本项目特定的完成定义、真实测试命令、历史事故数字。
- 只用一次的任务计划。
- 需要直接承载验收门的规则；验收门属于消费项目的 VEMO 配置与任务账本。

发布或修改 skill 的顺序：

1. 用 `authoring-skills-with-evals` 起草和评测触发描述。
2. 用 `naming-skills` 检查名称、目录、description。
3. 用 `publishing-skills` 注册到 `VEMO_SKILLS/skills/<category>/<name>/`。
4. 运行 `python VEMO_SKILLS\tools\vemo_skills_check.py --root VEMO_SKILLS selfcheck`。
5. 重新 bind 到 `.claude/skills/`。

## 什么时候改 VEMO

适合改 VEMO：

- 任务生命周期、scope、risk tier、验证 receipt、judge、CI/backstop 等治理机制。
- 新语言或技术栈 preset。
- 可机械执行的安全门或检查器。

不适合改 VEMO：

- 某个项目的一次性偏好。
- 某个模型供应商的临时价格或别名。
- 只属于一个业务仓的测试命令；这些放在 `vemo.config.preset.yaml`。

框架更新走 VEMO 自带 skill：`governance-sync` 检查上游，`governance-contribute` 把本地通用改动做成上游 PR，`governance-release` 用于 VEMO 本身发版。

## 反模式

- 照搬源 playbook 的诊断数字、测试命令、模型价格表。证据必须在当前项目重新采集。
- 把长规则塞进 `AGENTS.md`。它只做路由，细则放 `specs/` 或 skill。
- 没跑 `vemo verify` 就说完成。
- 手改 `.claude/skills/` 生成副本。
- 并行写同一批文件。并行只用于只读搜索、研究、审查；写路径保持单线程。
- 用更多文字规则代替可机械拦截的 hook 或 CI gate。
