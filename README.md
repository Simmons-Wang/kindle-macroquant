# kindle-macroquant

MacroQuant Intelligence：每日宏观与市场结构看板，显示在越狱 Kindle（8 代，600×800 墨水屏）上。

- `PROMPT.md`：每次定时生成时遵循的调研与发布规则
- `data/dashboard.json`：当期内容（五个模块）
- `render/render.py`：JSON → HTML → Playwright 截图 → 16 级灰度 PNG，自动缩放到单屏
- `out/dashboard.png`、`out/meta.txt`：Kindle 拉取的成品；meta 里写着下次拉取时间（UTC epoch）
- `kindle/`：Kindle 端脚本
  - `mq.sh` → `/mnt/us/macroquant/mq.sh`
  - `MacroQuant_Test.sh`、`MacroQuant_Start.sh` → `/mnt/us/documents/`（在书库里点开运行）

发布时间（伦敦）：周日至周四 20:36（PM 版，下一交易日），周一至周五 06:06（AM 版，当天）。
Kindle 在发布后约 40 分钟唤醒拉图；电池模式下其余时间深度休眠。退出：长按电源键 20–40 秒重启。
