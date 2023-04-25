# 标注工具

## 环境
当前目录下有含MultiSports标注信息的`match_matched_clips.csv`文件，并且有`dataset/annotate`文件夹。

依赖：PySide6，pandas，numpy, cv2

## 标注说明

### 标签
标签设置信息存储在`event.json`文件中。标签分为“变化事件”和“镜头情况”两类，“变化事件”不允许重叠。每个标签属于"shot"或者"interval"，"shot"表示该标签对应一个时间戳，而"interval"表示标签对应一段时间。目前仅有“视角切换”事件属于“shot”类。

注意事项：
1. 最终标注中标注的区间的并集应该覆盖整段视频。
2. 变化事件不允许重叠。

## 标注工具使用说明
需要在项目目录下新建`dataset`文件夹，事件标签放在`dataset/annotate/event`下。

标签文件名为：`<video_name>.txt`，每个标签为一行，格式为`标签名,起始帧,终止帧`。

准备好相关环境之后，使用`python main.py`即可启动标注工具。

### 操作
空格：暂停/播放

playrate：倍速播放

A/D：暂停时候进行后退/前进，连续按键会加速
