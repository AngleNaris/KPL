# 静态素材 layout 预演

标准 KPL 项目通过 [项目工具](project-structure.md) 的 `layout` 发布中文名视频到用户区、索引到 `.kpl`；下述 `--out` 用于旧项目或 Agent 临时工作区。

阶段 2 的导演设计检查先打开[分段总览与关键切点板](review-boards.md)，再在用户授权范围内用本工具制作节奏预演；检查疑点或总览无法辨认的细节时才回看具体原图并记理由。清单和素材只读取，不改原项目或提交生成。预演使用无声静态图片、明确指定的二维裁切变化和硬切；不模拟人物动作、真实景深、视差或三维运镜，也不是最终作品。

先查已有规格及用户确认；缺分辨率、帧率、画幅、镜头顺序、入出点、时长、构图裁切、运镜或切换意图时立即询问，不静默套默认运镜。只对用户明确委托决定的具体项目自行决定。清单中的 `confirmation_ref` 是声明来源，不是工具证明用户已经批准。其他转场目前不支持，会拒绝而不替换成硬切。

## 清单与命令

复用 [审片 manifest](../assets/review-manifest.example.json) 的 `version/spec/shots`，加入明确的 `layout`，并对每镜填写 `timeline_start/in/out/duration/shot_size/transition_in/camera`。以下是**虚构格式示例**，路径和确认依据须换成真实材料：

```json
{
  "version": 1,
  "spec": "creative-spec.md",
  "layout": {
    "width": 1280, "height": 720, "fps": "24", "aspect_ratio": "16:9",
    "confirmation_ref": "实际确认的规格或用户回答位置",
    "frame_policy": "exact"
  },
  "shots": [
    {
      "id": "S001", "section": "第一段", "media": "frames/S001.png",
      "timeline_start": 0, "in": 0, "out": 1, "duration": 1,
      "shot_size": "中景", "transition_in": "cut",
      "planned_action": "计划的人物动作；预演中仅标注",
      "camera": {"type": "static", "start_crop": [0, 0, 1, 1], "end_crop": [0, 0, 1, 1]}
    },
    {
      "id": "S002", "section": "第一段", "media": "frames/S002.png",
      "timeline_start": 1, "in": 0, "out": 1.5, "duration": 1.5,
      "shot_size": "近景", "transition_in": "cut",
      "camera": {"type": "linear", "start_crop": [0, 0, 1, 1], "end_crop": [0.1, 0.1, 0.9, 0.9]}
    }
  ]
}
```

用户规格中每镜的 `layout_camera` 与这里的 `camera` 格式相同，编辑器时间线可直接预览，见 [用户规格格式](spec-format.md)。裁切框为相对原图的 `[左, 上, 右, 下]`，范围 0–1；`linear` 在镜内首尾框间线性变化，可表现二维平移、推拉或同时变化，`static` 的两个框须相同。帧中按图像真实比例缩放并补黑边，不生成画外内容或自动裁掉主体；裁切框与画幅比例不符、裁切区域低于预览分辨率时，命令及索引会明确警告。若框或素材不支持构图须向用户确认。景别仅是清单声明，不是模型自动识别。

```sh
python "<KPL目录>/scripts/layout_preview.py" create --manifest "<清单.json>" --out "<新输出目录>"
python "<KPL目录>/scripts/layout_preview.py" create --manifest "<清单.json>" --out "<静态对照新目录>" --static
python "<KPL目录>/scripts/layout_preview.py" verify "<新输出目录>/index.json"
```

`--static` 用每镜的起始裁切框关闭所有模拟运镜，便于和指定的模拟运镜版本比较。输出目录必须不存在，父目录须存在；有旧文件、缺失/损坏图片、画幅不一致、越界裁切或时间线空隙时拒绝生成。需要现有 Python、Pillow、ffmpeg/ffprobe、H.264 编码器和中文字体，不自动安装。输出始终无音轨，不擅自添加音乐或配音。当前只支持相接硬切；宽高须为 64–4096 的偶数并与明确比例一致，帧率不高于 120 fps。超出范围时说明限制，不猜替代设置。

时间设计以秒为权威；`frame_policy:exact` 表示严格保持每处计划时间，无法做到时以秒显示计划、最接近的实际时长和偏差并停止。比如 24 fps 下计划 1.9 秒，最接近约 1.916667 秒，偏差约 +0.016667 秒；只有用户明确接受这种处理并留下依据，才可选 `nearest_cumulative`、填写 `rounding_authorized:true` 与真实 `rounding_ref`。也可由用户调整计划或输出设置。不要要求用户先选择帧数，不能静默改时间。工具内部以有理数处理累计边界，索引保留计划、实际和误差的精确分数与秒数，以及技术帧区间；画面以秒显示并四舍五入到毫秒，索引保留精确值。预览画面叠加镜头号、秒数、景别及计划动作提示，是审查副本，不覆盖成片素材。

每次输出 `layout-preview.mp4` 和 `index.json`；索引关联源图、清单和可选规格的 SHA256、帧区间、所声明与实际应用的裁切、工具/字体、输出哈希及误差。`verify` 发现源图、规格、清单、工具、字体或输出变化时返回 `stale:true`，应重建并复审。索引自身哈希须另记在项目审查记录；版本一致不证明确认或观看真实发生。

把预览的生成、播放工具、实际观看范围、发现和未验证项写进阶段 2 报告。若环境不能正常速度连续播放，只报告编码/时长/抽帧是否核对及未实际播放；即使看过静态 layout，也不能据此宣布真人动作速度、表演、真实三维运动或最终连剪通过。
