import pandas
from utils import TimeStamp

clip = None

def init_clip(path):
    global clip
    clip = pandas.read_csv(path)

def query_clip(video_name):
    global clip
    result = []
    part = clip[clip["video"] == video_name]
    start_part = part["start"]
    end_part = part["end"]
    for i in range(len(start_part)):
        start, end = start_part.iloc[i], end_part.iloc[i]
        start_ts = TimeStamp.from_str(start)
        end_ts = TimeStamp.from_str(end)
        result.append((start_ts, end_ts))
    return result

def get_clip_videos(sport):
    global clip
    result = []
    part = clip[clip["sport"] == sport]
    return list(set(part["video"]))

if __name__ == "__main__":
    init_clip("match_matched_clips.csv")
    query_clip("v_-hhDbvY5aAM")
