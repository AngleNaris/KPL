# MiniMax H3：格式适配

核查日期：2026-10-22。改写说明：只整理官方 rewrite 的字段、标记与模式；动作、运镜、叙事方法统一在 [视频模块](../video.md)。

官方依据固定于 MiniMax-AI/MiniMax-H3 commit `d21241f0a4b3acbb34c97dae47fa417b7065e438`：

- [主 SKILL](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/SKILL.md)
- [base-en.txt](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/base-en.txt)
- [ref-en.txt](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing/references/ref-en.txt)

以下整理的是官方英文 prompt rewrite 指南，不证明所有 H3 托管界面都要求相同字段或英文正文。自然语言按 [用户交付规则](../user-delivery.md) 默认中文；模型名、真正要求原样的标签与字段可保留英文，台词/歌词及画内文字保留原文。当前界面只接收普通文本时直接给中文提示，不自动套本页字段。使用这些字段但正文写中文时标明“中文内容＋参考标记”，不得声称完全符合官方英文 rewrite；若用户要求严格复现该英文 rewrite，与中文偏好冲突时先说明并询问。若实际平台协议明确要求英文，指出具体依据与例外范围，不擅自推广到其他平台或本地模型。外部 `minimax-h3-prompt-writing` 的英文规则属于该技能的官方改写约定，不等于目标平台已验证的语言限制；不要修改该外部技能。

## 基础模式

以下字段及英文对齐句是严格英文 rewrite 的参考样式，不应自动加到普通中文平台提示中。T2VA（文本）、I2VA（首帧）、FL2VA（首尾帧）、L2VA（尾帧）在该样式中使用三字段，按此顺序并留空行：

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

只有有帧锚点时，在这三字段前添加对应对齐首行，再空一行：

I2VA：

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
```

FL2VA：

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.
```

L2VA：

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.
```

`N` 换成真实末镜编号，`S.SS` 换成已确定的有效时长，保留两位小数。上面的 I2VA 等模式标题不写进提示。没有有效时长就不能伪造最终尾帧对齐值。

第一镜写 `[Shot 1]`，不加切点时间；真实后续切镜写 `[Shot 2] At 00:03.500, ...`，编号连续，时间严格递增且在外部时长内。不是每个动作都新建一个 Shot。

使用此 rewrite 标记时，给说话人稳定 `(S1)`、`(S2)`，一起发声可用 `(S1,S2)`；静默角色不分配发声 ID。台词格式为 `<d>[Chinese] 原文</d>`，标签中只放语言及原话。严格英文 rewrite 的旁白示例使用 `says in an off-screen voiceover`；中文正文可以用中文说明旁白并明确对应画内人物嘴闭合。跨切镜同一段讲话用 `<scenetrans>` 连接；结尾截断用 `<cutoff>`。画内文字用英文双引号包住原文。

## 全参考模式 Ref2VA

六字段固定顺序：

```text
subject_definitions:
...

summary:
...

retention_analysis:
...

detailed_description:
...

overall_soundscape:
...

non_diegetic_music:
...
```

| 标记 | 编码的输入职责 |
|---|---|
| `<Subject N>` | 从资产抽取、可复用的可见内容或属性，含身份/风格/动作 |
| `<Picture N>` | 具体首尾帧、关键帧、构图或分镜锚点 |
| `<Video N>` | 编辑/续接的源视频或整段时间结构（含机位/切镜结构） |
| `<Audio N>` | 明确复用或参考的音频信号 |

分别编号，类别索引不代表一对一配对。图片/视频仅作为某个 Subject 的来源时可在该定义内引用，无需另立独立条目。单独跟踪的每个条目有定义及 retention 行，跨字段保持意义不变；视频自带音轨不自动建立 Audio。转表演时用 Subject 表示可见表演，整段机位/剪辑结构才用 Video。

使用官方 rewrite 格式时，`summary` 以方括号列实际任务类型，按官方形式以 ` + ` 组合：`keyframe completion`、`reference generation`、`video editing`、`video continuation`、`audio reuse`、`audio reference`。严格英文 rewrite 的视频编辑摘要示例为 `The target video is an edited version of <Video 1>.`；中文正文用中文说明该关系。仅参考风格或动作不代表编辑源视频。

`retention_analysis` 每个独立条目一行：视觉关系仅用 `fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`；音频关系仅用 `fully_copy`、`partially_copy`、`reference`、`weak_reference`，再用文字明确保留/转移什么。画面时序写进 `detailed_description`，不再使用基础模式的长字段名。

## 声音、输出与设置边界

`overall_soundscape` 编码环境/物理/非语言声，台词与画内音乐在时序正文；`non_diegetic_music` 编码观众听见而角色听不见的配乐。无配乐写 `N/A`；全静音两者都写 `N/A` 且正文没有任何发声事件。不要为了填字段擅加音乐。其他外部设置单列，不放入 prompt 字段；正文只保留所需的切点与帧对齐时间。

仅在目标或用户要求使用该 rewrite 结构时输出对应格式。API 的模型 ID、素材上传、时长、尺寸与音频开关由实际平台配置，不能假装这些英文段落名就是 HTTP 请求键。
