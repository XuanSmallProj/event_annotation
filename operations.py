import subprocess
from vstat import (
    get_ann_lines,
    get_ann_video_list,
    get_mp4_list,
    get_full_list,
    get_clip_list,
    get_extract_meta,
    save_extract_meta,
    get_without_extract,
)
import os
import json
from utils import annotations_from_str, VideoMetaData, TimeStamp
import cv2


def download_sh(video):
    subprocess.run(f"./dataset/download_expect.sh {video}", shell=True)


def download_videos(videos):
    print(f"donwload videos {videos}")
    for video in videos:
        download_sh(video)


def remove_mp4_with_ann():
    """
    Remove mp4 with annotations
    """
    if os.path.exists("dataset/download.json"):
        with open("dataset/download.json", "r", encoding="utf-8") as f:
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
        with open("dataset/download.json", "w", encoding="utf-8") as f:
            json.dump(downloaded, f)


def download_without_ann(cnt, from_full_list=False):
    print(f"Download {cnt} videos")
    video_has, _ = get_ann_video_list()
    video_has = set(video_has)
    mp4_has = set(get_mp4_list())
    if from_full_list:
        clip_list = get_full_list()
    else:
        clip_list = get_clip_list()

    if os.path.exists("dataset/download.json"):
        with open("dataset/download.json", "r", encoding="utf-8") as f:
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

    with open("dataset/download.json", "w", encoding="utf-8") as f:
        json.dump(downloaded, f)


def remove_mp4_with_extract(meta=None):
    if meta is None:
        meta = get_extract_meta()
    mp4_list = get_mp4_list()
    for mp4 in mp4_list:
        if mp4 in meta:
            mp4_path = f"dataset/{mp4}.mp4"
            print(f"remove {mp4_path}")
            os.remove(mp4_path)


def download_without_extract(cnt, meta=None):
    if meta is None:
        meta = get_extract_meta()
    to_download = get_without_extract(meta=meta)
    to_download = to_download[:cnt]
    download_videos(to_download)
    return len(to_download)


def extract(v, meta=None):
    if meta is None:
        meta = get_extract_meta()

    with open(f"dataset/annotate/{v}.txt", encoding="utf-8") as f:
        s = f.read()
        anns = annotations_from_str(s)
    info = {}
    for i, ann in enumerate(anns):
        info[i] = [str(ann[0]), str(ann[1])]
    meta[v] = info

    cap = cv2.VideoCapture(f"dataset/{v}.mp4")
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"Extract {v}, fps: {fps}, {len(info)} clips.")

    imgsz = (
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    metadata = VideoMetaData(f"dataset/{v}.mp4", total_frames, fps)

    for k, val in info.items():
        t0, t1 = TimeStamp.from_str(val[0]), TimeStamp.from_str(val[1])
        fstart, fend = metadata.time_to_frame(t0), metadata.time_to_frame(t1)
        v_writer = cv2.VideoWriter(f"dataset/parts/{v}_{k}.mp4", fourcc, fps, imgsz)

        iframe = fstart
        cap.set(cv2.CAP_PROP_POS_FRAMES, fstart)
        print(f"{fstart} {fend} {(fend - fstart) / fps}")
        while iframe <= fend:
            ret, frame = cap.read()
            v_writer.write(frame)
            iframe += 1
        v_writer.release()
    save_extract_meta(meta)
    return meta


def extract_all(meta=None, exclude=None):
    if exclude is None:
        exclude = []
    exclude = set(exclude)
    if meta is None:
        meta = get_extract_meta()
    mp4_list = get_mp4_list()
    ann_list, _ = get_ann_video_list()

    for v in mp4_list:
        if v in ann_list and v not in meta and v not in exclude:
            meta = extract(v, meta=meta)

    return meta


def download_extract_remove_loop(cnt=None):
    """
    下载尚未抽帧的视频，并且下载完毕之后将原来的视频删除
    """
    import multiprocessing as mp

    def f_downloader(q: mp.Queue, v_list):
        import time

        for v in v_list:
            while True:
                mp4_list = get_mp4_list()
                if len(mp4_list) > 50:
                    time.sleep(60)
                else:
                    break
            print(f"Download {v}")
            download_sh(v)
            print(f"Download {v} finished")
            q.put(v, block=False)
        print("All downloaded")
        q.put(None, block=False)

    def f_extractor(q0: mp.Queue, q1: mp.Queue):
        meta = get_extract_meta()
        while True:
            v = q0.get(block=True)
            if v is not None:
                print("Extract start")
                meta = extract(v, meta=meta)
                print("Extract finished")
                q1.put(meta, block=False)
            else:
                print("Extractor quit")
                break
        q1.put(False, block=False)

    def f_remover(q: mp.Queue):
        while True:
            res = q.get(block=True)
            if isinstance(res, dict):
                print("Remove")
                remove_mp4_with_extract(meta=res)
            else:
                print("Remover quit")
                break

    q0 = mp.Queue()
    q1 = mp.Queue()
    to_download = get_without_extract()
    if cnt:
        to_download = to_download[:cnt]
    p_downloader = mp.Process(target=f_downloader, args=(q0, to_download))
    p_extractor = mp.Process(target=f_extractor, args=(q0, q1))
    p_remover = mp.Process(target=f_remover, args=(q1,))
    p_downloader.start()
    p_extractor.start()
    p_remover.start()
    p_downloader.join()
    p_extractor.join()
    p_remover.join()


def _group_extract():
    class GroupExtract:
        def __init__(self):
            self.meta = get_extract_meta()
            self.videos = list(self.meta.keys())

        def __len__(self):
            return len(self.meta)

        def __getitem__(self, i):
            v = self.videos[i]
            clip = self.meta[v]
            clips = list(clip.keys())
            return v, clips

    return GroupExtract()


def group_and_check():
    """
    检查dataset/part下是否含有全部视频片段
    """
    extracts = _group_extract()
    for i in range(len(extracts)):
        v, clips = extracts[i]
        lines = get_ann_lines(v)
        if lines != len(clips):
            print(f"Error: {v} has {lines} clips but found {len(clips)} extracts")
            return False
        for j in clips:
            v_path = os.path.join("dataset", "parts", f"{v}_{j}.mp4")
            if not os.path.exists(v_path):
                print(f"Error: {v_path} not found")
                return False
    return True


def group_and_zip():
    """
    将dataset/part下的视频打包放到dataset/zip中
    """
    extracts = _group_extract()
    for i in range(len(extracts)):
        v, clips = extracts[i]
        videos = [os.path.join("dataset", "parts", f"{v}_{j}.mp4") for j in clips]
        str_videos = " ".join(videos)
        print(f"{i}: compress {v}: {str_videos}")
        subprocess.run(f"zip -r dataset/zip/{v}.zip {str_videos}", shell=True)


if __name__ == "__main__":
    extract_all()