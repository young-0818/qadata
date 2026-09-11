"""M7 票 02 双轨连库：预置 YAML 注册表（A 轨）＋导入输入面纯函数（B 轨）。

A 轨：YAML 名→路径→默认 evidence/口径，接管票 01 的目录发现（无 YAML 时回退）。
口径单一来源：同库已有 ``metrics/<db>.yaml`` 者，口径由本模块确定性派生
（零 LLM、零连接），YAML 再写 evidence＝第二真源，加载期拒绝——禁双写漂移。
B 轨：上传落盘与本地路径直连的输入面校验（纯函数，建连接归沙箱唯一入口）。
权限校验层（魔数/大小帽/WAL）owner 裁暂缓（spec 数据节）；但输入面绝不
接受非 sqlite 引擎串/远程 URL 形态。本模块只写文件，从不打开数据库连接。
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from qadata.graph.metrics import Metric, load_registry

# 演示库根目录（YAML 注册表缺失时的回退发现，qadata serve --db-dir 可覆盖）
DEFAULT_DB_DIR = "data/bird/dev/dev_databases"
# A 轨预置注册表（仓库根，与 metrics/ 同款 cwd 约定；--registry 可覆盖）
DEFAULT_REGISTRY_PATH = "dbs.yaml"
# B 轨上传落盘目录（data/ 已在 .gitignore，不入库；重启后由 discover_imports 恢复）
DEFAULT_IMPORT_DIR = "data/web_imports"

# sqlite 家族扩展名白名单——输入面只认本地 sqlite 文件形态
SQLITE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})


class DbRegistryError(ValueError):
    """注册表/导入输入面的诚实拒绝（带病配置与越界输入一律拒，不带病运行）。"""


@dataclass(frozen=True)
class DbEntry:
    """库注册表的一行：列表展示名（唯一）→ 路径 → 默认口径 → 来源轨。"""
    name: str
    path: str
    evidence: str = ""
    source: str = "preset"  # "preset"（A 轨）| "import"（B 轨）


def discover_dbs(root: str | Path) -> dict[str, str]:
    """扫描 root 下 <name>/<name>.sqlite，返回 {库名: 路径}；目录缺失返回空 dict。

    BIRD 风格目录约定，不符合者诚实跳过（宁缺勿错，与指标层同气）。
    票 02 起仅作 dbs.yaml 缺失时的回退路径。
    """
    out: dict[str, str] = {}
    base = Path(root)
    if not base.is_dir():
        return out
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / f"{child.name}.sqlite"
        if candidate.is_file():
            out[child.name] = str(candidate)
    return out


# ── A 轨：YAML 预置注册表 ───────────────────────────────────────────


def load_preset_registry(path: str | Path, *, metrics_dir: str | Path = "metrics") -> dict[str, DbEntry]:
    """读 dbs.yaml（databases: 名→{path, evidence?}），返回 {名: DbEntry}。

    相对 path 一律以 YAML 所在目录为基准解析（cwd 无关）。有指标注册表的库
    禁在 YAML 写 evidence（双写＝第二真源），口径改由指标注册表派生。
    """
    p = Path(path)
    if not p.is_file():
        raise DbRegistryError(f"预置注册表文件不存在：{p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("databases"), dict):
        raise DbRegistryError("dbs.yaml 需要顶层 'databases' 映射（名→{path, evidence?}）")
    out: dict[str, DbEntry] = {}
    for name, spec in data["databases"].items():
        if not isinstance(spec, dict) or not str(spec.get("path") or "").strip():
            raise DbRegistryError(f"预置库 '{name}' 缺少非空 path，拒绝加载（不带病运行）")
        raw_path = str(spec["path"]).strip()
        resolved = Path(raw_path)
        if not resolved.is_absolute():
            resolved = (p.parent / resolved).resolve()
        evidence = str(spec.get("evidence") or "")
        metric_file = Path(metrics_dir) / f"{name}.yaml"
        if metric_file.is_file():
            if evidence:
                raise DbRegistryError(
                    f"预置库 '{name}' 已有指标注册表 {metric_file}，"
                    "dbs.yaml 不得再写 evidence——口径单一来源，禁双写漂移"
                )
            evidence = metric_evidence_text(name, load_registry(metric_file))
        out[str(name)] = DbEntry(name=str(name), path=str(resolved), evidence=evidence)
    return out


def metric_evidence_text(db_name: str, metrics: Sequence[Metric]) -> str:
    """从指标注册表确定性派生默认口径文本（演示摘录，零 LLM）。

    头行指回真源并标注「演示摘录，非第二真源」；每指标一行，多行 definition
    以全角分号承接（evidence 通道是喂 prompt 的自由文本，不炸排版）。
    """
    src = f"metrics/{db_name}.yaml"
    lines = [f"口径摘录（摘自 {src}，演示摘录，非第二真源，权威以指标注册表为准）："]
    for m in metrics:
        flat = "；".join(part.strip() for part in m.definition.splitlines() if part.strip())
        lines.append(f"- {m.display_name}（{m.name}）：{flat}")
    return "\n".join(lines)


# ── B 轨：导入输入面 ────────────────────────────────────────────────


# 引擎串/远程 URL＝scheme 形态（mysql://、sqlite:///、http://、file:/…）：
# 要求 scheme ≥2 字符再跟 :/ ——单字母打头交给盘符豁免（评审收紧：一刀切 ':/'
# 会误杀 UI 示例教用户填的 D:/data/my.db 正斜杠盘符路径）
_ENGINE_FORM = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:/")
# Windows 文件系统保留字符＋分隔符（别名会参与上传落盘命名，不拦＝OSError 变 500）
_ALIAS_BAD_CHARS = set(':*?"<>|/\\')


def sanitize_alias(raw: str) -> str:
    """列表展示别名：剥空白、拒空、拒路径分隔符、'..' 与文件系统保留字符。"""
    alias = (raw or "").strip()
    bad = bool(set(alias) & _ALIAS_BAD_CHARS) or any(ord(c) < 32 for c in alias)
    if not alias or ".." in alias or bad:
        raise DbRegistryError(f"别名不可用：{raw!r}（需非空，不含路径分隔符/保留字符 :*?\"<>|）")
    return alias


def validate_import_path(raw: str) -> str:
    """本地路径直连的输入面校验：只收存在的本地 sqlite 文件，拒引擎串/URL 形态。

    形态拒绝＝scheme 正则（见 _ENGINE_FORM 的盘符豁免注释）；拒绝后仍是
    「存在的本地 sqlite 文件」才收。路径里的 ?/# 等字符不改名不解析——
    连接形态一律由沙箱唯一入口 open_readonly 的 as_uri 转义产出。
    """
    s = (raw or "").strip()
    if _ENGINE_FORM.match(s):
        raise DbRegistryError("只接受本地文件路径，拒绝引擎串/URL 形态输入")
    p = Path(s).expanduser()
    if not p.is_file():
        raise DbRegistryError(f"文件不存在或不是本地文件：{s}")
    if p.suffix.lower() not in SQLITE_SUFFIXES:
        raise DbRegistryError(f"扩展名须为 {sorted(SQLITE_SUFFIXES)} 之一：{p.name}")
    return str(p.resolve())


def store_upload(import_dir: str | Path, data: bytes, filename: str) -> Path:
    """上传字节落盘 web_imports/<basename>，返回目标路径。

    文件名须为纯 basename＋sqlite 扩展名（带分隔符＝形态异常，宁缺勿错拒绝；
    空字节拒绝——0 字节文件连「不是数据库」都无从谈起）。本函数无条件覆写，
    「同名已存在」的 409 拒绝归调用方（那里才看得见注册表）。
    """
    if not data:
        raise DbRegistryError("上传内容为空")
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise DbRegistryError(f"上传文件名须为不含目录的纯文件名：{filename!r}")
    if Path(name).suffix.lower() not in SQLITE_SUFFIXES:
        raise DbRegistryError(f"只接受 sqlite 文件（{sorted(SQLITE_SUFFIXES)}）：{name}")
    base = Path(import_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    dest = base / name
    dest.write_bytes(data)
    return dest


def discover_imports(root: str | Path) -> dict[str, DbEntry]:
    """重启恢复：扫描 web_imports 平铺目录里的 sqlite 文件，登记为导入轨。"""
    out: dict[str, DbEntry] = {}
    base = Path(root)
    if not base.is_dir():
        return out
    for f in sorted(base.iterdir()):
        if f.is_file() and f.suffix.lower() in SQLITE_SUFFIXES:
            out[f.stem] = DbEntry(name=f.stem, path=str(f), source="import")
    return out
