# 标注工具

## 环境
当前目录下有含MultiSports标注信息的`match_matched_clips.csv`文件，并且有`dataset/annotate`文件夹。

依赖：PySide6，pandas，numpy

## 简易使用流程
切换到当前目录，`python main.py`打开窗口，点击左上角Open打开视频文件，移动到相关位置，点击Mark按钮表示当前标注的开始，到了结束的一帧再点击Mark就能够将这两个时间点记录在`dataset/annotate`下的相关文件里。

## 部分功能介绍

Mark按钮：在当前帧进行标记。第一次按下表示片段的开始，之后按钮变绿的时候按下表示片段的结束。在按钮为红色的时候表示不可在当前帧进行标记，依赖的逻辑为：
1. 标记片段之间不可重叠。
2. 标记点不可在MultiSports视频片段内部（不可分割MultiSports视频片段）。
3. 标记片段长为2-3分钟。

Cancel按钮：在标记了当前片段的开始后按下cancel按钮，表示放弃这个标记。

BreakPoint按钮：在当前帧打断点，方便之后跳转到当前帧。

Play Rate：倍速播放

### Tabs
右边有annotation、clips、breakpoints三个tab，双击tab中的内容可以跳转到相应的帧。annotation表示当前标记内容，clips表示当前视频相关的MultiSports片段，方便在片段附近进行标记，breakpoints提供了一个书签的作用。
