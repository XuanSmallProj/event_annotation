class Config:
    # 每次回退/前进算多少帧，可以是数字或者“exp”，exp代表连续按键会导致回退/前进的帧数指数增加，直到到达最大值
    FRAME_PER_BACK = 5
    FRAME_PER_FORWARD = "exp"
    FRAME_MOVE_MAX = 16  # 指数回退/前进的最大值
