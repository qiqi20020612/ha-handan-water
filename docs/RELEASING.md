# 发布到 HACS 自定义仓库

项目仓库为 [qiqi20020612/ha-handan-water](https://github.com/qiqi20020612/ha-handan-water)，manifest 已填写相应仓库与维护者。以下流程适用于后续发布或 fork。

1. 公共仓库只包含此集成、文档及合成测试数据。不能包含原始 ZIP、HAR、真实户号、openid、姓名/地址/手机号、令牌、真实账务数据或完整原始请求/响应。
2. 在本地执行 `python3 scripts/prepare_repository.py --repository 你的用户名/仓库名`。脚本只填写本项目 manifest 的 documentation、issue_tracker 和 codeowners，不创建仓库、不推送、不更改 HA。
3. 设置仓库说明及 `home-assistant`、`hacs` 等 topics，启用 Issues。
4. 运行测试、Ruff、公开文件检查、Hassfest 和 HACS Action。Tests 工作流在 HA 2026.10.0、.1、.2 上分别执行完整测试；HACS 校验在 push、pull_request 和手动触发时运行。添加到 HACS 自定义仓库，不等于提交到 HACS 默认列表。
5. 提交遵循 Conventional Commits，例如 `fix: keep valid readings available during recorder failures`。推送后核对 **同一提交** 的三个兼容性任务、Hassfest 和 HACS 全部成功；旧提交的绿色结果不能替代新提交验证。已将 checkout 更新到 Node 24 的官方 v7，消除旧 v4 的 Node 20 弃用警告。
6. 执行 `python3 scripts/build_release.py`，在 `dist/` 生成 `ha-handan-water-0.1.0.zip` 和 `SHA256SUMS`。它采用白名单，打包集成、README、LICENSE、HACS 元数据、公开说明文档和图标，不扫描下载目录或原始抓包。CI 的 2026.10.2 任务也上传 `release-candidate` artifact；CI 不自动创建标签或发布 Release。

## v0.1.0 准备状态

2026-10-19 核查：公共仓库 main 可供 HACS 自定义安装；manifest 和 pyproject 版本均为 0.1.0，hacs.json 最低 HA 为 2026.10.0。核查时没有远程标签、正式 Release 或 Release 草稿；填写 manifest 版本不等于已发布。

发布候选说明已准备在 [RELEASE_NOTES_0.1.0.md](RELEASE_NOTES_0.1.0.md)。自动化测试检查安装包完整性、版本一致、校验和以及私有/测试/缓存文件不被打包；实际 HACS 客户端安装/升级仍需验收。

发布时，从全部校验成功的提交创建 `v0.1.0` 标签和 Release，标签版本须与 manifest 一致；标签推送会再次触发校验。可先创建草稿供检查：

```sh
python3 scripts/check_public_files.py
python3 scripts/build_release.py
gh release create v0.1.0 \
  --repo qiqi20020612/ha-handan-water \
  --target "$(git rev-parse HEAD)" \
  --draft --title "邯郸供水 v0.1.0" \
  --notes-file docs/RELEASE_NOTES_0.1.0.md \
  dist/ha-handan-water-0.1.0.zip dist/SHA256SUMS
```

检查草稿的目标提交和附件，再发布。不要在目标提交尚未通过 CI 时发布，也不要移动已经公开发布的标签。

## HACS 下载方式

当前未启用 `zip_release`，目录为标准 `custom_components/handan_water`。无 Release 时 HACS 使用默认分支；有正式 Release 后可选择发布版本对应的源树。附件 ZIP 是手动安装包，保留仓库目录结构；不能直接把这种 ZIP 声明为 HACS 的组件根目录压缩包。此发布流程无须改变 hacs.json 的下载模式。

仓库内的 `brand` 是本项目自制水滴图标，不是供水公司商标。HACS 的仓库/品牌验证政策可能更新；发布前以 HACS 官方 Action 的结果为准。公开 fixture 由 `scripts/generate_fixtures.py` 生成，不从原始抓包复制数值；更新时应保持这一边界。

- [HACS 自定义集成要求](https://www.hacs.dev/docs/publish/integration/)
- [HACS 仓库元数据要求](https://www.hacs.dev/docs/publish/start/)
