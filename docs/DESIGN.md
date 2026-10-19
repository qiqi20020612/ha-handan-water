# 实现设计

## 最小版本边界

独立 domain 为 `handan_water`，一个配置条目对应一个供水户号。原生 config_flow、reconfigure、options；户号标准化后以 SHA-256 生成稳定内部身份，不在条目标题、异常信息和统计 ID 中公开户号。先 HTTPS 验证，明确同意后允许 HTTP。使用独立 DummyCookieJar，不读取浏览器或微信状态。

DataUpdateCoordinator 每 24 小时刷新；账户和账单是核心查询，缴费和网站分析是可分别失败的附加数据。附加查询失败时相关实体不可用，不显示旧数据为最新。核心失败使本次全部实体不可用并保留缓存。没有数据时为 unknown/空历史，不伪造零；服务明确返回 0 才显示 0。

账户为父设备，各真实 rlmcode 为子设备。所有实体中文名称，稳定 unique_id 与设备标识基于“账户身份 + 原始水表编号”的哈希；原始水表编号可在本地设备名称/属性查看。新表自动发现，当前查询不再包含某表时保留身份但不可用；仅有历史账单不能推断退役时间。不存在跨水表的累计读数实体。

## 本地历史账本

HA Store 按配置条目持久化仅必要的规范化账单、交易和同步状态；保存 Decimal 为字符串，不保存完整响应、姓名/地址/电话/openid。新查询合并账本，不因滚动一年的窗口而丢失旧数据。账单以 rlid 的哈希去重；同一读数区间重复账单不会再次增加用水。交易按 pid 的哈希去重。

水表观测由每张账单的两个端点构成，依据真实抄表日排序、同日同读数合并。`sum = 当前累计读数 - 此水表首个已知端点读数`，首点 sum=0。不将账期 rlsl 加进任意单日统计，不按月份排序读数，不做每日插值，也不生成缺失日的行。日期只有天，统一用该日 Asia/Shanghai 00:00 的时间桶，并在属性中声明日期精度；不声称已知抄表小时。

每块表独立验证：同日相反读数、区间用量不符、读数回退、区间重叠且观测矛盾均阻止该表导入及累计实体更新。新编号换表自然成为新设备/新统计；同编号换表或回退缺少换表凭据，保持阻断，不能让 total_increasing 把它误当合法归零并重复累计。其他表不受影响。

已经提交过的历史一旦被修订、删除端点、在已导入范围内补入新账单等，不能依靠只保存最后时间或追加增量来修补。导入器维护每条统计完整已提交点映射：允许重放相同点、追加更晚端点；历史点改变或插入时冻结该序列并显示诊断冲突，保留原有 Recorder 数据，等待核查。相同抄表区间但不同 rlid 的重复金额有歧义，水费统计阻断，不猜测是补发还是新增。这样宁可缺失，不能重复累计。

## Recorder 与故障恢复

使用 2026.10.2 `async_add_external_statistics`，外部 ID 为 `handan_water:<哈希>_water` / `_cost`；`source=handan_water`、`mean_type=StatisticMeanType.NONE`、水量 `unit_class="volume"`、费用 `unit_class=None`、`has_sum=True`。不直接操作 HA 数据库，不向实体 sensor.* 历史灌入自造状态。

写入采用绝对累计值和稳定时间键（Recorder 原生 upsert）。先持久化规范化账本与待导入点，再排队写入；等待 Recorder 队列完成后回读核对 state/sum，最后持久化已提交映射。任何阶段崩溃均可重放，不依赖内存“累计器”。每次启动会核对 Recorder，处理 Store 存在而 Recorder 被清空/尚未提交的情形；失败状态保留，手动“恢复历史同步”按钮可重试，不清空历史，不重设基线。

历史导入是选项，首次默认开启；关闭只停止后续导入，不删除数据，重新开启继续。除同步按钮外，普通刷新按钮先重新查询，再同步。已提交历史冲突不能靠“恢复”按钮强制覆盖；需要依据真实资料处理，文档说明恢复边界。

## 费用和能源面板

费用保留为三类：每表 rlje 应收账单（分项完整保留）、账户 ppayment 实交、账户预存净变动/抵扣。以账单真实结束抄表日期作为“应收账单截至该抄表日”的累计系列，**并非账单开立/付款时间**，因为接口没有开票时间。实际交易历史按 pdatetime 保留在账户历史实体，不能分配给没有编号的水表。

HA 2026.10 水资源支持 `stat_energy_from` 与独立 `stat_cost`（Energy data.py 的 WaterSourceType/WATER_SOURCE_SCHEMA）。历史水量选择外部水量统计，费用选择对应外部应收统计；也可只选择累计水表实体作为从安装后开始的来源。不设置固定单价。相同水表的实体与外部统计不能同时作为两个供水来源，否则会重复。

**时间分辨率限制：** 源站只有约双月的抄表端点，无法知道区间内每天实际消耗。导入的是累计观测；能源图按累计点求差可能把变化显示在端点附近，其日/小时柱形不能当作真实单日用水。若要求精确每日能源图，必须补充真实逐日/实时水表数据，不能从这些账单恢复。本集成在 README、UI 选项及实体属性中明确这一点，账期用量请看含起止日期的历史账单。

## 官方依据

- [HA 2026.10.2 Recorder external statistics](https://github.com/home-assistant/core/blob/2026.10.2/homeassistant/components/recorder/statistics.py)
- [2026.10.2 StatisticMetaData](https://github.com/home-assistant/core/blob/2026.10.2/homeassistant/components/recorder/models/statistics.py)
- [Recorder API 更新](https://developers.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/)
- [2026.10.2 水资源费用配置](https://github.com/home-assistant/core/blob/2026.10.2/homeassistant/components/energy/data.py)
- [配套 frontend 20260930.4 水资源配置界面](https://github.com/home-assistant/frontend/blob/20260930.4/src/panels/config/energy/dialogs/dialog-energy-water-settings.ts)
- [HA 水资源统计说明](https://www.home-assistant.io/docs/energy/water/)
- [HACS 集成仓库要求](https://www.hacs.dev/docs/publish/integration/)
