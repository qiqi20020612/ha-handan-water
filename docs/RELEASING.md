# 发布到 HACS 自定义仓库

项目仓库为 [qiqi20020612/ha-handan-water](https://github.com/qiqi20020612/ha-handan-water)，manifest 已填写相应仓库与维护者。以下流程适用于后续发布或 fork。

1. 公共仓库只包含此集成、文档及合成测试数据。不能包含原始 ZIP、HAR、真实户号、openid、姓名/地址/手机号、令牌、真实账务数据或完整原始请求/响应。
2. 在本地执行 `python3 scripts/prepare_repository.py --repository 你的用户名/仓库名`。脚本只填写本项目 manifest 的 documentation、issue_tracker 和 codeowners，不创建仓库、不推送、不更改 HA。
3. 设置仓库说明及 `home-assistant`、`hacs` 等 topics，启用 Issues。
4. 运行测试、Ruff、Hassfest。添加到 HACS 自定义仓库，不等于提交到 HACS 默认列表。
5. 提交遵循 Conventional Commits，例如 `feat: add read-only Handan Water integration`。推送代码后检查 GitHub Actions；发布版本标签和 release。HACS 也支持未发布 release 时从默认分支安装。
6. 可执行 `python3 scripts/build_release.py` 生成最小安装 ZIP；它采用白名单，只打包集成文件、README、LICENSE 与 HACS 元数据，不扫描/打包下载目录或原始抓包。

仓库内的 `brand` 是本项目自制水滴图标，不是供水公司商标。HACS 的仓库/品牌验证政策可能更新；发布前以 HACS 官方 Action 的结果为准。公开 fixture 由 `scripts/generate_fixtures.py` 生成，不从原始抓包复制数值；更新时应保持这一边界。

- [HACS 自定义集成要求](https://www.hacs.dev/docs/publish/integration/)
- [HACS 仓库元数据要求](https://www.hacs.dev/docs/publish/start/)
