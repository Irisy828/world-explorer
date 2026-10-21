# V3 配额、数据与合规

核对日期：2026-10-21。正式使用前必须再次核对 Cloud Console 和政策，代码不能替运营方取得用途许可。

## 用途许可

YouTube Developer Policies III.E.4.h 对派生指标有限制。III.L 和 Additional policies for derived metrics and data storage 为适用的、经审核的 Analytics & Reporting 用途提供补充条款；官方示例包括按播放增长排名。本项目快照差值/榜单应由运营方确认用途已获得相应许可并接受补充条款，之后才设置 `YOUTUBE_ANALYTICS_APPROVAL_CONFIRMED=true`。变量默认缺失时 CLI 在任何 API 请求前拒绝真实采集。不声称“非官方”声明本身能豁免政策。离线合成数据测试无需许可。

- https://developers.google.com/youtube/terms/developer-policies
- https://developers.google.com/youtube/terms/derived-metrics-policy

## 配额

现行官方 search.list 文档为独立 Search Queries 桶，默认每日100调用、每次1单位；其他方法使用另一配额桶。不要沿用旧版“每次搜索100单位”的预算模型，也不要假定自己的 Cloud 项目已分配相同限额。

默认每轮6次 videos chart、6次 videoCategories、12次 search、至多60次 videos详情，共84次API调用；每6小时运行一次约48次 search、288次其他调用/日。轮内总上限100、搜索上限12，无自动重试。每轮只取一页，以降低配额；无法保证覆盖所有视频。

额外在本地历史中**请求发出前**原子记录尝试，恢复后沿用最近滚动24小时账本，最多80次 search、总请求400次（含失败尝试）。这个保守限制为手动重跑保留余量，按滚动窗口而非假设服务端重置时区。失败后也持久化账本。账本无法计算其他应用共用密钥的耗用，Cloud Console 是实际配额依据；请专用受限密钥，不拆项目轮换绕过配额。

- https://developers.google.com/youtube/data/reference/rest/v3/search/list
- https://developers.google.com/youtube/data/reference/rest/v3/videos/list
- https://developers.google.com/youtube/data/getting-started

## 安全与留存

密钥仅来自环境/Actions Secret，置于请求头而非URL；不输出请求、响应错误正文或认证数据。PR CI不注入 Secret；正式workflow仅main、checkout不保留Git凭据，只有持久化任务具contents:write。

只采公开视频元数据，不下载视频、不抓取页面、不绕过访问限制、不获取个人观看历史。每轮刷新候选公开状态；不可用视频会从本地快照中删除并移除旧报告。历史及报告7天滚动清理；发现来源时间最多25天；旧Release资产在新包上传核对成功后删除，避免把原始数据永久写入Git。Artifact副本2天且只在成功持久化后上传。公开仓库的Release与artifact不能视为私有保险箱。

静态快照在停机期间不会自毁；停用或持续故障时运营方必须删除Release资产、旧版V1/V2资产、artifacts及下载副本，遵守非授权数据最长30天及删除要求。实现不宣称无人维护时自动保证合规。代码默认7天保存统计，不使用补充条款可能允许的长期存储。其他条款、品牌展示、版权与发布责任仍适用。

## 指标含义

增量由本程序计算，清楚区别于YouTube原始累计值和官方榜单。它是约24小时窗口的公开计数差，不是独立观众、观看时长、地域播放数或实时分析；平台更新延迟与修订仍可能影响结果。视频全部类型可进入候选，但覆盖受地区、类别轮转、前页、30日新候选发现窗、14日退池与池容量限制。

mostPopular覆盖变化依据：https://developers.google.com/youtube/data/revision_history （2025-07-10条目）。公开计数定义可能变更，以官方文档为准；本程序不补偿或改写原始计数。
