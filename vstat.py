import os
import argparse
import sys
import utils
import clip
import subprocess
import json
from typing import Tuple

def get_ann_lines(video_name):
    cnt = 0
    with open(f"dataset/annotate/{video_name}.txt") as f:
        for line in f.readlines():
            if line:
                cnt += 1
    return cnt

def get_ann_video_list() -> Tuple[list, list]:
    """
    Videos with annotation
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
    All mp4 files under dataset/
    """
    dataset_path = "dataset/"
    videos = []
    for file in os.listdir(dataset_path):
        if file.endswith(".mp4"):
            videos.append(utils.get_video_name(file))
    return videos

def download_sh(video):
    th = subprocess.run(f"./download_expect.sh {video}", shell=True)
    

def get_clip_list():
    football_list = clip.get_clip_videos("football")
    last_video = None
    result = []
    for vname in football_list:
        if vname == last_video:
            continue
        last_video = vname
        result.append(vname)
    return result

def get_full_list():
    videos = []
    with open("full_list.txt") as f:
        lines = f.readlines()
        for line in lines:
            if line:
                videos.append(utils.get_video_name(line))
    return videos

def download(cnt, from_full_list=False):
    print(f"Download {cnt} videos")
    video_has, _ = get_ann_video_list()
    video_has = set(video_has)
    mp4_has = set(get_mp4_list())
    if from_full_list:
        clip_list = get_full_list()
    else:
        clip_list = get_clip_list()

    if os.path.exists("dataset/download.json"):
        with open("dataset/download.json", "r") as f:
            downloaded = json.load(f)
    else:
        downloaded = {"videos": []}

    old_cwd = os.getcwd()
    dataset_path = os.path.join(os.path.dirname(__file__), "dataset")
    os.chdir(dataset_path)
    for clipname in clip_list:
        if cnt <= 0:
            break
        if clipname not in video_has and clipname not in mp4_has:
            download_sh(clipname)
            downloaded["videos"].append(clipname)
            cnt -= 1
    os.chdir(old_cwd)

    with open("dataset/download.json", "w") as f:
        json.dump(downloaded, f)

def remove():
    if os.path.exists("dataset/download.json"):
        with open("dataset/download.json", "r") as f:
            downloaded = json.load(f)
    else:
        downloaded = None
    _, video_completed = get_ann_video_list()
    video_completed = set(video_completed)
    mp4_list = get_mp4_list()
    for mp4 in mp4_list:
        mp4_path = f"dataset/{mp4}.mp4"
        if mp4 in video_completed:
            print(f"remove {mp4_path}")
            os.remove(mp4_path)
            if downloaded:
                new_videos = [video for video in downloaded["videos"] if video != mp4]
                downloaded["videos"] = new_videos

    # todo: save donwloaded
    if downloaded:
        with open("dataset/download.json", "w") as f:
            json.dump(downloaded, f)


def statatistic():
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
    clip.init_clip("match_matched_clips.csv")
    parser = argparse.ArgumentParser("statistics")
    parser.add_argument("-d", "--download", type=int)
    parser.add_argument("-r", "--remove", action="store_true")
    parser.add_argument("-s", "--statistic", action="store_true")
    opts = parser.parse_args()

    if opts.statistic:
        statatistic()
    if opts.remove:
        remove()
    if opts.download:
        download(opts.download, from_full_list=True)
