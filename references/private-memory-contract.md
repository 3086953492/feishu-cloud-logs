# 私有文档记忆合同

## 边界

私有文档记忆位于 Skill 根目录的 `.local/`，整个目录由 Git 忽略，也不得进入发布归档或公共示例：

- `.local/index.json`：版本化档案索引。
- `.local/trusted.json`：版本化、按文档和操作收窄的用户授权记录。
- `.local/documents/`：每个文档的非敏感写作和结构约定。
- `.local/archive/`：迁移或替换前的可恢复原件与清单。

公共 Skill 不预置任何真实文档档案或信任标识。日志写入、档案维护和信任维护是三个独立动作。

## 索引结构

索引顶层结构固定为 `{version: 1, profiles: [...]}`：只用 `version` 表示 schema 版本，并用 `profiles` 保存非空档案数组。不得使用旧的文档数组键替代 `profiles`。每个 profile 至少包含以下精确键：

- 身份与文件：`profile_id`、`aliases`、`memory_path`；
- 实时目标：`canonical_url`、`document_token`、`wiki_node_token`；
- 分类上下文：`document_kind`、`log_types`、`audience`、`language`、`timezone`；
- 结构策略：`date_semantics`、`ordering`、`stable_headings`、`insertion_rules`、`protected_regions`；
- 治理字段：`sensitivity`、`visual_policy`、`correction_policy`、`migrated_at`、`last_verified_at`。

Docx profile 不使用 Wiki 节点字段；Wiki-backed Docx profile 同时记录规范 Wiki 节点和实时解析出的底层文档目标。`migrated_at` 必须是带显式时区的 ISO 8601 时间。未完成实时核验时，`last_verified_at` 保持空值，不用迁移时间冒充核验时间。

标识只存在于忽略目录的索引中。活动记忆文件保存结构、受众和写作约定，不重复保存目标标识。

## 信任标识

`.local/trusted.json` 顶层固定为 `{version: 1, grants: [...]}`。每个 grant 必须且只能包含：

- `profile_id`：引用索引中的唯一 profile；
- `target_fingerprint`：由 `profile_id`、规范 URL、文档类型和规范目标 token 计算的 `sha256:` 摘要；Wiki 同时绑定节点 token 与底层 `document_token`，任一目标变化即失效；
- `identity`：固定为 `user`；
- `operations`：只允许 `append`、`block_insert_after`；
- `log_types`、`audiences`：不得超出对应 profile；
- `authorized_at`、`expires_at`、`revoked_at`：带显式时区的 ISO 8601；后两者可为 `null`。

使用 `scripts/private_memory.py trust-fingerprint` 为实时精确目标生成摘要。Wiki 目标必须先实时解析节点，并向 `trust-fingerprint` 和 `resolve` 传入 `--backing-document-token`；缺少或不匹配都不得写入。信任文件存在时，`validate` 和 `resolve` 会自动校验它；未知字段、重复 profile、目标漂移、破坏性操作、身份或内容范围扩大都使授权无效。

信任标识只消除重复确认，不创建写入意图。只有本轮用户明确要求云端写入，并且 `resolve` 同时收到 `--explicit-write-intent`、精确 URL/token、Wiki 的实时底层 Docx token（如适用）、`--write-operation`、`--log-type`、`--audience` 和 `--identity user`，返回 `may_mutate=true` 与 `trust_status=trusted` 后，才可直接执行普通追加。

## 禁止内容

不得保存凭据、Cookie、密钥、认证状态或登录状态、个人信息、块 ID、revision、owner ID 或原始 API 返回。信任文件保存的是用户对特定目标和操作的授权决定，不得写入 OAuth 状态、权限令牌或认证材料。

档案只表达相对稳定的约定，不保存一次写入的临时定位信息。日志正文需要的个人信息仍按最小化原则处理，不能因为档案位于忽略目录就放宽隐私要求。

## 匹配顺序

1. 接受用户本次明确给出的 URL 或标识作为首要线索。
2. 从实时服务解析规范文档类型、节点关系和底层文档目标。
3. 用实时规范标识精确匹配一个私有 profile。
4. 唯一别名只能形成候选；完成实时核验后才允许写入。

多个候选时停止并要求用户选择。没有档案时继续使用实时文档，不自动创建 profile，也不从相似名称猜测。跨受众、跨客户或跨环境的档案不得相互回退。

实时远端结构始终优先于过时记忆。档案中的标题、顺序或保护区域与实时文档冲突时，以实时读取为准，并报告差异。

## 变更与归档

只有用户明确要求“记住”“更新文档档案”“信任该文档”“撤销信任”或等价动作时才修改 `.local/`。普通追加、回填或更正不得顺便更新档案或信任标识。

迁移前先把原件复制到 `.local/archive/`，记录文件名、大小、摘要和迁移时间，再从头生成清理后的活动记忆。归档原件不得发布。升级公共 Skill 时先备份安装目录中的 `.local/`，替换公共文件后原样恢复；既有安装档案优先，不隐式合并来源档案。
