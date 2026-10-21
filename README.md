# world-explorer
Youtube trends crawler for World Explorer


## 世界漫游指南 · YouTube 采集 MVP

Python 3.12+，无第三方依赖。默认采集 US、GB、JP、KR、TW、AU 的 YouTube 热门视频，按视频 ID 合并地区、按累计播放量生成前 50 个候选。关键词和地区可在 `config.json` 修改。

### 启用（合并 PR 后）

1. 在本仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加 `YOUTUBE_API_KEY`。只在 GitHub 填入值，不写代码、不发到聊天。Google Cloud 需启用 YouTube Data API v3。
2. 打开 **Actions → Daily World Guide → Run workflow → main → Run workflow**。
3. 等待运行绿色成功，打开运行页面底部 **Artifacts → world-guide-data** 下载。
4. 检查 `reports/YYYY-MM-DD.md` 和 CSV 候选榜，以及 `history/YYYY-MM-DD.json`：`demo` 应为 `false`，`regions` 含配置的六个地区，视频 ID 不重复。空榜时检查关键词。

此后每天北京时间 **08:17** 自动运行（GitHub 调度可能延迟）。运行失败请查看失败步骤：缺 Secret、403、配额或地区支持错误都会明确失败。PR 中的 Offline tests 无需 Secret。

### 本地验证

```sh
python -m unittest discover -s tests -v
python collector.py --demo --output work/demo
```

模拟结果仅验证流程。正式运行从环境变量读取 `YOUTUBE_API_KEY`，执行 `python collector.py`。

### 历史和指标

同一天重跑更新当天快照，跨天独立保存；同一视频可在不同日期重复出现，以便观察趋势。只保留最近 25 天轻量元数据，滚动历史通过 Actions artifact 延续，每份 artifact 保留 2 天。连续两天未成功运行可能失去历史；数据不提交 Git。各地区任一请求失败则不生成当日榜。

这是热门榜候选采集，不是旅游视频搜索；地区表示榜单市场，累计播放量不等于当日增长。旅行相关性需人工审核，也可用关键词筛选。API 行为依据 [YouTube videos.list 官方文档](https://developers.google.com/youtube/data/reference/rest/v3/videos/list)。

完整配置、故障排查、配额与保留策略见 [使用指南](docs/MVP_GUIDE.md) 和 [配额及数据说明](docs/QUOTA_COMPLIANCE.md)。
