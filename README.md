# KPL · Kasidia's Prompt Law

一个 Agent Skill，用创作规格组织多镜视频制作、分镜与图片自检，也支持单条视频或图片提示词。

KPL 将全片段落、视觉主旨、调度、连续性和剪辑设计，与逐镜提示词编译分开处理；保留已确认的剧情、对白、时长和参考职责。视频格式适配仅包含 Seedance 2.5 与 MiniMax H3，图片格式适配为 OpenAI 官方 GPT Image 2.5 Sunburst/Flare。未指定模型时也可先完成通用草稿。

多阶段创作使用唯一规格及派生状态记录需求、任务依赖、素材版本和验收证据；按五阶段审查、及时澄清，支持局部回退和断点续作。创作时间以秒表达，输出帧率另行确认。单条提示词与局部修订可直接进入精简流程。

新项目可使用共用的 Windows x64 `.kpldoc` 编辑器，已有 HTML 规格保持原样；查看用 HTML 副本不自动同步。技能包同时包含程序、配套 WebView2 SDK 组件和完整源码；共享的 .NET Framework 4.8 与 WebView2 Runtime 需系统已有，均不在包内。首次交付前按 [原生编辑器说明](references/native-editor.md) 检查桌面账户、运行时和打开方式；[源码与构建](native-prototype/源码与构建.html)说明源码、版本校验和自编译。表格／时间线及静态图片预演不代替实际视频验收。

## 结构

```text
SKILL.md                       唯一入口（name: kpl）
references/
  spec-workflow.md             分步执行、恢复、规格与变更影响
  creative-review.md           文档、图片、镜头运动与分层验收
  project-structure.md         用户中文区、Agent 工作区和版本规则
  spec-format.md               用户规格字段、资产引用与分段标签（编辑器约定）
  native-editor.md             原生编辑器、准备与保存边界
  user-delivery.md             用户可读交付与提示词语言
  review-boards.md             本地序列、运动和切点审片板
  layout-preview.md            静态素材二维布局预演
  directing.md                 通用导演策划
  directing-evidence.md        导演原则的来源与边界
  video.md                     通用视频提示词
  image.md                     通用图片生成与编辑提示词
  formats/
    seedance-2.5.md             Seedance 2.5 格式适配
    minimax-h3.md               MiniMax H3 格式适配
    gpt-image-2.5.md            GPT Image 2.5 格式适配
assets/creative-spec.md         内部规格字段参考（用户文档不交付 Markdown）
assets/user-spec.js             离线用户规格页面的编辑逻辑
docs/                          参考资料与说明
tests/                         行为用例与文本试答
scripts/validate.mjs            结构与格式检查
scripts/kpl_check.py            可选的只读状态、版本和依赖检查
scripts/video_route.py          按已确认的在线／本地方式选择视频提示词路径
scripts/review_boards.py        三类审片板与来源哈希核验
scripts/layout_preview.py       有时长偏差检查的静音预演
scripts/project.py              项目初始化、检查及用户区输出
scripts/html_spec.py            读取和按稳定 ID 更新当前规格
scripts/editor_ready.py         只读规划打开方式；经明确授权可设置或修复 KPL 自有关联
scripts/editor_bundle.py        校验打包程序与构建输入的哈希
native-prototype/               编辑器源码、构建入口、合成测试和 Windows x64 程序
native-prototype/assets/        图标与应用内标识：设计原图、kpl.ico、矢量 logo-wide.svg 及生成脚本（make_icon.py 需 Pillow，trace_logo.py 需 numpy 和 Pillow，并会改写 ui/index.html 的标识精灵）
native-prototype/tests/         C# 测试：SessionTests、HostTests 由 tests/test_native_workflow.py 编译运行；TemplateTests、EngineIntegration、RuntimeProbe 目前没有脚本自动运行
LICENSE、LICENSES/、NOTICE      许可证与版权声明
```

参考仓库：[Square-Zero-Labs/video-prompting-skill](https://github.com/Square-Zero-Labs/video-prompting-skill)、[smixs/visual-skills](https://github.com/smixs/visual-skills)（CC BY 4.0，署名见 NOTICE）、[wuwangzhang1216/DirectorSKILL](https://github.com/wuwangzhang1216/DirectorSKILL)。
