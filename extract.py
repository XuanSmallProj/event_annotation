import cv2
from vstat import get_ann_video_list, get_mp4_list
from utils import annotations_from_str, VideoMetaData, TimeStamp
import json


def get_extract_meta():
    try:
        with open("extract.json", "r") as f:
            return json.load(f)
    except:
        return {}

def store_extract_meta(meta):
    with open("extract.json", "w") as f:
        json.dump(meta, f)

def extract_name_split(vname: str):
    p = vname.rfind("_")
    origin_name = vname[:p]
    index = int(vname[p+1:])
    return origin_name, index

def extract():
    meta = get_extract_meta()
    mp4_list = get_mp4_list()
    ann_list, _ = get_ann_video_list()

    for v in mp4_list:
        if v in ann_list and v not in meta:
            with open(f"dataset/annotate/{v}.txt") as f:
                s = f.read()
                anns = annotations_from_str(s)
            info = {}
            for i, ann in enumerate(anns):
                info[i] = [str(ann[0]), str(ann[1])]
            meta[v] = info

            cap = cv2.VideoCapture(f"dataset/{v}.mp4")
            fps = cap.get(cv2.CAP_PROP_FPS)
            imgsz = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            total_frame = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            metadata = VideoMetaData(f"dataset/{v}.mp4", total_frame, fps)

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

    store_extract_meta(meta)

if __name__ == "__main__":
    extract()
