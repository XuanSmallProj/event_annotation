import argparse
from annotation import AnnotationManager, sort_annotations, Annotation
from utils import VideoMetaData
from typing import Optional, List
import itertools
import glob
import os


def check_partition(groupname, annotations: List[Annotation], total_frames=None):
    last = -1
    errs = []
    continual = True
    for i, ann in enumerate(annotations):
        if i == 0:
            if ann.f0 != 0:
                errs.append(f"{groupname}: {annotations[i]}应从0开始")
                break
        elif last != ann.f0 - 1:
            errs.append(f"{groupname}: {annotations[i-1]}和{annotations[i]}不连续")
            continual = False
            break
        last = ann.f1

    if continual:
        if total_frames and last + 1 != total_frames:
            errs.append(f"{groupname}中的标签没有覆盖整段视频")
    return errs


def check_non_overlap(groupname, annotations: List[Annotation]):
    errs = []
    last = -1
    for i, ann in enumerate(annotations):
        if i > 0 and ann.f0 <= last:
            errs.append(f"{groupname}: {annotations[i-1]}和{annotations[i]}有重叠部分")
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
    6. 回放中的事件要么包含整个切换事件，要么与切换事件没有交集
    7. 一般情况下切换事件与镜头事件无交集，除非是手册中指明的特殊情况
    8. 不能出现两个连续的切换事件
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
    
    switch_annotations = [ann for ann in change_annotations if ann.event_name == "切换"]

    # 回放中的事件要么包含整个切换事件，要么与切换事件没有交集
    for pb_ann in playback_annotations:
        for sw_ann in switch_annotations:
            if pb_ann.overlap(sw_ann) and not pb_ann.contain(sw_ann):
                errs.append(f"{pb_ann}与{sw_ann}相交")
    
    # 一般情况下切换事件与镜头事件无交集，除非是手册中指明的特殊情况
    for sw_ann in switch_annotations:
        special = False
        for cm_ann in camera_annotations:
            if cm_ann.event_name == "视角切换":
                continue
            elif cm_ann.equal(sw_ann):
                special = True
            else:
                prev_shot, next_shot = False, False
                # 如果前/后存在变化事件起止都是同一帧，那么说明该切换应该向前/后延长一帧再与镜头事件进行比较
                for ch_ann in change_annotations:
                    if ch_ann.f0 == ch_ann.f1:
                        if ch_ann.f0 == sw_ann.f0 - 1:
                            prev_shot = True
                        elif ch_ann.f0 == sw_ann.f1 + 1:
                            next_shot = True
                f0, f1 = sw_ann.f0, sw_ann.f1
                if prev_shot:
                    f0 -= 1
                if next_shot:
                    f1 += 1
                prev_match = sw_ann.f0 == cm_ann.f0 or f0 == cm_ann.f0
                after_match = sw_ann.f1 == cm_ann.f1 or f1 == cm_ann.f1
                if prev_match and after_match:
                    special = True

            if special:
                break

        if special:
            continue

        for cm_ann in camera_annotations:
            if cm_ann.overlap(sw_ann):
                errs.append(f"{cm_ann}与{sw_ann}相交")

    # 不能出现两个连续的切换事件
    for sw_ann1 in switch_annotations:
        for sw_ann2 in switch_annotations:
            if sw_ann1.f0 == sw_ann2.f1 + 1:
                errs.append(f"{sw_ann2}与{sw_ann1}连续")
                break

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
    v_path = glob.glob(opt.path, recursive=True) if opt.path else []
    a_path = glob.glob(opt.annotation, recursive=True)

    for ann_path in a_path:
        basename = os.path.basename(ann_path)
        video_name, ext = os.path.splitext(basename)
        if ext != ".txt":
            continue
        video_path = None
        for v in v_path:
            basename = os.path.basename(v)
            v_name, ext = os.path.splitext(basename)
            if ext == ".mp4" and v_name == video_name:
                video_path = v
                break

        print(f"{video_name} {ann_path} {video_path}:")
        err_list = check_from_file(ann_path, video_path)
        if not err_list:
            print("No problem")
        else:
            for err in err_list:
                print(err)


if __name__ == "__main__":
    main()
