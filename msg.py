from enum import IntEnum, auto

class MsgType(IntEnum):
    # cmd to video
    CLOSE = 0
    OPEN = auto()
    READ = auto()
    FRAME_ACK = auto()
    OPEN_ACK = auto()

    # video to cmd
    VIDEO_OPEN_ACK = auto()
    VIDEO_FRAMES = auto()

    # view to cmd
    VIEW_OPEN = auto()
    VIEW_OPEN_ANN = auto()
    VIEW_PAUSE = auto()
    VIEW_PLAY = auto()
    VIEW_TOGGLE = auto()
    VIEW_SEEK = auto()
    VIEW_PLAYRATE = auto()
    VIEW_NAVIGATE = auto()

class Msg:
    def __init__(self, type: MsgType, v_id: int, data) -> None:
        self.type = type
        self.v_id = v_id
        self.data = data
