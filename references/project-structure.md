# KPL 项目目录与版本

这是一套目录规范的唯一说明。项目根目录沿用用户给定的名称，不强制改名。新建项目才运行 `init`；已有项目先只读 `inspect`。用户只要求审查时不改项目。用户授权常规修改时可在原位置工作；**迁移或重命名已有文件须先列具体映射和影响，再取得明确批准**。不得自动搬动或猜测确认状态。

```text
用户给定的项目根目录/
├─ 01_项目说明/       需求与唯一当前规格
├─ 02_导演方案/       段落、导演和剪辑方案
├─ 03_分镜设计/       镜头表和分镜
├─ 04_提示词/         图片与视频提示词
├─ 05_参考资产/       用户查看、编辑的参考资产
├─ 06_审查报告/       各阶段报告、镜头总览和切点/运动板
├─ 07_预演交付/       layout 预演、用户可看的成片交付
└─ .kpl/              Agent 工作区：state、index、cache、logs、tmp、imports
```

用户区如需增设子目录，所有层级都用 `两位序号_中文主名称`，如 `01_人物资产`；序号为 `01`–`99`，同级序号对应一个名称。用户区文件采用固定字段：

```text
序号_中文主名称[_id稳定标识][_p三位页码]_v三位版本_状态.扩展名
01_分镜脚本_v003_draft.html
02_参考画面_idS001_v002_review.png
01_镜头总览_p001_v001_draft.png
01_布局预演_v001_draft.mp4
```

中文主名称只用汉字，不含下划线；可选 `id` 只用字母、数字、连字符，`p` 为 001–999，版本为 `v001`–`v999`，状态用 `draft`、`review` 或 `final`。固定字段的位置及零填充使排序可预测。序号表示同目录顺序，不是镜头身份；调整顺序不改镜头 ID。原 ID 无法装入 `id` 字段时，在规格/索引记录原 ID 和显示名的对应关系，不能悄悄改身份。**`final` 必须有实际确认及适用验收依据，状态文字和版本号本身均不是证明；生成审片图或 layout 的工具只标 `draft`。** Windows 保留字符、路径分隔符、尾点及越界路径均不允许。重名、大小写碰撞或同级编号指向不同名称时先处理冲突，不覆盖。

`.kpl/project.json` 只指向用户区一个实际 `.kpldoc` 或 HTML 规格路径、该文件的哈希和选择依据，不复制规格；任务、检查与证据状态存于 `.kpl/state/project-state.json`，明确绑定规格路径和哈希，不是第二份创作规格。新项目默认 `.kpldoc`，明确选择兼容方案时可用 `init --format html`；已有 HTML 保持原文件为权威，`.kpldoc` 导出 HTML 只是只读查看副本，不能作为当前规格。v1 格式只读保留，原生 v2 编辑器不能直接打开；不能悄悄映射或丢弃旧字段。用户交付文档不使用 `.md`，旧项目 Markdown 只读兼容，不自动迁移。初始化的 `v001_draft` 未确认。明确记录当前文件/版本，不根据最大版本号、`final` 或时间戳猜测当前或已批准版本。用户编辑导致哈希变化时先核实并显式重新选择，机器状态停止提供下一任务，相关索引失效。用户明确确认的版本在规格中记录确认范围、来源和实际哈希，作为逻辑锁定；修订应另存新版本并保留旧文件及确认记录，不能靠文件名锁定。用户直接改动已锁定文件时报告原确认失效并重新核对。比较变更后，将受影响的规格、镜头、相邻切点和连剪证据置为待复核，保留无关结果，之后才更新机器状态绑定；不自动把旧检查恢复为通过。机器指针或状态自述也不证明用户授权或创作通过。

原始导入文件名和内容保留，不为统一命名而破坏引用。需要保留原始副本且获授权时可放 `.kpl/imports`，在用户区提供规范显示名或在规格中登记映射；机器索引记录原始路径和哈希。不要把原文件静默改名或改写引用。

## 操作与兼容

将 `<KPL>`、`<项目>`、`<清单>` 换成实际路径；下面仅是语法示例，不代表真实项目配置：

