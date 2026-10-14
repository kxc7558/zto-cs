"""制作客服交付安装包（白名单打包，绝不裸拷目录）。

用途：管理员给客服发一个干净的 zip/folder，而不是手拷 d:\\zto-cs。
白名单收录 = 只收运行所需的代码/脚本/配置/文档；显式排除一切密钥以外的敏感物。

收录：
  安装.bat / 安装教程.md / README.md / requirements.txt
  api/ service/ shared/ scripts/ tests/   （*.py、*.bat，不含 __pycache__）
  db/*.py                                （账本/指标代码，非数据）
  config/*.yaml config/reply_wiki.md config/VERSION config/bridge_secret.key
排除（防泄密 / 防串机）：
  .venv/ bak/ update-server/ audit/ logs/ .git/ __pycache__/ *.pyc
  db/*.db db/*.json                       （业务数据，不进包）
  config/machine_id                       （每机独有，装时生成，串机风险）
  config/access_code.txt                  （每机独有，装时生成并显示）
  tmp_fill.png                            （调试产物）

用法：python scripts/make_delivery.py [版本号]
输出：dist/zto-cs-<版本>/ （文件夹）+ dist/zto-cs-<版本>.zip
"""
import io
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# 顶层单文件
TOP_FILES = ["安装.bat", "安装教程.md", "README.md", "requirements.txt"]
# 整目录收录（过滤见 _excluded）
DIRS = ["api", "service", "shared", "scripts", "tests"]
# 目录内只收这些后缀
KEEP_SUFFIX = {".py", ".bat", ".html", ".json", ".md", ".yaml", ".yml", ".txt"}
# 排除的目录名/文件名（任意层级）
EXCLUDE_DIRS = {"__pycache__", ".venv", "bak", "update-server", "audit", "logs", ".git",
                "dist", ".pytest_cache"}
EXCLUDE_NAMES = {"machine_id", "access_code.txt", "tmp_fill.png",
                 "zto-cs.db", "bridge_cache.json", "metrics.json"}
# db/ 只收 .py
DB_ONLY_SUFFIX = {".py"}
# config/ 收录清单
CONFIG_KEEP = {"settings.yaml", "reply_templates.yaml", "reply_wiki.md", "VERSION",
               "bridge_secret.key"}


def _excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if rel.name in EXCLUDE_NAMES:
        return True
    return False


def collect() -> list[Path]:
    files: list[Path] = []
    for name in TOP_FILES:
        p = ROOT / name
        if p.exists():
            files.append(p)
    for d in DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            rel = p.relative_to(ROOT)
            if p.is_file() and not _excluded(rel) and p.suffix.lower() in KEEP_SUFFIX:
                files.append(p)
    dbp = ROOT / "db"
    if dbp.exists():
        for p in dbp.glob("*.py"):
            files.append(p)
    cfg = ROOT / "config"
    for name in CONFIG_KEEP:
        p = cfg / name
        if p.exists():
            files.append(p)
    return files


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "0.2.0"
    files = collect()
    if not any(f.name == "bridge_secret.key" for f in files):
        print("[警告] config/bridge_secret.key 缺失——客服将无法通过远程更新鉴权，请先生成。")
    out_dir = DIST / f"zto-cs-{version}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for p in files:
        rel = p.relative_to(ROOT)
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)

    # 打 zip
    zpath = DIST / f"zto-cs-{version}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            rel = p.relative_to(ROOT)
            zf.write(p, arcname=str(Path(f"zto-cs-{version}") / rel))

    # 自检：包里不得出现敏感物
    leak = [n for n in zipfile.ZipFile(zpath).namelist()
            if any(x in n for x in ("machine_id", "access_code.txt", ".db", "tmp_fill.png",
                                    "publish_key", ".venv", "bak/"))]
    print(f"[完成] 版本 {version}，{len(files)} 个文件")
    print(f"  文件夹：{out_dir}")
    print(f"  zip   ：{zpath}")
    if leak:
        print("[严重] 包内混入敏感文件，请检查排除规则：", leak)
        sys.exit(2)
    print("  [自检] 无 machine_id/access_code/数据库/发布密钥混入 [OK]")
    print("  提示：客服解压后双击 安装.bat；安装会生成每机 machine_id 与访问码并显示。")


if __name__ == "__main__":
    main()
