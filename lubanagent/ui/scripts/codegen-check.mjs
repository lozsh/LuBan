// codegen:check —— 从仓库根 openapi.snapshot.json 重生成 schema，与入仓产物 diff。
// 快照由后端测试 tests/lubanagent/api/test_openapi_snapshot.py 导出（契约 D4-1 决策 3）。
import { execFileSync } from 'node:child_process';
import { readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const uiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
// 仓库根 = lubanagent/ui 的上两级（lubanagent/ui → lubanagent → repo root）
const snapshot = join(uiRoot, '..', '..', 'openapi.snapshot.json');
const committed = join(uiRoot, 'src', 'api', 'schema.d.ts');
const tmpOut = join(uiRoot, 'src', 'api', '.schema.check.ts');
const bin = join(uiRoot, 'node_modules', '.bin', 'openapi-typescript');

try {
  execFileSync(bin, [snapshot, '-o', tmpOut], { stdio: 'inherit' });
  const before = readFileSync(committed, 'utf8');
  const after = readFileSync(tmpOut, 'utf8');
  if (before !== after) {
    console.error(
      'codegen:check FAILED —— src/api/schema.d.ts 与 openapi.snapshot.json 不一致。\n' +
        '后端 API 变更后请重跑：pytest tests/lubanagent/api/test_openapi_snapshot.py && npm run codegen（或从快照重生成）。',
    );
    process.exit(1);
  }
  console.log('codegen:check OK —— schema.d.ts 与 openapi.snapshot.json 一致');
} finally {
  rmSync(tmpOut, { force: true });
}
