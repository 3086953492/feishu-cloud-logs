# 飞书云日志

`feishu-cloud-logs` 是一个以飞书 Docx 和 Wiki-backed Docx 为载体的专业日志记录 Skill。它把技术变更、业务事实、证据和时间语义整理为面向明确受众的叙事日志，并在写入时保护并发修改、历史完整性和隐私。

## 能力范围

本 Skill 支持创建、审阅、追加、插入、回填、更正、重组和归档八类日志：

- 发布／产品变更
- 工程变更
- 项目／进展
- 事故／运维
- 决策
- 实验／研究
- 审计／合规
- 客户反馈／支持处理

它既可生成草稿，也可在目标明确且安全条件满足时维护云端日志。实时文档是结构、位置和样式的最终依据；本地档案只提供经精确匹配后的辅助约定。

## 路由边界

专业叙事日志由本 Skill 组织。普通方案、手册和任意段落编辑交给 `lark-doc`；Wiki 节点解析和放置交给 `lark-wiki`；行式记录、筛选和统计使用 Base 或 Sheets；任务分配使用 Task；会议原始产物使用 Minutes；云盘文件的上传、下载、移动、复制、权限和评论管理交给 `lark-drive`。服务器或应用运行日志流不属于本 Skill。

其他能力可以先收集提交、测试、会议或客户反馈等事实，再由本 Skill 转写成专业日志；组合使用不改变各自的责任边界。

## 按任务选择工作量

仅凭已有材料起草或审阅时，不加载飞书技能、检查认证或探测运行时。需要云端内容时，只加载对应操作的技能和参考，复用会话中已读且仍适用的内容。Docx 操作使用 `lark-doc`；Wiki 解析才增加 `lark-wiki`，认证依赖按所调用技能的当前规则处理。

`compatibility.json` 的可选 `profiles` 声明各操作的最小能力；顶层 `required_*` 保留为完整诊断集合。调用者将选中的能力传给现有运行时脚本，无需新接口；无 profiles 的旧 v1 配置仍兼容。

独立完整记录使用 `lint_log_draft.py --profile full`（默认）。沿用既有章节上下文的片段使用 `--profile append`，不强制重复完整字段、日期与时区；空白片段或显式错误的日期、时区仍会报错，两种模式均执行隐私、层级、视觉和历史检查。事实、推测、决定与待确认是语义区别，不要求每条更新填满四个栏目。云端追加操作也可能写入完整记录，不能仅因操作名选择轻量检查。

## 结构

- `SKILL.md`：触发边界、路由、核心工作流和硬性安全规则。
- `references/`：日志分类、模板、写作、版式、私有档案和执行安全合同。
- `scripts/`：离线运行时探测、私有档案检查、草稿检查和公共包验证。
- `tests/`：只使用临时目录、模拟进程和虚拟数据的合同测试。
- `agents/openai.yaml`：Skill 展示名称与默认提示。
- `compatibility.json`：能力门槛和已验证运行时证据。

## 安装与升级

仓库根目录就是 Skill 根目录，不存在嵌套的 `skills/` 层。可把根目录的公共运行文件复制到名为 `feishu-cloud-logs` 的活动 Skill 目录。

本仓库没有内置安装器。安装或升级流程应从 Git 跟踪清单或 `git archive` 构建公共暂存目录，而不是递归复制整个工作区。替换活动目录前先备份已有 `.local/`，替换公共文件后再恢复它，并在切换前后运行校验。首次安装可以带入单独准备且已验证的私有档案；升级时以安装目录中既有 `.local/` 为准，不做隐式合并。

`.local/`、`dist/` 和本地开发证据均被 Git 忽略，不会进入公共发布包。不要使用会删除或覆盖 `.local/` 的同步参数。

## 私有文档记忆

私有档案只保存文档的非敏感约定，例如受众、语言、时区、日期口径、排序、稳定标题、插入规则、保护区域和更正策略。`.local/trusted.json` 可另行保存按精确 profile、目标摘要、用户身份、日志类型、受众和普通追加操作收窄的授权记录。它不得保存凭据、认证状态、个人信息、临时块标识、revision、所有者标识或原始接口返回。

只有用户明确要求“记住”“更新文档档案”“信任该文档”或“撤销信任”时才修改 `.local/`。普通日志写入不应顺便更新档案或信任标识。

普通追加已有会话内明确授权且目标、身份与范围未变时直接执行，不要求先建立 profile 或 grant。长期信任只帮助复用此前授权，不产生写入意图；解析器的 `may_mutate` 仅表示持久信任路径是否满足。该路径要求实时目标精确匹配（Wiki 同时核对节点与底层 Docx）。缺失、过期或撤销的记录不能支持历史授权复用，不替代对当前明确授权的判断。别名只产生候选；多个候选或已授权目标漂移时澄清目标，不能把旧授权用于新目标。

两条授权路径都保留写前核对、并发保护、异常查重和写后读回。破坏性操作先完成具体差异预览；已有授权覆盖同一预览时不重复确认，否则在执行前取得确认。

## 验证

本地验证只访问仓库文件和模拟对象：

```powershell
python -m unittest discover -s tests -q
python scripts/validate_package.py --root . --mode source
python scripts/resolve_runtime.py --help
python scripts/private_memory.py --help
python scripts/lint_log_draft.py --help
python scripts/validate_package.py --help
```

持续集成使用 Python 3.12，运行同一测试套件和公共包验证，不安装运行时、不认证、不读取真实文档，也不执行 canary 写入。

## 隐私与发布

日志中禁止保存密钥和认证材料，即使得到确认也不例外。个人信息应最小化或脱敏；上传图片、图表和白板前必须检查客户数据、邮箱、电话和图片元数据。公共文件、Git 跟踪清单和发布归档都必须通过私有标识扫描。

本仓库当前不授予任何许可证。公开可见不等于获得复制、修改、分发或再许可的权利。

## English summary

`feishu-cloud-logs` is a Chinese-first Codex skill for drafting and safely maintaining professional narrative logs in Feishu Docx or Wiki-backed Docx documents. It provides eight log taxonomies, audience-aware writing guidance, offline linting, scoped trusted-destination grants, private-profile isolation, optimistic-concurrency rules, idempotency checks, and read-after-write verification. Public packaging excludes `.local/`, uses fixture-only CI, and grants no license at this time.
