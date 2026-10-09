# Paper Search · 机器人研究雷达

每天收集公开研究进展，使用本地 Codex 阅读和判断，再输出简洁的中文 HTML 简报。

**云端采集 → 本地语义评审 → 3–5 条重点 → 阅读/试验反馈。** 目标是理解方法能解决什么问题、有哪些限制、是否值得迁移到真实机器人。

## 工作方式

| 环节 | 运行位置 | 输出 |
| --- | --- | --- |
| arXiv 发现 | GitHub Actions，每天北京时间约 06:17 | 完整摘要、版本、采集健康 |
| 论文及官方发布评审 | 用户的 Codex 会话 / 本地自动化 | 证据、分类、行业重要性、迁移价值 |
| 日报 | 本地 | 横向比较表、方向标签、方法示意图、可展开依据 |
| 反馈与实验 | 本地 | 偏好和实际试验结果 |

云端无需模型 Key；GitHub 自带的工作流 token 仅用于提交公开 metadata。本地使用已登录的 Codex，受账号额度与电脑/App 可用性约束。电脑关闭期间云端继续采集，本地下一次运行补看积压；不会把规则分数伪装成模型判断。

## 开始

Python 3.11+：

```sh
python -m venv .venv
# 激活环境后
python -m pip install -r requirements-public.txt
# 将 review_profile.example.json 复制到 .review/profile.json，按需填写
python sync_catalog.py
python review.py prepare --backlog
```

然后让 Codex 按 [评审流程](docs/REVIEW_RUNBOOK.md) 阅读候选、核对原文、填写评审 JSON：

```sh
python review.py accept PACKET.json RESULT.json --output .review/batch.html
python review.py watch
python review.py compose SELECTION.json --output .review/reports/today.html
python review.py feedback PAPER_ID useful --reason "可借鉴的数据配对方式"
```

prepare 默认导出最近 7 天未评审候选；`--backlog` 不受日期限制，适合休息日后补看。`--ids` 可重评历史论文。compose 需要实际编辑选择，不按分数机械排序。未能阅读原始证据的候选不能进入重点。

## 检索与故障恢复

`discovery_queries.json` 包含 cs.RO 全量方向兜底与人类数据、适应、模型、部署、资源查询。显式布尔检索、按更新时间分页、完整摘要、跨查询去重；新版本重新进入待审。每个查询单独记录成功进度，重叠回看 3 天。初次增量回看 7 天并对齐 UTC 零点；另有独立的最近 30 天历史补采，每次推进一个七天窗口。可用 `python collect.py --backfill-windows 5` 尽快完成首次补采。每次对照官方 cs.RO recent 公告列表补齐缺项；健康状态单独报告历史进度和公告对账范围。

达到分页上限、API 报错或空页异常时，保留可用结果，报告失败且不推进该查询进度。GitHub Actions 会显示失败而不是生成假的“今日无更新”。默认 20 页/查询，触顶需增大 `--max-pages`。arXiv API 可能限流或延迟，非论文类官方发布由 Codex 补查；本系统不保证覆盖整个行业。

仓库默认分支 main 上的 Actions 包含测试和每日采集，也支持手动 Run workflow。定时任务可能延迟；请查看 Actions 与 `data/collection_health.json`，不要把提交时间当成准点保证。个人定时评审需在 Codex App 创建本地自动化，并使用上述 runbook；仅 clone 仓库不会自动获得模型评审。

## 数据边界

公开仓库仅放通用代码、示例配置和公开论文 metadata。`.review/` 内的实际硬件配置、评审、反馈、报告全部忽略；不发布公司仓库历史、凭据或本地论文库。不要把 `.review` 作为 CI artifact 上传。

## 验证

```sh
python -m unittest discover -s tests -p 'test_codex_review.py' -v
python -m unittest discover -s tests -p 'test_discovery.py' -v
```

测试覆盖分页、异常时保留进度、版本变化、全文摘要、评审完整性、证据门槛和 HTML 转义。格式校验不能证明模型的判断正确，仍需原文依据与实际实验反馈。
