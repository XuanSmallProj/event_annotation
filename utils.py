import os

def get_video_name(path):
    basename = os.path.basename(path)
    return os.path.splitext(basename)[0]
