import wandb
import torch

from detectron2.utils.events import EventWriter, get_event_storage
from detectron2.config import CfgNode

from collections import defaultdict

class WandbWriter(EventWriter):
    def __init__(self, cfg: CfgNode, window_size=20) -> None:
        self.window_size = window_size
        self.cfg = cfg

        wandb.config.update(cfg)
        self.seed = torch.initial_seed() % 2**31
        wandb.summary['seed'] = self.seed

        self._last_iters = defaultdict(lambda: -1)


    def write(self):
        storage = get_event_storage()

        for k, (v, record_iter) in storage.latest_with_smoothing_hint(self.window_size).items():
            # avoid unnecessary log
            if self._last_iters[k] < record_iter:
                self._last_iters[k]=record_iter
                wandb.log({k: v}, step=record_iter)
        

    def close(self):
        wandb.finish()
    