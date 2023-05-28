import os
import utils
import clip
import json
from typing import Tuple


def get_ann_lines(video_name):
    """
    获得annotate目录下标签的数目总和
    """
    cnt = 0
    with open(f"dataset/annotate/{video_name}.txt") as f:
        for line in f.readlines():
            if line:
                cnt += 1
    return cnt


def get_ann_video_list() -> Tuple[list, list]:
    """
    获得带有annotate的video列表, 同时返回是否有足够多的annotation的video列表
    """
    annotate_path = "dataset/annotate"
    files = os.listdir(annotate_path)
    videos = []
    completed = []
    for file in files:
        video_name = utils.get_video_name(file)
        if file.endswith(".txt"):
            videos.append(video_name)
            if get_ann_lines(video_name) >= 1:
                completed.append(video_name)
    return videos, completed


def get_mp4_list():
    """
    获得dataset目录下mp4的文件列表
    """
    dataset_path = "dataset/"
    videos = []
    for file in os.listdir(dataset_path):
        if file.endswith(".mp4"):
            videos.append(utils.get_video_name(file))
    return videos


def get_clip_list(category="football"):
    """
    获得原MultiSports里有标签的类别为category的视频列表
    """
    football_list = clip.get_clip_videos(category)
    last_video = None
    result = []
    for vname in football_list:
        if vname == last_video:
            continue
        last_video = vname
        result.append(vname)
    return result


def get_full_list():
    """
    获取所有视频的完整列表(无论是否已经标注, 无论是否在Multisports数据集中)(需要有full_list)
    """
    videos = []
    with open("full_list.txt") as f:
        lines = f.readlines()
        for line in lines:
            if line:
                videos.append(utils.get_video_name(line))
    return videos


def get_extract_meta():
    try:
        with open("extract.json", "r") as f:
            return json.load(f)
    except:
        return {}


def save_extract_meta(meta):
    with open("extract.json", "w") as f:
        json.dump(meta, f)


def get_without_extract(meta=None):
    if meta is None:
        meta = get_extract_meta()
    ann_list, _ = get_ann_video_list()
    to_download = []

    for v in ann_list:
        if v not in meta:
            to_download.append(v)
    return to_download


def statistic():
    clip_videos = list(clip.get_clip_videos("football"))
    ann_videos, completed = get_ann_video_list()
    mp4_list = get_mp4_list()
    full_list = get_full_list()

    print(f"Total videos: {len(full_list)}")
    print(f"Total video clip with MultiSports annotation: {len(clip_videos)}")
    print(f"Complete {len(completed)} videos")

    total_ann = 0
    for video_name in completed:
        total_ann += get_ann_lines(video_name)
    print(f"Total {total_ann} annotations")


if __name__ == "__main__":
    statistic()
