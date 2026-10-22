# KPL · Kasidia's Prompt Law

一个合并后的 Agent Skill，用于导演策划、视频提示词，以及图片生成与编辑提示词。

KPL 将全片段落、视觉主旨、调度、连续性和剪辑设计，与逐镜提示词编译分开处理；保留已确认的剧情、对白、时长和参考职责。视频格式适配仅包含 Seedance 2.5 与 MiniMax H3，图片格式适配为 OpenAI 官方 GPT Image 2.5 Sunburst/Flare。未指定模型时也可先完成通用草稿。

## 结构

```text
SKILL.md                       唯一入口（name: kpl）
references/
  directing.md                 通用导演策划
  video.md                     通用视频提示词
  image.md                     通用图片生成与编辑提示词
  formats/
    seedance-2.5.md             Seedance 2.5 格式适配
    minimax-h3.md               MiniMax H3 格式适配
    gpt-image-2.5.md            GPT Image 2.5 格式适配
docs/                          参考资料与说明
tests/                         行为用例与文本试答
scripts/validate.mjs            结构与格式检查
LICENSE、LICENSES/、NOTICE      许可证与版权声明
```

参考仓库：[Square-Zero-Labs/video-prompting-skill](https://github.com/Square-Zero-Labs/video-prompting-skill)、[wuwangzhang1216/DirectorSKILL](https://github.com/wuwangzhang1216/DirectorSKILL)。
