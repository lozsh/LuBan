#!/usr/bin/env bash
# spec-coding 最低门槛检查（双模式）
# 用法:
#   bash verify.sh <目标仓库> --mode forward   # 正向：类型/lint/测试
#   bash verify.sh <目标仓库> --mode sync      # 逆向：docs 结构 + 占位替换
# 退出码: 0=通过 1=门槛未过 2=参数错误
# 注意: 此脚本只做最低门槛检查，不能代替 strict-validation subagent。

set -u

if [ $# -lt 2 ]; then
  echo "用法: bash verify.sh <目标仓库> --mode forward|sync" >&2
  exit 2
fi

REPO="$1"
MODE=""
for a in "$@"; do
  case "$a" in
    --mode) : ;;
    forward|sync) MODE="$a" ;;
  esac
done

if [ -z "$MODE" ]; then
  echo "✗ 缺少 --mode forward|sync" >&2
  exit 2
fi
if [ ! -d "$REPO/.git" ]; then
  echo "✗ 不是 Git 仓库: $REPO" >&2
  exit 1
fi

cd "$REPO" || exit 1

# ───────────────── 正向：类型 / lint / 测试 ─────────────────
forward_check() {
  echo "正向门槛检查：$REPO"
  local fail=0

  # 探测语言
  local has_py=0 has_node=0 has_jvm=0 has_go=0
  [ -f pyproject.toml ] || [ -f setup.py ] && has_py=1
  [ -f package.json ] && has_node=1
  [ -f pom.xml ] || [ -f build.gradle ] || [ -f build.gradle.kts ] && has_jvm=1
  [ -f go.mod ] && has_go=1

  # 类型检查（有则跑，缺工具跳过并注明）
  if [ "$has_py" = 1 ]; then
    if command -v mypy >/dev/null 2>&1; then
      python -m mypy . >/dev/null 2>&1 && echo "  ✓ mypy 类型检查通过" || { echo "  ✗ mypy 类型检查失败"; fail=1; }
    else echo "  · mypy 未安装，跳过类型检查"; fi
    if command -v ruff >/dev/null 2>&1; then
      ruff check . >/dev/null 2>&1 && echo "  ✓ ruff lint 通过" || { echo "  ✗ ruff lint 失败"; fail=1; }
    else echo "  · ruff 未安装，跳过 lint"; fi
    if command -v pytest >/dev/null 2>&1; then
      python -m pytest -q >/dev/null 2>&1 && echo "  ✓ pytest 通过" || { echo "  ✗ pytest 失败"; fail=1; }
    else echo "  · pytest 未安装，跳过测试（验收标准要求的测试须手动确认）"; fi
  fi

  if [ "$has_node" = 1 ]; then
    [ -f node_modules/.bin/tsc ] && (npx tsc --noEmit >/dev/null 2>&1 && echo "  ✓ tsc 类型检查通过" || { echo "  ✗ tsc 失败"; fail=1; }) || echo "  · tsc 不可用，跳过类型检查"
    if grep -q '"test"' package.json 2>/dev/null; then
      npm test --silent >/dev/null 2>&1 && echo "  ✓ npm test 通过" || { echo "  ✗ npm test 失败"; fail=1; }
    else echo "  · package.json 无 test 脚本，跳过测试（验收标准要求的测试须手动确认）"; fi
  fi

  if [ "$has_jvm" = 1 ]; then
    if [ -x ./mvnw ]; then ./mvnw test -q >/dev/null 2>&1 && echo "  ✓ mvn test 通过" || { echo "  ✗ mvn test 失败"; fail=1; }
    elif [ -x ./gradlew ]; then ./gradlew test -q >/dev/null 2>&1 && echo "  ✓ gradle test 通过" || { echo "  ✗ gradle test 失败"; fail=1; }
    else echo "  · 无 Maven/Gradle wrapper，跳过测试"; fi
  fi

  if [ "$has_go" = 1 ]; then
    command -v go >/dev/null 2>&1 && { go vet ./... >/dev/null 2>&1 && echo "  ✓ go vet 通过" || { echo "  ✗ go vet 失败"; fail=1; }
      go test ./... >/dev/null 2>&1 && echo "  ✓ go test 通过" || { echo "  ✗ go test 失败"; fail=1; }; } || echo "  · go 未安装，跳过"
  fi

  [ "$has_py" = 0 ] && [ "$has_node" = 0 ] && [ "$has_jvm" = 0 ] && [ "$has_go" = 0 ] && echo "  · 未识别语言工具链，无自动门槛可跑"

  echo
  if [ "$fail" -gt 0 ]; then echo "✗ 正向门槛未通过"; exit 1; fi
  echo "✓ 正向最低门槛通过"
  echo "  → 仍需 strict-validation subagent（正向模式）做严格审稿，不可据此判定最终通过。"
}

# ───────────────── 逆向：docs/specs/ 结构 + 占位替换 ─────────────────
sync_check() {
  echo "逆向门槛检查：$REPO"
  local REQUIRED=(
    "docs/specs/index.md" "docs/specs/glossary.md" "docs/specs/risks.md"
  )
  local missing=0
  for f in "${REQUIRED[@]}"; do
    [ -f "$f" ] && echo "  ✓ $f" || { echo "  ✗ $f (缺失)"; missing=$((missing + 1)); }
  done

  # 至少一个模块文档
  local found
  found=$(find docs/specs/modules -maxdepth 1 -name "*.md" ! -name "_module-template.md" 2>/dev/null | head -1)
  [ -n "$found" ] && echo "  ✓ docs/specs/modules/ 至少一个模块文档" || { echo "  ✗ docs/specs/modules/ 无模块文档"; missing=$((missing + 1)); }

  # 关键占位已替换
  local placeholder=0
  [ -f "docs/specs/index.md" ] && grep -q "\[TODO: 系统名\]" "docs/specs/index.md" 2>/dev/null && { echo "  ✗ docs/specs/index.md 仍含未替换 [TODO: 系统名]"; placeholder=$((placeholder + 1)); }
  if [ -n "$found" ]; then
    grep -q "\[TODO: 模块名\]" "$found" 2>/dev/null && { echo "  ✗ $found 仍含未替换 [TODO: 模块名]"; placeholder=$((placeholder + 1)); }
  fi

  echo
  if [ "$missing" -gt 0 ] || [ "$placeholder" -gt 0 ]; then
    echo "✗ 逆向门槛未通过（缺 $missing 个文件，$placeholder 个未替换占位）"
    exit 1
  fi
  echo "✓ 逆向最低门槛通过（docs/specs/ 结构完整、关键占位已替换）"
  echo "  → 仍需 strict-validation subagent（逆向模式）做严格审稿，不可据此判定最终通过。"
}

case "$MODE" in
  forward) forward_check ;;
  sync) sync_check ;;
esac
