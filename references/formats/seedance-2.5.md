# Seedance 2.5：格式适配

核查日期：2026-10-22。中文整理与格式摘录，不继承上游的写作教学或端点默认值。通用内容先按 [视频模块](../video.md) 完成。

## 表面与输入

[ByteDance 官方模型页](https://seed.bytedance.com/en/seedance2_5)及[发布说明](https://seed.bytedance.com/en/blog/one-take-creation-flexible-referencing-introducing-seedance-2-5)确认文本、多模态参考、编辑和续接能力。[Dreamina 官方使用说明](https://dreamina.capcut.com/seedance/how-to-use-seedance-2-5)说明在 Omni reference 中上传资产，用 `@` 标签关联。

网页提示输入是自然语言，没有证据表明 `FORMAT`、`CAMERA`、`TIMELINE` 是强制字段。可使用短段和时间轴帮助表达，它们是 KPL 的可选组织方式。输出用用户需要的语言，不硬改英文。

参考生成使用当前界面实际生成的标签，例如界面显示 `@图片1`、`@视频1` 或 `@Image 1` 时原样使用；例子不是通用字符串规范，不自行去掉空格、翻译或重编号。每个标记必须对应已上传资产，并在文字中声明其职责。只给文本且没有界面映射时，用“参考图1（待替换为实际标签）”等占位，不能声称已绑定。

图生视频首帧、首尾帧、参考生成、视频编辑与续接采用用户当前表面的真实输入槽位；“把它当首帧”的一句话不能替代槽位设置。编辑/续接须提供实际源视频，不能仅引用它的文件名。

## API 与外部参数边界

[ModelArk 教程](https://docs.byteplus.com/en/docs/ModelArk/2607688)、[prompt guide](https://docs.byteplus.com/en/docs/ModelArk/2607689)和[创建任务参考](https://docs.byteplus.com/en/docs/modelark/create-video-generation-task-api)按 `content` 区分文本与资产，并通过资产 `role` 表示用途，例如 `first_frame`、`last_frame`、`reference_video`。这些是请求结构，不是让模型读的自然语言字段。

本次部分 ModelArk 页面仅返回动态外壳或索引摘录，未把完整 API 请求 schema 作为已验证交付；需要 API 请求时重新读取当前完整文档，核实所选模式的 `type`、URL 对象和 `role` 组合。不要从其他厂商端点补齐，也不凭名称猜模型 ID。

模型 ID、时长、尺寸/比例、分辨率、声音开关、扩展/编辑控制等放外部设置；正文可含事件时间和目标时段。产品界面的长视频流程与单次模型能力不同，不把产品宣传的最长时长直接当每个 API 的范围。KPL 不内置额度、价格或参数默认值。

## 输出检查

交付自然语言正文；用户要求参数时另列“外部设置（待在当前表面核对/设置）”。检查实际标记、帧槽位、模式与正文对应，不新增模型专用负面字段或自造 schema。格式核查不等于生成成功。
