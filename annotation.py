from typing import List, Dict
import functools
import json


class Annotation:
    def __init__(self, event_name, type) -> None:
        self.event_name = event_name
        self.type = type
        self.f0 = 0
        self.f1 = 0

    def overlap(self, e: "Annotation") -> bool:
        if self.f1 >= e.f0 and self.f1 <= e.f1:
            return True
        if e.f1 >= self.f0 and e.f1 <= self.f1:
            return True
        return False
    
    def contain(self, e: "Annotation") -> bool:
        if self.f0 <= e.f0 and self.f1 >= e.f1:
            return True
        return False
    
    def equal(self, e: "Annotation") -> bool:
        return self.contain(e) and e.contain(self)

    def __str__(self):
        return f"{self.event_name},{self.f0},{self.f1}"


def sort_annotations(annotations, ascend=True) -> List[Annotation]:
    def cmp(e0, e1):
        if e0.f0 == e1.f0:
            return e0.f1 - e1.f1
        else:
            return e0.f0 - e1.f0

    return sorted(annotations, key=functools.cmp_to_key(cmp), reverse=(not ascend))


class EventGroup:
    def __init__(self, group_name, meta) -> None:
        self.group_name = group_name
        self.allow_overlap = meta["_overlap"]
        self.table_id = meta["_table"]
        self.event_names = []
        self.event_types = []  # interval/shot
        for k, v in meta.items():
            if not k.startswith("_"):
                self.event_names.append(k)
                self.event_types.append(v)

    def get_type(self, name):
        for n, tp in zip(self.event_names, self.event_types):
            if n == name:
                return tp
        return None

    def has(self, name):
        return name in self.event_names


class AnnotationManager:
    def __init__(self, event_groups: Dict[str, EventGroup]) -> None:
        self.event_groups = event_groups
        self.annotations: Dict[str, List[Annotation]] = {}
        self.comments = {}
        for k in event_groups.keys():
            self.annotations[k] = []

    @classmethod
    def from_json(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        event_groups = {}
        for k, v in config.items():
            event_groups[k] = EventGroup(k, v)
        return cls(event_groups)

    def get_all_events(self) -> List[str]:
        result = []
        for group in self.event_groups.values():
            result.extend(group.event_names)
        return result

    def get_event_group(self, event_name):
        for _, v in self.event_groups.items():
            if v.has(event_name):
                return v
        raise ValueError(f"invalid event: {event_name}")

    def get_event_type(self, event_name):
        return self.get_event_group(event_name).get_type(event_name)

    def event_allow_overlap(self, event_name):
        return self.get_event_group(event_name).allow_overlap

    def add_annotation(self, event_name, start_frame, end_frame):
        group = self.get_event_group(event_name)
        type = group.get_type(event_name)
        e = Annotation(event_name, type)
        e.f0, e.f1 = start_frame, end_frame
        self.annotations[group.group_name].append(e)

    def parse_annotations(self, s: str):
        self.clear_annotations()
        for line in s.split("\n"):
            if not line:
                continue
            if line[0] == "#":
                # comment
                key, value = line[1:].split(":")
                key, value = key.strip(), value.strip()
                self.comments[key] = value
            else:
                lst = line.split(",")
                event_name = lst[0]
                self.add_annotation(event_name, int(lst[1]), int(lst[2]))

    def parse_annotations_from_file(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            return self.parse_annotations(f.read())

    def clear_annotations(self):
        keys = list(self.annotations.keys())
        self.comments = {}
        for k in keys:
            self.annotations[k] = []

    def sort(self):
        for k, ann in self.annotations.items():
            self.annotations[k] = sort_annotations(ann)

    def save(self, path):
        self.sort()
        all_comments = []
        for k, v in self.comments.items():
            all_comments.append(f"# {k}: {v}")
        all_anns = []
        for k, ann in self.annotations.items():
            all_anns.extend([str(a) for a in ann])
        
        content = "\n".join(all_comments + all_anns)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def check_overlap_conflict(self):
        for group_name, ann in self.annotations.items():
            if not self.event_groups[group_name].allow_overlap:
                for i in range(len(ann)):
                    for j in range(i + 1, len(ann)):
                        if ann[i].overlap(ann[j]):
                            return True
        return False
    
    def modify_annotation(self, group_name, idx, event_name, start_frame, end_frame):
        anns = self.annotations[group_name]
        assert anns[idx].event_name == event_name
        tp = self.event_groups[group_name].get_type(event_name)
        if tp == "interval":
            if start_frame <= end_frame:
                anns[idx].f0, anns[idx].f1 = start_frame, end_frame
        else:
            if start_frame == end_frame:
                anns[idx].f0, anns[idx].f1 = start_frame, end_frame

    def remove_annotations(self, group_name, indexes: List[int]):
        anns = self.annotations[group_name]
        to_remove = set(indexes)
        anns = [ann for i, ann in enumerate(anns) if i not in to_remove]
        self.annotations[group_name] = anns

    def annotations_tuple_list(self):
        result = {}
        for group_name, anns in self.annotations.items():
            lst = [(ann.event_name, ann.f0, ann.f1) for ann in anns]
            result[group_name] = lst
        return result
