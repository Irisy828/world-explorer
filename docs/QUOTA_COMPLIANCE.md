# 配额与合规说明

核对日期：2026-10-21。实施前和长期运行中应重新检查官方规则。

## 配额

本项目只调用 videos.list，每个请求 1 单位。默认 6 地区 × 最多 1 页，正常每日最多 6 单位；瞬时失败重试最多 3 次，默认最坏请求预算 18 单位。手动重跑、失败请求、同一 Cloud 项目其他程序也会消耗配额；每页均单独计费，以 Cloud Console 实际配额和用量为准。页数上限为5，可据地区数量预估 `地区数 × 页数 × 3` 的重试上限。

- [videos.list 官方参考](https://developers.google.com/youtube/data/reference/rest/v3/videos/list)
- [配额成本与重置规则](https://developers.google.com/youtube/data/determine_quota_cost)

## 数据生命周期

公开视频元数据仍是 API Data，不是可以任意永久存档的免费素材。普通未授权 API 数据应在30天内删除或刷新，且应及时响应适用的删除要求。本项目选择删除：运行目录最多25天、Actions产物保留2天，不用Git存储数据。下载、备份、日志和其他下游副本也由运营者负责；本地停止运行时应手动删除。源视频后续被删除或转私密时，旧快照不会实时同步删除；如收到删除要求，应立即清除相关历史、报告、artifact及副本，可直接清空全部数据重新开始。

不下载媒体，不转存封面，不获取非公开视频，不规避地域限制，不抓取网页，不采集个人账户授权数据。结果保留YouTube原始指标与来源链接，排序仅用于人工编辑候选，不声称为独立热度、全球代表性或官方榜单。不得把项目用于提供未经许可的API数据再分发服务；面向公众上线前，应按实际产品复核最低功能、归因和隐私政策要求。仅元数据不授予视频、图片、音频的再利用权；公众号转载或改编需另外取得许可。

- [YouTube API 开发者政策](https://developers.google.com/youtube/terms/developer-policies)
- [合规指南](https://developers.google.com/youtube/terms/developer-policies-guide)
- [YouTube 服务条款](https://www.youtube.com/t/terms)
- [Google 隐私政策](https://policies.google.com/privacy)

Key 仅放 GitHub Actions Secret `YOUTUBE_API_KEY` 或本地环境变量，不提交仓库、不写配置。若泄露应立即撤销/轮换。工作流只赋予仓库及 Actions 读取权限，不在 pull_request 工作流中使用 Key。GitHub运行日志不包含元数据正文；不把候选表写入永久 Job Summary。运行者仍须复核最新条款及其具体使用方式，本项目不代表已获官方审核。
