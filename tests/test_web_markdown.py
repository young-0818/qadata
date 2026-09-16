"""M8 票 07 纪律测：markdown 渲染器零 raw HTML＋零新依赖＋分节白名单与后端单源一致。

壳不判卷、CI 保持纯 Python（M7 沿袭），但票 07 的三条底线都用源码扫描钉死
（调用面源码扫描先例＝on_event）：
① textContent 纪律——渲染器与被接线文件不得出现任何 raw HTML 通道；
② 零新依赖——package.json 运行时依赖冻在 react/react-dom/recharts 三件套；
③ 前端分节白名单＝后端 compose_conclusion 节头集合——两处字面漂移＝报告被切碎片。
渲染元素实景归真链路冒烟眼验（票面勾④）。
"""
import inspect
import json
import re
from pathlib import Path

from qadata.graph.prompts import compose_conclusion

WEB = Path(__file__).resolve().parents[1] / "web"


def _src(name: str) -> str:
    return (WEB / "src" / name).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """剥 // 行注释与 /* */ 块注释再扫——纪律注释本身写着违禁词是文化不是违规
    （App.tsx 头部就是「全文件禁 dangerouslySetInnerHTML」的自述）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(re.sub(r"//.*", "", line) for line in src.splitlines())


def test_markdown_renderer_and_conclusion_wiring_emit_no_raw_html():
    for f in ("Markdown.tsx", "App.tsx"):
        src = _strip_comments(_src(f))
        for forbidden in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML",
                          "insertAdjacentHTML", "document.write"):
            assert forbidden not in src, f"{f} 违 textContent 纪律：{forbidden}"


def test_conclusion_section_wired_to_markdown():
    """接线单钉：结论段确实走渲染器（不接线＝markdown 原文糊成一行）。"""
    assert "<Markdown" in _src("App.tsx")


def test_report_upgrade_adds_no_new_dependency():
    pkg = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    assert set(pkg["dependencies"]) == {"react", "react-dom", "recharts"}


def test_section_whitelist_matches_backend_section_titles():
    """SECTION_RE 白名单必须与 compose_conclusion 实际产出的节头一字不差——
    多认＝报告正文里的【…】被误切成节；少认＝真节混进报告正文丢了折叠样式。"""
    backend = set(re.findall(r"【(.+?)】", inspect.getsource(compose_conclusion)))
    assert backend == {"结论", "数据依据", "口径说明", "校验标注"}  # 后端加节头须过本钉
    m = re.search(r"SECTION_RE\s*=\s*/\^【\((.+?)\)】", _src("App.tsx"))
    assert m, "App.tsx SECTION_RE 白名单形状变了，接线纪律失效"
    assert set(m.group(1).split("|")) == backend
