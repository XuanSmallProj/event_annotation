import argparse
from annotation import AnnotationManager, sort_annotations, Annotation
from utils import VideoMetaData
from typing import Optional, List
import itertools


def check_partition(groupname, annotations: List[Annotation], total_frames=None):
    last = -1
    errs = []
    continual = True
    for i, ann in enumerate(annotations):
        if i == 0:
            if ann.f0 != 0:
                errs.append(f"{groupname}: {ann[i]}应从0开始")
                break
        elif last != ann.f0 - 1:
            errs.append(f"{groupname}: {ann[i-1]}和{ann[i]}不连续")
            continual = False
            break
        last = ann.f1

    if continual:
        if total_frames and last != total_frames:
            errs.append(f"{groupname}中的标签没有覆盖整段视频")
    return errs


def check_non_overlap(groupname, annotations: List[Annotation]):
    errs = []
    last = -1
    for i, ann in enumerate(annotations):
        if i > 0 and ann.f0 <= last:
            errs.append(f"{groupname}: {ann[i-1]}和{ann[i]}有重叠部分")
        last = ann.f1
    return errs


def check_non_overlap2(anns1: List[Annotation], anns2: List[Annotation]):
    errs = []
    for a1 in anns1:
        for a2 in anns2:
            if a1.overlap(a2):
                errs.append(f"{a1}和{a2}有重叠部分")
                break
    return errs


def check(ann_manager: AnnotationManager, video_meta: Optional[VideoMetaData] = None):
    """
    规则：
    1. 相同事件之间不能重叠
    2. 变化事件需要构成对视频的划分
    3. 镜头拉近和镜头拉远之间不能重叠
    4. 视角切换只能是一帧
    5. 视角切换不能在变化事件、镜头情况中的事件的中间或者末尾
    """
    errs = []
    total_frames = video_meta.total_frames if video_meta else None
    if video_meta:
        if video_meta.fps != 25:
            errs.append("视频不是25fps")
    change_annotations = ann_manager.annotations["变化事件"]
    playback_annotations = ann_manager.annotations["回放"]
    camera_annotations = ann_manager.annotations["镜头情况"]
    change_annotations = sort_annotations(change_annotations)
    playback_annotations = sort_annotations(playback_annotations)
    camera_annotations = sort_annotations(camera_annotations)

    errs.extend(check_partition("变化事件", change_annotations, total_frames))
    errs.extend(check_non_overlap("变化事件", change_annotations))

    for e_name in ann_manager.event_groups["回放"].event_names:
        anns = [ann for ann in playback_annotations if ann.event_name == e_name]
        errs.extend(check_non_overlap("回放", anns))

    for e_name in ann_manager.event_groups["镜头情况"].event_names:
        anns = [ann for ann in camera_annotations if ann.event_name == e_name]
        errs.extend(check_non_overlap("镜头情况", anns))

    zoom_in_annotations = [
        ann for ann in camera_annotations if ann.event_name == "镜头拉近"
    ]
    zoom_out_annotations = [
        ann for ann in camera_annotations if ann.event_name == "镜头拉远"
    ]
    viewpoint_annotations = [
        ann for ann in camera_annotations if ann.event_name == "视角切换"
    ]

    errs.extend(check_non_overlap2(zoom_in_annotations, zoom_out_annotations))

    # 视角切换只能有一帧，并且和除回放外的事件，要么与它不重叠，要么在它的开头
    for vp_ann in viewpoint_annotations:
        if vp_ann.f0 != vp_ann.f1:
            errs.append(f"{vp_ann}超过一帧")
        else:
            for ann in itertools.chain(
                change_annotations, camera_annotations
            ):
                if ann.f0 == vp_ann.f0:
                    continue
                if ann.f0 < vp_ann.f0 and ann.f1 >= vp_ann.f0:
                    errs.append(f"{vp_ann}切割了{ann}")

    return errs


def check_from_file(ann_path, video_path=None):
    ann_manager = AnnotationManager.from_json("event.json")
    ann_manager.parse_annotations_from_file(ann_path)
    if video_path:
        video_meta = VideoMetaData.from_path(video_path)
    else:
        video_meta = None
    return check(ann_manager, video_meta)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--annotation", required=True, help="标注路径")
    parser.add_argument("-p", "--path", default="", help="视频路径")
    opt = parser.parse_args()
    v_path = opt.path if opt.path else None
    err_list = check_from_file(opt.annotation, v_path)
    for err in err_list:
        print(err)


if __name__ == "__main__":
    main()