```powershell
python "<KPL>/scripts/project.py" init "<不存在的新项目目录>"
python "<KPL>/scripts/project.py" inspect "<项目>"
python "<KPL>/scripts/project.py" select-spec --project "<项目>" --spec "01_项目说明/01_项目规格_v001_draft.kpldoc" --basis "实际选择依据"
python "<KPL>/scripts/project.py" board sequence --project "<项目>" --manifest "<清单>" --number 01 --version v001
python "<KPL>/scripts/project.py" board cut --project "<项目>" --manifest "<清单>" --number 02 --version v001 --left S001 --right S002 --interval 0.1
python "<KPL>/scripts/project.py" board motion --project "<项目>" --manifest "<清单>" --number 03 --version v001 --shot S001 --interval 0.5
python "<KPL>/scripts/project.py" layout --project "<项目>" --manifest "<清单>" --number 01 --version v001
python "<KPL>/scripts/project.py" layout --project "<项目>" --manifest "<清单>" --number 02 --version v001 --static
python "<KPL>/scripts/project.py" seconds-from-frames --frames 36 --fps 24
```

`inspect` 只读报告不合规路径及待确认映射，不执行迁移；实际迁移计划要补齐每项原路径、拟定新路径、引用/哈希、冲突、复制或移动方式及影响，用户明确批准后才执行。工具不会自动迁移。新项目用 `board`/`layout` 发布：用户图/视频落在对应中文目录，索引在 `.kpl/index`，临时文件在 `.kpl/tmp`；两者均不覆盖。清单应放 `.kpl/state` 并明确引用当前规格，素材路径相对清单，例如 `spec: "../../01_项目说明/01_项目规格_v001_draft.kpldoc"`；规格和时间/运镜仍须遵守实际确认与提问规则。所有时间和 `--interval` 使用秒；`seconds-from-frames` 只用于已核实帧率/时间基的旧记录，只读返回秒并保留来源，不改原项目。返回值给出具体用户文件及索引路径，用原工具 `verify` 校验索引。项目内部路径相对根记录，外部原件保留原路径；外部文件或环境不随项目移动时仍须核查。

`select-spec` 只选择现有规范 `.kpldoc` 或 HTML 并记录依据，不替用户确认内容；旧 Markdown 仍可只读检查但不能新选为用户规格。规格修改或选择不同版本使既有相关索引过期。新项目使用 `kpl_check.py "<用户规格>" --state "<项目>/.kpl/state/project-state.json" --allow-root "<项目>"`，审查时另加 `--review`；状态路径相对规格解析，允许根只需项目目录。完成影响检查及相关复核后，才把机器状态的 `spec_binding.path_from_state/sha256` 更新为实际选择的规格路径（相对状态文件）及哈希；这不是确认或自动恢复旧通过。静态文本提取不执行脚本，也不能证明页面或编辑器实际显示或被阅读；要实际打开检查。旧项目原有规格内 `kpl-state` 继续直接读取，无需改写；旧版审片工具的 `--out` 与索引仍可用。旧项目不需要新目录也可读取；把临时结果放 Agent 工作区，用户可交付内容须按本规范处理。校验器只检查命名、引用和哈希，不能证明用户确认、视觉质量或真实观看。

Agent 在获项目写入授权时，用 `html_spec.py read "<当前用户规格>"` 读取 `.kpldoc` 或 HTML 权威数据及实际 SHA256，不执行页面脚本。需要局部修改时，在 `.kpl/tmp` 写操作清单，调用 `html_spec.py apply --project "<项目>" --spec "01_项目说明/01_项目规格_v001_draft.kpldoc" --expected-sha256 "<刚读到的哈希>" --patch "<操作清单.json>"`；已有 HTML 填其真实路径。例如一条镜头文字修改为 `{"operations":[{"target":"shot","id":"实际稳定ID","field":"prompt","old":"实际旧值","new":"已授权的新值"}]}`；也支持 `project` 的 `title/summary/global_settings/layout_aspect`、按稳定 ID 添加或移除镜头，以及 `asset` 的添加、改 `name`/`path` 和移除。所有写入值须符合 [用户规格格式](spec-format.md)。移除须提供 `deletion_authorized:true` 和 `authorization_ref`；修改用户镜头标记也须提供 `authorization_ref`，这些字段本身不证明授权真实。不得按表格当前行号猜身份。文件、旧值或当前选择发生变化时拒绝写入；确认/最终版本不允许原地修改。写后读回并检查影响，旧状态保持过期，不能自动标通过。原生写入与编辑器共用互斥锁；其他程序未必遵守，仍须避免同时编辑，冲突或写入结果不确定时先核实原文件，不盲目重试。

原生打开方式、保存及受限回退见 [原生规格编辑器](native-editor.md)；浏览器编辑旧 HTML 的流程见 [用户可读交付](user-delivery.md)，不改变项目选择的唯一权威文件。
