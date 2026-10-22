# GPT Image 2.5：最小格式适配

核查日期：2026-10-22。中文整理；创作与编辑方法见 [图片模块](../image.md)。本页不复制本地宿主工具路由，不提供 API 客户端。

官方资料：[生成](https://developers.openai.com/api/reference/resources/images/methods/generate)、[编辑](https://developers.openai.com/api/reference/resources/images/methods/edit)、[图像生成指南](https://developers.openai.com/api/docs/guides/image-generation)。

## 模型标识与文本

官方 Images API 已列 `gpt-image-2.5-sunburst`、`gpt-image-2.5-flare`，以及各自 `-2026-09-08` 快照。按用户选择使用，不构造裸 `gpt-image-2.5` ID。

`prompt` 是自然语言目标描述，没有强制的“Use case”“Style”等行名；图片模块中的中文标签只是可选脚手架。正文中“图1、图2”是按输入顺序说明用途的自然语言，不是 MiniMax 的 `<Picture N>` 或 Seedance 的 `@` 绑定协议。

## 生成与编辑输入形态

以下仅展示与提示编译有关的请求形状，不是已提交请求：

```json
{
  "model": "gpt-image-2.5-flare",
  "prompt": "用户确认的图片生成描述"
}
```

编辑 JSON 使用 `images` 数组；每个对象选 `image_url`（完整 URL 或 data URL）或 `file_id`，不是把本地路径写进 prompt：

```json
{
  "model": "gpt-image-2.5-sunburst",
  "prompt": "只修改指定项，并保留约定内容",
  "images": [{"image_url": "https://example.com/base.png"}]
}
```

示例 URL 是占位，不是可用基图。接口另支持 multipart 的 `image[]` 文件输入；它与 JSON `images` 不混写。图序、编辑目标及每图职责必须相互对应。

官方编辑 JSON 的 `mask` 是含 `image_url` 或 `file_id` 的对象；multipart 使用实际 mask 文件。掩码用于引导修改区域，多图时应用于第一张图；不能用一句“红圈内部”假装已经传入掩码。官方指南说明掩码边界可能不精确遵循；不要承诺保留区逐像素相同。尺寸、格式及 alpha 要求使用当前接口文档核对。

## 外部参数与输出

`model`、`size`、`quality`、`n`、`background`、`output_format`、输入图像和 mask 均属于外部请求或工具输入，不靠 prompt 中写“4K”“透明”来宣称已设置。透明背景的官方参数为 `background: "transparent"`，搭配 PNG/WebP；自然语言仍可说明边缘与保留意图。

`input_fidelity` 对 2.5 的支持未在本次参数说明中明确，不从旧模型继承默认值，也不发送猜测值。实际宿主若不公开模型/参数，明确其不可见，不把官方 API schema 强塞进另一个工具。

官方 Images 响应的图像数据位于 `data`，GPT Image 返回 `b64_json`；这不是提示正文，也不代表实际图像已经经过视觉检查。只请求提示词时交付文本，不提交以上对象或调用生成。
