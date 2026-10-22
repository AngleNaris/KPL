# 来源、版本与发布边界

核查日：2026-10-22。可复核的逐文件 SHA-256 和许可指纹见 [sources.json](sources.json)。固定 commit 用于复现此次读取，不能代表后续产品界面一直不变。

| 来源 | 固定版本 | 用途与读取范围 |
|---|---|---|
| [video-prompting-skill](https://github.com/Square-Zero-Labs/video-prompting-skill/tree/8d72999ebe8bde390a658d3f6f8a79558b73bd5c) | `8d72999ebe8bde390a658d3f6f8a79558b73bd5c` | 主 SKILL、H3/Seedance 2.5 格式章、角色设定相关参考；Apache-2.0 |
| [DirectorSKILL](https://github.com/wuwangzhang1216/DirectorSKILL/tree/c65ae0d14457053efb1e354c7e7f7e120d97fad1) | `c65ae0d14457053efb1e354c7e7f7e120d97fad1` | 主 SKILL；MV、调度、镜头语言、连续性、声音、剪辑相关段落；MIT，Copyright (c) 2026 wangzhang-wu |
| [MiniMax 官方 H3](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing) | `d21241f0a4b3acbb34c97dae47fa417b7065e438` | 官方 SKILL、base-en/ref-en 格式契约，只摘接口事实与必要语法 |
| [OpenAI imagegen 公开对应来源](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.system/imagegen) | `49f948faa9258a0c61caceaf225e179651397431` | 仅检查公开来源、文件差异和 Apache-2.0 许可，未以其替代用户指定的本地读取 |

## 本地图片技能实际读取

读取当前 Codex Desktop catalog 指向的实际文件，不发布机器路径。checkout 根目录没有 `.agents/skills` 可读技能，也没有项目 AGENTS.md；没有在用户个人技能目录安装或修改文件。

- **imagegen**：完整读取 SKILL、prompting，读取 sample-prompts 的通用规则/示例部分，读取完整 LICENSE.txt。当前本地执行默认面向 GPT Image 2，不是 2.5 专用技能；只提取通用创作与编辑规则。所附许可为 Apache-2.0，全文保留。与上述公开 revision 的 SKILL/prompting/sample-prompts 内容不同，许可文本经换行归一化比较一致；不宣称本地修改具有该公开 commit。内容指纹记录的是本地实际读取文件。
- **image-prompt-refinement**：完整读取主 SKILL，属于跨模型的创作/修订方法，含宿主流程和项目侧例子。顶层未找到许可证，发布权利未建立；不复制原文、私有例子、脚本或内部执行规则。独立表述的通用方法通过下列官方图片指导交叉核对。

许可不明的本地文件只用于理解用户需要，未作为可再分发文本复制。KPL 不捆绑这些原始文件；无需以公开 imagegen 替代它们，也不把它们误标成 Apache-2.0。

## 当前格式的官方依据

| 格式 | 官方链接 | 本次确认与边界 |
|---|---|---|
| Seedance 2.5 | [模型页](https://seed.bytedance.com/en/seedance2_5)、[发布说明](https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5) | 模式/参考能力，不推断端点默认值 |
| Dreamina 界面 | [使用指南](https://dreamina.capcut.com/seedance/how-to-use-seedance-2-5) | Omni reference、实际 `@` 标签；不是统一 API 字段规范 |
| ModelArk | [2.5 教程](https://docs.byteplus.com/en/docs/ModelArk/2607688)、[2.5 prompt guide](https://docs.byteplus.com/en/docs/ModelArk/2607689)、[任务 API](https://docs.byteplus.com/en/docs/modelark/create-video-generation-task-api) | 页面和搜索索引确认 `content`/`role` 边界；部分正文动态加载，完整请求 schema 未列为已验证交付 |
| H3 | [基础模式](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt)、[全参考](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt) | 三/六字段、标签、对齐、对白与声音；此为 rewrite 约定，不当作任意供应商 HTTP schema |
| GPT Image 2.5 | [生成 API](https://developers.openai.com/api/reference/resources/images/methods/generate)、[编辑 API](https://developers.openai.com/api/reference/resources/images/methods/edit) | Sunburst/Flare/快照 ID，prompt 与 images/mask；input_fidelity 支持未明确 |
| 图片通用指导 | [Image prompting](https://developers.openai.com/api/docs/guides/image-prompting)、[Image generation](https://developers.openai.com/api/docs/guides/image-generation) | 精确文字、编辑保留项、多图/掩码与参数边界；不承诺像素一致 |

## 许可处理

三份来源许可证完整逐字保留在 [LICENSES](../LICENSES/)，原始许可文件字节的 SHA-256 可由验证脚本比对。根 [LICENSE](../LICENSE) 约束 KPL 原创新增和改写，不能取消 DirectorSKILL 的 MIT 条件；[NOTICE](../NOTICE)保留原版权/归属及修改声明。未发现两个主要上游固定版本包含另行 NOTICE。官方文档的全文和媒体不随仓库分发。
