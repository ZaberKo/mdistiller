import argparse

import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed

from mdistiller.engine.cfg import CFG as cfg
from mdistiller.engine.utils import log_msg


def run(cmds, gpu_ids):
    cmds = cmds.copy()
    cmds.insert(0, f'CUDA_VISIBLE_DEVICES={",".join(gpu_ids)}')
    cmd_str = " ".join(cmds)
    print(f'Running: {cmd_str}')
    subprocess.run(cmd_str, shell=True, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser("training for knowledge distillation.")
    parser.add_argument("--cfg", type=str, default="")
    parser.add_argument("--ngpu_per_test", type=int, default=1)
    parser.add_argument("--num_tests", type=int, default=1)
    parser.add_argument("--suffix", type=str, nargs="?", default="", const="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--data_workers", type=int, default=None)
    parser.add_argument("--port", type=int, default=29400)
    parser.add_argument("opts", nargs="*")

    args = parser.parse_args()
    cfg.merge_from_file(args.cfg)
    cfg.merge_from_list(args.opts)
    cfg.freeze()

    allgpu_ids = os.environ.get("CUDA_VISIBLE_DEVICES", "0").split(",")
    allgpu_ids = [int(i) for i in allgpu_ids if i != ""]

    if args.ngpu_per_test > len(allgpu_ids):
        raise ValueError("ngpu_per_test > all gpus")

    print("num_tests:", args.num_tests)
    print("ngpu_per_test:", args.ngpu_per_test)
    print("data_workers:", args.data_workers)

    cmds = ["torchrun",
            "--nproc_per_node", str(args.ngpu_per_test),
            "--nnodes", "1",
            "--master_port", "",
            "-m", "tools.train_ddp",
            "--cfg", args.cfg,
            "--group", "--id", "",
            "--record_loss"]
    if args.data_workers:
        cmds.append("--data_workers")
        cmds.append(str(args.data_workers))
    if args.suffix != "":
        cmds.append("--suffix")
        cmds.append(args.suffix)
    if args.resume:
        cmds.append("--resume")
    cmds.extend(args.opts)

    executor = ProcessPoolExecutor(args.num_tests)

    try:
        gpu_cnt = 0
        tasks = []
        for i in range(args.num_tests):
            _cmds = cmds.copy()
            # host_ip:
            _cmds[6] = str(args.port+i)
            # id:
            _cmds[13] = str(i)

            gpu_ids = []
            for _ in range(args.ngpu_per_test):
                gpu_ids.append(str(allgpu_ids[gpu_cnt]))
                gpu_cnt = (gpu_cnt+1) % len(allgpu_ids)

            tasks.append(
                executor.submit(run, _cmds, gpu_ids=gpu_ids)
            )

        for future in as_completed(tasks):
            future.result()

    except BaseException as e:
        print(e)
        # mostly handle keyboard interrupt
        print(log_msg("Training failed", "ERROR"))
    finally:
        executor.shutdown(wait=True)
