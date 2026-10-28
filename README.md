# KPL · Kasidia's Prompt Law

一个 Agent Skill，用创作规格组织多镜视频制作、分镜与图片自检，也支持单条视频或图片提示词。

KPL 将全片段落、视觉主旨、调度、连续性和剪辑设计，与逐镜提示词编译分开处理；保留已确认的剧情、对白、时长和参考职责。视频格式适配仅包含 Seedance 2.5 与 MiniMax H3，图片格式适配为 OpenAI 官方 GPT Image 2.5 Sunburst/Flare。未指定模型时也可先完成通用草稿。

多阶段创作使用唯一规格及派生状态记录需求、任务依赖、素材版本和验收证据；按五阶段审查、及时澄清，支持局部回退和断点续作。创作时间以秒表达，输出帧率另行确认。单条提示词与局部修订可直接进入精简流程。

## 结构

```text
SKILL.md                       唯一入口（name: kpl）
references/
  spec-workflow.md             分步执行、恢复、规格与变更影响
  creative-review.md           文档、图片、镜头运动与分层验收
  project-structure.md         用户中文区、Agent 工作区和版本规则
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
assets/creative-spec.md         单一创作规格模板
docs/                          参考资料与说明
tests/                         行为用例与文本试答
scripts/validate.mjs            结构与格式检查
scripts/kpl_check.py            可选的只读状态、版本和依赖检查
scripts/review_boards.py        三类审片板与来源哈希核验
scripts/layout_preview.py       有时长偏差检查的静音预演
scripts/project.py              项目初始化、检查及用户区输出
LICENSE、LICENSES/、NOTICE      许可证与版权声明
```

参考仓库：[Square-Zero-Labs/video-prompting-skill](https://github.com/Square-Zero-Labs/video-prompting-skill)、[wuwangzhang1216/DirectorSKILL](https://github.com/wuwangzhang1216/DirectorSKILL)。
