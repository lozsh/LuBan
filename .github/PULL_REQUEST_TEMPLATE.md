<!-- 提交前请确认：CI 全绿；若改动涉及接口/数据/行为/边界契约，已同步 docs/ 下对应规格 -->

## 这个 PR 做了什么

<!-- 一两句话说清改动内容与动机 -->

## 改动类型

- [ ] `[framework]` 框架领地（`lubanagent/`）
- [ ] `[scaffold-seed]` 脚手架种子（`app/`）
- [ ] `[shared]` 共享领地（demos / tests / docs / docker / scripts / migrations）
- [ ] `[migration]` DB schema 变更（附 alembic 升级与回滚）
- [ ] `[breaking]` 不兼容变更（升 major，附迁移示例）

## 自查清单

- [ ] 测试覆盖了改动（新增或修改），`python -m pytest` 全绿
- [ ] 涉及前端改动时 `lubanagent/ui` 下 vitest 全绿
- [ ] CHANGELOG.md 已按领地标签补条目
- [ ] 涉及契约（接口 / 数据 / 行为 / 边界）时已同步 `docs/specs/` 对应模块规格
- [ ] 文档影响已评估（用户手册 / 教程是否需要更新）

## 验证方式

<!-- 复现/验证步骤，让 reviewer 能亲手确认 -->
