from .occ_loader import load_occ_core


class OCCKernel:
    def __init__(self):
        self.core = load_occ_core()

    def create_box_mesh(self, width, depth, height):
        return self.core.make_box_mesh(float(width), float(depth), float(height))
