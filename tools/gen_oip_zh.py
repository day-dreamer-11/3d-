"""重新生成 OIP 中文词典 frontend/oip/src/editor/zh-cn.ts。

输入：
- data/logs/oip-catalog.json：i18n 消息 id → 英文源文（由 ?hook=1 的 messageCatalog() 导出）；
- data/logs/zh-map-*.json：英文源文 → 中文（人工维护）。

缺省条目回退英文，不影响运行。生成后需重新构建 OIP：cd frontend/oip && npm run build。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "data" / "logs"
OUT = ROOT / "frontend" / "oip" / "src" / "editor" / "zh-cn.ts"

catalog: dict[str, str] = json.loads((LOGS / "oip-catalog.json").read_text(encoding="utf-8"))
zh: dict[str, str] = {}
for path in sorted(LOGS.glob("zh-map-*.json")):
    zh.update(json.loads(path.read_text(encoding="utf-8")))

placeholder = re.compile(r"\{[^}]+\}")
ids: dict[str, str] = {}
for key, source in catalog.items():
    if key.endswith(("#one", "#other")):
        continue
    text = zh.get(source)
    if text is None:
        continue
    if sorted(placeholder.findall(source)) != sorted(placeholder.findall(text)):
        raise SystemExit(f"占位符不一致：{key}")
    ids[key] = text

lines = [
    "// AIC: 打包的中文词典，由 initLanguage 在启动时装入（上游 OIP 不自带翻译）。",
    "// 键为 i18n 消息 id，来源见 src/core/i18n.ts；缺省项回退英文。",
    "// 重新生成：python tools/gen_oip_zh.py（改完 data/logs/zh-map-*.json 与 oip-catalog.json 后）。",
    "export const ZH_CN: Record<string, string> = {",
]
for key in sorted(ids):
    lines.append(f"  {json.dumps(key)}: {json.dumps(ids[key], ensure_ascii=False)},")
lines.append("}")
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {len(ids)} entries -> {OUT}")
