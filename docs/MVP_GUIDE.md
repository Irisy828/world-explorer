# 世界漫游指南

多地区 YouTube 公共元数据采集与每日选题候选榜。Python 3.12+，只使用标准库，无需服务器。创建日期：2026-10-21。交付状态：离线测试通过；真实 API 和 GitHub Actions 尚未运行。目标仓库：Irisy828/world-explorer；以分支和 PR 交付。

## 快速体验（无需 Key）

```sh
python collector.py --demo --output work/demo
python -m unittest discover -s tests -v
```

模拟视频及播放量为虚构，不可用于选题事实。正式运行前不要把模拟目录作为生产历史。

## 在自己的 GitHub 仓库启用

1. 审阅并合并本项目 PR 到仓库默认分支 main。每日调度和首次手动触发需工作流已存在于默认分支。不要提交 data、work、outputs 或任何密钥。
2. Google Cloud 项目启用 YouTube Data API v3。将现有 Key 的 API 限制设为仅 YouTube Data API v3。GitHub 托管 runner 的出口 IP 可变化，勿直接配置为个人电脑 IP 限制。
3. 仓库 Settings → Secrets and variables → Actions → New repository secret，名称必须为 `YOUTUBE_API_KEY`，值只在 GitHub 页面填写。无需把 Key 发给助手。
4. Actions → Daily World Guide → Run workflow 手动运行。完成后打开运行页面的 Artifacts，下载 `world-guide-data`。按天生成 `reports/YYYY-MM-DD.md`、同名 CSV，以及 `history/YYYY-MM-DD.json`。
5. 定时任务每天 UTC 00:17，即北京时间 08:17，使用默认分支版本。GitHub 调度可能延迟或漏跑；公共仓库长期无活动可能停用定时任务，需检查 Actions。它不是可靠的准点调度服务。

项目不会自动发布公众号、推送代码、创建仓库或部署网站。公开仓库的产物可能被他人访问，请按用途选择仓库可见性。

## 本地正式运行

把 Key 通过操作系统/终端的环境变量 `YOUTUBE_API_KEY` 注入，然后执行 `python collector.py`。不要在命令历史或脚本里写明 Key，不读取或提供 `.env`。缺少 Key 会明确失败。默认输出 data/，支持 `--config` 和 `--output`。

## 配置与榜单含义

编辑 `config.json`：

| 配置 | 含义 |
| --- | --- |
| regions | 默认 US、GB、JP、KR、TW、AU；需要是 YouTube 支持的内容地区 |
| pages_per_region | 每地区最多页数，1–5；实际遇到无下一页即停止 |
| page_size | 每页 1–50 条 |
| top_n | 去重筛选后保留 1–500 条 |
| include_keywords | 标题/描述中任意关键词匹配，忽略大小写；空列表不过滤 |
| exclude_keywords | 任意命中则排除 |
| history_days | 1–25 天；同一天重复运行覆盖当天文件 |

来源为 `videos.list(chart=mostPopular)`。它并非全站采集，榜单覆盖受 YouTube 当前规则和地区支持限制，不能保证覆盖旅游内容。默认不过滤，便于先查看实际返回；旅行选题可填入 travel、walking、旅行等多语关键词，但只会过滤已获得的热门榜，不能搜索榜外视频。地区不是拍摄地点或作者国籍。按视频 ID 合并地区，按 API 累计 viewCount 降序、ID 次序打破同值排序；不构造新的“热度分”、增速或跨平台指标。缺失播放量留空并排在末尾，最终由人工审核相关性、事实、版权和时效。

## 历史、存储与故障

历史仅保留每天最终候选的轻量元数据，不存描述正文、图片、视频、音频、字幕、评论或完整响应。保留窗口默认 25 个北京时间日期；每次运行开始清理过期文件。Actions 从同分支 daily.yml 最近一次成功运行的未过期同名 artifact 恢复滚动历史，新 artifact 只保留 2 天，因此副本中的最老数据也留有低于 30 天的余量。数据不提交 Git，避免删除当前文件后旧版本仍长期存在。

连续两天未成功运行可能失去历史，下一次从空历史继续；这是短期可过期存储的取舍。断档不补造。API 任一区域失败即整体失败，不输出貌似完整的当日榜；403 立即失败，网络错误、429、5xx 最多尝试 3 次。过期数据清理在请求前执行。本地使用者须自行定期运行清理或删除 data；停跑不会自动清理电脑或已下载的副本。

上传 artifact 在采集成功后执行，失败时不会上传旧榜。历史恢复失败会使任务失败，避免静默丢失；没有未过期 artifact 才允许首次启动。每次 artifact 含完整滚动窗口，GitHub 在有效期内可能保留多个副本。不要把这些产物另行长期备份或永久提交到仓库。

## 配额与合规

见 [docs/QUOTA_COMPLIANCE.md](docs/QUOTA_COMPLIANCE.md)。本项目只读公开元数据，不绕过授权和访问限制，Key 从环境读取并通过请求头发送；不输出请求 URL、响应原文或密钥。CSV 对公式前缀做转义，Markdown 对外部文本做转义。

## 文件

`collector.py` 采集、导出和清理；`config.json` 参数；`.github/workflows/daily.yml` 每日采集及短期历史；`tests/` 离线行为测试；`demo_data.py` 虚构数据。没有第三方依赖。

验收范围：离线验证多地区去重、关键词、隐私过滤、分页、缺失统计、导出、保留清理及错误脱敏。实际 Key 权限、地区可用性、真实配额及 Actions 需由你的首次手动运行确认。
